"""Additive synth — sum of N harmonic sines.

8 partial amplitudes plus a global tilt that biases toward bright/dark by
exponentiating partial number. Spectral_centroid moves cleanly with
``tilt`` and ``partial*_amp`` so it's a great test surface for
parameter-explainer tooling.
"""

from __future__ import annotations

import numpy as np

from .base import BenchSynthRenderer, adsr_envelope

N_PARTIALS = 8


class AdditiveRenderer(BenchSynthRenderer):
    """Sum of 8 harmonic sines with per-partial amplitudes + spectral tilt."""

    PARAM_RANGES = {
        "freq": (40.0, 1500.0),
        "tilt": (-3.0, 3.0),  # +ve = brighter, -ve = darker (multiplies partial weights by k**tilt)
        "attack": (0.001, 1.0),
        "decay": (0.01, 1.0),
        "sustain": (0.0, 1.0),
        "release": (0.01, 1.0),
        "partial1_amp": (0.0, 1.0),
        "partial2_amp": (0.0, 1.0),
        "partial3_amp": (0.0, 1.0),
        "partial4_amp": (0.0, 1.0),
        "partial5_amp": (0.0, 1.0),
        "partial6_amp": (0.0, 1.0),
        "partial7_amp": (0.0, 1.0),
        "partial8_amp": (0.0, 1.0),
        "even_odd_balance": (-1.0, 1.0),  # -1 only odd, +1 only even, 0 = both
    }
    PARAM_DEFAULTS = {
        "freq": 220.0,
        "tilt": -1.0,
        "attack": 0.01,
        "decay": 0.4,
        "sustain": 0.6,
        "release": 0.2,
        "partial1_amp": 1.0,
        "partial2_amp": 0.7,
        "partial3_amp": 0.5,
        "partial4_amp": 0.4,
        "partial5_amp": 0.3,
        "partial6_amp": 0.2,
        "partial7_amp": 0.15,
        "partial8_amp": 0.1,
        "even_odd_balance": 0.0,
    }

    def _render(self, p: dict[str, float]) -> np.ndarray:
        sr = self.sample_rate
        n = self._n_samples()
        t = self._time_axis()

        amps = np.array(
            [p[f"partial{i + 1}_amp"] for i in range(N_PARTIALS)], dtype=np.float32
        )
        tilt = float(p["tilt"])
        balance = float(np.clip(p["even_odd_balance"], -1.0, 1.0))

        out = np.zeros(n, dtype=np.float32)
        f0 = float(p["freq"])
        nyq = sr * 0.5
        for k in range(1, N_PARTIALS + 1):
            f_k = f0 * k
            if f_k >= nyq:
                break
            # tilt: each successive partial scaled by k**tilt (negative = darker)
            tilt_w = float(k ** tilt)
            # even/odd balance
            if (k % 2) == 0:  # even
                eo_w = 1.0 + balance  # at +1 → 2.0
            else:  # odd
                eo_w = 1.0 - balance  # at +1 → 0.0
            eo_w = max(0.0, eo_w)
            w = amps[k - 1] * tilt_w * eo_w
            out += w * np.sin(2.0 * np.pi * f_k * t).astype(np.float32)

        env = adsr_envelope(n, sr, p["attack"], p["decay"], p["sustain"], p["release"])
        return (out * env).astype(np.float32)
