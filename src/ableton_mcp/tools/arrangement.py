"""Arrangement view: clips placed on the linear timeline.

Includes:

- ``arrangement_clips_list`` / ``arrangement_summary`` — read tools.
- ``arrangement_insert_midi_clip`` / ``arrangement_move_clip`` — write
  tools that go through the bridge (require AbletonFullControlBridge
  1.4.0+; the version handshake from PR #11 surfaces an actionable
  warning at first use on older bridges).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..osc_client import get_client
from ..section import DEFAULT_LEAD_KEYWORDS, find_lead_sections


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

    @mcp.tool()
    async def arrangement_find_sections(
        track_index: int,
        method: str = "auto",
        keywords: list[str] | None = None,
        min_length_beats: float = 1.0,
        max_overlapping_other_tracks: int = 1,
    ) -> dict[str, Any]:
        """Find regions where ``track_index`` is featured (e.g. "the solo").

        Layer 1.2 of the mix-aware shaping stack. The motivating use case
        is *"the lead doesn't cut through during the solo"* — to fix that,
        the system first needs to locate the solo. This tool answers the
        "where on the timeline" question.

        Three detection strategies:

        - ``method="clip_name"`` — match each clip's name against
          ``keywords`` (default: ``solo``, ``lead``, ``break``,
          ``bridge``, ``feature``, ``fill``, ``interlude``).
          Confidence 1.0 — user labeled it.
        - ``method="clip_overlap"`` — find clips on the focal track
          where at most ``max_overlapping_other_tracks`` other tracks
          are playing simultaneously (a drop-out solo arrangement).
          Confidence 0.5..0.9 based on how alone the focal is.
        - ``method="auto"`` (default) — try clip-name first; if it
          returns nothing, fall back to clip-overlap.
        - ``method="both"`` — run both, merge overlapping detections.

        Returns:
            ``{status, focal_track_index, method, sections: [...]}``.
            Each section has ``start_beats``, ``end_beats``,
            ``length_beats``, ``confidence`` in [0, 1], ``kind``,
            ``details`` (detector-specific).
        """
        osc = await get_client()
        try:
            sections = await find_lead_sections(
                focal_track_index=int(track_index),
                method=method,
                keywords=tuple(keywords) if keywords else DEFAULT_LEAD_KEYWORDS,
                min_length_beats=float(min_length_beats),
                max_overlapping_other_tracks=int(max_overlapping_other_tracks),
                osc_client=osc,
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "focal_track_index": int(track_index),
            "method": method,
            "sections": [s.to_dict() for s in sections],
        }

    @mcp.tool()
    async def arrangement_insert_midi_clip(
        track_index: int,
        position_beats: float,
        length_beats: float = 4.0,
    ) -> dict[str, Any]:
        """Create an empty MIDI clip on the arrangement timeline.

        - ``track_index`` must point to a MIDI track. Audio tracks raise.
        - ``position_beats`` is the start position in beats (0-based).
          Negative values are rejected.
        - ``length_beats`` is the clip length in beats (default 4 = one bar in 4/4).

        Live's LOM rejects overlapping clips on the same track, so the
        target [position, position+length] range must not intersect any
        existing arrangement clip on that track. We surface Live's error
        as ``status="error"`` rather than raising.

        Returns the new clip's ``clip_index`` so the caller can follow
        up with ``clip_*`` setters (e.g. notes, color, name) via the
        existing arrangement-clip tool surface.

        Requires bridge version 1.4.0+; older bridges raise
        :class:`AbletonBridgeOutdated` with the install hint via the
        handshake added in PR #11.
        """
        bridge = get_bridge_client()
        try:
            await bridge.require_handler("clip.create_arrangement_midi_clip")
        except Exception as exc:
            return {"status": "error", "error": str(exc), "stage": "version_check"}
        try:
            result = await bridge.call(
                "clip.create_arrangement_midi_clip",
                track_index=int(track_index),
                position=float(position_beats),
                length=float(length_beats),
            )
        except AbletonBridgeError as exc:
            return {"status": "error", "error": str(exc), "stage": "create"}
        return {"status": "ok", **result}

    @mcp.tool()
    async def arrangement_move_clip(
        track_index: int,
        clip_index: int,
        new_position_beats: float,
    ) -> dict[str, Any]:
        """Move an arrangement clip to a new start position on the timeline.

        Live 11+ exposes ``Clip.move(beats_delta)`` as the only way to
        relocate a clip — we compute the delta from the current
        ``start_time`` and call it. The result includes the actual
        post-move position (Live may snap to grid quantization).

        Live's LOM rejects moves that would overlap an existing clip on
        the same track; we surface that as ``status="error"``.

        Requires bridge version 1.4.0+ (Live 11+ in turn, where
        ``Clip.move`` exists).
        """
        bridge = get_bridge_client()
        try:
            await bridge.require_handler("clip.move_arrangement_clip")
        except Exception as exc:
            return {"status": "error", "error": str(exc), "stage": "version_check"}
        try:
            result = await bridge.call(
                "clip.move_arrangement_clip",
                track_index=int(track_index),
                clip_index=int(clip_index),
                new_position=float(new_position_beats),
            )
        except AbletonBridgeError as exc:
            return {"status": "error", "error": str(exc), "stage": "move"}
        if not result.get("moved"):
            return {"status": "error", **result}
        return {"status": "ok", **result}
