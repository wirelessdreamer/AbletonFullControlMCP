"""Tests for ``ableton_mcp.section`` — Layer 1.2 of the mix-aware
shaping stack.

The pure-data detectors and the merge logic are easy to unit-test (no
Live, no async). The async ``find_lead_sections`` orchestrator gets a
mocked OSC client.
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.section import (
    DEFAULT_LEAD_KEYWORDS,
    Section,
    find_lead_sections,
    find_sections_by_clip_name,
    find_sections_by_clip_overlap,
    merge_overlapping_sections,
)


def _clip(name: str | None, start: float, length: float) -> dict[str, Any]:
    return {"name": name, "start_time_beats": start, "length_beats": length}


# ---------------------------------------------------------------------------
# find_sections_by_clip_name
# ---------------------------------------------------------------------------


def test_clip_name_matches_default_keywords() -> None:
    """Solo / lead / break / bridge / feature / fill / interlude all hit."""
    clips = [
        _clip("Guitar Solo", 16.0, 16.0),
        _clip("Verse", 0.0, 16.0),
        _clip("Lead Hook", 64.0, 8.0),
        _clip("Break", 32.0, 4.0),
    ]
    sections = find_sections_by_clip_name(0, clips)
    matched_starts = {s.start_beats for s in sections}
    assert matched_starts == {16.0, 64.0, 32.0}
    # Verse is excluded.
    assert all(s.confidence == 1.0 for s in sections)


def test_clip_name_match_is_case_insensitive() -> None:
    clips = [_clip("BIG SOLO PART", 0.0, 8.0)]
    sections = find_sections_by_clip_name(0, clips)
    assert len(sections) == 1
    assert sections[0].details["matched_keyword"] == "solo"


def test_clip_name_skips_empty_or_short_clips() -> None:
    clips = [
        _clip("solo", 0.0, 0.5),  # too short
        _clip(None, 8.0, 16.0),  # no name
        _clip("", 24.0, 16.0),  # empty name
        _clip("solo", 32.0, 16.0),  # OK
    ]
    sections = find_sections_by_clip_name(0, clips, min_length_beats=1.0)
    assert len(sections) == 1
    assert sections[0].start_beats == 32.0


def test_clip_name_custom_keywords() -> None:
    """Caller can override the keyword list."""
    clips = [
        _clip("Big Hook", 0.0, 16.0),
        _clip("Solo", 16.0, 8.0),
    ]
    sections = find_sections_by_clip_name(0, clips, keywords=["hook"])
    assert len(sections) == 1
    assert sections[0].details["matched_keyword"] == "hook"
    assert sections[0].start_beats == 0.0


def test_clip_name_no_matches_returns_empty() -> None:
    clips = [_clip("Verse", 0, 16), _clip("Chorus", 16, 16)]
    assert find_sections_by_clip_name(0, clips) == []


def test_clip_name_section_track_index_is_passed_through() -> None:
    clips = [_clip("Solo", 0.0, 16.0)]
    sections = find_sections_by_clip_name(4, clips)
    assert sections[0].track_index == 4


# ---------------------------------------------------------------------------
# find_sections_by_clip_overlap
# ---------------------------------------------------------------------------


def test_overlap_zero_other_tracks_playing_is_high_confidence() -> None:
    """Focal alone on its clip range → 0.9 confidence (highest for overlap detector)."""
    focal = [_clip("anything", 16.0, 16.0)]
    sections = find_sections_by_clip_overlap(0, focal, other_tracks_clips={})
    assert len(sections) == 1
    assert sections[0].confidence == pytest.approx(0.9)
    assert sections[0].details["overlapping_track_count"] == 0


def test_overlap_one_other_track_passes_with_default_threshold() -> None:
    """Default max_overlapping_other_tracks=1 allows one accompanist."""
    focal = [_clip("solo", 16.0, 16.0)]
    other = {1: [_clip("rhythm", 16.0, 16.0)]}  # overlaps the focal exactly
    sections = find_sections_by_clip_overlap(0, focal, other)
    assert len(sections) == 1
    assert sections[0].details["overlapping_track_count"] == 1
    # 0.9 - 0.1 = 0.8 confidence.
    assert sections[0].confidence == pytest.approx(0.8)


def test_overlap_too_many_other_tracks_disqualifies() -> None:
    """If three other tracks are all playing during the focal's clip,
    it's not a featured section by this metric."""
    focal = [_clip("anything", 16.0, 16.0)]
    other = {
        1: [_clip("rhythm", 0.0, 64.0)],
        2: [_clip("bass", 0.0, 64.0)],
        3: [_clip("drums", 0.0, 64.0)],
    }
    sections = find_sections_by_clip_overlap(
        0, focal, other, max_overlapping_other_tracks=1,
    )
    assert sections == []


