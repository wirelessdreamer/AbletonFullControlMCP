"""Tests for ``ableton_mcp.mix_propose`` — L4.1 of the mix-aware
shaping stack.

Given a masking-analysis result (from L2.2) and an intent descriptor
(L3), produce a structured proposal of EQ / filter / shelf actions.
This module does NOT touch Live — it returns a plan; Layer 4.2 will
apply it.

Tests work against synthesized masking results (no bounce required).
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.mix_analysis import DB_FLOOR, make_third_octave_bands
from ableton_mcp.mix_descriptors import resolve_descriptor
from ableton_mcp.mix_propose import (
    MixAction,
    mix_propose_at_region,
    propose_actions,
)


# ---------------------------------------------------------------------------
# Helpers — build synthesized masking results
# ---------------------------------------------------------------------------


def _empty_spectrum() -> list[float]:
    return [DB_FLOOR] * len(make_third_octave_bands())


def _spike(center_hz: float, db: float = -10.0) -> list[float]:
    bands = make_third_octave_bands()
    s = _empty_spectrum()
    idx = next(i for i, b in enumerate(bands) if b.center_hz == center_hz)
    s[idx] = db
    return s


def _spike_multi(centers_db: dict[float, float]) -> list[float]:
    bands = make_third_octave_bands()
    s = _empty_spectrum()
    for c, db in centers_db.items():
        idx = next(i for i, b in enumerate(bands) if b.center_hz == c)
        s[idx] = db
    return s


def _synth_masking_result(
    focal_money_bands: list[float],
    competing: list[tuple[str, float, float]],
) -> dict[str, Any]:
    """Build the shape that compute_masking() returns, with hand-tuned
    money bands and competitor scores.

    competing: list of (name, masking_score, dominant_band_hz).
    """
    return {
        "focal_track": 0,
        "focal_name": "Lead",
        "focal_money_bands": [
            {"center_hz": hz, "low_hz": hz / 1.12, "high_hz": hz * 1.12,
             "energy_db": -6.0}
            for hz in focal_money_bands
        ],
        "competing_tracks": [
            {
                "track_index": i + 1,
                "name": name,
                "masking_score": score,
                "per_band": [
                    {"center_hz": dominant_band_hz,
                     "focal_energy_db": -8.0,
                     "other_energy_db": -8.0 + 4.0 * score,
                     "overlap_db": 4.0 * score,
                     "score": score,
                     "weight": 1.0}
                ],
            }
            for i, (name, score, dominant_band_hz) in enumerate(competing)
        ],
    }


# ---------------------------------------------------------------------------
# propose_actions — cut_competitors (cuts_through, buried)
# ---------------------------------------------------------------------------


def test_propose_cuts_through_cuts_top_competitor() -> None:
    """Intent ``cuts_through`` with one strong masker → one eq_cut on
    that competitor in the presence band."""
    descriptor = resolve_descriptor("cuts_through")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[("Rhythm", 0.8, 3150.0)],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    assert proposal["intent"] == "cuts_through"
    actions = proposal["actions"]
    assert len(actions) >= 1
    cut = actions[0]
    assert cut.kind == "eq_cut"
    assert cut.track_index == 1
    # Cut should land inside the presence band (2-5 kHz per descriptor).
    assert 2000.0 <= cut.freq_hz <= 5000.0
    assert cut.gain_db < 0


def test_propose_cuts_through_ranks_by_masking_score() -> None:
    """Top masker gets a bigger cut than weaker ones."""
    descriptor = resolve_descriptor("cuts_through")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[
            ("Rhythm", 0.9, 3150.0),
            ("Keys", 0.4, 3150.0),
        ],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    eq_cuts = [a for a in proposal["actions"] if a.kind == "eq_cut"]
    # Higher score → larger |gain_db|.
    by_track = {a.track_index: a for a in eq_cuts}
    assert abs(by_track[1].gain_db) >= abs(by_track[2].gain_db)


def test_propose_cuts_through_skips_low_score_competitors() -> None:
    """A competitor with a tiny masking score should not show up — it
    isn't actually masking the focal."""
    descriptor = resolve_descriptor("cuts_through")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[
            ("Strong", 0.8, 3150.0),
            ("Weak", 0.05, 3150.0),
        ],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    tracks_acted_on = {a.track_index for a in proposal["actions"]}
    # Track 2 was barely masking — should be omitted.
    assert 2 not in tracks_acted_on
    assert 1 in tracks_acted_on


