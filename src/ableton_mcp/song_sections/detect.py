"""Detection pipeline orchestrator: chart + transcript + beats → sections.

Pure orchestration over injectable inputs (a :class:`ChartModel`, a
:class:`Transcript`, a beat grid, an optional alignment callable) so tests
run without models. The MCP tool (:mod:`ableton_mcp.tools.sections`) wires
the real producers in.

Stages: discovery DP (:mod:`.reconcile`) → forced-alignment refinement of
section starts (PsalmsKaraoke's ``align(original_split=True)`` trick) →
instrumental-gap inference → downbeat snapping → ``sections.json``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Callable, Sequence

from ..pco import ChartModel
from .model import DetectedSection, MatchedBlock, SectionsResult, Transcript
from .reconcile import (
    assign_display_names,
    block_confidence,
    diff_sequences,
    discover_blocks,
)
from .transcribe import MIN_WORD_PROB, find_degenerate_segments

log = logging.getLogger(__name__)

SIDECAR_NAME = "sections.json"
REFINEMENT_MAX_SHIFT_SEC = 3.0
"""Alignment-refined starts further than this from the discovery start are
rejected (alignment probably latched onto the wrong repeat)."""

DEFAULT_MIN_GAP_BARS = 2.0
DEFAULT_PICKUP_BEATS = 1.0
DEFAULT_SNAP_WINDOW_BEATS = 1.5

_INSTRUMENTAL_ROLES = frozenset({"intro", "interlude", "outro", "solo", "fill", "hit"})


# ---------------------------------------------------------------------------
# Beat-grid helpers (pure)
# ---------------------------------------------------------------------------


def median_interval(times: Sequence[float]) -> float | None:
    """Median gap between consecutive times; None with fewer than 2 points."""
    if len(times) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(times, times[1:]))
    mid = len(gaps) // 2
    if len(gaps) % 2:
        return gaps[mid]
    return (gaps[mid - 1] + gaps[mid]) / 2.0


def beat_index_at(t_sec: float, beats_sec: Sequence[float]) -> float:
    """Fractional beat index of ``t_sec`` on the detected beat grid.

    Clamps before the first / after the last beat using the median beat
    interval, so early pickups and tail fades still get sane positions.
    """
    if not beats_sec:
        return 0.0
    interval = median_interval(beats_sec) or 0.5
    if t_sec <= beats_sec[0]:
        return max(0.0, (t_sec - beats_sec[0]) / interval)
    for i in range(len(beats_sec) - 1):
        if beats_sec[i] <= t_sec < beats_sec[i + 1]:
            span = beats_sec[i + 1] - beats_sec[i]
            frac = (t_sec - beats_sec[i]) / span if span > 0 else 0.0
            return i + frac
    return (len(beats_sec) - 1) + (t_sec - beats_sec[-1]) / interval


def snap_to_downbeat(
    t_raw: float,
    downbeats: Sequence[float],
    beat_duration: float,
    *,
    pickup_beats: float = DEFAULT_PICKUP_BEATS,
    snap_window_beats: float = DEFAULT_SNAP_WINDOW_BEATS,
    allow_pickup: bool = True,
) -> tuple[float, bool]:
    """Snap ``t_raw`` to a downbeat. Returns ``(snapped_time, did_snap)``.

    Pickup-forward rule first (sung anacrusis belongs to the bar it leads
    into): a downbeat within ``pickup_beats × beat_duration`` *after*
    ``t_raw`` wins even when the previous downbeat is numerically closer.
    Otherwise the nearest downbeat within ``snap_window_beats`` is used.
    Instrumental boundaries pass ``allow_pickup=False`` (no anacrusis
    concept) and get the nearest-only rule.
    """
    if not downbeats or beat_duration <= 0:
        return t_raw, False
    if allow_pickup:
        ahead = [d for d in downbeats if 0.0 < d - t_raw <= pickup_beats * beat_duration]
        if ahead:
            return ahead[0], True
    nearest = min(downbeats, key=lambda d: abs(d - t_raw))
    if abs(nearest - t_raw) <= snap_window_beats * beat_duration:
        return nearest, True
    return t_raw, False


# ---------------------------------------------------------------------------
# Performed-order lyric text (for alignment refinement)
# ---------------------------------------------------------------------------


def build_performed_text(
    blocks: Sequence[MatchedBlock], chart: ChartModel
) -> tuple[str, list[int]]:
    """Concatenate each matched block's chart lyrics in performed order.

    Returns ``(text, line_owner)`` where ``line_owner[k]`` is the block
    index owning line ``k`` — ``align(original_split=True)`` returns one
    segment per line, so this maps alignment segments back to sections.
    """
    by_label = {s.label.strip().lower(): s for s in chart.sections}
    lines: list[str] = []
    owner: list[int] = []
    for bi, block in enumerate(blocks):
        section = by_label.get(block.label.strip().lower())
        if section is None:
            continue
        for line in section.lyrics.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
                owner.append(bi)
    return "\n".join(lines), owner


def refine_block_starts(
    blocks: Sequence[MatchedBlock],
    align_segments: Sequence[dict[str, Any]],
    line_owner: Sequence[int],
    *,
    max_shift_sec: float = REFINEMENT_MAX_SHIFT_SEC,
) -> tuple[list[float], list[float], list[str]]:
    """Per-block refined (start, sung_end) from forced-alignment segments.

    Falls back to the discovery block's own span (and records a warning)
    when the relevant segments are degenerate or the refined start drifted
    more than ``max_shift_sec`` from the discovery start.
    """
    degenerate = find_degenerate_segments(list(align_segments))
    starts = [b.start_sec for b in blocks]
    ends = [b.end_sec for b in blocks]
    warnings: list[str] = []

    first_line: dict[int, int] = {}
    last_line: dict[int, int] = {}
    for li, bi in enumerate(line_owner):
        first_line.setdefault(bi, li)
        last_line[bi] = li

    for bi, block in enumerate(blocks):
        li = first_line.get(bi)
        if li is None or li >= len(align_segments):
            continue
        if li in degenerate:
            warnings.append(f"alignment_degenerate: {block.label} (block {bi})")
            continue
        refined = float(align_segments[li].get("start", block.start_sec))
        if abs(refined - block.start_sec) > max_shift_sec:
            warnings.append(
                f"alignment_drift: {block.label} (block {bi}) refined start "
                f"{refined:.2f}s is {abs(refined - block.start_sec):.2f}s from "
                f"discovery start {block.start_sec:.2f}s; keeping discovery"
            )
            continue
        starts[bi] = refined
        lj = last_line.get(bi)
        if lj is not None and lj < len(align_segments) and lj not in degenerate:
            end = float(align_segments[lj].get("end", block.end_sec))
            if end > refined:
                ends[bi] = end
    return starts, ends, warnings


# ---------------------------------------------------------------------------
# Instrumental gap inference (pure)
# ---------------------------------------------------------------------------


def _sequence_gap_label(
    chart: ChartModel,
    prev_chart_index: int | None,
    next_chart_index: int | None,
) -> str | None:
    """Label for a gap from the chart sequence, if it names one.

    Any sequence entry between the neighboring matched vocal entries that
    is not a lyric-bearing section (an "Intro"/"Interlude"/"Outro" entry,
    or a label with no section definition at all) donates its label —
    Intro before the first vocal, Outro after the last, Interlude between.
    """
    vocal_labels = {s.label.strip().lower() for s in chart.sections if s.lyrics.strip()}
    seq = [label.strip().lower() for label in chart.sequence]
    lo = -1 if prev_chart_index is None else prev_chart_index
    hi = len(seq) if next_chart_index is None else next_chart_index
    for idx in range(lo + 1, min(hi, len(seq))):
        if seq[idx] not in vocal_labels:
            return chart.sequence[idx]
    return None


def infer_gaps(
    vocal_sections: Sequence[DetectedSection],
    chart: ChartModel,
    *,
    audio_duration_sec: float,
    bar_duration_sec: float,
    min_gap_bars: float = DEFAULT_MIN_GAP_BARS,
) -> list[DetectedSection]:
    """Instrumental sections from gaps in vocal coverage.

    ``vocal_sections`` must be time-ordered with ``match["sung_span_sec"]``
    set. A gap qualifies when it spans at least ``min_gap_bars`` bars.
    """
    min_gap_sec = min_gap_bars * bar_duration_sec
    gaps: list[DetectedSection] = []

    def _prev_chart_index(upto: int) -> int | None:
        """Last known chart position before the gap. Extras (chart_index
        None) are looked *through* — otherwise a gap inside a run of extra
        repeats scans the whole sequence and steals the Intro label."""
        for s in reversed(vocal_sections[:upto]):
            if s.chart_index is not None:
                return s.chart_index
        return None

    def _next_chart_index(frm: int) -> int | None:
        for s in vocal_sections[frm:]:
            if s.chart_index is not None:
                return s.chart_index
        return None

    def _mk(start: float, end: float, prev_ci: int | None, next_ci: int | None) -> DetectedSection:
        from ..structure.parser import detect_role

        label = _sequence_gap_label(chart, prev_ci, next_ci)
        from_sequence = label is not None
        if label is None:
            label = "Inst"
        return DetectedSection(
            label=label,
            display_name=label,
            role=detect_role(label),
            kind="instrumental_gap",
            start_sec=start,
            end_sec=end,
            confidence=0.75 if from_sequence else 0.6,
            status="matched" if from_sequence else "extra",
            chart_index=None,
        )

    if not vocal_sections:
        if audio_duration_sec >= min_gap_sec:
            gaps.append(_mk(0.0, audio_duration_sec, None, None))
        return gaps

    first = vocal_sections[0]
    if first.start_sec >= min_gap_sec:
        gaps.append(_mk(0.0, first.start_sec, None, _next_chart_index(0)))

    for k, (a, b) in enumerate(zip(vocal_sections, vocal_sections[1:])):
        sung_end = float((a.match or {}).get("sung_span_sec", [a.start_sec, a.end_sec])[1])
        if b.start_sec - sung_end >= min_gap_sec:
            gaps.append(
                _mk(sung_end, b.start_sec, _prev_chart_index(k + 1), _next_chart_index(k + 1))
            )

    last = vocal_sections[-1]
    last_end = float((last.match or {}).get("sung_span_sec", [last.start_sec, last.end_sec])[1])
    if audio_duration_sec - last_end >= min_gap_sec:
        gaps.append(
            _mk(last_end, audio_duration_sec, _prev_chart_index(len(vocal_sections)), None)
        )

    return gaps


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def detect_sections(
    chart: ChartModel,
    transcript: Transcript,
    *,
    beats_sec: Sequence[float],
    downbeats_sec: Sequence[float],
    bpm_estimate: float | None,
    audio_duration_sec: float,
    audio_path: str | None = None,
    aligner: Callable[[str], list[dict[str, Any]]] | None = None,
    min_word_prob: float = MIN_WORD_PROB,
    min_gap_bars: float = DEFAULT_MIN_GAP_BARS,
    pickup_beats: float = DEFAULT_PICKUP_BEATS,
    snap_window_beats: float = DEFAULT_SNAP_WINDOW_BEATS,
) -> SectionsResult:
    """Run discovery → refinement → gaps → snapping. Pure given its inputs.

    Args:
        chart: normalized PCO chart (sections + sequence).
        transcript: free transcription of the vocal stem.
        beats_sec / downbeats_sec / bpm_estimate: beat grid (beat_this).
        audio_duration_sec: full-mix length (tiles the last section's end).
        audio_path: recorded in the sidecar source block.
        aligner: optional ``text -> alignment segments`` callable (stable-ts
            ``align`` with ``original_split=True``); None skips refinement.
        min_word_prob: hallucination-guard threshold for discovery words.
        min_gap_bars: minimum instrumental gap worth a section marker.
        pickup_beats / snap_window_beats: downbeat-snapping tuning.
    """
    warnings: list[str] = []
    usable = transcript.usable_words(min_word_prob)
    if len(usable) < len(transcript.words):
        dropped = len(transcript.words) - len(usable)
        log.info("dropped %d low-probability words before matching", dropped)

    blocks = discover_blocks(usable, chart.sections)
    if not blocks:
        warnings.append(
            "no chart sections matched the transcript — check that the chart "
            "lyrics correspond to this recording"
        )

    performed_labels = [b.label for b in blocks]
    seq_entries, seq_warnings = diff_sequences(performed_labels, list(chart.sequence))
    warnings.extend(seq_warnings)
    display_names = assign_display_names(performed_labels)

    # Refinement via forced alignment of the performed-order lyric text.
    starts = [b.start_sec for b in blocks]
    sung_ends = [b.end_sec for b in blocks]
    refined_source = ["discovery"] * len(blocks)
    if aligner is not None and blocks:
        text, line_owner = build_performed_text(blocks, chart)
        if text:
            try:
                segments = aligner(text)
            except Exception as exc:
                warnings.append(f"alignment_failed: {type(exc).__name__}: {exc}")
                segments = []
            if segments:
                starts, sung_ends, refine_warnings = refine_block_starts(
                    blocks, segments, line_owner
                )
                warnings.extend(refine_warnings)
                for bi in range(len(blocks)):
                    if starts[bi] != blocks[bi].start_sec:
                        refined_source[bi] = "align"

    # Snapping.
    beat_duration = median_interval(beats_sec) or (
        60.0 / bpm_estimate if bpm_estimate else 0.5
    )
    bar_duration = median_interval(downbeats_sec) or beat_duration * 4

    vocal_sections: list[DetectedSection] = []
    for bi, block in enumerate(blocks):
        raw = starts[bi]
        snapped, did_snap = snap_to_downbeat(
            raw,
            downbeats_sec,
            beat_duration,
            pickup_beats=pickup_beats,
            snap_window_beats=snap_window_beats,
        )
        section_warnings: list[str] = []
        if not did_snap:
            section_warnings.append("unsnapped: no downbeat within window")
        entry = seq_entries[bi] if bi < len(seq_entries) else {"status": "extra", "chart_index": None}
        confidence = block_confidence(block.score)
        if refined_source[bi] == "discovery" and aligner is not None:
            confidence = min(confidence, 0.5)
        vocal_sections.append(
            DetectedSection(
                label=block.label,
                display_name=display_names[bi],
                role=_role_for(chart, block.label),
                kind="vocal",
                start_sec=snapped,
                end_sec=snapped,  # tiled below
                confidence=confidence,
                status=str(entry["status"]),
                chart_index=entry["chart_index"],
                start_sec_raw=raw,
                snap_delta_sec=snapped - raw if did_snap else None,
                match={
                    "score": block.score,
                    "word_span": [block.word_start, block.word_end],
                    "sung_span_sec": [starts[bi], sung_ends[bi]],
                    "source": refined_source[bi],
                },
                warnings=tuple(section_warnings),
            )
        )

    # Monotonicity after snapping: nudge collisions forward.
    for k in range(1, len(vocal_sections)):
        prev, cur = vocal_sections[k - 1], vocal_sections[k]
        if cur.start_sec <= prev.start_sec:
            later = [d for d in downbeats_sec if d > prev.start_sec]
            new_start = later[0] if later else cur.start_sec_raw or cur.start_sec
            warnings.append(
                f"snap_collision: {cur.display_name} snapped onto "
                f"{prev.display_name}; nudged to {new_start:.2f}s"
            )
            vocal_sections[k] = dataclasses.replace(cur, start_sec=new_start)

    gaps = infer_gaps(
        vocal_sections,
        chart,
        audio_duration_sec=audio_duration_sec,
        bar_duration_sec=bar_duration,
        min_gap_bars=min_gap_bars,
    )
    # Snap gap starts (nearest rule, no pickup) except the song head at 0.0.
    snapped_gaps: list[DetectedSection] = []
    for g in gaps:
        if g.start_sec <= 0.0:
            snapped_gaps.append(g)
            continue
        snapped, did_snap = snap_to_downbeat(
            g.start_sec,
            downbeats_sec,
            beat_duration,
            snap_window_beats=snap_window_beats,
            allow_pickup=False,
        )
        snapped_gaps.append(
            dataclasses.replace(
                g,
                start_sec=snapped,
                start_sec_raw=g.start_sec,
                snap_delta_sec=snapped - g.start_sec if did_snap else None,
            )
        )

    ordered = sorted(vocal_sections + snapped_gaps, key=lambda s: s.start_sec)
    # De-dup display names once gaps joined ("Inst", "Inst 2", ...).
    names = assign_display_names([s.display_name for s in ordered])
    tiled: list[DetectedSection] = []
    for idx, section in enumerate(ordered):
        end = ordered[idx + 1].start_sec if idx + 1 < len(ordered) else audio_duration_sec
        tiled.append(
            dataclasses.replace(
                section,
                display_name=names[idx],
                end_sec=end,
                start_beats=round(beat_index_at(section.start_sec, list(beats_sec)), 3),
            )
        )

    result = SectionsResult(
        sections=tiled,
        chart_sequence=list(chart.sequence),
        performed_sequence=seq_entries,
        tempo={
            "bpm_estimate": bpm_estimate,
            "beat_duration_sec": round(beat_duration, 4),
            "bar_duration_sec": round(bar_duration, 4),
            "beat_count": len(beats_sec),
            "downbeat_count": len(downbeats_sec),
        },
        source={
            "audio_path": audio_path,
            "vocal_stem_path": transcript.source_path,
            "engines": {"transcribe": f"stable-ts/{transcript.model_size}"},
        },
        pco={
            "song_id": chart.song_id,
            "arrangement_id": chart.arrangement_id,
            "title": chart.title,
            "ccli_number": chart.ccli_number,
            "key": chart.key,
            "bpm": chart.bpm,
            "meter": chart.meter,
        },
        warnings=warnings,
    )
    return result


def _role_for(chart: ChartModel, label: str) -> str:
    for s in chart.sections:
        if s.label.strip().lower() == label.strip().lower():
            return s.role
    from ..structure.parser import detect_role

    return detect_role(label)


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------


def sidecar_path_for(audio_path: str | Path) -> Path:
    return Path(audio_path).parent / SIDECAR_NAME


def write_sidecar(result: SectionsResult, path: str | Path) -> Path:
    payload = result.to_dict()
    payload["generated_at"] = (
        _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return out


def read_sidecar(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"sections sidecar not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"unsupported sections.json schema_version: {data.get('schema_version')}")
    return data
