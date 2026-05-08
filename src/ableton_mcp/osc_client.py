"""Async client for AbletonOSC.

AbletonOSC (https://github.com/ideoforms/AbletonOSC, BSD-3-Clause) is a Live
Remote Script by Daniel Jones that exposes the Live Object Model over OSC. We
install it unmodified into the user's Live User Library
(scripts/install_abletonosc.py) — this file is the Python side that drives it.
The OSC framing itself is `python-osc` (Antoine Beauquesne, BSD-style); see
NOTICE.md at the repo root for the full third-party attribution.

AbletonOSC listens on UDP/11000 and sends replies on UDP/11001. There is no
request id in the protocol — replies use the same address as the request and
the leading positional args (the LOM selectors: track_id, device_id, ...).

Correlation strategy: each pending request is keyed on
`(address, args_prefix)` where ``args_prefix`` is the tuple of leading args we
sent. When a reply arrives we try increasingly specific prefix matches: a
reply with args ``(0, "Lead")`` is checked against waiters keyed on
``(addr, (0, "Lead"))`` first, then ``(addr, (0,))``, then ``(addr, ())``. We
take the first key with pending waiters and pop the FIFO-oldest waiter from
that bucket. AbletonOSC processes calls on Live's main thread, but this lets
us issue concurrent ``track/get/name`` requests for different ``track_id`` s
without crossing the wires.
"""

from __future__ import annotations

import asyncio
import errno
import logging
from collections import defaultdict, deque
from typing import Any, Iterable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from .config import Config

log = logging.getLogger(__name__)


# Windows reports EADDRINUSE as WSAEADDRINUSE = 10048 via WinError; on
# POSIX it's errno.EADDRINUSE (98 on Linux, 48 on macOS). Match either.
_ADDR_IN_USE_ERRNOS = {errno.EADDRINUSE, 10048}


class AbletonOSCError(RuntimeError):
    pass


class AbletonOSCTimeout(AbletonOSCError):
    pass


class AbletonOSCAddressInUse(AbletonOSCError):
    """Raised when the reply port is already bound by another process.

    The most common cause is a second MCP host (e.g. Claude Code desktop +
    Claude Code CLI, or Claude Desktop + Cursor) each spawning its own copy
    of this server — only one can hold UDP/recv_port; the rest fail here.
    """


_WaiterKey = tuple[str, tuple[Any, ...]]


