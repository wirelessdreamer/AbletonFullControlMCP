"""MIDI mapping helpers: bind a hardware CC to a device parameter."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def midi_map_cc(
        track_index: int,
        device_index: int,
        parameter_index: int,
        midi_channel: int,
        cc_number: int,
    ) -> dict[str, Any]:
        """Map a MIDI CC (1-based channel, 0..127 CC) to a device parameter."""
        (await get_client()).send(
            "/live/midimap/map_cc",
            int(track_index),
            int(device_index),
            int(parameter_index),
            int(midi_channel),
            int(cc_number),
        )
        return {
            "track": track_index,
            "device": device_index,
            "parameter": parameter_index,
            "channel": midi_channel,
            "cc": cc_number,
            "status": "mapped",
        }
