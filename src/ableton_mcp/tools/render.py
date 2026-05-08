"""Audio rendering / bouncing — legacy stubs.

Active bouncing tools live in ``tools/bounce.py`` and use Live's built-in
Resampling input (no Max for Live, no loopback driver). The two stubs in
this module predate that path and remain ``not_implemented`` — kept only
because removing them would silently change the registered tool surface.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def render_master(output_path: str, length_beats: float | None = None) -> dict[str, Any]:
        """[Phase 2] Bounce the master bus to a wav file."""
        return {
            "status": "not_implemented",
            "phase": 2,
            "output_path": output_path,
            "length_beats": length_beats,
            "todo": (
                "Use sounddevice to record a configured loopback input while transport plays the section. "
                "On Windows install VB-Audio Cable and route Live master to it."
            ),
        }

    @mcp.tool()
    async def render_clip(
        track_index: int, clip_index: int, output_path: str
    ) -> dict[str, Any]:
        """[Phase 2] Bounce a single Session clip to wav."""
        return {
            "status": "not_implemented",
            "phase": 2,
            "track_index": track_index,
            "clip_index": clip_index,
            "output_path": output_path,
        }
