"""Tests for song_sections.locators — idempotent cue writing over a FakeOSC.

The fake mimics AbletonOSC's semantics faithfully: cue list in CREATION
order, ``add_or_delete`` toggles at the current playhead (deleting an
existing cue there), rename by list index.
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.song_sections.locators import (
    check_tempo,
    write_section_locators,
)


class FakeOSC:
    """Stateful fake of the AbletonOSC endpoints locators.py touches."""

    def __init__(
        self,
        tempo: float = 120.0,
        playing: bool = False,
        cues: list[tuple[str, float]] | None = None,
    ):
        self.tempo = tempo
        self.playing = playing
        # Creation-ordered (name, time_beats) — deliberately NOT time-sorted.
        self.cues: list[tuple[str, float]] = list(cues or [])
        self.playhead = 0.0
        self.sent: list[tuple] = []

    async def request(self, path: str, *args: Any):
        if path == "/live/song/get/tempo":
            return [self.tempo]
        if path == "/live/song/get/is_playing":
            return [1 if self.playing else 0]
        if path == "/live/song/get/cue_points":
            flat: list[Any] = []
            for name, t in self.cues:
                flat.extend([name, t])
            return flat
        raise AssertionError(f"unexpected request {path}")

    def send(self, path: str, *args: Any) -> None:
        self.sent.append((path, *args))
        if path == "/live/song/set/current_song_time":
            self.playhead = float(args[0])
        elif path == "/live/song/stop_playing":
            self.playing = False
        elif path == "/live/song/cue_point/add_or_delete":
            # Toggle at playhead: delete an existing cue there, else add.
            for i, (_name, t) in enumerate(self.cues):
                if abs(t - self.playhead) < 1e-6:
                    del self.cues[i]
                    return
            self.cues.append((f"Cue {len(self.cues) + 1}", self.playhead))
        elif path == "/live/song/cue_point/set/name":
            index, name = int(args[0]), str(args[1])
            old_name, t = self.cues[index]
            self.cues[index] = (name, t)


SECTIONS = [
    {"display_name": "Intro", "start_beats": 0.0},
    {"display_name": "Verse 1", "start_beats": 16.0},
    {"display_name": "Chorus", "start_beats": 48.0},
]


def _bpm() -> float:
    return 120.0


# ---------------------------------------------------------------------------
# check_tempo
# ---------------------------------------------------------------------------


def test_check_tempo_ok() -> None:
    assert check_tempo(120.0, 120.4)["ok"] is True


def test_check_tempo_mismatch() -> None:
    r = check_tempo(100.0, 120.0)
    assert r["ok"] is False and "note" in r


def test_check_tempo_half_time_hint() -> None:
    r = check_tempo(144.0, 72.0)
    assert r["ok"] is False
    assert "2x" in r["note"] or "half" in r["note"].lower()


def test_check_tempo_no_estimate_skips() -> None:
    assert check_tempo(120.0, None)["ok"] is True


# ---------------------------------------------------------------------------
# Fresh add flow
# ---------------------------------------------------------------------------


async def test_fresh_add_creates_named_locators() -> None:
    osc = FakeOSC()
    report = await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    assert [a["name"] for a in report.added] == ["Intro", "Verse 1", "Chorus"]
    assert report.failed == [] and report.skipped == [] and report.renamed == []
    assert sorted(osc.cues, key=lambda c: c[1]) == [
        ("Intro", 0.0), ("Verse 1", 16.0), ("Chorus", 48.0),
    ]


async def test_second_run_is_noop() -> None:
    osc = FakeOSC()
    await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    cues_after_first = list(osc.cues)
    report2 = await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    assert report2.added == [] and report2.renamed == [] and report2.failed == []
    assert len(report2.skipped) == 3
    assert osc.cues == cues_after_first


async def test_arrangement_offset_applied() -> None:
    osc = FakeOSC()
    report = await write_section_locators(
        SECTIONS, bpm_estimate=_bpm(), arrangement_start_beats=8.0, osc_client=osc
    )
    assert [a["time_beats"] for a in report.added] == [8.0, 24.0, 56.0]


async def test_transport_stopped_before_writing() -> None:
    osc = FakeOSC(playing=True)
    await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    assert ("/live/song/stop_playing",) in osc.sent
    stop_idx = osc.sent.index(("/live/song/stop_playing",))
    toggles = [i for i, s in enumerate(osc.sent) if s[0] == "/live/song/cue_point/add_or_delete"]
    assert all(i > stop_idx for i in toggles)


# ---------------------------------------------------------------------------
# Rename-instead-of-add (toggle would delete)
# ---------------------------------------------------------------------------


async def test_foreign_cue_near_target_renamed_not_toggled() -> None:
    # A cue 0.1 beats from the Verse 1 target: toggling there would delete
    # a locator the user may have placed — rename it instead.
    osc = FakeOSC(cues=[("My Marker", 16.1)])
    report = await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    renamed = [r for r in report.renamed if r["name"] == "Verse 1"]
    assert len(renamed) == 1
    assert renamed[0]["previous_name"] == "My Marker"
    assert ("Verse 1", 16.1) in osc.cues
    # Verse 1 was NOT also added at 16.0.
    assert not any(t == 16.0 for _n, t in osc.cues)


async def test_same_name_within_tolerance_skipped_even_if_offset() -> None:
    osc = FakeOSC(cues=[("Chorus", 48.3)])
    report = await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    assert any(s["name"] == "Chorus" for s in report.skipped)
    assert sum(1 for n, _t in osc.cues if n == "Chorus") == 1


# ---------------------------------------------------------------------------
# clear_existing
# ---------------------------------------------------------------------------


async def test_clear_existing_moves_stale_locator() -> None:
    # "Verse 1" exists at a stale position (30.0, > tolerance from 16.0).
    osc = FakeOSC(cues=[("Verse 1", 30.0)])
    report = await write_section_locators(
        SECTIONS, bpm_estimate=_bpm(), clear_existing=True, osc_client=osc
    )
    assert any(d["name"] == "Verse 1" and d["time_beats"] == 30.0 for d in report.deleted)
    assert any(a["name"] == "Verse 1" and a["time_beats"] == 16.0 for a in report.added)
    assert ("Verse 1", 30.0) not in osc.cues


async def test_without_clear_existing_stale_stays_and_new_added() -> None:
    osc = FakeOSC(cues=[("Verse 1", 30.0)])
    report = await write_section_locators(SECTIONS, bpm_estimate=_bpm(), osc_client=osc)
    assert ("Verse 1", 30.0) in osc.cues  # untouched
    assert any(a["name"] == "Verse 1" and a["time_beats"] == 16.0 for a in report.added)


# ---------------------------------------------------------------------------
# Tempo guard
# ---------------------------------------------------------------------------


async def test_tempo_mismatch_refuses_without_force() -> None:
    osc = FakeOSC(tempo=95.0)
    report = await write_section_locators(SECTIONS, bpm_estimate=120.0, osc_client=osc)
    assert report.tempo_check["ok"] is False
    assert report.added == []
    assert osc.cues == []
    assert any("tempo check failed" in w for w in report.warnings)


async def test_tempo_mismatch_force_writes() -> None:
    osc = FakeOSC(tempo=95.0)
    report = await write_section_locators(
        SECTIONS, bpm_estimate=120.0, force=True, osc_client=osc
    )
    assert len(report.added) == 3


# ---------------------------------------------------------------------------
# Malformed sections
# ---------------------------------------------------------------------------


async def test_sections_missing_fields_warn() -> None:
    osc = FakeOSC()
    report = await write_section_locators(
        [{"display_name": "X"}, {"start_beats": 4.0}, SECTIONS[0]],
        bpm_estimate=_bpm(),
        osc_client=osc,
    )
    assert len(report.warnings) == 2
    assert len(report.added) == 1


async def test_creation_order_indices_used_for_rename() -> None:
    # Existing cues in creation order NOT matching time order — rename must
    # target the right index after re-fetching.
    osc = FakeOSC(cues=[("Late", 100.0), ("Early", 0.05)])
    report = await write_section_locators(
        [{"display_name": "Intro", "start_beats": 0.0}],
        bpm_estimate=_bpm(),
        osc_client=osc,
    )
    assert any(r["previous_name"] == "Early" for r in report.renamed)
    assert ("Intro", 0.05) in osc.cues
    assert ("Late", 100.0) in osc.cues


async def test_added_cue_found_by_time_diff_not_index() -> None:
    # Pre-existing cues at odd creation order; the fresh cue is appended by
    # the fake — the writer must identify it by time diff and rename THAT.
    osc = FakeOSC(cues=[("Z", 99.0), ("A", 7.0)])
    report = await write_section_locators(
        [{"display_name": "Chorus", "start_beats": 48.0}],
        bpm_estimate=_bpm(),
        osc_client=osc,
    )
    assert report.added == [{"name": "Chorus", "time_beats": 48.0}]
    assert ("Chorus", 48.0) in osc.cues
    assert ("Z", 99.0) in osc.cues and ("A", 7.0) in osc.cues
