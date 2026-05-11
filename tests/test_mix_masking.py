"""Tests for ``ableton_mcp.mix_masking`` — Layer 2.2 of the mix-aware
shaping stack.

The interesting math is pure-data and tests against synthetic spectra
(arrays of dB-per-band) so we don't need Live or even a real bounce.
The async wrapper ``mix_masking_at_region`` is exercised against a
monkey-patched ``mix_spectrum_at_region``.
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.mix_analysis import (
    DB_FLOOR,
    THIRD_OCTAVE_CENTERS_HZ,
    make_third_octave_bands,
)
from ableton_mcp.mix_masking import (
    band_perceptual_weight,
    compute_masking,
    compute_masking_score,
    find_focal_money_bands,
    mix_masking_at_region,
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic spectra
# ---------------------------------------------------------------------------


def _empty_spectrum() -> list[float]:
    """A spectrum at the floor everywhere."""
    return [DB_FLOOR] * len(THIRD_OCTAVE_CENTERS_HZ)


def _spike(center_hz: float, db: float = -10.0) -> list[float]:
    """A spectrum that's at the floor except for one band at ``db``."""
    bands = make_third_octave_bands()
    s = _empty_spectrum()
    idx = next(i for i, b in enumerate(bands) if b.center_hz == center_hz)
    s[idx] = db
    return s


def _spike_multi(centers_db: dict[float, float]) -> list[float]:
    """A spectrum with several spikes (center_hz → db)."""
    bands = make_third_octave_bands()
    s = _empty_spectrum()
    for c, db in centers_db.items():
        idx = next(i for i, b in enumerate(bands) if b.center_hz == c)
        s[idx] = db
    return s


# ---------------------------------------------------------------------------
# find_focal_money_bands
# ---------------------------------------------------------------------------


def test_find_focal_money_bands_picks_top_n_loudest() -> None:
    """Bands ranked by dB descending; top_n returned."""
    spectrum = _spike_multi({
        500.0: -15.0,
        1000.0: -5.0,
        2000.0: -8.0,
        4000.0: -2.0,
        8000.0: -20.0,
    })
    bands = make_third_octave_bands()
    money = find_focal_money_bands(spectrum, bands, top_n=3)
    centers = [m["center_hz"] for m in money]
    # The three loudest are 4 kHz (-2), 1 kHz (-5), 2 kHz (-8).
    assert centers == [4000.0, 1000.0, 2000.0]


def test_find_focal_money_bands_skips_floor() -> None:
    """Bands at DB_FLOOR are excluded even if top_n hasn't filled."""
    spectrum = _spike(1000.0, db=-10.0)  # only one real band
    bands = make_third_octave_bands()
    money = find_focal_money_bands(spectrum, bands, top_n=5)
    assert len(money) == 1
    assert money[0]["center_hz"] == 1000.0


def test_find_focal_money_bands_silence_returns_empty() -> None:
    bands = make_third_octave_bands()
    money = find_focal_money_bands(_empty_spectrum(), bands, top_n=5)
    assert money == []


def test_find_focal_money_bands_respects_min_db_above_floor() -> None:
    """A band only barely above the floor (within ``min_db_above_floor``)
    is treated as silence and excluded."""
    spectrum = _empty_spectrum()
    bands = make_third_octave_bands()
    idx = next(i for i, b in enumerate(bands) if b.center_hz == 1000.0)
    # Just 5 dB above floor — likely numerical noise.
    spectrum[idx] = DB_FLOOR + 5.0
    money = find_focal_money_bands(
        spectrum, bands, top_n=5, min_db_above_floor=20.0,
    )
    assert money == []


# ---------------------------------------------------------------------------
# compute_masking_score
# ---------------------------------------------------------------------------


def test_masking_score_zero_when_other_far_below_focal() -> None:
    """Other 30 dB below focal contributes nothing."""
    assert compute_masking_score(focal_db=-10.0, other_db=-50.0) == 0.0


def test_masking_score_high_when_other_matches_focal() -> None:
    """Other at the same level as focal → strong masking, near 1.0."""
    s = compute_masking_score(focal_db=-10.0, other_db=-10.0)
    assert 0.5 < s <= 1.0


def test_masking_score_capped_at_one_when_other_louder() -> None:
    """Other louder than focal → score saturates at 1.0."""
    s = compute_masking_score(focal_db=-10.0, other_db=0.0)
    assert s == 1.0


def test_masking_score_monotonic_in_other_db() -> None:
    """As the other track gets louder, the masking score never decreases."""
    scores = [
        compute_masking_score(focal_db=-10.0, other_db=db)
        for db in (-60.0, -40.0, -20.0, -10.0, 0.0)
    ]
    for a, b in zip(scores, scores[1:]):
        assert b >= a


# ---------------------------------------------------------------------------
# band_perceptual_weight
# ---------------------------------------------------------------------------


