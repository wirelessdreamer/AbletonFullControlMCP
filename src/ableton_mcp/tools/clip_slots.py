"""Clip-slot operations distinct from the contained clip itself."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def clip_slot_has_clip(track_index: int, clip_index: int) -> dict[str, Any]:
        """Check whether a slot contains a clip."""
        client = await get_client()
        args = await client.request(
            "/live/clip_slot/get/has_clip", int(track_index), int(clip_index)
        )
        return {"track": track_index, "slot": clip_index, "has_clip": bool(args[2])}

    @mcp.tool()
    async def clip_slot_get_stop_button(track_index: int, clip_index: int) -> dict[str, Any]:
        """Check whether a slot displays a stop button."""
        client = await get_client()
        args = await client.request(
            "/live/clip_slot/get/has_stop_button", int(track_index), int(clip_index)
        )
        return {"track": track_index, "slot": clip_index, "has_stop_button": bool(args[2])}

    @mcp.tool()
    async def clip_slot_set_stop_button(track_index: int, clip_index: int, on: bool) -> dict[str, Any]:
        """Show / hide the stop button on a slot."""
        (await get_client()).send(
            "/live/clip_slot/set/has_stop_button",
            int(track_index),
            int(clip_index),
            1 if on else 0,
        )
        return {"track": track_index, "slot": clip_index, "has_stop_button": on}

    @mcp.tool()
    async def clip_slot_fire(track_index: int, clip_index: int) -> dict[str, Any]:
        """Fire a slot (will trigger its clip or stop column depending on configuration)."""
        (await get_client()).send(
            "/live/clip_slot/fire", int(track_index), int(clip_index)
        )
        return {"track": track_index, "slot": clip_index, "status": "fired"}

    @mcp.tool()
    async def clip_slot_duplicate_to(
        source_track: int, source_clip: int, target_track: int, target_clip: int
    ) -> dict[str, Any]:
        """Duplicate a clip from one slot to another (target track must be compatible)."""
        (await get_client()).send(
            "/live/clip_slot/duplicate_clip_to",
            int(source_track), int(source_clip),
            int(target_track), int(target_clip),
        )
        return {
            "source": [source_track, source_clip],
            "target": [target_track, target_clip],
            "status": "duplicated",
        }
