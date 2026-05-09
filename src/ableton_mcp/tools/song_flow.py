"""MCP tool surface for the conversational song-flow.

Four orthogonal tools the LLM client chains based on the user's request:

- ``song_analyze``    — tempo + length + librosa-estimated key + Live scale UI hint
- ``song_transpose``  — per-clip in-place transpose to a target key, then bounce
- ``song_make_variations`` — instrument-up remixes from a stem set
- ``song_import_variations_to_live`` — drop variation wavs into the active set as new tracks

Stem separation itself is the existing ``stems_split`` tool (now with
``n_stems=6`` for the song flow). No monolithic pipeline tool — the LLM
orchestrates these conversationally.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..song_flow import (
    analyze_song,
    import_variations_to_live,
    load_wav_to_arrangement,
    make_variations,
    transpose_song,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def song_analyze(
        output_dir: str | None = None,
        slice_duration_sec: float | None = None,
    ) -> dict[str, Any]:
        """Snapshot the active Live song: tempo, length, key estimate, scale hint.

        Bounces a 30-second slice (or shorter on a shorter song) and runs
        librosa chroma key estimation on it. Tempo + length come from Live
        directly via OSC. ``scale_hint`` reflects Live's UI Scale feature
        if the user has set it, otherwise None.

        Realtime cost: ~30 seconds for the slice bounce. Use this when you
        need to know what to transpose to/from, or to confirm tempo before
        a downstream operation.
        """
        return await analyze_song(
            output_dir=output_dir,
            slice_duration_sec=slice_duration_sec,
        )

    @mcp.tool()
    async def song_transpose(
        target_key: str,
        source_key: str | None = None,
        direction: str = "auto",
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """Transpose the arrangement to ``target_key`` (high quality, not chipmunk).

        For every audio arrangement clip: enables warping, sets warp mode to
        Complex Pro (Live's gold-standard pitch-preserving engine), and adds
        the semitone delta to ``pitch_coarse`` (so any pre-existing pitch
        offset rides along). For every MIDI arrangement clip: shifts every
        note pitch by the delta. Then bounces the song. Then restores every
        snapshot so the original session state is preserved.

        Args:
            target_key: e.g. ``"F#"``, ``"Bb"``, ``"c minor"`` (case- and
                        suffix-tolerant; we only honour the tonic).
            source_key: explicit source. Omit to auto-detect via librosa
                        chroma. Detection is surfaced in the result so the
                        caller can re-call with an override if it was wrong.
            direction: ``"auto"`` (shortest signed path; tritone goes down),
                       ``"up"``, or ``"down"``.
            output_path: where to write the transposed wav. Defaults to
                         ``data/song_flow/<timestamp>/transposed_<key>.wav``.

        Realtime cost: ~30 s slice bounce (only when source_key is auto)
        plus the full song bounce.
        """
        return await transpose_song(
            target_key=target_key,
            source_key=source_key,
            direction=direction,
            output_path=output_path,
        )

    @mcp.tool()
    async def song_make_variations(
        stems: list[dict[str, Any]],
        output_dir: str,
        boost_db: float = 6.0,
        attenuation_db: float = -3.0,
        output_set: str = "remix",
        name_prefix: str = "",
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
    ) -> dict[str, Any]:
        """Produce instrument-up remixes (or practice-pack tracks) from a stem set.

        Pass the ``stems`` list returned by ``stems_split`` directly.

        ``output_set="remix"`` (default) produces the conversational
        song-flow shape:

        - ``original.wav``     — sum at unity gain (sanity check)
        - ``instrumental.wav`` — sum without the vocals stem
        - ``<stem>_up.wav``    — full mix with one stem at +``boost_db`` and
                                 the rest at ``attenuation_db``. One file
                                 per stem (vocals included).

        With a 6-stem htdemucs_6s split that's 8 wavs.

        ``output_set="practice_pack"`` produces practice-track shape with
        two variants per non-vocal instrument (so a player can rehearse
        with or without the singer's part audible):

        - ``no_vocals.wav``                    — instrumental backing
        - ``<stem>_boost_no_vocals.wav``       — focal +boost, others -duck,
                                                  vocals dropped
        - ``<stem>_boost_with_vocals.wav``     — same focal/non-vocal balance
                                                  but with vocals at unity

        With a 6-stem split (5 instrument stems + 1 vocals) that's 11 wavs.

        ``name_prefix`` is prepended to every output filename — useful for
        batching multiple songs into one directory (e.g. ``"Reasons - "``).

        Pure file-on-disk math — no Live, no realtime. mp3 encoding is
        opt-in and best-effort.
        """
        return make_variations(
            stems,
            output_dir,
            boost_db=boost_db,
            attenuation_db=attenuation_db,
            output_set=output_set,
            name_prefix=name_prefix,
            encode_mp3=encode_mp3,
            bitrate_kbps=bitrate_kbps,
        )

    @mcp.tool()
    async def song_load_wav_to_arrangement(
        wav_path: str,
        track_name: str | None = None,
        position_beats: float = 0.0,
    ) -> dict[str, Any]:
        """Drop a wav file onto a fresh arrangement audio track at ``position_beats``.

        Use this when you have a standalone wav on disk and want to run
        ``song_transpose`` / ``stems_split`` / ``song_make_variations``
        against it — those tools operate on the active Live arrangement,
        so the wav has to land in arrangement view first.

        Live 11's LOM doesn't expose a clean single-call "load wav into
        arrangement" — the bridge probes a few signature variants and
        returns ``status="not_supported"`` with a clear workaround if
        none work on your build. The temp track is auto-cleaned in that
        case so your session isn't littered.
        """
        return await load_wav_to_arrangement(
            wav_path,
            track_name=track_name,
            position_beats=position_beats,
        )

    @mcp.tool()
    async def song_import_variations_to_live(
        variations: list[dict[str, Any]],
        track_name_prefix: str = "Variation: ",
        clip_index: int = 0,
    ) -> dict[str, Any]:
        """Drop variation wavs into the active Live set as new audio tracks.

        ``variations`` is the list returned by ``song_make_variations``
        under the ``variations`` key (or any list of ``{label, wav_path}``).
        For each: creates a fresh audio track named
        ``f"{track_name_prefix}{label}"`` and loads the wav into session
        clip slot ``clip_index`` via the bridge's ``browser.load_sample``.

        Per-variation failures are reported in the response rather than
        aborting the batch.
        """
        return await import_variations_to_live(
            variations,
            track_name_prefix=track_name_prefix,
            clip_index=clip_index,
        )
