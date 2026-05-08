"""Arrangement view: clips placed on the linear timeline."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def _parse_pairs(args: tuple[Any, ...]) -> dict[int, Any]:
    if not args:
        return {}
    # First element is track_id; remainder is (slot_idx, value) pairs.
    return {int(args[i]): args[i + 1] for i in range(1, len(args) - 1, 2)}


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def arrangement_clips_list(track_index: int) -> list[dict[str, Any]]:
        """List all clips placed on a track in the Arrangement view, with name, length, start_time."""
        client = await get_client()
        names = await client.request("/live/track/get/arrangement_clips/name", int(track_index))
        lengths = await client.request("/live/track/get/arrangement_clips/length", int(track_index))
        starts = await client.request("/live/track/get/arrangement_clips/start_time", int(track_index))
        names_m = _parse_pairs(names)
        lengths_m = _parse_pairs(lengths)
        starts_m = _parse_pairs(starts)
        idxs = sorted(set(names_m) | set(lengths_m) | set(starts_m))
        out: list[dict[str, Any]] = []
        for i in idxs:
            out.append(
                {
                    "track_index": track_index,
                    "arrangement_clip_index": i,
                    "name": names_m.get(i),
                    "length_beats": lengths_m.get(i),
                    "start_time_beats": starts_m.get(i),
                }
            )
        return out

    @mcp.tool()
    async def arrangement_summary() -> dict[str, Any]:
        """High-level snapshot of the arrangement: tempo, length, signature, num tracks/scenes."""
        client = await get_client()
        async def g(addr: str) -> Any:
            return (await client.request(addr))[0]
        return {
            "song_length_beats": float(await g("/live/song/get/song_length")),
            "tempo": float(await g("/live/song/get/tempo")),
            "time_signature": f"{await g('/live/song/get/signature_numerator')}/{await g('/live/song/get/signature_denominator')}",
            "num_tracks": int(await g("/live/song/get/num_tracks")),
            "num_scenes": int(await g("/live/song/get/num_scenes")),
            "current_song_time": float(await g("/live/song/get/current_song_time")),
        }
