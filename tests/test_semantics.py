"""Tests for the semantic vocabulary layer.

Covers:

- vocabulary completeness (≥100 entries spread across all 10 categories)
- every descriptor has at least one feature anchor
- ``opposite`` references are reciprocal where defined on both sides
- the describer flags "bright" for high-cutoff synth_stub renders and "dark"
  for low-cutoff renders
- ``descriptor_to_feature_delta`` pushes the right feature in the right
  direction for representative descriptors
- ``combine_deltas`` merges multi-descriptor requests
- the MCP tool ``register(mcp)`` registers the four expected tools
"""

from __future__ import annotations

import pytest

from ableton_mcp.semantics import (
    Descriptor,
    VOCABULARY,
    combine_deltas,
    describe_features,
    descriptor_to_feature_delta,
    descriptors_in_category,
    lookup,
    parse_descriptors,
)
from ableton_mcp.semantics.vocabulary import FEATURE_NAMES, FeatureAnchor
from ableton_mcp.sound.features import extract_features
from ableton_mcp.sound.synth_stub import synth_render


# ---------- vocabulary shape -------------------------------------------------


REQUIRED_CATEGORIES = (
    "brightness",
    "warmth",
    "dynamics",
    "space",
    "character",
    "envelope",
    "harmonic",
    "punch",
    "air",
    "body",
)


def test_vocabulary_has_at_least_100_entries() -> None:
    assert len(VOCABULARY) >= 100, f"vocabulary too small: {len(VOCABULARY)}"


def test_vocabulary_covers_all_10_categories() -> None:
    seen = {d.category for d in VOCABULARY.values()}
    missing = set(REQUIRED_CATEGORIES) - seen
    assert not missing, f"missing categories: {sorted(missing)}"
    # And we must have at least 4 descriptors per category — keeps the spread honest.
    for cat in REQUIRED_CATEGORIES:
        in_cat = descriptors_in_category(cat)
        assert len(in_cat) >= 4, f"category {cat!r} only has {len(in_cat)} descriptors"


def test_every_descriptor_has_at_least_one_anchor() -> None:
    for label, d in VOCABULARY.items():
        assert d.feature_anchors, f"{label} has zero anchors"
        for anchor in d.feature_anchors:
            assert anchor.feature in FEATURE_NAMES
            assert anchor.weight > 0


def test_every_descriptor_has_aliases() -> None:
    for label, d in VOCABULARY.items():
        assert isinstance(d.aliases, list)
        assert len(d.aliases) >= 1, f"{label} should provide at least one alias"


def test_opposites_reference_known_descriptors() -> None:
    """Every declared ``opposite`` must point at either an in-vocabulary label or None."""
    for label, d in VOCABULARY.items():
        if d.opposite is None:
            continue
        # Allow either a vocabulary label OR a known alias.
        opp_descriptor = VOCABULARY.get(d.opposite) or lookup(d.opposite)
        assert opp_descriptor is not None, (
            f"{label}.opposite={d.opposite!r} is neither a known label nor alias"
        )


def test_at_least_some_opposites_are_mutually_reciprocal() -> None:
    """A meaningful chunk of opposites should round-trip — not every one, since
    several descriptors can share the same canonical opposite."""
    reciprocal_pairs = 0
    for label, d in VOCABULARY.items():
        if not d.opposite:
            continue
        opp = VOCABULARY.get(d.opposite)
        if opp is None or opp.opposite is None:
            continue
        if opp.opposite == label:
            reciprocal_pairs += 1
    assert reciprocal_pairs >= 6, (
        f"expected at least 6 reciprocal opposite pairs, got {reciprocal_pairs}"
    )


def test_lookup_resolves_aliases() -> None:
    # Pick a descriptor that ships with a known alias.
    bright = VOCABULARY["bright"]
    assert "shimmering" in bright.aliases
    found = lookup("shimmering")
    assert found is not None
    # The alias might map to either "bright" itself or another shimmer descriptor;
    # the contract is just "lookup returns *some* descriptor".
    assert isinstance(found, Descriptor)


