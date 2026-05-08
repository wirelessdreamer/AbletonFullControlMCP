"""Translate a parsed :class:`ShapeRequest` + current features into target features.

Given the user's *current* sound (a :class:`Features` or feature-vector or
the dict returned by ``Features.to_dict()``) and a parsed
:class:`ShapeRequest`, produce a ``{feature_name: target_value}`` dict that
the matcher can then search for in the probe dataset.

Two delta sources, in order of preference:

1. ``ableton_mcp.semantics.transforms.descriptor_to_feature_delta`` if the
   ``semantics`` package is importable. Agent 2 owns that package.
2. The hardcoded :mod:`shaping.fallback_vocab`.

The :func:`semantics_source` helper lets callers stamp their response with
``semantics_source: 'package' | 'fallback'`` so users can see which
vocabulary backed the recommendation.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import numpy as np

from ..sound.features import (
    FEATURE_VECTOR_NAMES,
    Features,
    feature_vector,
)
from . import fallback_vocab
from .parser import ShapeRequest

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional semantics package wiring
# ---------------------------------------------------------------------------


def _try_import_semantics() -> Any:
    """Attempt to import the optional ``ableton_mcp.semantics`` package.

    Returns the ``transforms`` module if available, else ``None``. Catches
    every Exception subclass so a partial / broken semantics package doesn't
    break the headline shaping flow. The module must expose a usable
    ``descriptor_to_feature_delta`` callable, otherwise we treat it as
    missing and fall back.
    """
    try:
        from ..semantics import transforms as _transforms  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover — best-effort
        log.debug("semantics package unavailable, using fallback vocab: %r", exc)
        return None
    if not hasattr(_transforms, "descriptor_to_feature_delta"):
        return None
    return _transforms


def semantics_source() -> str:
    """Return ``'package'`` if the semantics package loaded, else ``'fallback'``."""
    return "package" if _try_import_semantics() is not None else "fallback"


# Map our parser's integer intensity {-2..+2} onto the semantics package's
# intensity scale (0..1). Polarity is preserved so "less bright" = -ve magnitude.
_SEMANTICS_INTENSITY_BY_MAGNITUDE = {0: 0.0, 1: 0.5, 2: 1.0}


def _semantics_delta(transforms: Any, label: str, intensity: int, current: dict[str, float]) -> dict[str, float]:
    """Convert the semantics package's *relative* deltas into absolute ones.

    The semantics package returns ``{feature: relative_delta}`` where relative
    is a fraction of the current value (e.g. +0.25 = +25%). We multiply by the
    current value here so the rest of the planner can treat both code paths
    uniformly (additive deltas in absolute units).
    """
    magnitude = _SEMANTICS_INTENSITY_BY_MAGNITUDE.get(abs(int(intensity)), 1.0)
    polarity = -1.0 if intensity < 0 else 1.0
    try:
        rel = transforms.descriptor_to_feature_delta(label, current, intensity=magnitude)
    except KeyError:
        return {}
    except Exception as exc:  # pragma: no cover
        log.debug("semantics.descriptor_to_feature_delta(%r) failed: %r", label, exc)
        return {}
    out: dict[str, float] = {}
    for feature, rel_delta in rel.items():
        signed = float(rel_delta) * polarity
        cur = float(current.get(feature, 0.0))
        # Convert relative -> absolute. If the current value is zero (e.g. silence
        # came in or the feature isn't measured), fall back to a reasonable scale
        # so the delta still has *some* nudge in the right direction.
        if abs(cur) < 1e-6:
            scale = _ABSOLUTE_SCALE_HINTS.get(feature, 1.0)
            out[feature] = signed * scale
        else:
            out[feature] = signed * cur
    return out


# Sensible default magnitudes for each scalar feature, used when the current
# value is ~0 and we still want a reasonable absolute delta.
_ABSOLUTE_SCALE_HINTS: dict[str, float] = {
    "spectral_centroid": 2000.0,
    "spectral_bandwidth": 1500.0,
    "spectral_rolloff": 4000.0,
    "zcr": 0.05,
    "rms": 0.1,
    "spectral_flatness": 0.1,
}


def _delta_for(label: str, intensity: int, current: dict[str, float]) -> dict[str, float]:
    """Get the per-descriptor feature delta for one (label, intensity) pair."""
    transforms = _try_import_semantics()
    if transforms is not None:
        delta = _semantics_delta(transforms, label, intensity, current)
        if delta:
            return delta
        # Empty delta means the semantics package didn't recognise the label —
        # try the fallback vocab in case we have a hardcoded mapping for it.
    return fallback_vocab.scaled_delta(label, intensity)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def features_to_dict(features: Features | Mapping[str, Any] | np.ndarray) -> dict[str, float]:
    """Coerce a :class:`Features`, dict, or feature_vector into a flat name→float dict.

    Only includes the *scalar* dims that the shaping vocabulary cares about
    (centroid / bandwidth / rolloff / zcr / rms / flatness). MFCC mean/std
    are passed through untouched if a feature_vector is supplied so the
    resulting dict round-trips faithfully.
    """
    if isinstance(features, Features):
        return _features_dataclass_to_dict(features)
    if isinstance(features, np.ndarray):
        return _vector_to_dict(features)
    if isinstance(features, Mapping):
        if "mfcc_mean" in features and "spectral_centroid" in features:
            # Looks like Features.to_dict() output.
            out = _features_dict_to_flat(features)
            return out
        # Treat as already flat.
        return {k: float(v) for k, v in features.items() if isinstance(v, (int, float))}
    raise TypeError(f"unsupported features input type: {type(features)!r}")


def _features_dataclass_to_dict(f: Features) -> dict[str, float]:
    out: dict[str, float] = {
        "spectral_centroid": float(f.spectral_centroid),
        "spectral_bandwidth": float(f.spectral_bandwidth),
        "spectral_rolloff": float(f.spectral_rolloff),
        "zcr": float(f.zcr),
        "rms": float(f.rms),
        "spectral_flatness": float(f.spectral_flatness),
    }
    for i, v in enumerate(np.asarray(f.mfcc_mean, dtype=np.float64).tolist()):
        out[f"mfcc_mean_{i}"] = float(v)
    for i, v in enumerate(np.asarray(f.mfcc_std, dtype=np.float64).tolist()):
        out[f"mfcc_std_{i}"] = float(v)
    return out


def _features_dict_to_flat(d: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for scalar_name in (
        "spectral_centroid",
        "spectral_bandwidth",
        "spectral_rolloff",
        "zcr",
        "rms",
        "spectral_flatness",
    ):
        if scalar_name in d:
            out[scalar_name] = float(d[scalar_name])
    for key in ("mfcc_mean", "mfcc_std"):
        seq = d.get(key)
        if seq is None:
            continue
        for i, v in enumerate(seq):
            out[f"{key}_{i}"] = float(v)
    return out


def _vector_to_dict(vec: np.ndarray) -> dict[str, float]:
    flat = np.asarray(vec, dtype=np.float64).ravel()
    out: dict[str, float] = {}
    for i, name in enumerate(FEATURE_VECTOR_NAMES):
        if i < flat.shape[0]:
            out[name] = float(flat[i])
    return out


# ---------------------------------------------------------------------------
# Specific-target reconciliation
# ---------------------------------------------------------------------------


_SPECIFIC_TARGET_FEATURE_MAP = {
    "centroid": "spectral_centroid",
    "bandwidth": "spectral_bandwidth",
    "rolloff": "spectral_rolloff",
    "rms": "rms",
    "zcr": "zcr",
    "flatness": "spectral_flatness",
    "brightness": "spectral_centroid",
}


def _apply_specific_targets(
    targets: dict[str, float], specific: list[dict], current: dict[str, float]
) -> None:
    """Mutate ``targets`` with overrides from explicit "centroid above 3000Hz" clauses."""
    for spec in specific:
        feature = _SPECIFIC_TARGET_FEATURE_MAP.get(str(spec.get("feature", "")).lower())
        if not feature:
            continue
        try:
            value = float(spec.get("value"))
        except (TypeError, ValueError):
            continue
        comparator = spec.get("comparator", "=")
        cur = float(current.get(feature, value))
        if comparator == "<":
            # Pick a target slightly below the threshold.
            targets[feature] = min(value * 0.9, cur)
        elif comparator == ">":
            targets[feature] = max(value * 1.1, cur)
        else:  # "=", "~"
            targets[feature] = value


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def plan_target_features(
    current_features: Features | Mapping[str, Any] | np.ndarray,
    shape_request: ShapeRequest,
) -> dict[str, float]:
    """Compute target feature values from the user's current sound + intent.

    The output dict is keyed by feature names from
    :data:`FEATURE_VECTOR_NAMES`. Features the request doesn't touch are
    carried over unchanged from ``current_features``.
    """
    base = features_to_dict(current_features)
    targets: dict[str, float] = dict(base)

    # Apply each descriptor's scaled delta on top of the current features.
    for label, intensity in shape_request.descriptors:
        delta = _delta_for(label, int(intensity), base)
        for feature_name, dv in delta.items():
            targets[feature_name] = float(targets.get(feature_name, 0.0)) + float(dv)

    # Specific overrides ("centroid above 3000Hz") take precedence.
    if shape_request.targets_specific:
        _apply_specific_targets(targets, shape_request.targets_specific, base)

    # Sanity clamp the bounded features back into legal ranges.
    targets["zcr"] = float(np.clip(targets.get("zcr", 0.0), 0.0, 1.0))
    targets["rms"] = float(max(0.0, targets.get("rms", 0.0)))
    targets["spectral_flatness"] = float(np.clip(targets.get("spectral_flatness", 0.0), 0.0, 1.0))
    targets["spectral_centroid"] = float(max(0.0, targets.get("spectral_centroid", 0.0)))
    targets["spectral_bandwidth"] = float(max(0.0, targets.get("spectral_bandwidth", 0.0)))
    targets["spectral_rolloff"] = float(max(0.0, targets.get("spectral_rolloff", 0.0)))

    return targets


def targets_to_feature_vector(
    targets: Mapping[str, float],
    current_features: Features | Mapping[str, Any] | np.ndarray,
) -> np.ndarray:
    """Build a length-``FEATURE_VECTOR_DIM`` array from ``targets``.

    Names not present in ``targets`` are filled from ``current_features`` so
    the resulting vector is safe to feed straight into ``find_nearest``.
    """
    base = features_to_dict(current_features)
    merged = dict(base)
    merged.update(targets)
    out = np.zeros(len(FEATURE_VECTOR_NAMES), dtype=np.float32)
    for i, name in enumerate(FEATURE_VECTOR_NAMES):
        out[i] = float(merged.get(name, 0.0))
    return out


def feature_vector_from_features(features: Features | np.ndarray) -> np.ndarray:
    """Resolve any ``Features``/array input to a feature vector."""
    if isinstance(features, Features):
        return feature_vector(features)
    arr = np.asarray(features, dtype=np.float32).ravel()
    return arr