class AbletonOSCClient:
    """Send commands to AbletonOSC and await replies."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = SimpleUDPClient(cfg.osc_host, cfg.osc_send_port)
        # Keys are (address, args_prefix). A request sent with args (0,) keys
        # under (addr, (0,)); a reply with args (0, "Lead") will match it via
        # prefix lookup in _on_message.
        self._waiters: dict[_WaiterKey, deque[asyncio.Future[tuple[Any, ...]]]] = defaultdict(deque)
        self._listeners: dict[str, list[asyncio.Queue[tuple[Any, ...]]]] = defaultdict(list)
        self._server: AsyncIOOSCUDPServer | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._loop = asyncio.get_running_loop()
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._on_message)
        self._server = AsyncIOOSCUDPServer(
            (self._cfg.osc_host, self._cfg.osc_recv_port), dispatcher, self._loop
        )
        try:
            self._transport, _ = await self._server.create_serve_endpoint()
        except OSError as exc:
            self._server = None
            self._transport = None
            if exc.errno in _ADDR_IN_USE_ERRNOS or "10048" in str(exc):
                raise AbletonOSCAddressInUse(
                    f"Cannot bind UDP/{self._cfg.osc_recv_port} on "
                    f"{self._cfg.osc_host} — the port is already in use. "
                    "The most likely cause is another MCP host running this "
                    "server (e.g. both Claude Code CLI and the Claude Code "
                    "desktop app, or Claude Desktop + Cursor); each spawns "
                    "its own MCP subprocess and only one can own the OSC "
                    "reply port. Close the other client, or set "
                    "ABLETON_OSC_RECV_PORT (and matching AbletonOSC config) "
                    "to a different port. See docs/TROUBLESHOOTING.md "
                    "'Port conflict' for details."
                ) from exc
            raise AbletonOSCError(
                f"Failed to bind UDP/{self._cfg.osc_recv_port} on "
                f"{self._cfg.osc_host}: {exc}"
            ) from exc
        log.info(
            "OSC client listening on %s:%d, sending to %s:%d",
            self._cfg.osc_host, self._cfg.osc_recv_port,
            self._cfg.osc_host, self._cfg.osc_send_port,
        )

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._server = None

    def _resolve_waiter(self, address: str, args: tuple[Any, ...]) -> asyncio.Future[tuple[Any, ...]] | None:
        """Find the most-specific pending waiter whose key prefix matches `args`.

        Tries longest prefix first (full args) down to the empty prefix. Returns
        the FIFO-oldest future from the first matching bucket, or None.
        """
        for i in range(len(args), -1, -1):
            key = (address, tuple(args[:i]))
            waiters = self._waiters.get(key)
            if waiters:
                fut = waiters.popleft()
                if not waiters:
                    # Tidy up empty buckets so _waiters doesn't grow unbounded.
                    self._waiters.pop(key, None)
                return fut
        return None

    def _on_message(self, address: str, *args: Any) -> None:
        log.debug("recv %s %r", address, args)
        # Resolve the next pending get for this (address, args-prefix), if any.
        fut = self._resolve_waiter(address, args)
        delivered_to_waiter = False
        if fut is not None and not fut.done():
            fut.set_result(args)
            delivered_to_waiter = True
        # Fan out to listeners. Replies with no matching waiter still reach
        # listeners; if neither a waiter nor a listener is interested the
        # message is silently dropped.
        listeners = self._listeners.get(address, ())
        for q in listeners:
            q.put_nowait(args)
        if not delivered_to_waiter and not listeners:
            log.debug("dropped reply %s %r (no waiter, no listener)", address, args)

    def send(self, address: str, *args: Any) -> None:
        """Fire-and-forget OSC send."""
        log.debug("send %s %r", address, args)
        self._client.send_message(address, list(args) if args else [])

    async def request(self, address: str, *args: Any, timeout: float | None = None) -> tuple[Any, ...]:
        """Send and wait for a reply on the same address.

        AbletonOSC always replies to /live/.../get/X on the same address X. The
        reply normally echoes the leading selector args (track_id, etc.); we
        key waiters on (address, args) so concurrent calls with different
        selectors don't cross.
        """
        assert self._loop is not None, "client not started"
        timeout = timeout if timeout is not None else self._cfg.request_timeout
        key: _WaiterKey = (address, tuple(args))
        fut: asyncio.Future[tuple[Any, ...]] = self._loop.create_future()
        self._waiters[key].append(fut)
        self.send(address, *args)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            bucket = self._waiters.get(key)
            if bucket is not None:
                try:
                    bucket.remove(fut)
                except ValueError:
                    pass
                if not bucket:
                    self._waiters.pop(key, None)
            raise AbletonOSCTimeout(
                f"OSC request {address!r} args={args!r} timed out after {timeout}s. "
                "Common causes: (1) Ableton Live isn't open; (2) AbletonOSC isn't "
                "the active Control Surface in Preferences > Link/Tempo/MIDI; "
                "(3) another MCP host (Claude Code/Desktop/Cursor) is also running "
                "this server and is intercepting the reply on UDP/"
                f"{self._cfg.osc_recv_port}. Run live_ping for a structured diagnostic."
            )

    async def ping(self) -> bool:
        """Return True if AbletonOSC responds to /live/test."""
        try:
            args = await self.request("/live/test", timeout=1.5)
            return bool(args) and args[0] == "ok"
        except AbletonOSCTimeout:
            return False

    def listen(self, address: str) -> asyncio.Queue[tuple[Any, ...]]:
        """Subscribe to all incoming messages on `address`. Returns a queue."""
        q: asyncio.Queue[tuple[Any, ...]] = asyncio.Queue()
        self._listeners[address].append(q)
        return q

    def stop_listening(self, address: str, q: asyncio.Queue[tuple[Any, ...]]) -> None:
        if q in self._listeners.get(address, []):
            self._listeners[address].remove(q)


_singleton: AbletonOSCClient | None = None


async def get_client(cfg: Config | None = None) -> AbletonOSCClient:
    """Return the process-wide AbletonOSC client, starting it if needed.

    If startup fails (e.g. the reply port is taken by another MCP instance)
    we clear the singleton so a later retry — after the user closes the
    conflicting client — actually re-attempts the bind.
    """
    global _singleton
    if _singleton is None:
        client = AbletonOSCClient(cfg or Config.from_env())
        try:
            await client.start()
        except Exception:
            # Don't cache a half-initialized client; the next call should retry.
            raise
        _singleton = client
    return _singleton


def chunked(seq: Iterable[Any], n: int) -> Iterable[list[Any]]:
    """Yield successive n-sized chunks from seq."""
    buf: list[Any] = []
    for x in seq:
        buf.append(x)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf
