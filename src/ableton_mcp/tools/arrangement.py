"""Arrangement view: clips placed on the linear timeline."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def _strip_track_id(args: tuple[Any, ...]) -> list[Any]:
    """AbletonOSC's track replies are shaped ``(track_id, val_0, val_1, ...)``
    — a flat tuple with the track id leading and one entry per arrangement
    clip after it. Strip the track id and return the value list.

    For arrangement clips specifically, the count of entries equals the
    number of clips on the track and the index in the list is the clip's
    arrangement index. There are NO None placeholders (unlike the session
    clip-slots reply which uses None for empty slots).

    See ``track.py`` in AbletonOSC for the canonical reply shape:
        return tuple(clip.length for clip in track.arrangement_clips)
    wrapped by ``track_callback`` which prepends the track id.
    """
    if not args:
        return []
    return list(args[1:])


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def arrangement_clips_list(track_index: int) -> list[dict[str, Any]]:
        """List all clips placed on a track in the Arrangement view, with name, length, start_time."""
        client = await get_client()
        names = await client.request("/live/track/get/arrangement_clips/name", int(track_index))
        lengths = await client.request("/live/track/get/arrangement_clips/length", int(track_index))
        starts = await client.request("/live/track/get/arrangement_clips/start_time", int(track_index))
        name_list = _strip_track_id(names)
        length_list = _strip_track_id(lengths)
        start_list = _strip_track_id(starts)
        n = max(len(name_list), len(length_list), len(start_list), 0)
        out: list[dict[str, Any]] = []
        for i in range(n):
            out.append(
                {
                    "track_index": track_index,
                    "arrangement_clip_index": i,
                    "name": name_list[i] if i < len(name_list) else None,
                    "length_beats": length_list[i] if i < len(length_list) else None,
                    "start_time_beats": start_list[i] if i < len(start_list) else None,
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
