"""Tests for the per-(address, args-prefix) reply correlation in AbletonOSCClient.

These exercise behaviours that the original FIFO-by-address scheme could not
guarantee: concurrent gets that share an address but differ on their selector
args (e.g. ``track_id``) must not cross the wires.
"""

from __future__ import annotations

import asyncio

import pytest
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from ableton_mcp.config import Config
from ableton_mcp.osc_client import AbletonOSCClient


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _FakeAbleton:
    """A small UDP responder that we can drive deterministically.

    Unlike the simpler stand-in used in test_osc_client.py, this version
    queues incoming requests and replies on demand so we can interleave
    reply order to mimic Ableton's main-thread scheduling.
    """

    def __init__(self, host: str, recv_port: int) -> None:
        self._host = host
        self._reply_client = SimpleUDPClient(host, recv_port)
        self.received: list[tuple[str, tuple]] = []
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self, listen_port: int) -> None:
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._handler)
        server = AsyncIOOSCUDPServer(
            (self._host, listen_port), dispatcher, asyncio.get_running_loop()
        )
        self._transport, _ = await server.create_serve_endpoint()

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _handler(self, address: str, *args: object) -> None:
        self.received.append((address, args))

    def reply(self, address: str, *args: object) -> None:
        self._reply_client.send_message(address, list(args))


async def _wait_for_n_received(fake: _FakeAbleton, n: int, timeout: float = 1.0) -> None:
    """Spin briefly until the fake has logged n incoming messages."""
    deadline = asyncio.get_event_loop().time() + timeout
    while len(fake.received) < n:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"only {len(fake.received)} of {n} requests reached the fake")
        await asyncio.sleep(0.005)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_track_name_gets_do_not_cross() -> None:
    """Two pending /live/track/get/name calls keyed on different track_ids must
    each receive the reply that matches their selector, regardless of the order
    in which the fake responds.
    """
    cfg = Config(osc_send_port=17100, osc_recv_port=17101, request_timeout=2.0)
    fake = _FakeAbleton("127.0.0.1", recv_port=17101)
    await fake.start(listen_port=17100)

    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        # Issue two concurrent gets that share an address but differ on track_id.
        task0 = asyncio.create_task(client.request("/live/track/get/name", 0))
        task1 = asyncio.create_task(client.request("/live/track/get/name", 1))

        await _wait_for_n_received(fake, 2)

        # Reply in the *opposite* order to expose any FIFO-by-address bug.
        fake.reply("/live/track/get/name", 1, "Bass")
        fake.reply("/live/track/get/name", 0, "Lead")

        r0 = await asyncio.wait_for(task0, timeout=1.0)
        r1 = await asyncio.wait_for(task1, timeout=1.0)
        assert r0 == (0, "Lead"), f"track 0 got the wrong reply: {r0!r}"
        assert r1 == (1, "Bass"), f"track 1 got the wrong reply: {r1!r}"
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_listener_receives_alongside_active_get() -> None:
    """A listener subscribed to an address should still get a copy of every
    reply, even when one of those replies is also being awaited by a
    request().
    """
    cfg = Config(osc_send_port=17110, osc_recv_port=17111, request_timeout=2.0)
    fake = _FakeAbleton("127.0.0.1", recv_port=17111)
    await fake.start(listen_port=17110)

    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        addr = "/live/track/get/volume"
        q = client.listen(addr)

        task = asyncio.create_task(client.request(addr, 2))
        await _wait_for_n_received(fake, 1)
        # 0.5 round-trips losslessly through 32-bit OSC float encoding.
        fake.reply(addr, 2, 0.5)

        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == (2, 0.5)

        listener_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert listener_event == (2, 0.5)
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_unmatched_reply_drops_to_listener_or_silently() -> None:
    """A reply that matches no waiter should reach a listener if one is
    registered, and otherwise be dropped without raising.
    """
    cfg = Config(osc_send_port=17120, osc_recv_port=17121, request_timeout=2.0)
    fake = _FakeAbleton("127.0.0.1", recv_port=17121)
    await fake.start(listen_port=17120)

    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        addr = "/live/song/get/beat"

        # 1) No waiter, no listener: must not raise.
        fake.reply(addr, 1.0)
        await asyncio.sleep(0.05)  # give the UDP packet time to arrive

        # 2) No waiter, but a listener: listener gets the reply.
        q = client.listen(addr)
        fake.reply(addr, 2.0)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event == (2.0,)
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_prefix_match_with_extra_reply_args() -> None:
    """Replies often echo the selector plus an extra value (e.g. (track_id, value)).
    A request keyed on ``(addr, (track_id,))`` must match such a reply.
    """
    cfg = Config(osc_send_port=17130, osc_recv_port=17131, request_timeout=2.0)
    fake = _FakeAbleton("127.0.0.1", recv_port=17131)
    await fake.start(listen_port=17130)

    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        task = asyncio.create_task(client.request("/live/track/get/mute", 3))
        await _wait_for_n_received(fake, 1)
        # Reply echoes the track id and adds the boolean result.
        fake.reply("/live/track/get/mute", 3, 1)
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == (3, 1)
    finally:
        await client.stop()
        fake.stop()


@pytest.mark.asyncio
async def test_fifo_within_same_key() -> None:
    """Two concurrent requests with *identical* selector args must still resolve
    in FIFO order — the prefix scheme falls back to per-key FIFO when keys tie.
    """
    cfg = Config(osc_send_port=17140, osc_recv_port=17141, request_timeout=2.0)
    fake = _FakeAbleton("127.0.0.1", recv_port=17141)
    await fake.start(listen_port=17140)

    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        addr = "/live/song/get/tempo"
        first = asyncio.create_task(client.request(addr))
        second = asyncio.create_task(client.request(addr))
        await _wait_for_n_received(fake, 2)

        fake.reply(addr, 120.0)
        fake.reply(addr, 121.0)

        assert (await asyncio.wait_for(first, timeout=1.0)) == (120.0,)
        assert (await asyncio.wait_for(second, timeout=1.0)) == (121.0,)
    finally:
        await client.stop()
        fake.stop()
