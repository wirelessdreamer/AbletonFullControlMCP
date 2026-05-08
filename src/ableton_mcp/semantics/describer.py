"""Map raw audio features to ranked natural-language descriptors.

Given a :class:`Features` instance (the 32-dim fingerprint produced by
``sound.features.extract_features``), this module evaluates every entry of
:data:`vocabulary.VOCABULARY` and returns a ranked list of
``(descriptor_label, confidence)`` tuples.

Confidence is in [0, 1] — 0 means "not at all"; 1 means "this descriptor's
anchors are unambiguously satisfied". The describer combines per-anchor
"satisfaction" scores (how far past the threshold the feature is) and weights
to produce one number per descriptor.
"""

from __future__ import annotations

from typing import Iterable

from ..sound.features import Features
from .reference_distributions import DEFAULT, ReferenceDistributions
from .vocabulary import Descriptor, FeatureAnchor, VOCABULARY


def _features_to_dict(features: Features) -> dict[str, float]:
    """Flatten a :class:`Features` to the same names FeatureAnchor uses."""
    out: dict[str, float] = {
        "spectral_centroid": float(features.spectral_centroid),
        "spectral_bandwidth": float(features.spectral_bandwidth),
        "spectral_rolloff": float(features.spectral_rolloff),
        "zcr": float(features.zcr),
        "rms": float(features.rms),
        "spectral_flatness": float(features.spectral_flatness),
    }
    for i, v in enumerate(features.mfcc_mean):
        out[f"mfcc_mean_{i}"] = float(v)
    for i, v in enumerate(features.mfcc_std):
        out[f"mfcc_std_{i}"] = float(v)
    return out


def _smooth_step(x: float) -> float:
    """Map [0,1] → [0,1] with a soft S-curve so confidences don't saturate at the edges."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def _anchor_score(
    anchor: FeatureAnchor,
    features: dict[str, float],
    refs: ReferenceDistributions,
) -> float:
    """Return how strongly an anchor is satisfied (0..1) for the given features.

    For ``high``/``low`` we map the raw value to a percentile via the reference
    distribution and then test against the anchor threshold.  For
    ``percentile_above``/``percentile_below`` we compare the value's percentile
    directly to the anchor threshold (0..1).
    """
    value = features.get(anchor.feature)
    if value is None:
        return 0.0

    if anchor.predicate == "high":
        # Fraction of how far the value sits past the absolute threshold,
        # scaled by the spread of the reference distribution to [0,1].
        ref_lo = refs.percentile(anchor.feature, 0.0)
        ref_hi = refs.percentile(anchor.feature, 1.0)
        span = max(ref_hi - anchor.threshold, 1e-9)
        frac = (value - anchor.threshold) / span
        # Clamp/scale: 0 when value <= threshold, 1 when at top of range.
        return _smooth_step(max(0.0, min(1.0, frac)))

    if anchor.predicate == "low":
        ref_lo = refs.percentile(anchor.feature, 0.0)
        ref_hi = refs.percentile(anchor.feature, 1.0)
        span = max(anchor.threshold - ref_lo, 1e-9)
        frac = (anchor.threshold - value) / span
        return _smooth_step(max(0.0, min(1.0, frac)))

    if anchor.predicate == "percentile_above":
        pct = refs.value_to_percentile(anchor.feature, value)
        # Soft margin around the threshold so confidence ramps rather than flips.
        excess = (pct - anchor.threshold) / max(1.0 - anchor.threshold, 1e-3)
        return _smooth_step(max(0.0, min(1.0, excess)))

    if anchor.predicate == "percentile_below":
        pct = refs.value_to_percentile(anchor.feature, value)
        excess = (anchor.threshold - pct) / max(anchor.threshold, 1e-3)
        return _smooth_step(max(0.0, min(1.0, excess)))

    return 0.0  # pragma: no cover — predicate is validated at construction time


def _descriptor_confidence(
    descriptor: Descriptor,
    features: dict[str, float],
    refs: ReferenceDistributions,
) -> float:
    """Weighted average of anchor scores."""
    if not descriptor.feature_anchors:  # pragma: no cover — guarded by Descriptor
        return 0.0
    total_weight = 0.0
    total_score = 0.0
    for anchor in descriptor.feature_anchors:
        score = _anchor_score(anchor, features, refs)
        total_score += score * anchor.weight
        total_weight += anchor.weight
    if total_weight <= 0.0:  # pragma: no cover
        return 0.0
    return float(total_score / total_weight)


def describe_features(
    features: Features,
    *,
    top_k: int | None = None,
    min_confidence: float = 0.05,
    descriptors: Iterable[Descriptor] | None = None,
    refs: ReferenceDistributions | None = None,
) -> list[tuple[str, float]]:
    """Rank descriptors by how well their feature anchors fit ``features``.

    Returns descriptors sorted by descending confidence (ties broken by label).
    Filters out anything below ``min_confidence``. ``top_k=None`` returns all
    matches; pass an int to truncate.
    """
    refs = refs or DEFAULT
    feat_dict = _features_to_dict(features)
    pool = list(descriptors) if descriptors is not None else list(VOCABULARY.values())

    scored: list[tuple[str, float]] = []
    for d in pool:
        c = _descriptor_confidence(d, feat_dict, refs)
        if c >= min_confidence:
            scored.append((d.label, float(c)))

    scored.sort(key=lambda x: (-x[1], x[0]))
    if top_k is not None:
        scored = scored[: int(top_k)]
    return scored
