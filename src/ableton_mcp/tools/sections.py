"""Lyric-aligned song section detection tools.

``sections_detect`` correlates a Planning Center chart against a recording:
stable-ts transcription of the Demucs vocal stem → discovery of the
performed section order → forced-alignment refinement → instrumental gap
inference → downbeat snapping → ``sections.json`` sidecar.

``sections_write_locators`` (M5) writes the detected sections into Ableton
as named arrangement locators.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..click_track import detect_beats as _detect_beats
from ..paths import data_dir
from ..pco import ChartModel, PCOClient, PCOError, PCONotConfigured
from ..song_sections.detect import (
    detect_sections as _detect_sections,
    read_sidecar as _read_sidecar,
    sidecar_path_for as _sidecar_path_for,
    write_sidecar as _write_sidecar,
)
from ..song_sections.locators import write_section_locators as _write_section_locators
from ..song_sections.transcribe import (
    align_lyrics as _align_lyrics,
    load_model as _load_model,
    transcribe_vocal as _transcribe_vocal,
)

DEFAULT_STEMS_ROOT = data_dir("stems")
DEFAULT_STEM_MODEL = "htdemucs_6s"


def _err(exc: Exception) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _default_vocal_stem(audio_path: Path) -> Path:
    """Where stems_split puts the vocal stem for this audio file."""
    return DEFAULT_STEMS_ROOT / DEFAULT_STEM_MODEL / audio_path.stem / "vocals.wav"


def _audio_duration_sec(audio_path: Path) -> float:
    import soundfile as sf

    info = sf.info(str(audio_path))
    return float(info.frames) / float(info.samplerate)


async def _resolve_chart(
    pco_song_id: str | None, pco_query: str | None, arrangement_id: str | None
) -> ChartModel | dict[str, Any]:
    """ChartModel, or an error/disambiguation dict ready to return."""
    if not pco_song_id and not pco_query:
        return {
            "status": "error",
            "error": "pass pco_song_id or pco_query to identify the chart",
        }
    client = PCOClient()
    if pco_song_id:
        return await client.get_chart(str(pco_song_id), arrangement_id)
    matches = await client.list_songs(query=pco_query, limit=5)
    if not matches:
        return {"status": "error", "error": f"no PCO songs match {pco_query!r}"}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "error": f"{len(matches)} PCO songs match {pco_query!r}; "
            "pass pco_song_id to pick one",
            "candidates": matches,
        }
    return await client.get_chart(matches[0]["song_id"], arrangement_id)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def sections_detect(
        audio_path: str,
        vocal_stem_path: str | None = None,
        pco_song_id: str | None = None,
        pco_query: str | None = None,
        arrangement_id: str | None = None,
        model_size: str = "small",
        device: str | None = None,
        min_gap_bars: float = 2.0,
        refine_with_alignment: bool = True,
        write_sidecar: bool = True,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """Detect song sections in a recording by correlating its PCO chart.

        Pipeline: transcribe the Demucs vocal stem (stable-ts, cached) →
        fuzzy-match each chart section's lyrics against the transcript in
        time order (discovers the PERFORMED order, including extra repeats
        the chart doesn't list) → refine starts via known-text forced
        alignment → infer instrumental sections from vocal gaps → snap all
        boundaries to beat_this downbeats → write a sections.json sidecar.

        Prereqs: run stems_split on the audio first (the vocal stem is
        expected at data/stems/htdemucs_6s/<basename>/vocals.wav unless
        vocal_stem_path is given — this tool does NOT run Demucs itself),
        and set PCO_APP_ID/PCO_SECRET for chart access.

        Args:
            audio_path: the full-mix recording (beat grid + duration source).
            vocal_stem_path: override the conventional stem location.
            pco_song_id / pco_query: chart identity (id wins; query must
                match exactly one song or candidates are returned).
            arrangement_id: pick a specific arrangement (default: first).
            model_size: Whisper checkpoint (``small`` default).
            device: ``cuda``/``cpu``/None (auto).
            min_gap_bars: minimum instrumental gap worth marking, in bars.
            refine_with_alignment: disable to skip the forced-alignment
                refinement pass (faster, coarser starts).
            write_sidecar: write sections.json next to audio_path (or to
                output_path).
            output_path: explicit sidecar destination.

        Returns `{status, sections: [{label, display_name, role, kind,
        start_sec, end_sec, start_beats, confidence, status, ...}],
        performed_sequence, chart_sequence, tempo, warnings,
        sections_json_path}`.
        """
        try:
            audio = Path(audio_path)
            if not audio.is_file():
                return {"status": "error", "error": f"audio file not found: {audio}"}

            stem = Path(vocal_stem_path) if vocal_stem_path else _default_vocal_stem(audio)
            if not stem.is_file():
                return {
                    "status": "error",
                    "error": (
                        f"vocal stem not found: {stem} — run stems_split on "
                        f"{audio.name} first (or pass vocal_stem_path)"
                    ),
                }

            chart = await _resolve_chart(pco_song_id, pco_query, arrangement_id)
            if isinstance(chart, dict):
                return chart
            if not any(s.lyrics for s in chart.sections):
                return {
                    "status": "error",
                    "error": (
                        f"chart for {chart.title!r} has no section lyrics — "
                        "lyric-based detection needs them (check the PCO "
                        "arrangement, or run pco_library_probe)"
                    ),
                    "chart_warnings": list(chart.warnings),
                }

            beats = _detect_beats(str(audio), device=device)
            model = _load_model(model_size, device)
            transcript = _transcribe_vocal(
                stem, model_size=model_size, device=device, model=model
            )
            aligner = None
            if refine_with_alignment:
                def aligner(text: str) -> list[dict[str, Any]]:
                    return _align_lyrics(stem, text, model=model)

            result = _detect_sections(
                chart,
                transcript,
                beats_sec=list(beats.beats_sec),
                downbeats_sec=list(beats.downbeats_sec),
                bpm_estimate=beats.bpm_estimate,
                audio_duration_sec=_audio_duration_sec(audio),
                audio_path=str(audio),
                aligner=aligner,
                min_gap_bars=float(min_gap_bars),
            )

            sidecar_path: str | None = None
            if write_sidecar:
                dest = Path(output_path) if output_path else _sidecar_path_for(audio)
                sidecar_path = str(_write_sidecar(result, dest))

            payload = result.to_dict()
        except PCONotConfigured as exc:
            return {"status": "not_configured", "error": str(exc)}
        except PCOError as exc:
            return _err(exc)
        except FileNotFoundError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
        return {
            "status": "ok",
            "sections": payload["sections"],
            "performed_sequence": payload["performed_sequence"],
            "chart_sequence": payload["chart_sequence"],
            "tempo": payload["tempo"],
            "warnings": payload["warnings"],
            "sections_json_path": sidecar_path,
        }

    @mcp.tool()
    async def sections_write_locators(
        sections_json_path: str | None = None,
        audio_path: str | None = None,
        arrangement_start_beats: float = 0.0,
        tolerance_beats: float = 0.5,
        clear_existing: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Write detected sections into Ableton as named arrangement locators.

        Loads a sections.json (explicit path, or next to audio_path) and
        creates one locator per section at its start_beats, idempotently:
        a same-named locator already within tolerance is skipped (re-runs
        are no-ops); a differently-named locator within 0.25 beats of a
        target is renamed in place. Live's cue API is a playhead toggle, so
        the transport is stopped and the playhead moves during the write.

        Contract: the practice WAV must start at `arrangement_start_beats`
        (default 0.0 = 1.1.1) and Live's tempo must match the detected BPM
        (the recording's musical beats must land on Live's grid). A tempo
        mismatch > 1 BPM refuses to write unless force=True.

        Args:
            sections_json_path: sidecar to load (wins over audio_path).
            audio_path: locate the sidecar next to this file instead.
            arrangement_start_beats: where WAV beat 0 sits in the arrangement.
            tolerance_beats: idempotency window for same-named locators.
            clear_existing: delete same-named locators at stale positions first.
            force: write even if the tempo guard fails.

        Returns `{status, added, renamed, skipped, deleted, failed,
        tempo_check, warnings}`.
        """
        try:
            if sections_json_path:
                sidecar = Path(sections_json_path)
            elif audio_path:
                sidecar = _sidecar_path_for(audio_path)
            else:
                return {
                    "status": "error",
                    "error": "pass sections_json_path or audio_path",
                }
            data = _read_sidecar(sidecar)
            report = await _write_section_locators(
                data.get("sections") or [],
                bpm_estimate=(data.get("tempo") or {}).get("bpm_estimate"),
                arrangement_start_beats=float(arrangement_start_beats),
                tolerance_beats=float(tolerance_beats),
                clear_existing=bool(clear_existing),
                force=bool(force),
            )
        except (FileNotFoundError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
        status = "ok" if not report.failed else "partial"
        if not report.tempo_check.get("ok", True) and not force:
            status = "tempo_mismatch"
        return {"status": status, **report.to_dict()}
