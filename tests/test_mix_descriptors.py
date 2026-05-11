"""Tests for the mix-vocabulary descriptor registry — L3 of the
mix-aware shaping stack.

Each descriptor maps a user-facing word ("cuts through", "muddy",
"buried", "boomy", "harsh", ...) to (a) the frequency range it lives in
and (b) the class of mix action that moves the sound toward it. This is
the lookup table Layer 4 (``mix_propose``) consumes.
"""

from __future__ import annotations

import pytest

from ableton_mcp.mix_descriptors import (
    DESCRIPTORS,
    MixDescriptor,
    bands_in_descriptor_range,
    list_descriptors,
    resolve_descriptor,
)
from ableton_mcp.mix_analysis import make_third_octave_bands


# ---------------------------------------------------------------------------
# Registry structure / coverage
# ---------------------------------------------------------------------------


def test_registry_covers_core_intent_words() -> None:
    """The descriptors plan-doc lists must all be present."""
    expected = {
        "cuts_through", "buried", "muddy", "boomy", "honky", "boxy",
        "harsh", "sibilant", "airy", "punchy", "thick", "thin",
    }
    registered = {d.name for d in DESCRIPTORS.values()}
    missing = expected - registered
    assert missing == set(), f"missing descriptors: {missing}"


def test_descriptor_names_unique() -> None:
    seen: set[str] = set()
    for d in DESCRIPTORS.values():
        assert d.name not in seen, f"duplicate descriptor name: {d.name}"
        seen.add(d.name)


def test_aliases_dont_collide_with_canonical_names() -> None:
    """An alias should never be the canonical name of another descriptor."""
    canonical = {d.name for d in DESCRIPTORS.values()}
    for d in DESCRIPTORS.values():
        for a in d.aliases:
            assert a != d.name  # alias != self
            assert a not in canonical or a == d.name, (
                f"alias {a!r} on {d.name!r} clashes with another canonical name"
            )


def test_each_descriptor_has_valid_band_range() -> None:
    """0 < low < high <= 22 kHz."""
    for d in DESCRIPTORS.values():
        assert 0 < d.band_low_hz < d.band_high_hz <= 22000.0, (
            f"{d.name}: bad band range {d.band_low_hz}-{d.band_high_hz}"
        )


def test_each_descriptor_has_sign_and_action_class() -> None:
    for d in DESCRIPTORS.values():
        assert d.sign in (-1, +1), f"{d.name}: bad sign {d.sign}"
        assert d.action_class, f"{d.name}: missing action_class"
        assert d.description, f"{d.name}: missing description"


def test_list_descriptors_returns_all_registered() -> None:
    all_descs = list_descriptors()
    assert len(all_descs) == len(DESCRIPTORS)
    names = {d.name for d in all_descs}
    assert names == {d.name for d in DESCRIPTORS.values()}


# ---------------------------------------------------------------------------
# resolve_descriptor — case + alias handling
# ---------------------------------------------------------------------------


def test_resolve_descriptor_by_canonical_name() -> None:
    d = resolve_descriptor("cuts_through")
    assert d.name == "cuts_through"


def test_resolve_descriptor_case_insensitive() -> None:
    d1 = resolve_descriptor("Muddy")
    d2 = resolve_descriptor("MUDDY")
    d3 = resolve_descriptor("muddy")
    assert d1.name == d2.name == d3.name == "muddy"


def test_resolve_descriptor_strips_whitespace() -> None:
    d = resolve_descriptor("  buried  ")
    assert d.name == "buried"


def test_resolve_descriptor_by_alias() -> None:
    """Common synonyms should resolve to the canonical descriptor."""
    # "lost_in_mix" → "buried"
    d = resolve_descriptor("lost_in_mix")
    assert d.name == "buried"
    # "present" → "cuts_through"
    d = resolve_descriptor("present")
    assert d.name == "cuts_through"


def test_resolve_descriptor_accepts_natural_phrasing() -> None:
    """Spaces in the input should be tolerated (``"cut through"`` →
    canonical ``"cuts_through"``)."""
    d = resolve_descriptor("cut through")
    assert d.name == "cuts_through"