def test_overlap_non_overlapping_clips_are_ignored() -> None:
    """An other-track clip that ends before the focal starts doesn't count."""
    focal = [_clip("solo", 16.0, 16.0)]
    other = {1: [_clip("intro", 0.0, 16.0)]}  # ends exactly when focal starts
    sections = find_sections_by_clip_overlap(0, focal, other)
    assert len(sections) == 1
    assert sections[0].details["overlapping_track_count"] == 0


def test_overlap_short_focal_clips_are_skipped() -> None:
    """min_length_beats filters out e.g. fills."""
    focal = [_clip("anything", 16.0, 2.0)]
    sections = find_sections_by_clip_overlap(
        0, focal, other_tracks_clips={}, min_length_beats=4.0,
    )
    assert sections == []


def test_overlap_one_track_with_multiple_clips_counts_once() -> None:
    """Track 1 has two clips that both overlap the focal — still count
    as one overlapping track, not two."""
    focal = [_clip("solo", 0.0, 32.0)]
    other = {1: [_clip("a", 0.0, 16.0), _clip("b", 16.0, 16.0)]}
    sections = find_sections_by_clip_overlap(0, focal, other)
    assert sections[0].details["overlapping_track_count"] == 1


# ---------------------------------------------------------------------------
# merge_overlapping_sections
# ---------------------------------------------------------------------------


def _s(start: float, end: float, confidence: float = 0.5,
       kind: str = "clip_overlap") -> Section:
    return Section(track_index=0, start_beats=start, end_beats=end,
                   confidence=confidence, kind=kind)


def test_merge_disjoint_sections_unchanged() -> None:
    secs = [_s(0, 8), _s(16, 24), _s(32, 40)]
    merged = merge_overlapping_sections(secs)
    assert [(m.start_beats, m.end_beats) for m in merged] == [(0, 8), (16, 24), (32, 40)]


def test_merge_overlapping_sections_collapses_to_one() -> None:
    secs = [_s(0, 16), _s(8, 24)]
    merged = merge_overlapping_sections(secs)
    assert len(merged) == 1
    assert merged[0].start_beats == 0
    assert merged[0].end_beats == 24


def test_merge_touching_sections_collapses() -> None:
    """Adjacent (end == start) sections merge."""
    secs = [_s(0, 8), _s(8, 16)]
    merged = merge_overlapping_sections(secs)
    assert len(merged) == 1
    assert merged[0].start_beats == 0
    assert merged[0].end_beats == 16


def test_merge_picks_highest_confidence_kind() -> None:
    """When detectors overlap, the higher-confidence one's kind wins."""
    named = _s(0, 16, confidence=1.0, kind="clip_name")
    overlap = _s(8, 24, confidence=0.8, kind="clip_overlap")
    merged = merge_overlapping_sections([named, overlap])
    assert len(merged) == 1
    assert merged[0].kind == "clip_name"
    assert merged[0].confidence == 1.0
    # Both contributions tracked in details.
    contribs = merged[0].details["merged_from"]
    assert {c["kind"] for c in contribs} == {"clip_name", "clip_overlap"}


def test_merge_empty_returns_empty() -> None:
    assert merge_overlapping_sections([]) == []


# ---------------------------------------------------------------------------
# find_lead_sections — async orchestrator with mocked OSC
# ---------------------------------------------------------------------------


class FakeOSC:
    """OSC stub for section-detection tests.

    Provides arrangement_clips replies per track. Track 0 is the focal
    track unless overridden by the test.
    """

    def __init__(
        self,
        *,
        track_clips: dict[int, list[dict[str, Any]]] | None = None,
        muted_tracks: set[int] | None = None,
        non_audio_tracks: set[int] | None = None,
    ) -> None:
        self.track_clips = track_clips or {}
        self.muted_tracks = muted_tracks or set()
        self.non_audio_tracks = non_audio_tracks or set()

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/num_tracks":
            return (max(self.track_clips.keys()) + 1 if self.track_clips else 0,)
        if addr == "/live/track/get/mute" and args:
            ti = int(args[0])
            return (ti, 1 if ti in self.muted_tracks else 0)
        if addr == "/live/track/get/has_audio_output" and args:
            ti = int(args[0])
            return (ti, 0 if ti in self.non_audio_tracks else 1)
        if addr.startswith("/live/track/get/arrangement_clips/") and args:
            ti = int(args[0])
            field = addr.removeprefix("/live/track/get/arrangement_clips/")
            clips = self.track_clips.get(ti, [])
            if field == "name":
                return (ti, *(c.get("name") or "" for c in clips))
            if field == "length":
                return (ti, *(c.get("length_beats") or 0.0 for c in clips))
            if field == "start_time":
                return (ti, *(c.get("start_time_beats") or 0.0 for c in clips))
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")


