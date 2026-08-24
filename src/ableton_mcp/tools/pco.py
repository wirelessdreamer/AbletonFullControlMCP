"""Planning Center Services tools: library probe, song search, chart fetch.

Chart-side inputs for lyric-aligned section detection (see tools/sections.py).
Requires the PCO_APP_ID / PCO_SECRET environment variables (a Personal
Access Token from https://api.planningcenteronline.com/oauth/applications).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..pco import PCOClient, PCOError, PCONotConfigured


def _err(exc: Exception) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def pco_library_probe(
        limit: int = 25, query: str | None = None
    ) -> dict[str, Any]:
        """Sample the Planning Center song library and report data coverage.

        Run this FIRST when setting up section detection: it reports what
        fraction of songs have arrangement sequences, per-section lyrics,
        BPM, and meter — plus `schema_notes` describing the actual sections
        endpoint shape if it differs from expectations.

        Args:
            limit: number of songs to sample (throttled ~4/sec for rate limits).
            query: optional title filter, e.g. "goodness".

        Returns `{status, sampled, coverage: {pct_with_sequence,
        pct_with_section_lyrics, pct_with_bpm, pct_with_meter,
        pct_with_chord_chart, errors}, songs: [...], schema_notes, warnings}`.
        """
        client = PCOClient()
        if not client.is_configured():
            return {
                "status": "not_configured",
                "error": (
                    "Set the PCO_APP_ID and PCO_SECRET environment variables "
                    "(Personal Access Token from "
                    "https://api.planningcenteronline.com/oauth/applications)."
                ),
            }
        try:
            report = await client.library_probe(limit=int(limit), query=query)
        except PCONotConfigured as exc:
            return {"status": "not_configured", "error": str(exc)}
        except PCOError as exc:
            return _err(exc)
        return {"status": "ok", **report}

    @mcp.tool()
    async def pco_find_song(query: str) -> dict[str, Any]:
        """Search Planning Center songs by title.

        Returns `{status, matches: [{song_id, title, author, ccli_number,
        arrangements: [{arrangement_id, name, bpm, sequence_len,
        has_chord_chart}]}]}` — pass a song_id (and optionally an
        arrangement_id) on to pco_get_chart / sections_detect.
        """
        client = PCOClient()
        try:
            songs = await client.list_songs(query=query, limit=10)
            matches: list[dict[str, Any]] = []
            for song in songs:
                arrangements = await client.list_arrangements(song["song_id"])
                matches.append(
                    {
                        **song,
                        "arrangements": [
                            {
                                "arrangement_id": a["arrangement_id"],
                                "name": a.get("name"),
                                "bpm": a.get("bpm"),
                                "sequence_len": len(a.get("sequence") or []),
                                "has_chord_chart": a.get("has_chord_chart", False),
                            }
                            for a in arrangements
                        ],
                    }
                )
        except PCONotConfigured as exc:
            return {"status": "not_configured", "error": str(exc)}
        except PCOError as exc:
            return _err(exc)
        return {"status": "ok", "matches": matches, "count": len(matches)}

    @mcp.tool()
    async def pco_get_chart(
        song_id: str, arrangement_id: str | None = None
    ) -> dict[str, Any]:
        """Fetch the normalized chart for a song's arrangement.

        Returns `{status, chart: {song_id, arrangement_id, title,
        ccli_number, key, bpm, meter, sequence: [labels...],
        sections: [{label, role, lyrics}], warnings}}`. Defaults to the
        first arrangement; a warning lists the alternatives when several
        exist. Falls back to parsing the arrangement's ChordPro chord_chart
        when the sections endpoint carries no lyrics.
        """
        client = PCOClient()
        try:
            chart = await client.get_chart(str(song_id), arrangement_id)
        except PCONotConfigured as exc:
            return {"status": "not_configured", "error": str(exc)}
        except PCOError as exc:
            return _err(exc)
        return {"status": "ok", "chart": chart.to_dict()}