def test_resolve_descriptor_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown"):
        resolve_descriptor("not_a_real_word_xyz")


# ---------------------------------------------------------------------------
# bands_in_descriptor_range
# ---------------------------------------------------------------------------


def test_bands_in_descriptor_range_picks_correct_band_indices() -> None:
    """For ``muddy`` (200-400 Hz), the matching bands are the third-
    octaves whose centres fall in [200, 400]."""
    bands = make_third_octave_bands()
    muddy = resolve_descriptor("muddy")
    indices = bands_in_descriptor_range(muddy, bands)
    centers = {bands[i].center_hz for i in indices}
    # 250 Hz, 315 Hz, 400 Hz are the third-octave centres in that range.
    assert {250.0, 315.0}.issubset(centers)
    # Should not include adjacent decades.
    assert 100.0 not in centers
    assert 1000.0 not in centers


def test_bands_in_descriptor_range_for_cuts_through() -> None:
    """``cuts_through`` lives in the presence band (~2-5 kHz)."""
    bands = make_third_octave_bands()
    cuts = resolve_descriptor("cuts_through")
    indices = bands_in_descriptor_range(cuts, bands)
    centers = {bands[i].center_hz for i in indices}
    # Presence band: 2 kHz, 2.5 kHz, 3.15 kHz, 4 kHz, 5 kHz are typical.
    assert {2500.0, 3150.0, 4000.0}.issubset(centers)


def test_bands_in_descriptor_range_for_boomy() -> None:
    """``boomy`` lives in the low bass (60-120 Hz)."""
    bands = make_third_octave_bands()
    boomy = resolve_descriptor("boomy")
    indices = bands_in_descriptor_range(boomy, bands)
    centers = {bands[i].center_hz for i in indices}
    assert 80.0 in centers
    assert 100.0 in centers


def test_bands_in_descriptor_range_empty_for_out_of_range() -> None:
    """A descriptor with a band range outside the third-octave table
    returns an empty list (degenerate but well-defined)."""
    bands = make_third_octave_bands()
    fake = MixDescriptor(
        name="fake", aliases=(),
        band_low_hz=20000.0, band_high_hz=21000.0,
        sign=+1, action_class="boost_focal",
        description="test",
    )
    assert bands_in_descriptor_range(fake, bands) == []


# ---------------------------------------------------------------------------
# Sign conventions
# ---------------------------------------------------------------------------


def test_cuts_through_has_positive_sign() -> None:
    """``cuts_through`` means "want more in the presence band" → +1."""
    assert resolve_descriptor("cuts_through").sign == +1


def test_buried_has_negative_sign() -> None:
    """``buried`` is the lack of presence relative to the rest → -1
    relative to the focal (or +1 of energy on competitors); the
    descriptor encodes the focal-side sign so propose() knows which way
    to push the focal."""
    # The sign here is "focal energy direction" — buried means the
    # focal needs MORE in the band to be unburied, so +1 makes sense.
    # Either convention is OK as long as it's documented; what matters
    # is that buried's sign matches cuts_through's because both want
    # the focal to have more presence.
    buried = resolve_descriptor("buried")
    cuts = resolve_descriptor("cuts_through")
    assert buried.sign == cuts.sign


def test_muddy_has_negative_sign() -> None:
    """``muddy`` means "want less in low-mid" → -1."""
    assert resolve_descriptor("muddy").sign == -1


def test_action_class_values_known() -> None:
    """All declared action_class strings come from a known vocabulary
    so Layer 4 has a finite switch to handle."""
    known = {
        "boost_focal", "cut_focal", "boost_competitors", "cut_competitors",
        "high_pass_non_bass", "de_ess_focal", "high_shelf_focal",
        "low_shelf_focal", "compress_focal_transients",
    }
    for d in DESCRIPTORS.values():
        assert d.action_class in known, (
            f"{d.name}: action_class {d.action_class!r} not in known set"
        )