@pytest.mark.asyncio
async def test_find_lead_sections_clip_name_only() -> None:
    """Explicit method='clip_name' uses only the keyword detector."""
    osc = FakeOSC(track_clips={
        0: [_clip("Solo", 16.0, 16.0), _clip("Verse", 0.0, 16.0)],
        1: [_clip("rhythm", 0.0, 32.0)],
    })
    sections = await find_lead_sections(0, method="clip_name", osc_client=osc)
    assert len(sections) == 1
    assert sections[0].start_beats == 16.0
    assert sections[0].kind == "clip_name"


@pytest.mark.asyncio
async def test_find_lead_sections_auto_falls_back_to_overlap() -> None:
    """method='auto' uses clip_name first; if it returns nothing,
    falls back to clip_overlap."""
    osc = FakeOSC(track_clips={
        # Focal: one named-non-keyword clip; isolated (zero overlap).
        0: [_clip("Section A", 16.0, 16.0)],
        # Other track has a clip that doesn't overlap the focal.
        1: [_clip("rhythm", 0.0, 8.0)],
    })
    sections = await find_lead_sections(0, method="auto", osc_client=osc)
    # No clip-name keyword match → overlap fallback kicks in. Focal
    # clip has zero overlapping other tracks → highest-confidence overlap.
    assert len(sections) == 1
    assert sections[0].kind == "clip_overlap"
    assert sections[0].start_beats == 16.0


@pytest.mark.asyncio
async def test_find_lead_sections_auto_uses_clip_name_when_match_exists() -> None:
    """If clip_name returns matches, auto does NOT also run overlap
    (auto is short-circuit, unlike 'both')."""
    osc = FakeOSC(track_clips={
        0: [_clip("Solo", 0.0, 16.0)],
        1: [_clip("rhythm", 0.0, 16.0)],  # overlaps the focal
    })
    sections = await find_lead_sections(0, method="auto", osc_client=osc)
    assert all(s.kind == "clip_name" for s in sections)


@pytest.mark.asyncio
async def test_find_lead_sections_both_runs_both_and_merges() -> None:
    """method='both' runs both detectors and merges overlapping results."""
    osc = FakeOSC(track_clips={
        0: [_clip("Solo", 16.0, 16.0)],
        1: [],  # no overlap on track 1
    })
    sections = await find_lead_sections(0, method="both", osc_client=osc)
    assert len(sections) == 1
    # Merged section reports the highest-confidence kind (clip_name).
    assert sections[0].kind == "clip_name"
    contribs = sections[0].details["merged_from"]
    kinds = {c["kind"] for c in contribs}
    assert "clip_name" in kinds and "clip_overlap" in kinds


@pytest.mark.asyncio
async def test_find_lead_sections_skips_muted_other_tracks() -> None:
    """Muted other tracks should not count as overlapping."""
    osc = FakeOSC(
        track_clips={
            0: [_clip("anything", 0.0, 16.0)],
            1: [_clip("would overlap but muted", 0.0, 16.0)],
        },
        muted_tracks={1},
    )
    sections = await find_lead_sections(
        0, method="clip_overlap", osc_client=osc,
    )
    # Track 1 is muted; effective overlap count = 0 → high confidence.
    assert sections[0].details["overlapping_track_count"] == 0


@pytest.mark.asyncio
async def test_find_lead_sections_skips_non_audio_tracks() -> None:
    """Tracks with has_audio_output=False (group/folder tracks) are
    skipped in the overlap calculation."""
    osc = FakeOSC(
        track_clips={
            0: [_clip("focal", 0.0, 16.0)],
            1: [_clip("would overlap but group", 0.0, 16.0)],
        },
        non_audio_tracks={1},
    )
    sections = await find_lead_sections(
        0, method="clip_overlap", osc_client=osc,
    )
    assert sections[0].details["overlapping_track_count"] == 0


@pytest.mark.asyncio
async def test_find_lead_sections_unknown_method_raises() -> None:
    osc = FakeOSC(track_clips={0: []})
    with pytest.raises(ValueError, match="method"):
        await find_lead_sections(0, method="bogus", osc_client=osc)


def test_default_keywords_includes_expected_terms() -> None:
    """Sanity check the public default keyword list."""
    keywords_lower = {k.lower() for k in DEFAULT_LEAD_KEYWORDS}
    for required in {"solo", "lead", "break", "bridge", "feature"}:
        assert required in keywords_lower
