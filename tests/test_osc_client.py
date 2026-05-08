"""Unit tests for the OSC client without requiring a running Ableton."""

from __future__ import annotations

import asyncio

import pytest
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from ableton_mcp.config import Config
from ableton_mcp.osc_client import AbletonOSCClient, AbletonOSCTimeout


@pytest.fixture
def free_ports() -> tuple[int, int]:
    # Use distinct, unlikely-to-collide ports. (Loopback only.)
    return 17000, 17001


async def _fake_ableton(send_port: int, recv_port: int, host: str = "127.0.0.1") -> tuple[AsyncIOOSCUDPServer, asyncio.DatagramTransport, list[tuple[str, tuple]]]:
    """A tiny stand-in for Ableton: listens on `send_port`, replies on `recv_port`."""
    received: list[tuple[str, tuple]] = []
    reply_client = SimpleUDPClient(host, recv_port)

    def handler(address: str, *args):
        received.append((address, args))
        if address == "/live/test":
            reply_client.send_message("/live/test", ["ok"])
        elif address == "/live/song/get/tempo":
            reply_client.send_message("/live/song/get/tempo", [120.0])

    dispatcher = Dispatcher()
    dispatcher.set_default_handler(handler)
    server = AsyncIOOSCUDPServer((host, send_port), dispatcher, asyncio.get_running_loop())
    transport, _ = await server.create_serve_endpoint()
    return server, transport, received


@pytest.mark.asyncio
async def test_ping_round_trip(free_ports: tuple[int, int]) -> None:
    send_port, recv_port = free_ports
    cfg = Config(osc_send_port=send_port, osc_recv_port=recv_port, request_timeout=2.0)
    _, fake_transport, _ = await _fake_ableton(send_port, recv_port)
    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        assert await client.ping() is True
        tempo = (await client.request("/live/song/get/tempo"))[0]
        assert tempo == 120.0
    finally:
        await client.stop()
        fake_transport.close()


@pytest.mark.asyncio
async def test_request_times_out_when_no_server() -> None:
    cfg = Config(osc_send_port=18999, osc_recv_port=18998, request_timeout=0.4)
    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        with pytest.raises(AbletonOSCTimeout):
            await client.request("/live/song/get/tempo")
    finally:
        await client.stop()