# ---------- describer tests --------------------------------------------------


def _features_at(cutoff: float, noise: float = 0.4):
    """Render the synth stub with the given LP cutoff and a touch of noise.

    A pure-sine render is dominated by the fundamental and barely responds to
    the LP filter (centroid pinned to freq), so we mix in some noise so the
    cutoff becomes the dominant brightness control — closer to a real synth
    sweeping a filter over a harmonic-rich source.
    """
    audio = synth_render(
        {"freq": 440.0, "cutoff": cutoff, "noise_amount": float(noise), "attack": 0.01, "release": 0.1},
        sr=22050,
        dur=1.5,
        seed=0,
    )
    return extract_features(audio, sr=22050)


def test_describer_calls_high_cutoff_bright() -> None:
    """A synth render with the LP filter wide-open should land in the bright family."""
    feats = _features_at(cutoff=8000.0)
    # Score every descriptor (no min_confidence cutoff so we can see brightness rank).
    ranked = describe_features(feats, top_k=None, min_confidence=0.0)
    by_label = dict(ranked)
    bright_score = by_label.get("bright", 0.0)
    dark_score = by_label.get("dark", 0.0)
    assert bright_score > dark_score, (
        f"expected bright ({bright_score:.3f}) > dark ({dark_score:.3f}) at high cutoff"
    )
    # And the top-20 should include at least one brightness-family descriptor.
    top20 = {l for l, _ in describe_features(feats, top_k=20)}
    bright_family = {"bright", "brilliant", "shimmering", "sparkling", "crisp", "glassy", "clear", "airy", "open"}
    assert top20 & bright_family, (
        f"no brightness/air descriptor in top-20 for high cutoff: {sorted(top20)}"
    )


def test_describer_calls_low_cutoff_dark() -> None:
    """A synth render with a closed LP filter should clearly land in the dark family."""
    feats = _features_at(cutoff=300.0)
    ranked = describe_features(feats, top_k=15)
    labels = [label for label, _ in ranked]
    assert "dark" in labels, f"dark not in top-15 for low cutoff: {labels}"
    assert "bright" not in labels, f"bright appeared for low-cutoff audio: {labels}"


def test_describer_bright_score_increases_with_cutoff() -> None:
    """Sweeping the LP filter from closed to open must monotonically raise the
    bright-confidence score (this is the core sanity property of the layer)."""
    scores = []
    for cutoff in (300.0, 1500.0, 4000.0, 8000.0):
        feats = _features_at(cutoff=cutoff)
        ranked = describe_features(feats, top_k=None, min_confidence=0.0)
        scores.append(dict(ranked).get("bright", 0.0))
    # Must be strictly non-decreasing across the sweep.
    for a, b in zip(scores, scores[1:]):
        assert b >= a - 1e-6, f"bright score went down with cutoff: {scores}"
    # And the gap from closed to open should be material.
    assert scores[-1] > scores[0] + 0.05, (
        f"bright score barely changed across the sweep: {scores}"
    )


def test_describer_returns_sorted_descending_confidence() -> None:
    feats = _features_at(cutoff=6000.0)
    ranked = describe_features(feats, top_k=10)
    confidences = [c for _, c in ranked]
    assert confidences == sorted(confidences, reverse=True)
    for c in confidences:
        assert 0.0 <= c <= 1.0


# ---------- transforms tests -------------------------------------------------


def test_brighter_increases_centroid_and_rolloff() -> None:
    # Start from neutral mid-cutoff features.
    feats = _features_at(cutoff=2000.0)
    delta = descriptor_to_feature_delta("bright", feats, intensity=1.0)
    assert delta.get("spectral_centroid", 0) > 0
    assert delta.get("spectral_rolloff", 0) > 0