def test_band_perceptual_weight_peaks_in_presence_band() -> None:
    """Weight peaks somewhere in 2-5 kHz (the "cut through" range)."""
    presence_weights = [
        band_perceptual_weight(hz) for hz in (2000.0, 2500.0, 3150.0, 4000.0)
    ]
    extreme_weights = [
        band_perceptual_weight(hz) for hz in (25.0, 16000.0)
    ]
    assert max(presence_weights) > max(extreme_weights)


def test_band_perceptual_weight_in_unit_range() -> None:
    """Weights are normalized to roughly [0, 1]."""
    for hz in THIRD_OCTAVE_CENTERS_HZ:
        w = band_perceptual_weight(hz)
        assert 0.0 <= w <= 1.0


# ---------------------------------------------------------------------------
# compute_masking — full per-track scoring
# ---------------------------------------------------------------------------


def test_compute_masking_ranks_overlapping_track_highest() -> None:
    """A competing track that lives in the focal's money bands should
    rank above one that lives somewhere else entirely."""
    bands = make_third_octave_bands()
    focal = _spike_multi({2500.0: -6.0, 3150.0: -7.0, 4000.0: -8.0})
    other_in_band = _spike_multi({2500.0: -6.0, 3150.0: -8.0})  # masks
    other_elsewhere = _spike(100.0, db=-3.0)                     # doesn't mask
    result = compute_masking(
        focal_spectrum=focal,
        focal_meta={"track_index": 0, "name": "Lead"},
        other_spectra=[
            ({"track_index": 1, "name": "Rhythm"}, other_in_band),
            ({"track_index": 2, "name": "Bass"}, other_elsewhere),
        ],
        bands=bands,
    )
    competitors = result["competing_tracks"]
    assert len(competitors) == 2
    # The one in the focal's presence band is the bigger masker.
    assert competitors[0]["name"] == "Rhythm"
    assert competitors[0]["masking_score"] > competitors[1]["masking_score"]


def test_compute_masking_no_overlap_low_scores() -> None:
    """Other tracks living in entirely different bands score near zero."""
    bands = make_third_octave_bands()
    focal = _spike(3150.0, db=-6.0)
    other = _spike(100.0, db=-6.0)
    result = compute_masking(
        focal_spectrum=focal,
        focal_meta={"track_index": 0, "name": "Lead"},
        other_spectra=[({"track_index": 1, "name": "Bass"}, other)],
        bands=bands,
    )
    assert result["competing_tracks"][0]["masking_score"] < 0.1


def test_compute_masking_includes_per_band_breakdown() -> None:
    """For the focal's money bands, every competitor should have a
    per-band entry with its level and overlap delta."""
    bands = make_third_octave_bands()
    focal = _spike_multi({1000.0: -6.0, 2000.0: -8.0})
    other = _spike_multi({1000.0: -8.0})
    result = compute_masking(
        focal_spectrum=focal,
        focal_meta={"track_index": 0, "name": "Lead"},
        other_spectra=[({"track_index": 1, "name": "Comp"}, other)],
        bands=bands,
    )
    per_band = result["competing_tracks"][0]["per_band"]
    centers = {b["center_hz"] for b in per_band}
    # Money bands present.
    assert 1000.0 in centers
    assert 2000.0 in centers
    # Each entry has the required fields.
    for entry in per_band:
        assert "center_hz" in entry
        assert "other_energy_db" in entry
        assert "focal_energy_db" in entry
        assert "overlap_db" in entry


def test_compute_masking_focal_money_bands_in_result() -> None:
    """The result echoes the focal's money bands so downstream layers
    can see what was scored against."""
    bands = make_third_octave_bands()
    focal = _spike_multi({1000.0: -6.0, 2000.0: -8.0})
    result = compute_masking(
        focal_spectrum=focal,
        focal_meta={"track_index": 0, "name": "Lead"},
        other_spectra=[],
        bands=bands,
    )
    centers = {b["center_hz"] for b in result["focal_money_bands"]}
    assert 1000.0 in centers
    assert 2000.0 in centers


def test_compute_masking_empty_others() -> None:
    bands = make_third_octave_bands()
    focal = _spike(1000.0, db=-6.0)
    result = compute_masking(
        focal_spectrum=focal,
        focal_meta={"track_index": 0, "name": "Lead"},
        other_spectra=[],
        bands=bands,
    )
    assert result["competing_tracks"] == []
    assert result["focal_track"] == 0


def test_compute_masking_silent_focal_returns_empty_money_bands() -> None:
    """If the focal has no energy, there's nothing to mask. Result should
    have empty money_bands and empty competitors (nothing to rank against)."""
    bands = make_third_octave_bands()
    other = _spike(1000.0, db=-6.0)
    result = compute_masking(
        focal_spectrum=_empty_spectrum(),
        focal_meta={"track_index": 0, "name": "Lead"},
        other_spectra=[({"track_index": 1, "name": "Other"}, other)],
        bands=bands,
    )
    assert result["focal_money_bands"] == []
    # Competitor still listed but with score 0 (nothing to mask).
    for c in result["competing_tracks"]:
        assert c["masking_score"] == 0.0