def test_propose_actions_include_rationale_strings() -> None:
    """Every action should explain itself — references real numbers."""
    descriptor = resolve_descriptor("cuts_through")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[("Rhythm", 0.8, 3150.0)],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    for a in proposal["actions"]:
        assert a.rationale, f"{a.kind} action missing rationale"
        # Rationale should mention the masking number or the band.
        assert any(
            tok in a.rationale.lower()
            for tok in ("hz", "khz", "mask", "db", "presence")
        )


def test_propose_buried_same_action_class_as_cuts_through() -> None:
    """``buried`` has sign=+1 / action_class=cut_competitors — same
    semantic, same fix direction."""
    descriptor = resolve_descriptor("buried")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[("Rhythm", 0.8, 3150.0)],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    assert any(a.kind == "eq_cut" for a in proposal["actions"])


# ---------------------------------------------------------------------------
# propose_actions — cut_focal (harsh)
# ---------------------------------------------------------------------------


def test_propose_harsh_cuts_the_focal() -> None:
    """``harsh`` → cut on the focal in the upper presence band."""
    descriptor = resolve_descriptor("harsh")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0, 4000.0],
        competing=[("X", 0.3, 3150.0)],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    actions = proposal["actions"]
    # At least one eq_cut on the focal (track_index = 0).
    focal_cuts = [a for a in actions if a.track_index == 0 and a.kind == "eq_cut"]
    assert len(focal_cuts) >= 1
    assert 2000.0 <= focal_cuts[0].freq_hz <= 5000.0


# ---------------------------------------------------------------------------
# propose_actions — high_shelf_focal (airy)
# ---------------------------------------------------------------------------


def test_propose_airy_high_shelf_boost_on_focal() -> None:
    """``airy`` → high-shelf boost on the focal above 10 kHz."""
    descriptor = resolve_descriptor("airy")
    masking = _synth_masking_result(focal_money_bands=[], competing=[])
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    shelves = [a for a in proposal["actions"] if a.kind == "high_shelf"]
    assert len(shelves) == 1
    assert shelves[0].track_index == 0  # focal
    assert shelves[0].gain_db > 0       # boost
    # Knee around 10 kHz (descriptor's low edge).
    assert 8000.0 <= shelves[0].freq_hz <= 12000.0


# ---------------------------------------------------------------------------
# propose_actions — low_shelf_focal (thick / thin)
# ---------------------------------------------------------------------------


def test_propose_thick_low_shelf_on_focal() -> None:
    descriptor = resolve_descriptor("thick")
    masking = _synth_masking_result(focal_money_bands=[], competing=[])
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    shelves = [a for a in proposal["actions"] if a.kind == "low_shelf"]
    assert len(shelves) == 1
    assert shelves[0].track_index == 0
    assert shelves[0].gain_db > 0


# ---------------------------------------------------------------------------
# propose_actions — high_pass_non_bass (boomy)
# ---------------------------------------------------------------------------


def test_propose_boomy_high_passes_non_bass_tracks() -> None:
    """``boomy`` → high-pass on every non-bass track at ~120 Hz.

    Identification: a track is "bass-family" if its highest-energy band
    is below ~200 Hz. We pass each competitor's per-band data; the
    propose code uses the dominant band to classify."""
    descriptor = resolve_descriptor("boomy")
    # Two competitors. Bass: dominant band 80 Hz. Guitar: 1 kHz.
    bands = make_third_octave_bands()
    masking = {
        "focal_track": 0,
        "focal_name": "Lead",
        "focal_money_bands": [{"center_hz": 3000.0, "low_hz": 2670, "high_hz": 3360,
                               "energy_db": -6.0}],
        "competing_tracks": [
            {"track_index": 1, "name": "Bass",
             "masking_score": 0.1,
             "per_band": [],
             "spectrum": _spike(80.0, db=-5.0)},  # extra info for classifier
            {"track_index": 2, "name": "Guitar",
             "masking_score": 0.6,
             "per_band": [],
             "spectrum": _spike(1000.0, db=-5.0)},
        ],
    }
    proposal = propose_actions(masking, descriptor, bands=bands)
    high_passes = [a for a in proposal["actions"] if a.kind == "high_pass"]
    targets = {a.track_index for a in high_passes}
    # Guitar (non-bass) gets a high-pass; bass does NOT.
    assert 2 in targets
    assert 1 not in targets


