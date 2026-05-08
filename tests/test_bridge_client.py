"""Unit tests for AbletonBridgeClient against an in-process fake bridge.

The fake server speaks the same line-delimited JSON protocol as the real
AbletonFullControlBridge Live Remote Script. We exercise at least one handler from
each category (browser / track / clip / project) to confirm the wire format
works end-to-end and the singleton + error paths behave.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from typing import Any, Callable

import pytest

from ableton_mcp import bridge_client
from ableton_mcp.bridge_client import (
    AbletonBridgeClient,
    AbletonBridgeError,
    AbletonBridgeTimeout,
    AbletonBridgeUnavailable,
    BridgeConfig,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


HandlerFn = Callable[[dict[str, Any]], Any]


class FakeBridge:
    """Minimal asyncio TCP server that mimics AbletonFullControlBridge."""

    def __init__(self, handlers: dict[str, HandlerFn]) -> None:
        self.handlers = handlers
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode("utf-8"))
            self.requests.append(req)
            op = req.get("op")
            handler = self.handlers.get(op)
            if handler is None:
                resp = {"id": req.get("id"), "ok": False, "error": "unknown op: %s" % op}
            else:
                try:
                    result = handler(req.get("args") or {})
                    resp = {"id": req.get("id"), "ok": True, "result": result}
                except Exception as exc:
                    resp = {"id": req.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


@contextlib.asynccontextmanager
async def _bridge(handlers: dict[str, HandlerFn]) -> AsyncIterator[tuple[FakeBridge, AbletonBridgeClient]]:
    fake = FakeBridge(handlers)
    await fake.start()
    bridge_client.reset_bridge_client()
    cfg = BridgeConfig(host="127.0.0.1", port=fake.port, request_timeout=2.0)
    client = AbletonBridgeClient(cfg)
    try:
        yield fake, client
    finally:
        await fake.stop()
        bridge_client.reset_bridge_client()


# ----- ping -----


@pytest.mark.asyncio
async def test_ping_round_trip() -> None:
    handlers: dict[str, HandlerFn] = {
        "system.ping": lambda args: {"ok": True, "service": "AbletonFullControlBridge"},
    }
    async with _bridge(handlers) as (_, client):
        assert await client.ping() is True


@pytest.mark.asyncio
async def test_ping_returns_false_when_unreachable() -> None:
    bridge_client.reset_bridge_client()
    cfg = BridgeConfig(host="127.0.0.1", port=_free_port(), request_timeout=0.4)
    client = AbletonBridgeClient(cfg)
    assert await client.ping() is False


# ----- one of each category -----


@pytest.mark.asyncio
async def test_browser_search_round_trip() -> None:
    seen_args: dict[str, Any] = {}

    def search(args: dict[str, Any]) -> Any:
        seen_args.update(args)
        return {
            "query": args.get("query"),
            "category": args.get("category"),
            "count": 1,
            "results": [{"name": "Operator", "path": "instruments/Operator", "is_loadable": True}],
        }

    async with _bridge({"browser.search": search}) as (_, client):
        result = await client.call("browser.search", query="operator", category="instruments")
    assert result["count"] == 1
    assert result["results"][0]["name"] == "Operator"
    assert seen_args == {"query": "operator", "category": "instruments"}


@pytest.mark.asyncio
async def test_track_freeze_round_trip() -> None:
    async with _bridge({"track.freeze": lambda a: {"track_index": a["track_index"], "frozen": True}}) as (_, client):
        result = await client.call("track.freeze", track_index=2)
    assert result == {"track_index": 2, "frozen": True}


@pytest.mark.asyncio
async def test_clip_consolidate_round_trip() -> None:
    async with _bridge({
        "clip.consolidate": lambda a: {
            "track_index": a["track_index"], "clip_index": a["clip_index"], "consolidated": True,
        }
    }) as (_, client):
        result = await client.call("clip.consolidate", track_index=1, clip_index=0)
    assert result["consolidated"] is True
    assert result["track_index"] == 1


@pytest.mark.asyncio
async def test_project_save_round_trip() -> None:
    async with _bridge({"project.save": lambda a: {"saved": True, "save_as_supported": False}}) as (_, client):
        result = await client.call("project.save")
    assert result == {"saved": True, "save_as_supported": False}


# ----- error & robustness paths -----


@pytest.mark.asyncio
async def test_unknown_op_raises_bridge_error() -> None:
    async with _bridge({}) as (_, client):
        with pytest.raises(AbletonBridgeError) as exc:
            await client.call("totally.fake")
    assert "unknown op" in str(exc.value)


@pytest.mark.asyncio
async def test_handler_exception_surfaces_as_bridge_error() -> None:
    def boom(args: dict[str, Any]) -> Any:
        raise ValueError("oops")
    async with _bridge({"x.boom": boom}) as (_, client):
        with pytest.raises(AbletonBridgeError) as exc:
            await client.call("x.boom")
    assert "ValueError" in str(exc.value)


@pytest.mark.asyncio
async def test_unavailable_when_no_server() -> None:
    bridge_client.reset_bridge_client()
    cfg = BridgeConfig(host="127.0.0.1", port=_free_port(), request_timeout=0.4)
    client = AbletonBridgeClient(cfg)
    with pytest.raises((AbletonBridgeUnavailable, AbletonBridgeTimeout)):
        await client.call("system.ping")


@pytest.mark.asyncio
async def test_get_bridge_client_is_singleton() -> None:
    bridge_client.reset_bridge_client()
    a = bridge_client.get_bridge_client()
    b = bridge_client.get_bridge_client()
    assert a is b
    bridge_client.reset_bridge_client()
    c = bridge_client.get_bridge_client()
    assert c is not a


@pytest.mark.asyncio
async def test_request_id_increments_per_call() -> None:
    handlers: dict[str, HandlerFn] = {"system.ping": lambda a: {"ok": True}}
    async with _bridge(handlers) as (fake, client):
        await client.call("system.ping")
        await client.call("system.ping")
        await client.call("system.ping")
    ids = [r["id"] for r in fake.requests]
    assert ids == sorted(ids)
    assert len(set(ids)) == 3
