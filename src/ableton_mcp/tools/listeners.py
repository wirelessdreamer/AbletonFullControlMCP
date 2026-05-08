"""Subscribe to AbletonOSC change notifications via short-lived poll handles.

Why poll instead of streaming back through MCP? FastMCP tool results are one-shot
JSON, not async streams. We expose a `subscribe → poll → unsubscribe` triplet so
that clients can drive their own event loops at the cadence they want without
holding a long-running call open. Each subscription is identified by a
zero-overhead handle. Memory is bounded — each handle keeps at most `max_buffer`
events.

Supported subscription kinds (mirrors AbletonOSC's `start_listen` endpoints):
  - song.<property>           e.g. tempo, is_playing, beat
  - track.<property>          e.g. mute (provide track_index)
  - scene.<property>          e.g. is_triggered (provide scene_index)
  - clip.playing_position     (provide track_index + clip_index)
  - device.parameter.value    (provide track_index, device_index, parameter_index)
  - view.selected_scene / view.selected_track
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client

log = logging.getLogger(__name__)

_MAX_BUFFER_DEFAULT = 256


@dataclass
class _Subscription:
    handle: str
    address: str
    start_addr: str  # the /start_listen endpoint we sent
    stop_addr: str   # the /stop_listen endpoint to undo it
    args: tuple[Any, ...]
    queue: asyncio.Queue[tuple[Any, ...]] = field(default_factory=asyncio.Queue)
    max_buffer: int = _MAX_BUFFER_DEFAULT
    dropped: int = 0


_subs: dict[str, _Subscription] = {}


def _addr_for_song(prop: str) -> tuple[str, str, str]:
    """Return (reply_addr, start_addr, stop_addr) for a song-level property."""
    if prop == "beat":
        return "/live/song/get/beat", "/live/song/start_listen/beat", "/live/song/stop_listen/beat"
    return (
        f"/live/song/get/{prop}",
        f"/live/song/start_listen/{prop}",
        f"/live/song/stop_listen/{prop}",
    )


def _addr_for_track(prop: str) -> tuple[str, str, str]:
    return (
        f"/live/track/get/{prop}",
        f"/live/track/start_listen/{prop}",
        f"/live/track/stop_listen/{prop}",
    )


def _addr_for_scene(prop: str) -> tuple[str, str, str]:
    return (
        f"/live/scene/get/{prop}",
        f"/live/scene/start_listen/{prop}",
        f"/live/scene/stop_listen/{prop}",
    )


def _addr_for_view(prop: str) -> tuple[str, str, str]:
    return (
        f"/live/view/get/{prop}",
        f"/live/view/start_listen/{prop}",
        f"/live/view/stop_listen/{prop}",
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def listen_song(property: str, max_buffer: int = _MAX_BUFFER_DEFAULT) -> dict[str, Any]:
        """Subscribe to a song-level property (e.g. tempo, is_playing, beat). Returns a handle."""
        client = await get_client()
        reply, start, stop = _addr_for_song(property)
        sub = _Subscription(
            handle=uuid4().hex,
            address=reply,
            start_addr=start,
            stop_addr=stop,
            args=(),
            max_buffer=max_buffer,
        )
        sub.queue = client.listen(reply)
        client.send(start)
        _subs[sub.handle] = sub
        return {"handle": sub.handle, "address": reply}

    @mcp.tool()
    async def listen_track(track_index: int, property: str, max_buffer: int = _MAX_BUFFER_DEFAULT) -> dict[str, Any]:
        """Subscribe to a track-level property (e.g. mute, volume, panning, fired_slot_index)."""
        client = await get_client()
        reply, start, stop = _addr_for_track(property)
        sub = _Subscription(
            handle=uuid4().hex,
            address=reply,
            start_addr=start,
            stop_addr=stop,
            args=(int(track_index),),
            max_buffer=max_buffer,
        )
        sub.queue = client.listen(reply)
        client.send(start, int(track_index))
        _subs[sub.handle] = sub
        return {"handle": sub.handle, "address": reply, "track": track_index}

    @mcp.tool()
    async def listen_scene(scene_index: int, property: str, max_buffer: int = _MAX_BUFFER_DEFAULT) -> dict[str, Any]:
        """Subscribe to a scene-level property (e.g. is_triggered, name)."""
        client = await get_client()
        reply, start, stop = _addr_for_scene(property)
        sub = _Subscription(
            handle=uuid4().hex,
            address=reply,
            start_addr=start,
            stop_addr=stop,
            args=(int(scene_index),),
            max_buffer=max_buffer,
        )
        sub.queue = client.listen(reply)
        client.send(start, int(scene_index))
        _subs[sub.handle] = sub
        return {"handle": sub.handle, "address": reply, "scene": scene_index}

    @mcp.tool()
    async def listen_view(property: str, max_buffer: int = _MAX_BUFFER_DEFAULT) -> dict[str, Any]:
        """Subscribe to a view selection (selected_scene or selected_track)."""
        client = await get_client()
        reply, start, stop = _addr_for_view(property)
        sub = _Subscription(
            handle=uuid4().hex,
            address=reply,
            start_addr=start,
            stop_addr=stop,
            args=(),
            max_buffer=max_buffer,
        )
        sub.queue = client.listen(reply)
        client.send(start)
        _subs[sub.handle] = sub
        return {"handle": sub.handle, "address": reply}

    @mcp.tool()
    async def listen_clip_playing_position(
        track_index: int, clip_index: int, max_buffer: int = _MAX_BUFFER_DEFAULT
    ) -> dict[str, Any]:
        """Subscribe to a clip's playing position (in beats)."""
        client = await get_client()
        reply = "/live/clip/get/playing_position"
        start = "/live/clip/start_listen/playing_position"
        stop = "/live/clip/stop_listen/playing_position"
        sub = _Subscription(
            handle=uuid4().hex,
            address=reply,
            start_addr=start,
            stop_addr=stop,
            args=(int(track_index), int(clip_index)),
            max_buffer=max_buffer,
        )
        sub.queue = client.listen(reply)
        client.send(start, int(track_index), int(clip_index))
        _subs[sub.handle] = sub
        return {"handle": sub.handle, "address": reply, "track": track_index, "clip": clip_index}

    @mcp.tool()
    async def listen_device_parameter(
        track_index: int,
        device_index: int,
        parameter_index: int,
        max_buffer: int = _MAX_BUFFER_DEFAULT,
    ) -> dict[str, Any]:
        """Subscribe to a single device parameter's value."""
        client = await get_client()
        reply = "/live/device/get/parameter/value"
        start = "/live/device/start_listen/parameter/value"
        stop = "/live/device/stop_listen/parameter/value"
        sub = _Subscription(
            handle=uuid4().hex,
            address=reply,
            start_addr=start,
            stop_addr=stop,
            args=(int(track_index), int(device_index), int(parameter_index)),
            max_buffer=max_buffer,
        )
        sub.queue = client.listen(reply)
        client.send(start, int(track_index), int(device_index), int(parameter_index))
        _subs[sub.handle] = sub
        return {
            "handle": sub.handle,
            "address": reply,
            "track": track_index,
            "device": device_index,
            "parameter": parameter_index,
        }

    @mcp.tool()
    async def listen_poll(handle: str, max_events: int = 64) -> dict[str, Any]:
        """Drain queued events for a subscription. Returns at most `max_events`."""
        sub = _subs.get(handle)
        if sub is None:
            return {"error": f"unknown handle {handle!r}"}
        events: list[list[Any]] = []
        while not sub.queue.empty() and len(events) < max_events:
            args = sub.queue.get_nowait()
            events.append(list(args))
        # Trim if oversize.
        approx_qsize = sub.queue.qsize()
        if approx_qsize > sub.max_buffer:
            drop = approx_qsize - sub.max_buffer
            for _ in range(drop):
                try:
                    sub.queue.get_nowait()
                    sub.dropped += 1
                except asyncio.QueueEmpty:
                    break
        return {"handle": handle, "events": events, "dropped_total": sub.dropped}

    @mcp.tool()
    async def listen_stop(handle: str) -> dict[str, Any]:
        """Unsubscribe and free the handle."""
        sub = _subs.pop(handle, None)
        if sub is None:
            return {"error": f"unknown handle {handle!r}"}
        client = await get_client()
        client.send(sub.stop_addr, *sub.args)
        client.stop_listening(sub.address, sub.queue)
        return {"handle": handle, "status": "stopped"}

    @mcp.tool()
    async def listen_list() -> list[dict[str, Any]]:
        """List active listener handles."""
        return [
            {
                "handle": s.handle,
                "address": s.address,
                "args": list(s.args),
                "queued": s.queue.qsize(),
                "dropped_total": s.dropped,
            }
            for s in _subs.values()
        ]
