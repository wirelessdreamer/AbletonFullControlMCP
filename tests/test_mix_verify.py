"""Tests for ``ableton_mcp.mix_verify`` — Layer 5 of the mix-aware
shaping stack.

The diff math is pure-data. The async wrapper is exercised against
monkey-patched masking analyses (no real bounce required).
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp import mix_verify
from ableton_mcp.mix_analysis import DB_FLOOR, make_third_octave_bands
from ableton_mcp.mix_descriptors import resolve_descriptor
from ableton_mcp.mix_verify import (
    diff_masking,
    mix_snapshot_for_verification,
    mix_verify_intent,
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic masking results
# ---------------------------------------------------------------------------


def _empty_spectrum() -> list[float]:
    return [DB_FLOOR] * len(make_third_octave_bands())


def _spike_multi(centers_db: dict[float, float]) -> list[float]:
    bands = make_third_octave_bands()
    s = _empty_spectrum()
    for c, db in centers_db.items():
        idx = next(i for i, b in enumerate(bands) if b.center_hz == c)
        s[idx] = db
    return s


def _masking(
    *,
    focal_band_energies: dict[float, float],
    competitors: list[tuple[int, str, float, dict[float, float]]],
) -> dict[str, Any]:
    """Build a masking-result dict with explicit focal + competitor
    per-band energies."""
    money_band_dicts = [
        {"center_hz": hz, "low_hz": hz / 1.12, "high_hz": hz * 1.12,
         "energy_db": db}
        for hz, db in focal_band_energies.items()
    ]
    competing = []
    for ti, name, score, per_band in competitors:
        competing.append({
            "track_index": ti, "name": name,
            "masking_score": score,
            "per_band": [
                {"center_hz": hz,
                 "focal_energy_db": focal_band_energies.get(hz, DB_FLOOR),
                 "other_energy_db": db,
                 "overlap_db": db - focal_band_energies.get(hz, DB_FLOOR),
                 "score": score, "weight": 1.0}
                for hz, db in per_band.items()
            ],
        })
    return {
        "focal_track": 0, "focal_name": "Lead",
        "focal_money_bands": money_band_dicts,
        "competing_tracks": competing,
    }


# ---------------------------------------------------------------------------
# diff_masking — pure
# ---------------------------------------------------------------------------


def test_diff_cuts_through_achieved_when_competitors_cut() -> None:
    """The classic success: focal stayed, competitor in 3 kHz dropped."""
    descriptor = resolve_descriptor("cuts_through")
    baseline = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "Rhythm", 0.8, {3150.0: -5.0})],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "Rhythm", 0.4, {3150.0: -9.0})],  # cut 4 dB
    )
    result = diff_masking(baseline, after, descriptor)
    assert result["intent_achieved"] is True
    # Competitor band dropped by ~4 dB.
    comp = result["per_competitor_diffs"][0]
    assert comp["band_energy_delta_db"] == pytest.approx(-4.0, abs=0.5)
    assert comp["masking_score_delta"] == pytest.approx(-0.4, abs=0.05)


def test_diff_cuts_through_not_achieved_when_no_change() -> None:
    """Same masking before and after → intent NOT achieved."""
    descriptor = resolve_descriptor("cuts_through")
    state = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "X", 0.8, {3150.0: -5.0})],
    )
    result = diff_masking(state, state, descriptor)
    assert result["intent_achieved"] is False


def test_diff_cuts_through_regressed_when_competitors_louder() -> None:
    """If applying made it WORSE (competitors got louder), explicitly
    flag that — intent_achieved=False AND a regressed flag."""
    descriptor = resolve_descriptor("cuts_through")
    baseline = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "X", 0.5, {3150.0: -8.0})],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "X", 0.9, {3150.0: -3.0})],  # got LOUDER
    )
    result = diff_masking(baseline, after, descriptor)
    assert result["intent_achieved"] is False
    assert result.get("regressed") is True


def test_diff_harsh_achieved_when_focal_band_dropped() -> None:
    """``harsh`` = focal too loud in 2-5 kHz. Success = focal cut there."""
    descriptor = resolve_descriptor("harsh")
    baseline = _masking(
        focal_band_energies={3150.0: -3.0, 4000.0: -4.0},
        competitors=[],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0, 4000.0: -7.0},  # cut 3 dB
        competitors=[],
    )
    result = diff_masking(baseline, after, descriptor)
    assert result["intent_achieved"] is True
    assert result["focal_band_energy_delta_db"] == pytest.approx(-3.0, abs=0.5)


def test_diff_airy_achieved_when_focal_top_end_boosted() -> None:
    """``airy`` (high_shelf_focal, sign +1): success = focal top-end up."""
    descriptor = resolve_descriptor("airy")
    baseline = _masking(
        focal_band_energies={12500.0: -15.0, 16000.0: -18.0},
        competitors=[],
    )
    after = _masking(
        focal_band_energies={12500.0: -12.0, 16000.0: -15.0},  # +3
        competitors=[],
    )
    result = diff_masking(baseline, after, descriptor)
    assert result["intent_achieved"] is True
    assert result["focal_band_energy_delta_db"] > 0


def test_diff_reports_per_competitor_breakdown() -> None:
    """Every competitor present in baseline should get a diff entry."""
    descriptor = resolve_descriptor("cuts_through")
    baseline = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[
            (1, "Rhythm", 0.8, {3150.0: -5.0}),
            (2, "Keys", 0.4, {3150.0: -10.0}),
        ],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[
            (1, "Rhythm", 0.4, {3150.0: -9.0}),
            (2, "Keys", 0.2, {3150.0: -12.0}),
        ],
    )
    result = diff_masking(baseline, after, descriptor)
    track_indices = {c["track_index"] for c in result["per_competitor_diffs"]}
    assert track_indices == {1, 2}


def test_diff_handles_competitor_missing_after() -> None:
    """If a competitor dropped out of the after-snapshot (e.g. got
    muted) the diff still works — that competitor is listed with a
    'removed' flag."""
    descriptor = resolve_descriptor("cuts_through")
    baseline = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[
            (1, "A", 0.8, {3150.0: -5.0}),
            (2, "B", 0.4, {3150.0: -10.0}),
        ],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "A", 0.4, {3150.0: -9.0})],
    )
    result = diff_masking(baseline, after, descriptor)
    diffs = {c["track_index"]: c for c in result["per_competitor_diffs"]}
    assert diffs[2].get("removed") is True


def test_diff_includes_summary_string() -> None:
    """A natural-language summary should always be present."""
    descriptor = resolve_descriptor("cuts_through")
    baseline = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "Rhythm", 0.8, {3150.0: -5.0})],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "Rhythm", 0.4, {3150.0: -9.0})],
    )
    result = diff_masking(baseline, after, descriptor)
    assert "summary" in result
    s = result["summary"].lower()
    # Summary should mention the intent and at least one number.
    assert "cuts_through" in s or "cut" in s
    assert "db" in s


def test_diff_silent_baseline_no_crash() -> None:
    """A silent baseline (no money bands) should not crash."""
    descriptor = resolve_descriptor("cuts_through")
    baseline = _masking(focal_band_energies={}, competitors=[])
    after = _masking(focal_band_energies={}, competitors=[])
    result = diff_masking(baseline, after, descriptor)
    assert result["intent_achieved"] is False


# ---------------------------------------------------------------------------
# Async snapshot helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_snapshot_for_verification_pipes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The snapshot helper just delegates to mix_masking_at_region;
    verify the result is passed through cleanly."""
    fake = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "X", 0.5, {3150.0: -8.0})],
    )

    async def fake_masking(focal_track_index, start_beats, end_beats, **_):
        return fake

    monkeypatch.setattr(
        "ableton_mcp.mix_verify.mix_masking_at_region", fake_masking,
    )
    snap = await mix_snapshot_for_verification(
        focal_track_index=0, start_beats=0.0, end_beats=8.0,
    )
    assert snap == fake


