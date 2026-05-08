"""Build the reference feature distributions used by the semantics layer.

Renders ~200 random ``synth_stub`` parameter combinations, extracts the 32-dim
feature vector for each, and persists per-feature samples to
``data/semantics/feature_distributions.json`` so the describer/transforms can
do percentile lookups against an empirical distribution rather than the
hand-tuned fallback.

Run with::

    python -m ableton_mcp.scripts.build_semantics_reference --n 200

Without this file the semantics layer still works — see
``ableton_mcp.semantics.reference_distributions._FALLBACK_PERCENTILES`` for the
hand-tuned values used as a default.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

from ..semantics.reference_distributions import save as save_distributions
from ..semantics.vocabulary import FEATURE_NAMES
from ..sound.features import extract_features
from ..sound.synth_stub import SYNTH_STUB_PARAM_RANGES, synth_render


def render_random_features(n_samples: int, sr: int, dur: float, seed: int) -> dict[str, list[float]]:
    """Render n random synth_stub configs and collect their feature distributions."""
    rng = np.random.default_rng(int(seed))
    distributions: dict[str, list[float]] = {f: [] for f in FEATURE_NAMES}

    for i in range(int(n_samples)):
        params = {
            name: float(rng.uniform(lo, hi))
            for name, (lo, hi) in SYNTH_STUB_PARAM_RANGES.items()
        }
        # Per-render seed so silent edge cases are not perfectly correlated.
        audio = synth_render(params, sr=int(sr), dur=float(dur), seed=int(seed) + i)
        feats = extract_features(audio, sr=int(sr))
        flat = _flatten(feats)
        for f in FEATURE_NAMES:
            distributions[f].append(float(flat.get(f, 0.0)))
    return distributions


def _flatten(features: Any) -> dict[str, float]:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_semantics_reference")
    parser.add_argument("--n", type=int, default=200, help="Number of random renders.")
    parser.add_argument("--sr", type=int, default=22050)
    parser.add_argument("--dur", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Override JSON output path (default: data/semantics/feature_distributions.json).",
    )
    args = parser.parse_args(argv)

    distributions = render_random_features(args.n, args.sr, args.dur, args.seed)
    out_path = Path(args.out).resolve() if args.out else None
    target = save_distributions(distributions, path=out_path)
    print(f"Wrote {target} ({args.n} samples, {len(distributions)} features)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
