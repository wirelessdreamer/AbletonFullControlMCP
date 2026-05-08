"""Arrangement cue points / locators."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def cue_points_list() -> list[dict[str, Any]]:
        """Return all cue points (locators) in the arrangement: name + time."""
        client = await get_client()
        args = await client.request("/live/song/get/cue_points")
        # Reply pattern from AbletonOSC: flat list of (name, time) pairs.
        out: list[dict[str, Any]] = []
        for i in range(0, len(args) - 1, 2):
            out.append({"name": args[i], "time_beats": float(args[i + 1])})
        return out

    @mcp.tool()
    async def cue_point_add_or_delete() -> dict[str, str]:
        """Toggle a cue point at the current playback position (Live's [Set] / [Delete] action)."""
        (await get_client()).send("/live/song/cue_point/add_or_delete")
        return {"status": "toggled"}

    @mcp.tool()
    async def cue_point_jump(cue_point_index: int) -> dict[str, int]:
        """Jump arrangement playback to a numbered cue point."""
        (await get_client()).send("/live/song/cue_point/jump", int(cue_point_index))
        return {"cue_point": cue_point_index, "status": "jumped"}

    @mcp.tool()
    async def cue_point_set_name(cue_point_index: int, name: str) -> dict[str, Any]:
        """Rename a cue point by index."""
        (await get_client()).send(
            "/live/song/cue_point/set/name", int(cue_point_index), str(name)
        )
        return {"cue_point": cue_point_index, "name": name}

    @mcp.tool()
    async def cue_point_jump_next() -> dict[str, str]:
        (await get_client()).send("/live/song/jump_to_next_cue")
        return {"status": "jumped_next"}

    @mcp.tool()
    async def cue_point_jump_prev() -> dict[str, str]:
        (await get_client()).send("/live/song/jump_to_prev_cue")
        return {"status": "jumped_prev"}