# ---------------------------------------------------------------------------
# Async wrapper mix_verify_intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_verify_intent_uses_supplied_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When baseline_snapshot is passed, mix_verify_intent should NOT
    bounce again to compute the baseline — only the after-snapshot."""
    bounce_calls = []

    baseline = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "Rhythm", 0.8, {3150.0: -5.0})],
    )
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "Rhythm", 0.4, {3150.0: -9.0})],
    )

    async def fake_masking(focal_track_index, start_beats, end_beats, **_):
        bounce_calls.append((focal_track_index, start_beats, end_beats))
        return after

    monkeypatch.setattr(
        "ableton_mcp.mix_verify.mix_masking_at_region", fake_masking,
    )
    result = await mix_verify_intent(
        focal_track_index=0, intent="cuts_through",
        start_beats=0.0, end_beats=8.0,
        baseline_snapshot=baseline,
    )
    # Only one bounce — for the after-snapshot.
    assert len(bounce_calls) == 1
    assert result["intent_achieved"] is True


@pytest.mark.asyncio
async def test_mix_verify_intent_without_baseline_bounces_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No baseline supplied → we only have the after-snapshot, so we
    can't compute a diff. The function should report that clearly
    rather than fabricating numbers."""
    after = _masking(
        focal_band_energies={3150.0: -6.0},
        competitors=[(1, "X", 0.5, {3150.0: -8.0})],
    )
    calls = []

    async def fake_masking(focal_track_index, start_beats, end_beats, **_):
        calls.append((focal_track_index, start_beats, end_beats))
        return after

    monkeypatch.setattr(
        "ableton_mcp.mix_verify.mix_masking_at_region", fake_masking,
    )
    result = await mix_verify_intent(
        focal_track_index=0, intent="cuts_through",
        start_beats=0.0, end_beats=8.0,
    )
    # One bounce (for after). No baseline, no diff possible.
    assert len(calls) == 1
    assert result.get("baseline_missing") is True
    assert result.get("after_snapshot") is not None


@pytest.mark.asyncio
async def test_mix_verify_intent_unknown_intent_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"bounce": False}

    async def fake_masking(*_a, **_kw):
        called["bounce"] = True
        return {}

    monkeypatch.setattr(mix_verify, "mix_masking_at_region", fake_masking)
    with pytest.raises(KeyError):
        await mix_verify_intent(
            focal_track_index=0, intent="not_a_word",
            start_beats=0.0, end_beats=4.0,
        )
    # Should fail BEFORE the bounce.
    assert called["bounce"] is False
