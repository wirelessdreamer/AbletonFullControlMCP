"""Tests for song_sections.detect — pipeline, gaps, snapping, sidecar."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.pco import ChartModel, ChartSection
from ableton_mcp.song_sections.detect import (
    beat_index_at,
    build_performed_text,
    detect_sections,
    infer_gaps,
    median_interval,
    read_sidecar,
    refine_block_starts,
    sidecar_path_for,
    snap_to_downbeat,
    write_sidecar,
)
from ableton_mcp.song_sections.model import (
    DetectedSection,
    MatchedBlock,
    Transcript,
    TranscriptWord,
)

# ---------------------------------------------------------------------------
# Fixtures — a 4/4 song at 120 BPM (0.5 s/beat, 2 s/bar), 60 s long
# ---------------------------------------------------------------------------

BEATS = [i * 0.5 for i in range(120)]        # 0.0 .. 59.5
DOWNBEATS = [i * 2.0 for i in range(30)]     # 0.0 .. 58.0

VERSE_1 = "in the quiet in the stillness i know that you are god"
CHORUS = "im caught up in your presence i just want to sit here at your feet"


def _chart(sequence: tuple[str, ...] = ("Intro", "Verse 1", "Chorus")) -> ChartModel:
    return ChartModel(
        song_id="s1",
        arrangement_id="a1",
        title="Test Song",
        bpm=120.0,
        meter="4/4",
        sequence=sequence,
        sections=(
            ChartSection(label="Verse 1", role="verse", lyrics=VERSE_1),
            ChartSection(label="Chorus", role="chorus", lyrics=CHORUS),
        ),
    )


def _words(text: str, start: float, wps: float = 2.5) -> list[TranscriptWord]:
    out, t = [], start
    for tok in text.split():
        out.append(TranscriptWord(text=tok, start=t, end=t + 1.0 / wps, prob=0.9))
        t += 1.0 / wps
    return out


def _transcript(*parts: tuple[str, float]) -> Transcript:
    words: list[TranscriptWord] = []
    for text, start in parts:
        words.extend(_words(text, start))
    return Transcript(
        words=tuple(words), language="en", model_size="small", source_path="vocals.wav"
    )


def _run(transcript: Transcript, chart: ChartModel | None = None, **kw: Any):
    return detect_sections(
        chart or _chart(),
        transcript,
        beats_sec=BEATS,
        downbeats_sec=DOWNBEATS,
        bpm_estimate=120.0,
        audio_duration_sec=60.0,
        audio_path="song.wav",
        **kw,
    )


# ---------------------------------------------------------------------------
# Beat-grid helpers
# ---------------------------------------------------------------------------


def test_median_interval() -> None:
    assert median_interval([0.0, 0.5, 1.0]) == pytest.approx(0.5)
    assert median_interval([0.0]) is None


def test_beat_index_at_grid_points() -> None:
    assert beat_index_at(0.0, BEATS) == pytest.approx(0.0)
    assert beat_index_at(2.0, BEATS) == pytest.approx(4.0)
    assert beat_index_at(2.25, BEATS) == pytest.approx(4.5)


def test_beat_index_before_first_beat_clamps_to_zero() -> None:
    assert beat_index_at(-0.5, BEATS) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# snap_to_downbeat — anacrusis rules
# ---------------------------------------------------------------------------


def test_snap_pickup_forward_wins_over_nearer_previous() -> None:
    # 3.7 s: previous downbeat 2.0 is 1.7 away, next 4.0 is 0.3 ahead.
    # Pickup rule (1 beat = 0.5 s) snaps FORWARD to 4.0.
    snapped, ok = snap_to_downbeat(3.7, DOWNBEATS, 0.5)
    assert ok and snapped == pytest.approx(4.0)


def test_snap_nearest_when_no_pickup_candidate() -> None:
    # 4.2 s: next downbeat 6.0 is 1.8 s away (> 1 beat) — nearest is 4.0.
    snapped, ok = snap_to_downbeat(4.2, DOWNBEATS, 0.5)
    assert ok and snapped == pytest.approx(4.0)


def test_snap_no_pickup_for_instrumentals() -> None:
    snapped, ok = snap_to_downbeat(3.7, DOWNBEATS, 0.5, allow_pickup=False)
    assert ok and snapped == pytest.approx(4.0)  # nearest anyway
    snapped2, ok2 = snap_to_downbeat(3.3, DOWNBEATS, 0.5, allow_pickup=False)
    assert ok2 and snapped2 == pytest.approx(4.0)  # nearest is 4.0 (0.7) vs 2.0 (1.3)


def test_snap_outside_window_returns_raw() -> None:
    snapped, ok = snap_to_downbeat(100.0, [0.0, 2.0], 0.5)
    assert not ok and snapped == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def test_pipeline_basic_sections_and_tiling() -> None:
    # Verse at ~8.3 s (ends ~13.1), chorus right after at 14.1 (gap 1 s — no
    # marker), chorus ends ~20.1 → the 40 s tail becomes an "Inst" gap.
    t = _transcript((VERSE_1, 8.3), (CHORUS, 14.1))
    result = _run(t)
    names = [s.display_name for s in result.sections]
    # 8 s intro gap (4 bars) → "Intro" label donated by the chart sequence.
    assert names == ["Intro", "Verse 1", "Chorus", "Inst"]
    kinds = [s.kind for s in result.sections]
    assert kinds == ["instrumental_gap", "vocal", "vocal", "instrumental_gap"]

    starts = [s.start_sec for s in result.sections]
    assert starts == sorted(starts)
    # Sections tile the timeline exactly.
    for a, b in zip(result.sections, result.sections[1:]):
        assert a.end_sec == pytest.approx(b.start_sec)
    assert result.sections[-1].end_sec == pytest.approx(60.0)
    # Vocal starts snapped to downbeats (8.3 → nearest downbeat 8.0;
    # 14.1 → nearest 14.0).
    assert result.sections[1].start_sec == pytest.approx(8.0)
    assert result.sections[1].start_beats == pytest.approx(16.0)
    assert result.sections[2].start_sec == pytest.approx(14.0)


def test_pipeline_extra_chorus_flagged() -> None:
    t = _transcript((VERSE_1, 4.0), (CHORUS, 16.0), (CHORUS, 30.0), (CHORUS, 44.0))
    chart = _chart(sequence=("Verse 1", "Chorus", "Chorus"))
    result = _run(t, chart)
    statuses = [e["status"] for e in result.performed_sequence]
    assert statuses.count("extra") == 1
    assert any("extra_repeat" in w for w in result.warnings)
    vocal = [s for s in result.sections if s.kind == "vocal"]
    assert [s.display_name for s in vocal] == ["Verse 1", "Chorus", "Chorus 2", "Chorus 3"]


def test_pipeline_gap_between_sections_labeled_from_sequence() -> None:
    # Chart says Verse 1 / Interlude / Chorus; 10 s of silence between the
    # sung sections → the gap inherits "Interlude".
    t = _transcript((VERSE_1, 2.0), (CHORUS, 20.0))
    chart = _chart(sequence=("Verse 1", "Interlude", "Chorus"))
    result = _run(t, chart)
    names = [s.display_name for s in result.sections]
    assert "Interlude" in names
    inter = result.sections[names.index("Interlude")]
    assert inter.kind == "instrumental_gap"
    assert inter.role == "interlude"
    assert inter.confidence == pytest.approx(0.75)


def test_pipeline_unnamed_gap_is_inst() -> None:
    t = _transcript((VERSE_1, 2.0), (CHORUS, 20.0))
    chart = _chart(sequence=("Verse 1", "Chorus"))
    result = _run(t, chart)
    names = [s.display_name for s in result.sections]
    assert "Inst" in names
    inst = result.sections[names.index("Inst")]
    assert inst.confidence == pytest.approx(0.6)


def test_pipeline_short_gap_not_marked() -> None:
    # Verse ends ~6.8 s, chorus at 8.1 → gap ≈ 1.3 s < 2 bars (4 s): no
    # marker BETWEEN them (the long tail after the chorus legitimately gets
    # one).
    t = _transcript((VERSE_1, 2.0), (CHORUS, 8.1))
    chart = _chart(sequence=("Verse 1", "Chorus"))
    result = _run(t, chart)
    assert [s.kind for s in result.sections[:2]] == ["vocal", "vocal"]
    between = [
        s for s in result.sections
        if s.kind == "instrumental_gap" and s.start_sec < result.sections[1].start_sec
    ]
    assert between == []


def test_pipeline_no_match_warns() -> None:
    t = _transcript(("totally different words entirely unrelated here", 5.0))
    result = _run(t)
    assert any("no chart sections matched" in w for w in result.warnings)
    assert all(s.kind == "instrumental_gap" for s in result.sections)


def test_pipeline_raw_and_snapped_recorded() -> None:
    t = _transcript((VERSE_1, 8.3), (CHORUS, 24.1))
    result = _run(t)
    verse = next(s for s in result.sections if s.display_name == "Verse 1")
    assert verse.start_sec_raw == pytest.approx(8.3)
    assert verse.snap_delta_sec == pytest.approx(-0.3)


# ---------------------------------------------------------------------------
# Alignment refinement
# ---------------------------------------------------------------------------


def test_refinement_applied_when_clean() -> None:
    t = _transcript((VERSE_1, 8.3), (CHORUS, 24.1))

    def fake_aligner(text: str) -> list[dict[str, Any]]:
        # One segment per input line; the chart lyrics are single lines here.
        lines = text.splitlines()
        segs = []
        starts = [8.05, 24.4]  # refined starts near the discovery ones
        for i, line in enumerate(lines):
            segs.append({"start": starts[i], "end": starts[i] + 4.0,
                         "text": line, "words": [{"word": w} for w in line.split()]})
        return segs

    result = _run(t, aligner=fake_aligner)
    verse = next(s for s in result.sections if s.display_name == "Verse 1")
    assert verse.match is not None and verse.match["source"] == "align"
    assert verse.start_sec_raw == pytest.approx(8.05)
    assert verse.start_sec == pytest.approx(8.0)  # snapped


def test_refinement_rejected_on_drift() -> None:
    t = _transcript((VERSE_1, 8.3), (CHORUS, 24.1))

    def drifting_aligner(text: str) -> list[dict[str, Any]]:
        lines = text.splitlines()
        return [
            {"start": 40.0 + i, "end": 41.0 + i, "text": line,
             "words": [{"word": w} for w in line.split()]}
            for i, line in enumerate(lines)
        ]

    result = _run(t, aligner=drifting_aligner)
    assert any("alignment_drift" in w for w in result.warnings)
    verse = next(s for s in result.sections if s.display_name == "Verse 1")
    # Discovery start kept; confidence downgraded because refinement failed.
    assert verse.start_sec_raw == pytest.approx(8.3)
    assert verse.confidence <= 0.5


def test_refinement_failure_is_soft() -> None:
    t = _transcript((VERSE_1, 8.3))

    def broken_aligner(text: str) -> list[dict[str, Any]]:
        raise RuntimeError("model exploded")

    result = _run(t, aligner=broken_aligner)
    assert any("alignment_failed" in w for w in result.warnings)
    assert any(s.kind == "vocal" for s in result.sections)


# ---------------------------------------------------------------------------
# refine_block_starts / build_performed_text (pure)
# ---------------------------------------------------------------------------


def test_build_performed_text_maps_lines_to_blocks() -> None:
    chart = _chart()
    blocks = [
        MatchedBlock(label="Verse 1", score=95.0, word_start=0, word_end=12,
                     start_sec=8.0, end_sec=13.0),
        MatchedBlock(label="Chorus", score=95.0, word_start=12, word_end=27,
                     start_sec=24.0, end_sec=30.0),
    ]
    text, owner = build_performed_text(blocks, chart)
    assert text.splitlines() == [VERSE_1, CHORUS]
    assert owner == [0, 1]


def test_refine_block_starts_degenerate_skipped() -> None:
    blocks = [
        MatchedBlock(label="Verse 1", score=95.0, word_start=0, word_end=12,
                     start_sec=8.0, end_sec=13.0),
    ]
    # Degenerate: 5 words in 0.1 s.
    segs = [{"start": 8.0, "end": 8.1,
             "words": [{"word": f"w{i}"} for i in range(5)], "text": "x"}]
    starts, _ends, warnings = refine_block_starts(blocks, segs, [0])
    assert starts == [8.0]
    assert any("alignment_degenerate" in w for w in warnings)


# ---------------------------------------------------------------------------
# infer_gaps edge cases (pure)
# ---------------------------------------------------------------------------


def _vocal(label: str, start: float, sung_end: float, chart_index: int | None) -> DetectedSection:
    return DetectedSection(
        label=label, display_name=label, role="verse", kind="vocal",
        start_sec=start, end_sec=start, confidence=0.9,
        chart_index=chart_index,
        match={"sung_span_sec": [start, sung_end], "score": 95.0},
    )


def test_infer_gaps_no_vocals_whole_song() -> None:
    gaps = infer_gaps([], _chart(), audio_duration_sec=60.0, bar_duration_sec=2.0)
    assert len(gaps) == 1
    assert gaps[0].start_sec == 0.0 and gaps[0].end_sec == 60.0


def test_infer_gaps_between_extras_does_not_steal_intro_label() -> None:
    # Regression: a gap between two EXTRA sections (chart_index None) must
    # look through the extras to the nearest matched neighbors — not scan
    # the whole sequence and label itself "Intro".
    chart = _chart(sequence=("Intro", "Verse 1", "Chorus"))
    vocal = [
        _vocal("Verse 1", 4.0, 10.0, 1),
        _vocal("Chorus", 10.0, 16.0, 2),      # matched: chart_index 2
        _vocal("Chorus", 30.0, 36.0, None),   # extra repeat
        _vocal("Chorus", 50.0, 56.0, None),   # extra repeat
    ]
    gaps = infer_gaps(vocal, chart, audio_duration_sec=60.0, bar_duration_sec=2.0)
    mid = [g for g in gaps if 16.0 <= g.start_sec < 50.0]
    assert all(g.label == "Inst" for g in mid), [g.label for g in gaps]


def test_infer_gaps_outro_labeled_from_sequence() -> None:
    chart = _chart(sequence=("Verse 1", "Chorus", "Outro"))
    vocal = [_vocal("Verse 1", 0.0, 20.0, 0), _vocal("Chorus", 20.0, 40.0, 1)]
    gaps = infer_gaps(vocal, chart, audio_duration_sec=60.0, bar_duration_sec=2.0)
    assert len(gaps) == 1
    assert gaps[0].label == "Outro"
    assert gaps[0].start_sec == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------


def test_sidecar_round_trip(tmp_path: Path) -> None:
    t = _transcript((VERSE_1, 8.3), (CHORUS, 14.1))
    result = _run(t)
    dest = tmp_path / "sections.json"
    write_sidecar(result, dest)
    data = read_sidecar(dest)
    assert data["schema_version"] == 1
    assert data["pco"]["title"] == "Test Song"
    assert "generated_at" in data
    assert [s["display_name"] for s in data["sections"]] == [
        "Intro", "Verse 1", "Chorus", "Inst",
    ]
    assert data["tempo"]["bpm_estimate"] == 120.0


def test_sidecar_bad_schema_rejected(tmp_path: Path) -> None:
    dest = tmp_path / "sections.json"
    dest.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        read_sidecar(dest)


def test_sidecar_path_for() -> None:
    assert sidecar_path_for("D:/music/song.wav").name == "sections.json"
    assert sidecar_path_for("D:/music/song.wav").parent == Path("D:/music")
