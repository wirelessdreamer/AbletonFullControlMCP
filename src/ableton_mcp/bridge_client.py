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


class AbletonBridgeError(RuntimeError):
    """Raised when the bridge returns ok=false or speaks badly."""


class AbletonBridgeTimeout(AbletonBridgeError):
    """Raised when the bridge does not respond in time."""


class AbletonBridgeUnavailable(AbletonBridgeError):
    """Raised when we cannot connect to the bridge at all."""


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
    """

    def __init__(self, cfg: BridgeConfig | None = None) -> None:
        self._cfg = cfg or BridgeConfig.from_env()
        self._next_id = 1
        self._lock = asyncio.Lock()

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
