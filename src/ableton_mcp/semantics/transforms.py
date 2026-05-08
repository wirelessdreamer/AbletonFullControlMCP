"""Translate descriptors into feature deltas — the "make it brighter" half.

Given a descriptor label (or free-text containing several) and the current
features of an audio clip, return a dict ``{feature_name: delta_pct}`` where
``delta_pct`` is a relative move expressed as a fraction of the current value
(e.g. ``+0.25`` = "increase this feature by 25%"). The downstream NL shaping
engine can decide how to actually apply that delta — knob mapping, EQ move,
filter sweep, etc.

The transforms layer leans on the same :class:`FeatureAnchor` predicates used
by the describer. Each anchor's predicate tells us the desired direction for
that feature; ``intensity`` (0..1) scales the magnitude.
"""

from __future__ import annotations

import re
from typing import Iterable

from .reference_distributions import DEFAULT, ReferenceDistributions
from .vocabulary import FeatureAnchor, VOCABULARY, lookup as lookup_descriptor

# Default knob throw — at intensity=1.0 a single-anchor "make it brighter"
# request asks for a 50% relative shift on the targeted feature. With the
# soft-step modifier in :func:`_step_size`, intensity=0.5 lands near 25%.
_DEFAULT_FULL_DELTA = 0.5


def _step_size(intensity: float, headroom: float) -> float:
    """Pick a delta magnitude given desired intensity and remaining headroom.

    ``headroom`` is a fraction in [0, 1] expressing how much room there is to
    move the feature in the requested direction (e.g. 1.0 = at the wrong end
    of the distribution, 0.0 = already saturated). We multiply intensity by
    headroom so a feature that is already saturated doesn't get pushed past 100%.
    """
    intensity = max(0.0, min(1.0, float(intensity)))
    headroom = max(0.0, min(1.0, float(headroom)))
    return _DEFAULT_FULL_DELTA * intensity * (0.4 + 0.6 * headroom)


def _direction_from_predicate(predicate: str) -> int:
    """+1 means push the feature higher; -1 means push it lower."""
    if predicate in ("high", "percentile_above"):
        return +1
    if predicate in ("low", "percentile_below"):
        return -1
    return 0  # pragma: no cover — anchors are validated at construction


def _anchor_delta(
    anchor: FeatureAnchor,
    current_value: float,
    intensity: float,
    refs: ReferenceDistributions,
) -> float:
    """Return the relative delta this anchor would request on its feature."""
    direction = _direction_from_predicate(anchor.predicate)
    if direction == 0:
        return 0.0

    current_pct = refs.value_to_percentile(anchor.feature, current_value)
    if direction > 0:
        # Want the value to move higher in its distribution.
        headroom = 1.0 - current_pct
    else:
        headroom = current_pct

    magnitude = _step_size(intensity, headroom) * float(anchor.weight)
    return float(direction * magnitude)


def descriptor_to_feature_delta(
    descriptor_label: str,
    current_features,
    intensity: float = 1.0,
    *,
    refs: ReferenceDistributions | None = None,
) -> dict[str, float]:
    """Per-feature relative deltas to push the audio toward a descriptor.

    ``current_features`` may be a :class:`ableton_mcp.sound.features.Features`
    or a dict already keyed by feature name (useful for tests). Returns a dict
    ``{feature_name: delta_pct}``. Multiple anchors on the same feature are
    averaged, weighted by anchor weight.
    """
    descriptor = lookup_descriptor(descriptor_label)
    if descriptor is None:
        raise KeyError(f"unknown descriptor: {descriptor_label!r}")

    refs = refs or DEFAULT

    # Coerce current features to a flat dict.
    if hasattr(current_features, "spectral_centroid"):
        feat_dict = {
            "spectral_centroid": float(current_features.spectral_centroid),
            "spectral_bandwidth": float(current_features.spectral_bandwidth),
            "spectral_rolloff": float(current_features.spectral_rolloff),
            "zcr": float(current_features.zcr),
            "rms": float(current_features.rms),
            "spectral_flatness": float(current_features.spectral_flatness),
        }
        for i, v in enumerate(current_features.mfcc_mean):
            feat_dict[f"mfcc_mean_{i}"] = float(v)
        for i, v in enumerate(current_features.mfcc_std):
            feat_dict[f"mfcc_std_{i}"] = float(v)
    else:
        feat_dict = {k: float(v) for k, v in dict(current_features).items()}

    # Apply intensity scale from descriptor (lets us mark some descriptors as
    # weaker influences, e.g. atmospheric vs. aggressive).
    eff_intensity = float(intensity) * float(descriptor.intensity_scale)

    weighted: dict[str, list[tuple[float, float]]] = {}
    for anchor in descriptor.feature_anchors:
        value = feat_dict.get(anchor.feature, 0.0)
        delta = _anchor_delta(anchor, value, eff_intensity, refs)
        weighted.setdefault(anchor.feature, []).append((delta, float(anchor.weight)))

    out: dict[str, float] = {}
    for feature, pairs in weighted.items():
        total_weight = sum(w for _, w in pairs) or 1.0
        weighted_sum = sum(d * w for d, w in pairs)
        out[feature] = float(weighted_sum / total_weight)
    return out


