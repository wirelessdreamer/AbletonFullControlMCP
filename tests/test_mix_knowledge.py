"""Tests for ``ableton_mcp.mix_knowledge`` — money-band + standard-fix
data file for the mix-aware shaping stack.

This is a knowledge file rather than a tool — the tests verify the data
shape and the lightweight name/spectrum classifiers it ships with.
"""

from __future__ import annotations

import pytest

from ableton_mcp.mix_analysis import DB_FLOOR, make_third_octave_bands
from ableton_mcp.mix_knowledge import (
    INSTRUMENT_MONEY_BANDS,
    STANDARD_FIXES,
    InstrumentMoneyBands,
    StandardFix,
    classify_track_by_name,
    classify_track_by_spectrum,
    money_bands_for_instrument,
)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


def test_registry_covers_core_instruments() -> None:
    """Every instrument family from the plan doc is present."""
    expected = {
        "lead_vocal", "lead_guitar", "rhythm_guitar", "bass", "kick",
        "snare", "hihat", "piano",
    }
    registered = set(INSTRUMENT_MONEY_BANDS.keys())
    missing = expected - registered
    assert missing == set(), f"missing instruments: {missing}"


def test_every_entry_has_valid_ranges() -> None:
    for inst in INSTRUMENT_MONEY_BANDS.values():
        # Body range optional (hihat has none); when present, low < high.
        if inst.body_low_hz is not None:
            assert inst.body_high_hz is not None
            assert 0 < inst.body_low_hz < inst.body_high_hz <= 22000.0
        # Presence range is required for every instrument.
        assert 0 < inst.presence_low_hz < inst.presence_high_hz <= 22000.0


def test_every_entry_has_aliases() -> None:
    for inst in INSTRUMENT_MONEY_BANDS.values():
        assert len(inst.aliases) > 0, f"{inst.name} has no aliases"


def test_canonical_names_are_unique() -> None:
    names = [i.name for i in INSTRUMENT_MONEY_BANDS.values()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# classify_track_by_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("track_name,expected", [
    ("Lead Vocal", "lead_vocal"),
    ("Vocals", "lead_vocal"),
    ("Vox", "lead_vocal"),
    ("Bass", "bass"),
    ("Bass Guitar", "bass"),
    ("Sub Bass", "bass"),
    ("Kick", "kick"),
    ("Kick Drum", "kick"),
    ("BD", "kick"),
    ("Snare", "snare"),
    ("SD", "snare"),
    ("Hi Hat", "hihat"),
    ("Hi-Hat", "hihat"),
    ("Hat", "hihat"),
    ("HH", "hihat"),
    ("Lead Guitar", "lead_guitar"),
    ("Solo Guitar", "lead_guitar"),
    ("Rhythm Guitar", "rhythm_guitar"),
    ("Rhythm Gtr", "rhythm_guitar"),
    ("Piano", "piano"),
    ("Keys", "piano"),
    ("Rhodes", "piano"),
])
def test_classify_track_by_name_known(track_name: str, expected: str) -> None:
    result = classify_track_by_name(track_name)
    assert result is not None, f"{track_name} → None"
    assert result.name == expected


def test_classify_track_by_name_unknown_returns_none() -> None:
    assert classify_track_by_name("UnknownInstrument") is None
    assert classify_track_by_name("") is None


def test_classify_track_by_name_case_insensitive() -> None:
    assert classify_track_by_name("BASS").name == "bass"
    assert classify_track_by_name("bass").name == "bass"
    assert classify_track_by_name("Bass").name == "bass"


def test_classify_track_by_name_picks_more_specific_match() -> None:
    """'Lead Guitar' should win over plain 'Guitar' (fallback to
    rhythm) because the more-specific keyword fires first."""
    assert classify_track_by_name("Lead Guitar").name == "lead_guitar"
    # Bare 'guitar' falls back to rhythm.
    assert classify_track_by_name("Guitar").name == "rhythm_guitar"


