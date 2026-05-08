"""Unit tests for ``ableton_mcp.tape.client.TapeClient``.

The fake tape OSC server is a python-osc dispatcher that listens on the
configured send port (i.e. the Python -> Max direction) and replies on the
configured recv port (Max -> Python) using `SimpleUDPClient`. The shape mirrors
the existing OSC-client tests so the FIFO correlation behaviour is checked
end-to-end through real UDP sockets.
"""

from __future__ import annotations

import asyncio

import pytest
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from ableton_mcp.tape import CaptureConfig, TapeClient, TapeError, TapeTimeout


class _FakeTape:
    """Stand-in for the AbletonFullControlTape M4L device.

    Listens on the Python->Max port; replies on the Max->Python port.
    """

    def __init__(self, host: str, send_port: int, recv_port: int) -> None:
        self._host = host
        self._send_port = send_port  # we listen here
        self._recv_port = recv_port  # we reply here
        self._reply_client = SimpleUDPClient(host, recv_port)
        self.received: list[tuple[str, tuple]] = []
        self._transport: asyncio.DatagramTransport | None = None
        self.auto_pong = True
        self.auto_done_after: float | None = 0.05  # default: ack record~50ms after request
        self.error_on_record: str | None = None

    async def start(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._handler)
        server = AsyncIOOSCUDPServer(
            (self._host, self._send_port), dispatcher, asyncio.get_running_loop()
        )
        self._transport, _ = await server.create_serve_endpoint()

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _handler(self, address: str, *args: object) -> None:
        self.received.append((address, args))
        if address == "/tape/ping" and self.auto_pong:
            self._reply_client.send_message("/tape/pong", [])
        elif address == "/tape/record":
            if self.error_on_record is not None:
                self._reply_client.send_message("/tape/error", [self.error_on_record])
                return
            if self.auto_done_after is None:
                return
            path = str(args[0])
            duration = float(args[1])
            asyncio.get_event_loop().call_later(
                self.auto_done_after,
                lambda: self._reply_client.send_message("/tape/done", [path, duration]),
            )

    def reply(self, address: str, *args: object) -> None:
        self._reply_client.send_message(address, list(args))


@pytest.fixture
def free_ports() -> tuple[int, int]:
    return 17200, 17201


@pytest.mark.asyncio
async def test_ping_round_trip(free_ports: tuple[int, int]) -> None:
    send_port, recv_port = free_ports
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        assert await client.ping(timeout=1.0) is True
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_ping_returns_false_when_no_server() -> None:
    cfg = CaptureConfig(tape_send_port=18900, tape_recv_port=18901)
    client = TapeClient(cfg)
    await client.start()
    try:
        assert await client.ping(timeout=0.3) is False
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_record_success_returns_path_and_duration() -> None:
    send_port, recv_port = 17210, 17211
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        result = await client.record("/tmp/x.wav", duration_sec=0.1, timeout=2.0)
        assert result["path"] == "/tmp/x.wav"
        assert abs(result["duration_actual"] - 0.1) < 1e-6
        # Confirm the wire message arrived as expected.
        rec = [m for m in fake.received if m[0] == "/tape/record"]
        assert len(rec) == 1
        assert rec[0][1][0] == "/tmp/x.wav"
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_record_includes_track_index_when_provided() -> None:
    send_port, recv_port = 17220, 17221
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        await client.record("/tmp/y.wav", duration_sec=0.1, track_index=3, timeout=2.0)
        rec = [m for m in fake.received if m[0] == "/tape/record"]
        assert len(rec) == 1
        assert rec[0][1][2] == 3
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_record_times_out_when_no_done_reply() -> None:
    send_port, recv_port = 17230, 17231
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    fake.auto_done_after = None  # never reply
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        with pytest.raises(TapeTimeout):
            await client.record("/tmp/never.wav", duration_sec=0.05, timeout=0.2)
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_concurrent_records_do_not_cross() -> None:
    """Two concurrent records to different paths must each get the right
    ``/tape/done`` reply, even when the fake answers them in the opposite
    order from the requests.
    """
    send_port, recv_port = 17240, 17241
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    fake.auto_done_after = None  # we'll reply manually
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        task_a = asyncio.create_task(client.record("/tmp/a.wav", 0.1, timeout=2.0))
        task_b = asyncio.create_task(client.record("/tmp/b.wav", 0.1, timeout=2.0))

        # Wait for both requests to land at the fake.
        deadline = asyncio.get_event_loop().time() + 1.0
        while len([m for m in fake.received if m[0] == "/tape/record"]) < 2:
            assert asyncio.get_event_loop().time() < deadline
            await asyncio.sleep(0.01)

        # Reply in REVERSE order to expose any FIFO-by-address bug.
        fake.reply("/tape/done", "/tmp/b.wav", 0.111)
        fake.reply("/tape/done", "/tmp/a.wav", 0.222)

        result_a = await asyncio.wait_for(task_a, timeout=1.0)
        result_b = await asyncio.wait_for(task_b, timeout=1.0)
        assert result_a == {"path": "/tmp/a.wav", "duration_actual": pytest.approx(0.222)}
        assert result_b == {"path": "/tmp/b.wav", "duration_actual": pytest.approx(0.111)}
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_fifo_within_same_path() -> None:
    """Two records to the same path resolve in FIFO order."""
    send_port, recv_port = 17250, 17251
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    fake.auto_done_after = None
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        first = asyncio.create_task(client.record("/tmp/same.wav", 0.1, timeout=2.0))
        second = asyncio.create_task(client.record("/tmp/same.wav", 0.1, timeout=2.0))

        deadline = asyncio.get_event_loop().time() + 1.0
        while len([m for m in fake.received if m[0] == "/tape/record"]) < 2:
            assert asyncio.get_event_loop().time() < deadline
            await asyncio.sleep(0.01)

        fake.reply("/tape/done", "/tmp/same.wav", 1.0)
        fake.reply("/tape/done", "/tmp/same.wav", 2.0)

        a = await asyncio.wait_for(first, timeout=1.0)
        b = await asyncio.wait_for(second, timeout=1.0)
        assert a["duration_actual"] == 1.0
        assert b["duration_actual"] == 2.0
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_error_reply_propagates_as_tape_error() -> None:
    send_port, recv_port = 17260, 17261
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    fake.error_on_record = "file open failed"
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        with pytest.raises(TapeError):
            await client.record("/no/such/dir/x.wav", 0.1, timeout=1.0)
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_stop_recording_is_fire_and_forget() -> None:
    send_port, recv_port = 17270, 17271
    cfg = CaptureConfig(tape_send_port=send_port, tape_recv_port=recv_port)
    fake = _FakeTape("127.0.0.1", send_port, recv_port)
    await fake.start()
    client = TapeClient(cfg)
    await client.start()
    try:
        await client.stop_recording()
        # Give the UDP packet a moment to land.
        deadline = asyncio.get_event_loop().time() + 0.5
        while not any(m[0] == "/tape/stop" for m in fake.received):
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.01)
        assert any(m[0] == "/tape/stop" for m in fake.received)
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_pick_capture_backend_returns_tape_client_by_default() -> None:
    from ableton_mcp.tape import pick_capture_backend

    backend = pick_capture_backend(CaptureConfig(backend="tape"))
    assert isinstance(backend, TapeClient)


@pytest.mark.asyncio
async def test_pick_capture_backend_unknown_raises() -> None:
    from ableton_mcp.tape import pick_capture_backend

    with pytest.raises(ValueError):
        pick_capture_backend(CaptureConfig(backend="bogus"))  # type: ignore[arg-type]
