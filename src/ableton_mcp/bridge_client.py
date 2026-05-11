"""Async JSON-over-TCP client for AbletonFullControlBridge.

AbletonOSC is fast and exposes the LOM but does NOT cover Live's browser, group/freeze/
flatten/consolidate ops, or save_set. AbletonFullControlBridge is a separate Live Remote Script
that runs alongside AbletonOSC and listens on TCP/11002. Each request is a single line
of JSON terminated by a newline; the server replies with a single line of JSON.

Wire format:
    request:  {"id": <int>, "op": "<dotted.handler>", "args": {...}}\n
    response: {"id": <int>, "ok": true,  "result": <any>}\n
              {"id": <int>, "ok": false, "error": "<message>"}\n

Each connection is short-lived (one request → one response → close) so we don't have
to worry about head-of-line blocking. The server runs a poll loop on Live's main
thread (driven by the Remote Script `update_display` heartbeat) so it is safe to call
LOM methods from inside handlers.

PRIOR ART / CREDITS: the architectural shape (one Live Remote Script listening for
external commands and dispatching to LOM) was first popularised by Siddharth Ahuja's
ahujasid/ableton-mcp (https://github.com/ahujasid/ableton-mcp, MIT licence). We use a
different transport (TCP/JSON vs his Python-pickle socket) and a different scope
(complementing AbletonOSC instead of replacing it), but the design owes a debt. See
NOTICE.md at the repo root for the full third-party attribution.

VERIFIED RESPONSE SHAPES — see ``docs/LIVE_API_GOTCHAS.md`` for the full list. Common
ones a caller needs to know:

* ``browser.search`` returns ``{query, category, count, results: [{name, path,
  uri, is_loadable}, ...]}``. The list lives under ``results``, NOT ``items``
  or ``hits``. Empty query string returns 0 hits regardless of category.
* ``browser.load_device`` (NOT ``browser.load``) is the load op for instruments
  and effects. Siblings: ``browser.load_drum_kit`` (drum-category alias) and
  ``browser.load_sample``.
* ``clip.duplicate_to_arrangement(track_index, slot_index, time)`` places a
  Session clip onto the Arrangement timeline at ``time`` beats. Returns
  ``{track_index, slot_index, time, method, lom_returned}``.
* ``system.reload`` re-imports all handler modules in-place. Lets you hot-swap
  handler code without restarting Live or toggling the Control Surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# Bridge protocol version this client was built against. The bridge running
# inside Live reports its own ``BRIDGE_VERSION`` via ``system.version``; on
# a mismatch the client logs an actionable warning and (for older bridges
# that lack a needed handler) raises a clear error from ``require_handler``.
#
# Keep this in sync with ``BRIDGE_VERSION`` in
# ``live_remote_script/AbletonFullControlBridge/bridge_server.py``.
#
# Semver:
#   MAJOR — breaking changes (handler removed/renamed)
#   MINOR — backwards-compatible additions (new handler)
#   PATCH — bug fixes that don't change the surface
EXPECTED_BRIDGE_VERSION = "1.2.0"


class AbletonBridgeError(RuntimeError):
    """Raised when the bridge returns ok=false or speaks badly."""


class AbletonBridgeTimeout(AbletonBridgeError):
    """Raised when the bridge does not respond in time."""


class AbletonBridgeUnavailable(AbletonBridgeError):
    """Raised when we cannot connect to the bridge at all."""


class AbletonBridgeOutdated(AbletonBridgeError):
    """Raised when a caller asks for a handler the running bridge doesn't
    expose. The message tells the user how to fix it (reinstall the Remote
    Script + restart Live)."""


def _parse_semver(s: str) -> tuple[int, int, int]:
    """Lenient semver parse — returns (0, 0, 0) on anything weird so the
    handshake never crashes the client just because Live shipped a build
    that reports a weird version string."""
    try:
        parts = s.split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2].split("-")[0].split("+")[0]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except Exception:
        return (0, 0, 0)


@dataclass(frozen=True)
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 11002
    request_timeout: float = 10.0  # browser ops in big libraries can be slow

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            host=os.environ.get("ABLETON_BRIDGE_HOST", cls.host),
            port=int(os.environ.get("ABLETON_BRIDGE_PORT", cls.port)),
            request_timeout=float(os.environ.get("ABLETON_BRIDGE_TIMEOUT", cls.request_timeout)),
        )


class AbletonBridgeClient:
    """Send JSON requests to AbletonFullControlBridge and await JSON replies.

    One TCP connection per request — simple and avoids any framing concerns.
    Caches the bridge's reported version + handler list on first use so
    callers can ``require_handler(op)`` before making a call that the
    running bridge doesn't know about (typical when the user has upgraded
    the Python package but not reinstalled the Remote Script).
    """

    def __init__(self, cfg: BridgeConfig | None = None) -> None:
        self._cfg = cfg or BridgeConfig.from_env()
        self._next_id = 1
        self._lock = asyncio.Lock()
        # Populated lazily by ``version_info()`` / ``require_handler()``.
        self._version_cache: dict[str, Any] | None = None
        self._version_warning_logged = False

    @property
    def config(self) -> BridgeConfig:
        return self._cfg

    async def call(self, op: str, timeout: float | None = None, **args: Any) -> Any:
        """Call a handler on the bridge. Returns the `result` field on success."""
        async with self._lock:
            req_id = self._next_id
            self._next_id += 1
        timeout = timeout if timeout is not None else self._cfg.request_timeout
        payload = json.dumps({"id": req_id, "op": op, "args": args}, separators=(",", ":")) + "\n"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._cfg.host, self._cfg.port),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise AbletonBridgeTimeout(
                f"AbletonFullControlBridge connect to {self._cfg.host}:{self._cfg.port} timed out"
            ) from exc
        except OSError as exc:
            raise AbletonBridgeUnavailable(
                f"AbletonFullControlBridge not reachable at {self._cfg.host}:{self._cfg.port} "
                f"(is the AbletonFullControlBridge Remote Script enabled in Live's preferences?): {exc}"
            ) from exc

        try:
            writer.write(payload.encode("utf-8"))
            await writer.drain()
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise AbletonBridgeTimeout(
                    f"AbletonFullControlBridge op {op!r} timed out after {timeout}s"
                ) from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # pragma: no cover — closing errors are non-fatal
                pass

        if not line:
            raise AbletonBridgeError("AbletonFullControlBridge closed connection without replying")
        try:
            resp = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AbletonBridgeError(f"AbletonFullControlBridge sent malformed JSON: {line!r}") from exc

        if not isinstance(resp, dict):
            raise AbletonBridgeError(f"AbletonFullControlBridge reply was not an object: {resp!r}")
        if not resp.get("ok"):
            raise AbletonBridgeError(str(resp.get("error", "unknown bridge error")))
        return resp.get("result")

    async def ping(self) -> bool:
        """Return True if AbletonFullControlBridge is reachable."""
        try:
            r = await self.call("system.ping", timeout=1.5)
        except AbletonBridgeError:
            return False
        return bool(r) and (r is True or (isinstance(r, dict) and r.get("ok")))

    async def version_info(self, *, refresh: bool = False) -> dict[str, Any]:
        """Query and cache the bridge's reported version + handler list.

        On the first call (or when ``refresh=True``) this issues
        ``system.version`` and stashes the response under
        ``self._version_cache``. The cache is process-wide — restart the
        client (or call with ``refresh=True``) if the user reinstalls the
        Remote Script mid-session.

        Returns a dict with at least:
            - ``bridge_version`` (str) — semver of the running bridge,
              ``"0.0.0"`` if the bridge predates the handshake
            - ``live_version`` (str | None)
            - ``handlers`` (list[str]) — handler names available
            - ``expected_bridge_version`` (str) — what THIS client expects
            - ``compatible`` (bool) — True iff the major version matches
        """
        if self._version_cache is not None and not refresh:
            return self._version_cache
        try:
            raw = await self.call("system.version", timeout=2.5)
        except AbletonBridgeError as exc:
            # The bridge is unreachable or speaks badly — let callers see
            # the failure on their actual call instead of pretending we
            # have version info.
            raise exc
        if not isinstance(raw, dict):
            raw = {}
        bridge_version = str(raw.get("bridge_version") or "0.0.0")
        live_version = raw.get("live_version")
        handlers = list(raw.get("handlers") or [])

        exp = _parse_semver(EXPECTED_BRIDGE_VERSION)
        got = _parse_semver(bridge_version)
        compatible = exp[0] == got[0]  # major must match
        outdated = got < exp  # bridge is older than client expects

        info = {
            "bridge_version": bridge_version,
            "live_version": live_version,
            "handlers": handlers,
            "expected_bridge_version": EXPECTED_BRIDGE_VERSION,
            "compatible": compatible,
            "outdated": outdated,
        }
        self._version_cache = info

        if not self._version_warning_logged:
            self._version_warning_logged = True
            if not compatible:
                log.error(
                    "AbletonFullControlBridge MAJOR version mismatch: client "
                    "expects %s, bridge reports %s. Some handlers may behave "
                    "differently or be missing. Reinstall the Remote Script "
                    "to match: `python -m ableton_mcp.scripts.install_bridge` "
                    "then restart Ableton Live.",
                    EXPECTED_BRIDGE_VERSION, bridge_version,
                )
            elif outdated:
                log.warning(
                    "AbletonFullControlBridge is outdated (client expects %s, "
                    "bridge reports %s). Some newer handlers may be missing. "
                    "Reinstall: `python -m ableton_mcp.scripts.install_bridge` "
                    "then restart Ableton Live.",
                    EXPECTED_BRIDGE_VERSION, bridge_version,
                )
            else:
                log.debug(
                    "AbletonFullControlBridge version OK (client %s, bridge %s)",
                    EXPECTED_BRIDGE_VERSION, bridge_version,
                )
        return info

    async def require_handler(self, op: str) -> None:
        """Fail-fast guard for callers that depend on a recently-added handler.

        Use this immediately before a bridge call that requires a handler
        introduced in a later bridge version. If the running bridge doesn't
        expose ``op``, raises :class:`AbletonBridgeOutdated` with an
        actionable message (the install command + which version is needed)
        instead of letting the underlying call fail with a cryptic
        ``unknown op`` error from the dispatcher.

        Bridges built before the handshake (``bridge_version`` reported as
        ``"0.0.0"`` because they predate ``BRIDGE_VERSION``) return an
        empty handler list — we treat that as "unknown" and let the call
        proceed; the caller will see a clear error from the original
        dispatch on real mismatch, just without the fail-fast.
        """
        try:
            info = await self.version_info()
        except AbletonBridgeError:
            return  # bridge unreachable — let the actual call surface it
        handlers = info.get("handlers") or []
        if not handlers:
            # Pre-handshake bridge; can't verify, skip the check.
            return
        if op not in handlers:
            raise AbletonBridgeOutdated(
                f"AbletonFullControlBridge handler {op!r} is not available on "
                f"the running bridge (version {info['bridge_version']}). "
                f"This client expects bridge version "
                f"{EXPECTED_BRIDGE_VERSION} or compatible. Reinstall the "
                f"Remote Script and restart Live: "
                f"`python -m ableton_mcp.scripts.install_bridge`."
            )


_singleton: AbletonBridgeClient | None = None


def get_bridge_client(cfg: BridgeConfig | None = None) -> AbletonBridgeClient:
    """Process-wide bridge client. Cheap to call repeatedly."""
    global _singleton
    if _singleton is None:
        _singleton = AbletonBridgeClient(cfg)
    return _singleton


def reset_bridge_client() -> None:
    """Test hook — drop the singleton."""
    global _singleton
    _singleton = None
