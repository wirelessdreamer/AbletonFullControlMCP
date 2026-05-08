"""Tiny built-in descriptor → feature-delta vocabulary.

Used by :mod:`shaping.planner` when the optional ``ableton_mcp.semantics``
package is missing or fails to import. Deliberately small (~25 entries) but
covers the most common shaping vocabulary so the headline UX still works
without the bigger semantic library.

Each entry maps a canonical descriptor (lower-case) to a dict of feature
deltas. Keys are feature-vector names from
:data:`ableton_mcp.sound.features.FEATURE_VECTOR_NAMES`. Values are
*unitless* relative deltas — the planner will scale them by the requested
intensity ({-2, -1, +1, +2}) and combine them additively with the current
features to compute targets.

Conventions:

- ``spectral_centroid``, ``spectral_rolloff`` are in Hz; deltas are absolute.
- ``zcr`` is in [0, 1]; deltas should be small (<= 0.05).
- ``rms`` is in [0, ~1]; deltas should be small (<= 0.1).
- ``spectral_flatness`` is in [0, 1]; deltas should be small.
- ``spectral_bandwidth`` is in Hz.

The deltas are intentionally coarse — the matcher does the actual heavy
lifting, this just nudges the target features in the right direction so kNN
lands somewhere structurally aligned with the user's intent.
"""

from __future__ import annotations

from typing import Mapping


# Per-descriptor feature deltas. Magnitude here ≈ "one step" in the user's
# vocabulary; intensity_modifier multiplies these (-2..+2).
FALLBACK_VOCAB: dict[str, dict[str, float]] = {
    # --- spectral tilt -------------------------------------------------------
    "bright": {
        "spectral_centroid": +1500.0,
        "spectral_rolloff": +2000.0,
        "spectral_bandwidth": +400.0,
    },
    "dark": {
        "spectral_centroid": -1500.0,
        "spectral_rolloff": -2000.0,
        "spectral_bandwidth": -400.0,
    },
    "warm": {
        "spectral_centroid": -800.0,
        "spectral_rolloff": -1000.0,
        "spectral_flatness": -0.05,
    },
    "cold": {
        "spectral_centroid": +600.0,
        "spectral_flatness": +0.04,
    },
    "airy": {
        "spectral_centroid": +1200.0,
        "spectral_rolloff": +2500.0,
        "spectral_flatness": +0.06,
    },
    "shimmery": {
        "spectral_centroid": +1000.0,
        "spectral_rolloff": +2000.0,
        "spectral_flatness": +0.05,
    },
    "hifi": {
        "spectral_centroid": +600.0,
        "spectral_rolloff": +1500.0,
        "spectral_flatness": -0.02,
    },
    "lofi": {
        "spectral_centroid": -600.0,
        "spectral_rolloff": -1500.0,
        "spectral_flatness": +0.06,
    },
    # --- body / weight -------------------------------------------------------
    "thin": {
        "spectral_centroid": +500.0,
        "spectral_bandwidth": -300.0,
        "rms": -0.05,
    },
    "thick": {
        "spectral_centroid": -400.0,
        "spectral_bandwidth": +500.0,
        "rms": +0.05,
    },
    "fat": {
        "spectral_centroid": -500.0,
        "spectral_bandwidth": +600.0,
        "rms": +0.07,
    },
    "lean": {
        "spectral_bandwidth": -400.0,
        "rms": -0.05,
    },
    "boomy": {
        "spectral_centroid": -1200.0,
        "spectral_rolloff": -1500.0,
        "rms": +0.05,
    },
    "full": {
        "spectral_bandwidth": +500.0,
        "rms": +0.05,
    },
    "hollow": {
        "spectral_bandwidth": -400.0,
        "rms": -0.04,
    },
    # --- transient / dynamics -----------------------------------------------
    "punchy": {
        "rms": +0.08,
        "zcr": +0.02,
        "spectral_centroid": +400.0,
    },
    "soft": {
        "rms": -0.05,
        "zcr": -0.01,
        "spectral_centroid": -300.0,
    },
    "tight": {
        "spectral_bandwidth": -300.0,
        "rms": +0.02,
    },
    "loose": {
        "spectral_bandwidth": +300.0,
        "rms": -0.02,
    },
    # --- character -----------------------------------------------------------
    "harsh": {
        "spectral_centroid": +1500.0,
        "spectral_flatness": +0.08,
        "zcr": +0.03,
    },
    "smooth": {
        "spectral_centroid": -400.0,
        "spectral_flatness": -0.04,
        "zcr": -0.02,
    },
    "clear": {
        "spectral_centroid": +600.0,
        "spectral_flatness": -0.04,
    },
    "muddy": {
        "spectral_centroid": -1000.0,
        "spectral_flatness": +0.05,
        "spectral_rolloff": -1500.0,
    },
    "crisp": {
        "spectral_centroid": +1000.0,
        "spectral_rolloff": +1500.0,
        "zcr": +0.02,
    },
    "dirty": {
        "spectral_flatness": +0.07,
        "zcr": +0.03,
    },
    "clean": {
        "spectral_flatness": -0.05,
        "zcr": -0.02,
    },
    "noisy": {
        "spectral_flatness": +0.10,
        "zcr": +0.04,
    },
    "rich": {
        "spectral_bandwidth": +600.0,
        "spectral_flatness": +0.02,
    },
    "sharp": {
        "spectral_centroid": +1200.0,
        "zcr": +0.03,
    },
    "dull": {
        "spectral_centroid": -1200.0,
        "zcr": -0.02,
    },
    "aggressive": {
        "spectral_centroid": +1200.0,
        "spectral_flatness": +0.05,
        "rms": +0.06,
    },
    "mellow": {
        "spectral_centroid": -800.0,
        "spectral_flatness": -0.04,
        "rms": -0.03,
    },
    "vintage": {
        # Vintage gear typically rolls off the very top, adds a touch of grit.
        "spectral_rolloff": -1500.0,
        "spectral_flatness": +0.03,
    },
    "modern": {
        "spectral_centroid": +600.0,
        "spectral_rolloff": +1200.0,
    },
    "edgy": {
        "spectral_centroid": +1000.0,
        "zcr": +0.03,
    },
    "round": {
        "spectral_centroid": -500.0,
        "spectral_flatness": -0.03,
    },
}


def descriptor_to_feature_delta(label: str) -> dict[str, float]:
    """Return the unscaled feature deltas for a descriptor, or ``{}`` if unknown."""
    return dict(FALLBACK_VOCAB.get(label.strip().lower(), {}))


def known_labels() -> tuple[str, ...]:
    return tuple(FALLBACK_VOCAB.keys())


def supports(label: str) -> bool:
    return label.strip().lower() in FALLBACK_VOCAB


def scaled_delta(label: str, intensity_modifier: int) -> dict[str, float]:
    """Return ``descriptor_to_feature_delta(label)`` scaled by ``intensity_modifier``.

    ``intensity_modifier`` is an integer in ``{-2, -1, 0, +1, +2}``.
    """
    base = descriptor_to_feature_delta(label)
    if not base or intensity_modifier == 0:
        return {} if not base else dict(base)
    return {k: float(v) * float(intensity_modifier) for k, v in base.items()}


def combine_deltas(deltas: list[Mapping[str, float]]) -> dict[str, float]:
    """Add up several feature-delta dicts."""
    out: dict[str, float] = {}
    for d in deltas:
        for k, v in d.items():
            out[k] = out.get(k, 0.0) + float(v)
    return out