# ---------------------------------------------------------------------------
# Async wrapper mix_masking_at_region
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_masking_at_region_pipes_through_spectrum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async wrapper bounces, analyzes, then masks — verify the
    end-to-end shape and that the focal_track_index actually picks the
    focal out of the spectrum result."""
    bands = make_third_octave_bands()
    bands_json = [b.to_dict() for b in bands]

    async def fake_mix_spectrum_at_region(
        start_beats, end_beats, **_kw,
    ) -> dict[str, Any]:
        return {
            "region": {
                "start_beats": start_beats, "end_beats": end_beats,
                "duration_sec": 4.0, "tempo": 120.0,
            },
            "bands": bands_json,
            "tracks": [
                {"track_index": 0, "name": "Drums", "analyzed": True,
                 **_dummy_spectrum(_spike(80.0, db=-6.0))},
                {"track_index": 1, "name": "Lead", "analyzed": True,
                 **_dummy_spectrum(_spike_multi(
                     {2500.0: -6.0, 3150.0: -7.0, 4000.0: -8.0},
                 ))},
                {"track_index": 2, "name": "Rhythm", "analyzed": True,
                 **_dummy_spectrum(_spike_multi(
                     {2500.0: -7.0, 3150.0: -8.0},
                 ))},
            ],
            "bounce_diagnostics": None,
            "output_dir": "/tmp/fake",
        }

    monkeypatch.setattr(
        "ableton_mcp.mix_masking.mix_spectrum_at_region",
        fake_mix_spectrum_at_region,
    )

    result = await mix_masking_at_region(
        focal_track_index=1, start_beats=0.0, end_beats=8.0,
    )
    assert result["focal_track"] == 1
    assert result["region"]["start_beats"] == 0.0
    # Focal money bands should include 2.5 / 3.15 / 4 kHz.
    centers = {b["center_hz"] for b in result["focal_money_bands"]}
    assert {2500.0, 3150.0, 4000.0}.issubset(centers)
    # Competitors: Drums (track 0) and Rhythm (track 2), Rhythm ranks higher
    # because it overlaps the focal's presence band.
    assert len(result["competing_tracks"]) == 2
    top = result["competing_tracks"][0]
    assert top["name"] == "Rhythm"


@pytest.mark.asyncio
async def test_mix_masking_at_region_raises_when_focal_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for masking against a focal_track_index that wasn't in the
    bounce should raise so the user gets a clear error."""
    bands_json = [b.to_dict() for b in make_third_octave_bands()]

    async def fake_mix_spectrum_at_region(
        start_beats, end_beats, **_kw,
    ) -> dict[str, Any]:
        return {
            "region": {"start_beats": 0.0, "end_beats": 4.0,
                       "duration_sec": 2.0, "tempo": 120.0},
            "bands": bands_json,
            "tracks": [
                {"track_index": 0, "name": "Drums", "analyzed": True,
                 **_dummy_spectrum(_spike(80.0, db=-6.0))},
            ],
            "bounce_diagnostics": None, "output_dir": "/tmp/fake",
        }

    monkeypatch.setattr(
        "ableton_mcp.mix_masking.mix_spectrum_at_region",
        fake_mix_spectrum_at_region,
    )

    with pytest.raises(ValueError, match="focal_track_index"):
        await mix_masking_at_region(
            focal_track_index=99, start_beats=0.0, end_beats=4.0,
        )


@pytest.mark.asyncio
async def test_mix_masking_at_region_skips_unanalyzed_tracks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stem that failed to bounce should not show up as a competitor."""
    bands_json = [b.to_dict() for b in make_third_octave_bands()]

    async def fake_mix_spectrum_at_region(
        start_beats, end_beats, **_kw,
    ) -> dict[str, Any]:
        return {
            "region": {"start_beats": 0.0, "end_beats": 4.0,
                       "duration_sec": 2.0, "tempo": 120.0},
            "bands": bands_json,
            "tracks": [
                {"track_index": 0, "name": "Lead", "analyzed": True,
                 **_dummy_spectrum(_spike(2500.0, db=-6.0))},
                {"track_index": 1, "name": "Broken",
                 "analyzed": False, "error": "bounce failed"},
            ],
            "bounce_diagnostics": None, "output_dir": "/tmp/fake",
        }

    monkeypatch.setattr(
        "ableton_mcp.mix_masking.mix_spectrum_at_region",
        fake_mix_spectrum_at_region,
    )
    result = await mix_masking_at_region(
        focal_track_index=0, start_beats=0.0, end_beats=4.0,
    )
    # Broken stem is in the "skipped" list, not competitors.
    assert result["competing_tracks"] == []
    assert any(s["track_index"] == 1 for s in result["skipped_tracks"])


def _dummy_spectrum(energy_db_per_band: list[float]) -> dict[str, Any]:
    """Build the shape that ``compute_band_energy`` would return,
    populated with the supplied band energies and reasonable peaks."""
    return {
        "energy_db_per_band": energy_db_per_band,
        "peak_db": -3.0,
        "rms_db": -10.0,
        "spectral_centroid_hz": 1000.0,
        "top_bands": [],
        "wav_path": "/tmp/fake.wav",
        "samplerate": 22050,
        "duration_sec": 2.0,
    }
