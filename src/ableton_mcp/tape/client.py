"""Async OSC client for the AbletonFullControlTape Max for Live device.

Wire protocol (see ``live_max_for_live/AbletonFullControlTape/PROTOCOL.md`` for the
authoritative spec):

    Python -> Max (UDP/11003 by default):
        /tape/ping                    no args, expects /tape/pong
        /tape/record <path> <duration_sec>     start sfrecord~ writing to <path>
                                               for <duration_sec> seconds
        /tape/stop                    bail out of an in-progress record

    Max -> Python (UDP/11004 by default):
        /tape/pong                    reply to /tape/ping
        /tape/done <path> <duration_actual>    record complete, wav written
        /tape/error <message>         something went wrong (path bad, etc.)

Like ``osc_client.AbletonOSCClient`` we run a UDP listener on the recv port
and key pending requests on ``(reply_address, args_prefix)`` so that two
concurrent ``record(...)`` calls writing to different paths can be correlated
by their ``path`` arg in the FIFO bucket. The server is single-threaded
(Max main thread) so heavy concurrency is not expected, but the discipline
matches the rest of the codebase.

The class also exposes a :meth:`use_external_send` hook so tests can plug a
fake-server response loop in without going through real UDP — useful when
exercising correlation and timeout behaviour deterministically.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any, Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from .config import CaptureConfig

log = logging.getLogger(__name__)


class TapeError(RuntimeError):
    """Generic tape-device protocol error (e.g. /tape/error reply)."""


class TapeTimeout(TapeError):
    """Raised when the tape device does not respond within the timeout."""


_WaiterKey = tuple[str, tuple[Any, ...]]


class TapeClient:
    """Async OSC client to the M4L AbletonFullControlTape device.

    Lifecycle:
        client = TapeClient(cfg)
        await client.start()
        ...
        await client.stop()
    """

    def __init__(self, cfg: CaptureConfig | None = None) -> None:
        self._cfg = cfg or CaptureConfig.from_env()
        self._client: SimpleUDPClient = SimpleUDPClient(
            self._cfg.tape_host, self._cfg.tape_send_port
        )
        self._waiters: dict[_WaiterKey, deque[asyncio.Future[tuple[Any, ...]]]] = defaultdict(deque)
        self._server: AsyncIOOSCUDPServer | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Tests can hook this to bypass UDP entirely:
        self._send_override: Callable[[str, tuple[Any, ...]], None] | None = None

    @property
    def config(self) -> CaptureConfig:
        return self._cfg

    async def start(self) -> None:
        if self._server is not None:
            return
        self._loop = asyncio.get_running_loop()
        if self._send_override is not None:
            # Test mode: no real UDP listener.
            return
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._on_message)
        self._server = AsyncIOOSCUDPServer(
            (self._cfg.tape_host, self._cfg.tape_recv_port), dispatcher, self._loop
        )
        self._transport, _ = await self._server.create_serve_endpoint()
        log.info(
            "TapeClient listening on %s:%d, sending to %s:%d",
            self._cfg.tape_host, self._cfg.tape_recv_port,
            self._cfg.tape_host, self._cfg.tape_send_port,
        )

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._server = None

    # ------------------------------------------------------------------
    # Test injection helpers
    # ------------------------------------------------------------------

    def use_external_send(self, sender: Callable[[str, tuple[Any, ...]], None]) -> None:
        """Replace the UDP send path with a callable; tests use this to wire
        directly to a fake server's dispatcher instead of going through UDP.
        Must be called BEFORE ``start()``.
        """
        self._send_override = sender

    def deliver_reply(self, address: str, *args: Any) -> None:
        """Inject a reply as if it had arrived on the recv socket.

        Tests use this together with :meth:`use_external_send` to drive the
        correlation logic deterministically.
        """
        self._on_message(address, *args)

    # ------------------------------------------------------------------
    # Correlation
    # ------------------------------------------------------------------

    def _resolve_waiter(self, address: str, args: tuple[Any, ...]) -> asyncio.Future[tuple[Any, ...]] | None:
        for i in range(len(args), -1, -1):
            key = (address, tuple(args[:i]))
            waiters = self._waiters.get(key)
            if waiters:
                fut = waiters.popleft()
                if not waiters:
                    self._waiters.pop(key, None)
                return fut
        return None

    def _on_message(self, address: str, *args: Any) -> None:
        log.debug("tape recv %s %r", address, args)
        if address == "/tape/error":
            # Surface to the FIFO-oldest pending request of any kind. Heuristic:
            # try /tape/done with same args first, then /tape/pong, then bare.
            for probe_addr in ("/tape/done", "/tape/pong"):
                fut = self._resolve_waiter(probe_addr, args)
                if fut is not None:
                    if not fut.done():
                        fut.set_exception(TapeError(args[0] if args else "tape error"))
                    return
            log.warning("tape /tape/error with no pending waiter: %r", args)
            return
        fut = self._resolve_waiter(address, args)
        if fut is not None and not fut.done():
            fut.set_result(args)
            return
        log.debug("tape dropped reply %s %r (no waiter)", address, args)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def _send(self, address: str, *args: Any) -> None:
        log.debug("tape send %s %r", address, args)
        if self._send_override is not None:
            self._send_override(address, tuple(args))
            return
        self._client.send_message(address, list(args) if args else [])

    async def _await_reply(
        self,
        reply_address: str,
        match_args: tuple[Any, ...],
        timeout: float,
    ) -> tuple[Any, ...]:
        assert self._loop is not None, "TapeClient.start() not called"
        key: _WaiterKey = (reply_address, match_args)
        fut: asyncio.Future[tuple[Any, ...]] = self._loop.create_future()
        self._waiters[key].append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            bucket = self._waiters.get(key)
            if bucket is not None:
                try:
                    bucket.remove(fut)
                except ValueError:
                    pass
                if not bucket:
                    self._waiters.pop(key, None)
            raise TapeTimeout(
                f"Tape reply {reply_address!r} match={match_args!r} timed out after {timeout}s. "
                "Is the AbletonFullControlTape M4L device loaded on a track and showing 'idle'?"
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ping(self, timeout: float | None = None) -> bool:
        """Verify the M4L tape device is alive. Returns True on /tape/pong."""
        timeout = timeout if timeout is not None else 1.5
        try:
            self._send("/tape/ping")
            await self._await_reply("/tape/pong", (), timeout=timeout)
            return True
        except TapeTimeout:
            return False

    async def record(
        self,
        path: str,
        duration_sec: float,
        track_index: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Record the M4L device's parent-track output to ``path`` for ``duration_sec``.

        ``track_index`` is informational — the tape device records the track
        it sits on, not an arbitrary track. Including the track index in the
        OSC args lets advanced multi-tape setups disambiguate which device to
        target if the user has multiple tapes loaded.

        Returns ``{"path": str, "duration_actual": float}`` from the /tape/done
        reply. Raises :class:`TapeTimeout` if the device doesn't reply within
        ``duration_sec + timeout`` seconds.
        """
        # Allow real records to take their full duration plus a bit of slack:
        slack = self._cfg.tape_timeout if timeout is None else timeout
        full_timeout = float(duration_sec) + float(slack)
        # The shipped .maxpat sends /tape/done with NO args (simplest reliable
        # wiring — see live_max_for_live/AbletonFullControlTape/PROTOCOL.md).
        # We therefore match on empty args, which is FIFO-correlated. This is
        # safe for sequential records; concurrent records on multiple tape
        # devices would need /tape/done to carry a per-device id — a future
        # protocol revision can extend this.
        match: tuple = ()
        if track_index is None:
            self._send("/tape/record", str(path), float(duration_sec))
        else:
            self._send("/tape/record", str(path), float(duration_sec), int(track_index))
        reply = await self._await_reply("/tape/done", match, timeout=full_timeout)
        # /tape/done <path> <duration_actual> if patcher carries them; else empty.
        ret_path = str(reply[0]) if reply and len(reply) >= 1 else str(path)
        ret_dur = float(reply[1]) if reply and len(reply) >= 2 else float(duration_sec)
        return {"path": ret_path, "duration_actual": ret_dur}

    async def stop_recording(self) -> None:
        """Best-effort early-stop. Does not wait for /tape/done."""
        self._send("/tape/stop")