# ---------------------------------------------------------------------------
# Coverage / edge cases
# ---------------------------------------------------------------------------


def test_propose_with_no_masking_returns_minimal_proposal() -> None:
    """If the focal isn't being masked at all, cut_competitors should
    return no competitor actions — there's nothing to fix on the others.
    The result still has the intent + descriptor metadata."""
    descriptor = resolve_descriptor("cuts_through")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    assert proposal["intent"] == "cuts_through"
    eq_cuts = [a for a in proposal["actions"] if a.kind == "eq_cut"]
    assert eq_cuts == []


def test_propose_includes_descriptor_metadata() -> None:
    """The proposal should echo the intent / band / sign so the caller
    sees what was used to generate it."""
    descriptor = resolve_descriptor("cuts_through")
    masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[("X", 0.7, 3150.0)],
    )
    proposal = propose_actions(masking, descriptor, bands=make_third_octave_bands())
    assert proposal["descriptor"]["name"] == "cuts_through"
    assert proposal["descriptor"]["band_low_hz"] == 2000.0
    assert proposal["descriptor"]["band_high_hz"] == 5000.0
    assert proposal["descriptor"]["action_class"] == "cut_competitors"


def test_mix_action_serializable() -> None:
    """MixAction.to_dict() returns a JSON-friendly dict."""
    a = MixAction(
        track_index=1, kind="eq_cut", device_hint="EQ Eight",
        freq_hz=3000.0, q=2.0, gain_db=-3.0, rationale="masking 4 dB",
    )
    d = a.to_dict()
    assert d["track_index"] == 1
    assert d["kind"] == "eq_cut"
    assert d["freq_hz"] == 3000.0
    assert d["gain_db"] == -3.0


# ---------------------------------------------------------------------------
# Async wrapper mix_propose_at_region
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_propose_at_region_chains_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end shape: monkeypatch mix_masking_at_region, verify the
    propose result returns the expected structure."""
    bands = make_third_octave_bands()
    fake_masking = _synth_masking_result(
        focal_money_bands=[3150.0],
        competing=[("Rhythm", 0.8, 3150.0)],
    )
    fake_masking["region"] = {
        "start_beats": 0.0, "end_beats": 8.0,
        "duration_sec": 4.0, "tempo": 120.0,
    }
    fake_masking["bands"] = [b.to_dict() for b in bands]
    fake_masking["skipped_tracks"] = []

    async def fake_masking_call(focal_track_index, start_beats, end_beats, **_):
        return fake_masking

    monkeypatch.setattr(
        "ableton_mcp.mix_propose.mix_masking_at_region",
        fake_masking_call,
    )

    proposal = await mix_propose_at_region(
        focal_track_index=0, intent="cuts_through",
        start_beats=0.0, end_beats=8.0,
    )
    assert proposal["intent"] == "cuts_through"
    assert proposal["region"]["start_beats"] == 0.0
    assert proposal["region"]["end_beats"] == 8.0
    assert proposal["focal_track"] == 0
    assert len(proposal["actions"]) >= 1


@pytest.mark.asyncio
async def test_mix_propose_at_region_unknown_intent_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown descriptor → KeyError, no bounce wasted."""
    called = {"bounce": False}

    async def fake_masking_call(*_a, **_kw):
        called["bounce"] = True
        return {}

    monkeypatch.setattr(
        "ableton_mcp.mix_propose.mix_masking_at_region",
        fake_masking_call,
    )
    with pytest.raises(KeyError):
        await mix_propose_at_region(
            focal_track_index=0, intent="nonsense",
            start_beats=0.0, end_beats=8.0,
        )
    # Should fail before the expensive bounce.
    assert called["bounce"] is False
