"""Audio rendering / bouncing.

Live exposes Export Audio/Video only via the GUI. To render programmatically we have three options:
  (a) drive the export dialog via UI automation (fragile),
  (b) loop the section, set a track's output to a virtual device (BlackHole on macOS, VB-Audio Cable on Windows),
      capture from the OS, write wav,
  (c) ship a Max for Live "tape" device that captures the master bus to disk on demand (Phase 5).

For now this module exposes stubs and a working "loop a section for N seconds and capture from a chosen
input device" helper using sounddevice (added in Phase 2).
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