def test_classify_track_by_name_doesnt_match_bass_for_bass_drum() -> None:
    """'Bass Drum' is a kick, not a bass guitar — the kick keywords
    should take priority over plain 'bass'."""
    result = classify_track_by_name("Bass Drum")
    assert result is not None
    assert result.name == "kick"


# ---------------------------------------------------------------------------
# classify_track_by_spectrum
# ---------------------------------------------------------------------------


def _empty_spectrum() -> list[float]:
    return [DB_FLOOR] * len(make_third_octave_bands())


def _spike(center_hz: float, db: float = -6.0) -> list[float]:
    bands = make_third_octave_bands()
    s = _empty_spectrum()
    idx = next(i for i, b in enumerate(bands) if b.center_hz == center_hz)
    s[idx] = db
    return s


def test_classify_by_spectrum_low_end_picks_bass_family() -> None:
    """Energy concentrated below 100 Hz → bass or kick."""
    bands = make_third_octave_bands()
    spectrum = _spike(80.0, db=-3.0)
    result = classify_track_by_spectrum(spectrum, bands)
    assert result is not None
    assert result.name in {"bass", "kick"}


def test_classify_by_spectrum_high_freq_picks_hihat() -> None:
    bands = make_third_octave_bands()
    spectrum = _spike(10000.0, db=-3.0)
    result = classify_track_by_spectrum(spectrum, bands)
    assert result is not None
    assert result.name == "hihat"


def test_classify_by_spectrum_silence_returns_none() -> None:
    bands = make_third_octave_bands()
    assert classify_track_by_spectrum(_empty_spectrum(), bands) is None


# ---------------------------------------------------------------------------
# money_bands_for_instrument
# ---------------------------------------------------------------------------


def test_money_bands_for_instrument_returns_body_and_presence() -> None:
    """For an instrument with both body + presence, returns two
    (low, high, role) tuples."""
    triples = money_bands_for_instrument("bass")
    roles = {t[2] for t in triples}
    assert "body" in roles
    assert "presence" in roles


def test_money_bands_for_hihat_has_no_body() -> None:
    """Hi-hat has no body range — only presence."""
    triples = money_bands_for_instrument("hihat")
    roles = {t[2] for t in triples}
    assert "body" not in roles
    assert "presence" in roles


def test_money_bands_for_unknown_raises() -> None:
    with pytest.raises(KeyError):
        money_bands_for_instrument("not_an_instrument")


# ---------------------------------------------------------------------------
# STANDARD_FIXES
# ---------------------------------------------------------------------------


def test_standard_fixes_present() -> None:
    """At least the three documented fixes are codified."""
    names = {f.name for f in STANDARD_FIXES}
    expected = {
        "high_pass_non_bass",
        "carve_focal_presence",
        "sidechain_to_kick",
    }
    assert expected.issubset(names)


def test_standard_fixes_have_descriptions() -> None:
    for f in STANDARD_FIXES:
        assert f.description, f"fix {f.name} has no description"


def test_standard_fix_dataclass_immutable() -> None:
    """StandardFix is frozen — accidental mutation should fail."""
    fix = STANDARD_FIXES[0]
    with pytest.raises(Exception):
        fix.name = "mutated"  # type: ignore[misc]


def test_instrument_money_bands_dataclass_immutable() -> None:
    inst = next(iter(INSTRUMENT_MONEY_BANDS.values()))
    with pytest.raises(Exception):
        inst.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Integration shape — pluggable into the rest of the stack
# ---------------------------------------------------------------------------


def test_instrument_money_bands_dataclass_shape() -> None:
    """Smoke test: dataclass exposes the documented fields."""
    inst = InstrumentMoneyBands(
        name="test", aliases=("t",),
        body_low_hz=80.0, body_high_hz=200.0,
        presence_low_hz=2000.0, presence_high_hz=5000.0,
        notes="example",
    )
    assert inst.name == "test"
    assert inst.body_low_hz == 80.0
    assert inst.presence_low_hz == 2000.0


def test_standard_fix_dataclass_shape() -> None:
    fix = StandardFix(name="test_fix", description="hi", action_class="cut_focal")
    assert fix.name == "test_fix"