def combine_deltas(deltas: Iterable[dict[str, float]]) -> dict[str, float]:
    """Merge multiple deltas into one — additive, then clamped to [-1, +1].

    Useful for free-text requests like "brighter and punchier" where each word
    contributes a delta dict and the caller wants a single combined target.
    """
    merged: dict[str, float] = {}
    for d in deltas:
        for k, v in d.items():
            merged[k] = merged.get(k, 0.0) + float(v)
    # Clamp the combined ask so absurd stacking ("brighter, brighter, brighter")
    # doesn't request a 300% shift.
    return {k: float(max(-1.0, min(1.0, v))) for k, v in merged.items()}


# --------------------------------------------------------------------------
# Free-text parser — extracts known descriptors from arbitrary user prose.
# --------------------------------------------------------------------------

# Polarity-flipping cues. If one of these precedes a descriptor we apply the
# delta to its opposite (or invert the delta if no opposite is defined).
_NEGATION_TOKENS = ("less", "not", "no", "without", "unless")


def _tokenise(text: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z\-]+", text.lower()) if t]


def _stem_candidates(token: str) -> list[str]:
    """Yield possible base forms of a comparative/superlative token.

    "brighter" → ["brighter", "bright"]; "warmest" → ["warmest", "warm"].
    Conservative — only strips the common -er/-est suffixes so we don't
    accidentally turn "punchy" into "punch".
    """
    candidates = [token]
    if token.endswith("er") and len(token) > 4:
        candidates.append(token[:-2])
        if token.endswith("ier") and len(token) > 5:
            candidates.append(token[:-3] + "y")  # punchier → punchy
    if token.endswith("est") and len(token) > 5:
        candidates.append(token[:-3])
    return candidates


def parse_descriptors(text: str) -> list[tuple[str, float]]:
    """Find ``(label, signed_intensity)`` tuples for every descriptor matched in ``text``.

    "more bright" → +1.0, "less bright" → -1.0. "very" / "slightly" modulate.
    Returns the descriptors in the order they appear in the text.
    """
    if not text:
        return []
    tokens = _tokenise(text)
    matches: list[tuple[str, float]] = []
    i = 0
    while i < len(tokens):
        # Look ahead up to 3 tokens for a multi-word alias ("laid back").
        consumed = 0
        descriptor = None
        for window in (3, 2, 1):
            if i + window > len(tokens):
                continue
            phrase = "-".join(tokens[i : i + window])  # try hyphenated form too
            descriptor = lookup_descriptor(phrase) or lookup_descriptor(" ".join(tokens[i : i + window]))
            if descriptor is None and window == 1:
                # Try comparative/superlative stems for single-token matches.
                for stem in _stem_candidates(tokens[i]):
                    descriptor = lookup_descriptor(stem)
                    if descriptor is not None:
                        break
            if descriptor is not None:
                consumed = window
                break
        if descriptor is None:
            i += 1
            continue

        # Look back for intensifier / negation cues.
        polarity = +1.0
        magnitude = 1.0
        prev = tokens[i - 1] if i > 0 else ""
        if prev in _NEGATION_TOKENS:
            polarity = -1.0
        elif prev == "very":
            magnitude = 1.3
        elif prev in ("slightly", "a-little", "a-bit"):
            magnitude = 0.5
        matches.append((descriptor.label, polarity * magnitude))
        i += consumed
    return matches


def parse_text_to_combined_delta(
    text: str,
    current_features,
    *,
    base_intensity: float = 1.0,
    refs: ReferenceDistributions | None = None,
) -> dict[str, float]:
    """End-to-end: free-text → combined per-feature delta dict.

    Negative-polarity matches (e.g. "less bright") flip onto the descriptor's
    opposite when one is defined, otherwise the delta is negated in place.
    """
    refs = refs or DEFAULT
    parsed = parse_descriptors(text)
    deltas: list[dict[str, float]] = []
    for label, signed in parsed:
        descriptor = VOCABULARY[label]
        if signed >= 0:
            d = descriptor_to_feature_delta(
                label,
                current_features,
                intensity=base_intensity * float(signed),
                refs=refs,
            )
            deltas.append(d)
        else:
            opposite = descriptor.opposite
            mag = base_intensity * float(-signed)
            if opposite and opposite in VOCABULARY:
                d = descriptor_to_feature_delta(
                    opposite, current_features, intensity=mag, refs=refs
                )
            else:
                # No opposite — invert the descriptor's own delta.
                d = descriptor_to_feature_delta(
                    label, current_features, intensity=mag, refs=refs
                )
                d = {k: -v for k, v in d.items()}
            deltas.append(d)
    return combine_deltas(deltas)
