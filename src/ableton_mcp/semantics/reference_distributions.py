"""Reference percentile tables for audio features.

The describer + transforms layer needs a sense of "what value of
spectral_centroid counts as bright?" — a percentile predicate is only
meaningful relative to a reference distribution.

If ``data/semantics/feature_distributions.json`` exists (built by
``scripts/build_semantics_reference.py`` from a synthetic ``synth_stub``
sweep) we use it. Otherwise we fall back to a hand-tuned table that
roughly matches the dynamic range produced by the synth stub at 22.05 kHz.

The fallback exists so the semantics layer works out-of-the-box without
requiring the user to run the build script first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Hand-tuned percentile tables. Keys are percentile (0..100), values are the
# corresponding feature value. Picked to match the rough output of the
# ``synth_stub`` synth across its full param ranges at sr=22050.
#
# These are the "bones" of the semantics layer — accurate enough that
# "centroid above the 65th percentile" reliably maps to "bright" without
# any pre-built reference data, while still being trumpable by a real
# probe-based distribution from disk.
_FALLBACK_PERCENTILES: dict[str, dict[float, float]] = {
    "spectral_centroid": {0.0: 200.0, 0.1: 500.0, 0.25: 900.0, 0.5: 1800.0, 0.75: 3200.0, 0.9: 5000.0, 1.0: 7500.0},
    "spectral_bandwidth": {0.0: 200.0, 0.1: 500.0, 0.25: 900.0, 0.5: 1500.0, 0.75: 2400.0, 0.9: 3600.0, 1.0: 5000.0},
    "spectral_rolloff": {0.0: 400.0, 0.1: 1000.0, 0.25: 2000.0, 0.5: 4000.0, 0.75: 6500.0, 0.9: 9000.0, 1.0: 11000.0},
    "zcr": {0.0: 0.0, 0.1: 0.02, 0.25: 0.04, 0.5: 0.08, 0.75: 0.15, 0.9: 0.25, 1.0: 0.5},
    "rms": {0.0: 0.0, 0.1: 0.05, 0.25: 0.1, 0.5: 0.2, 0.75: 0.35, 0.9: 0.5, 1.0: 0.7},
    "spectral_flatness": {0.0: 0.0, 0.1: 0.005, 0.25: 0.02, 0.5: 0.05, 0.75: 0.12, 0.9: 0.25, 1.0: 0.5},
    # MFCC coefficients are trickier — these are coarse but good enough as a
    # default. Mean_0 is roughly proportional to log-energy; means 1-4 capture
    # spectral slope. Std reflects time variation.
    "mfcc_mean_0": {0.0: -600.0, 0.1: -450.0, 0.25: -350.0, 0.5: -250.0, 0.75: -150.0, 0.9: -80.0, 1.0: 0.0},
    "mfcc_mean_1": {0.0: -50.0, 0.1: -20.0, 0.25: 0.0, 0.5: 30.0, 0.75: 60.0, 0.9: 100.0, 1.0: 150.0},
    "mfcc_mean_2": {0.0: -100.0, 0.1: -50.0, 0.25: -20.0, 0.5: 0.0, 0.75: 30.0, 0.9: 60.0, 1.0: 100.0},
    "mfcc_mean_3": {0.0: -80.0, 0.1: -40.0, 0.25: -15.0, 0.5: 0.0, 0.75: 20.0, 0.9: 45.0, 1.0: 80.0},
    "mfcc_mean_4": {0.0: -60.0, 0.1: -30.0, 0.25: -10.0, 0.5: 5.0, 0.75: 20.0, 0.9: 40.0, 1.0: 70.0},
    "mfcc_std_0": {0.0: 0.0, 0.1: 5.0, 0.25: 15.0, 0.5: 30.0, 0.75: 55.0, 0.9: 90.0, 1.0: 150.0},
    "mfcc_std_1": {0.0: 0.0, 0.1: 3.0, 0.25: 8.0, 0.5: 18.0, 0.75: 35.0, 0.9: 55.0, 1.0: 90.0},
    "mfcc_std_2": {0.0: 0.0, 0.1: 2.0, 0.25: 6.0, 0.5: 12.0, 0.75: 22.0, 0.9: 35.0, 1.0: 60.0},
}


def _default_path() -> Path:
    """Repo-relative path to the optional persisted reference distributions JSON."""
    # ``__file__`` lives at src/ableton_mcp/semantics/reference_distributions.py.
    # Three parents up from there is the repo root.
    return Path(__file__).resolve().parents[3] / "data" / "semantics" / "feature_distributions.json"


@dataclass
class FeatureDistribution:
    """Sorted samples of a single feature dimension."""

    feature: str
    sorted_values: np.ndarray  # ascending

    def percentile(self, p: float) -> float:
        """Return the value at percentile ``p`` (0..1) using linear interpolation."""
        if self.sorted_values.size == 0:
            return 0.0
        p = float(np.clip(p, 0.0, 1.0))
        return float(np.quantile(self.sorted_values, p))

    def value_to_percentile(self, value: float) -> float:
        """Inverse — return where ``value`` lies as a fraction of the distribution."""
        if self.sorted_values.size == 0:
            return 0.5
        # searchsorted returns the insertion point; divide to get a fraction in [0,1].
        idx = int(np.searchsorted(self.sorted_values, float(value), side="right"))
        return float(np.clip(idx / float(self.sorted_values.size), 0.0, 1.0))


@dataclass
class _FallbackDistribution:
    """Reads the hand-tuned ``_FALLBACK_PERCENTILES`` table for a feature."""

    feature: str

    def _table(self) -> list[tuple[float, float]]:
        rows = _FALLBACK_PERCENTILES.get(self.feature)
        if not rows:
            return [(0.0, 0.0), (1.0, 1.0)]  # neutral default
        return sorted(rows.items())

    def percentile(self, p: float) -> float:
        p = float(np.clip(p, 0.0, 1.0))
        rows = self._table()
        ps = np.array([r[0] for r in rows])
        vs = np.array([r[1] for r in rows])
        return float(np.interp(p, ps, vs))

    def value_to_percentile(self, value: float) -> float:
        rows = self._table()
        ps = np.array([r[0] for r in rows])
        vs = np.array([r[1] for r in rows])
        # Inverse interpolation along the value axis. Handle non-monotone defensively.
        order = np.argsort(vs)
        return float(np.clip(np.interp(value, vs[order], ps[order]), 0.0, 1.0))


_DistType = FeatureDistribution | _FallbackDistribution


class ReferenceDistributions:
    """Container for per-feature distributions; falls back when data is missing."""

    def __init__(self, distributions: dict[str, _DistType]) -> None:
        self._d = distributions

    def get(self, feature: str) -> _DistType:
        d = self._d.get(feature)
        if d is None:
            d = _FallbackDistribution(feature)
            self._d[feature] = d
        return d

    def percentile(self, feature: str, p: float) -> float:
        return self.get(feature).percentile(p)

    def value_to_percentile(self, feature: str, value: float) -> float:
        return self.get(feature).value_to_percentile(value)

    def features(self) -> list[str]:
        return sorted(self._d.keys())


def load(path: Path | None = None) -> ReferenceDistributions:
    """Load reference distributions from JSON, falling back per-feature if absent.

    The JSON layout is ``{feature_name: [v1, v2, ...]}`` where the values are
    raw samples (we sort them on load).  Missing features fall through to the
    hardcoded table — so partial JSONs are OK.
    """
    target = path or _default_path()
    distributions: dict[str, _DistType] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover — malformed JSON falls through
            data = {}
        for feature, values in data.items():
            if not isinstance(values, list) or not values:
                continue
            arr = np.asarray(values, dtype=np.float64)
            arr.sort()
            distributions[feature] = FeatureDistribution(feature=feature, sorted_values=arr)
    # Anything not in the file uses the fallback at lookup time.
    return ReferenceDistributions(distributions)


def save(distributions: dict[str, list[float]], path: Path | None = None) -> Path:
    """Persist distributions JSON for later loads."""
    target = path or _default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {k: [float(x) for x in v] for k, v in distributions.items()}
    target.write_text(json.dumps(serialisable, indent=2), encoding="utf-8")
    return target


# Module-level singleton — the describer/transforms call ``DEFAULT.get(...)``
# everywhere so the persisted-or-fallback decision is centralised.
DEFAULT: ReferenceDistributions = load()