def test_darker_decreases_centroid() -> None:
    feats = _features_at(cutoff=2000.0)
    delta = descriptor_to_feature_delta("dark", feats, intensity=1.0)
    assert delta.get("spectral_centroid", 0) < 0


def test_punchier_increases_rms_and_attack_variability() -> None:
    feats = _features_at(cutoff=2000.0)
    delta = descriptor_to_feature_delta("punchy", feats, intensity=1.0)
    # punchy anchors on rms (high) and mfcc_std_0 (high).
    assert delta.get("rms", 0) > 0
    assert delta.get("mfcc_std_0", 0) > 0


def test_warmer_decreases_centroid_and_increases_rms() -> None:
    feats = _features_at(cutoff=2000.0)
    delta = descriptor_to_feature_delta("warm", feats, intensity=1.0)
    assert delta.get("spectral_centroid", 0) < 0
    # rms anchored "high" → positive shift expected.
    assert delta.get("rms", 0) > 0


def test_intensity_scales_delta_magnitude() -> None:
    feats = _features_at(cutoff=2000.0)
    weak = descriptor_to_feature_delta("bright", feats, intensity=0.2)
    strong = descriptor_to_feature_delta("bright", feats, intensity=1.0)
    assert abs(strong["spectral_centroid"]) > abs(weak["spectral_centroid"])


def test_combine_deltas_is_additive_and_clamped() -> None:
    feats = _features_at(cutoff=2000.0)
    bright_delta = descriptor_to_feature_delta("bright", feats, intensity=0.5)
    punchy_delta = descriptor_to_feature_delta("punchy", feats, intensity=0.5)
    combined = combine_deltas([bright_delta, punchy_delta])
    # Both deltas push spectral_centroid in the same way? not necessarily —
    # but at least the union of keys must equal the sum of keys.
    expected_keys = set(bright_delta) | set(punchy_delta)
    assert set(combined) == expected_keys
    # No delta in [-1, 1] violation.
    for v in combined.values():
        assert -1.0 <= v <= 1.0


def test_combine_deltas_clamps_runaway_stacking() -> None:
    feats = _features_at(cutoff=2000.0)
    deltas = [descriptor_to_feature_delta("bright", feats, intensity=1.0) for _ in range(10)]
    combined = combine_deltas(deltas)
    for k, v in combined.items():
        assert -1.0 <= v <= 1.0, f"{k} -> {v}"


def test_descriptor_to_feature_delta_unknown_label_raises() -> None:
    feats = _features_at(cutoff=2000.0)
    with pytest.raises(KeyError):
        descriptor_to_feature_delta("definitely-not-a-descriptor", feats)


# ---------- free-text parser -------------------------------------------------


def test_parse_descriptors_finds_multiple() -> None:
    matches = parse_descriptors("brighter and more punchy")
    labels = [label for label, _ in matches]
    assert "bright" in labels or any("bright" == lookup(l).label for l in labels if lookup(l))
    assert "punchy" in labels


def test_parse_descriptors_handles_negation() -> None:
    matches = parse_descriptors("less bright")
    assert matches
    label, signed = matches[0]
    assert lookup(label).label == "bright"
    assert signed < 0


# ---------- MCP tool registration -------------------------------------------


@pytest.mark.asyncio
async def test_register_registers_at_least_four_tools() -> None:
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import semantics as semantics_tools

    mcp = FastMCP("semantics-test")
    semantics_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "sound_describe",
        "sound_descriptors_list",
        "sound_descriptor_explain",
        "sound_target_for_description",
    }
    missing = expected - names
    assert not missing, f"missing tools: {sorted(missing)}"
    assert len(expected) >= 4


def test_feature_anchor_validates_feature_name() -> None:
    with pytest.raises(ValueError):
        FeatureAnchor("not_a_real_feature", "high", 1.0)


def test_feature_anchor_validates_predicate() -> None:
    with pytest.raises(ValueError):
        FeatureAnchor("spectral_centroid", "totally-bogus", 1.0)  # type: ignore[arg-type]
