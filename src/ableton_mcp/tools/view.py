"""View / selection state: which scene/track/clip/device the user has focused."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def view_get_selection() -> dict[str, Any]:
        """Return the currently selected scene, track, clip, and device."""
        client = await get_client()
        scene = (await client.request("/live/view/get/selected_scene"))
        track = (await client.request("/live/view/get/selected_track"))
        clip = (await client.request("/live/view/get/selected_clip"))
        device = (await client.request("/live/view/get/selected_device"))
        return {
            "selected_scene": scene[0] if scene else None,
            "selected_track": track[0] if track else None,
            "selected_clip": list(clip) if clip else None,  # [track_index, scene_index]
            "selected_device": list(device) if device else None,  # [track_index, device_index]
        }

    @mcp.tool()
    async def view_select_scene(scene_index: int) -> dict[str, int]:
        (await get_client()).send("/live/view/set/selected_scene", int(scene_index))
        return {"selected_scene": scene_index}

    @mcp.tool()
    async def view_select_track(track_index: int) -> dict[str, int]:
        (await get_client()).send("/live/view/set/selected_track", int(track_index))
        return {"selected_track": track_index}

    @mcp.tool()
    async def view_select_clip(track_index: int, scene_index: int) -> dict[str, int]:
        (await get_client()).send(
            "/live/view/set/selected_clip", int(track_index), int(scene_index)
        )
        return {"selected_track": track_index, "selected_scene": scene_index}

    @mcp.tool()
    async def view_select_device(track_index: int, device_index: int) -> dict[str, int]:
        (await get_client()).send(
            "/live/view/set/selected_device", int(track_index), int(device_index)
        )
        return {"selected_track": track_index, "selected_device": device_index}
