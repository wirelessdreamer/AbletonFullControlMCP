"""Tiny numpy/scipy synth used as a stand-in for a real Live device.

Exists so we can prove the Phase 3 sweep → match → recover pipeline end-to-end
without needing AbletonOSC or render-in-the-loop. The matcher and dataset
treat it like any other ``Renderer``.

Signal flow per render call::

    sine(freq) + noise * noise_amount  →  ADSR  →  biquad LP(cutoff, resonance)

Params (all floats; see ``SYNTH_STUB_PARAM_RANGES`` for legal values):
    freq          Hz, fundamental of the sine osc
    attack        sec
    decay         sec
    sustain       0..1, sustain level relative to peak
    release       sec, applied at the very end of the buffer
    cutoff        Hz, biquad LP cutoff
    resonance     biquad Q (>0)
    noise_amount  0..1, white-noise mix relative to sine peak
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
from scipy.signal import lfilter

# Sensible default ranges. The sweep planner samples within these.
SYNTH_STUB_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "freq": (80.0, 1200.0),
    "attack": (0.001, 0.5),
    "decay": (0.01, 0.6),
    "sustain": (0.0, 1.0),
    "release": (0.01, 0.5),
    "cutoff": (200.0, 8000.0),
    "resonance": (0.5, 6.0),
    "noise_amount": (0.0, 0.5),
}

SYNTH_STUB_DEFAULTS: dict[str, float] = {
    "freq": 220.0,
    "attack": 0.01,
    "decay": 0.1,
    "sustain": 0.7,
    "release": 0.1,
    "cutoff": 2000.0,
    "resonance": 1.0,
    "noise_amount": 0.0,
}


def _adsr(n: int, sr: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    """Build an ADSR envelope of length ``n`` samples."""
    sustain = float(np.clip(sustain, 0.0, 1.0))
    a = max(0, int(attack * sr))
    d = max(0, int(decay * sr))
    r = max(0, int(release * sr))
    if a + d + r >= n:
        # Squash proportionally so the envelope still fits the buffer.
        scale = (n - 1) / max(1, (a + d + r))
        a, d, r = int(a * scale), int(d * scale), int(r * scale)
    s = max(0, n - a - d - r)

    env = np.zeros(n, dtype=np.float32)
    idx = 0
    if a > 0:
        env[idx : idx + a] = np.linspace(0.0, 1.0, a, endpoint=False, dtype=np.float32)
        idx += a
    if d > 0:
        env[idx : idx + d] = np.linspace(1.0, sustain, d, endpoint=False, dtype=np.float32)
        idx += d
    if s > 0:
        env[idx : idx + s] = sustain
        idx += s
    if r > 0:
        end = min(idx + r, n)
        env[idx:end] = np.linspace(sustain, 0.0, end - idx, endpoint=True, dtype=np.float32)
        idx = end
    return env


def _biquad_lowpass(x: np.ndarray, sr: int, cutoff: float, q: float) -> np.ndarray:
    """RBJ biquad lowpass — handles cutoff clamping at ~Nyquist for us."""
    cutoff = float(np.clip(cutoff, 20.0, sr * 0.49))
    q = float(max(q, 1e-3))
    w0 = 2.0 * np.pi * cutoff / sr
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = (1.0 - cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return lfilter(b, a, x.astype(np.float64)).astype(np.float32)


def _resolve_params(params: Mapping[str, float]) -> dict[str, float]:
    """Fill in defaults and clamp to legal ranges."""
    out: dict[str, float] = dict(SYNTH_STUB_DEFAULTS)
    out.update({k: float(v) for k, v in params.items() if k in SYNTH_STUB_DEFAULTS})
    for name, (lo, hi) in SYNTH_STUB_PARAM_RANGES.items():
        out[name] = float(np.clip(out[name], lo, hi))
    return out


def synth_render(
    params: Mapping[str, float],
    sr: int = 22050,
    dur: float = 2.0,
    seed: int | None = 0,
) -> np.ndarray:
    """Render the synth stub with the given params and return mono float32 audio.

    Deterministic given ``seed`` — useful so kNN tests can render the "truth"
    twice and get exactly matching features.
    """
    p = _resolve_params(params)
    n = max(1, int(round(dur * sr)))
    t = np.arange(n, dtype=np.float32) / float(sr)

    sine = np.sin(2.0 * np.pi * p["freq"] * t).astype(np.float32)
    rng = np.random.default_rng(seed if seed is not None else 0)
    noise = rng.standard_normal(n).astype(np.float32)

    env = _adsr(n, sr, p["attack"], p["decay"], p["sustain"], p["release"])
    raw = sine + p["noise_amount"] * noise
    voiced = raw * env
    filtered = _biquad_lowpass(voiced, sr, p["cutoff"], p["resonance"])

    # Normalise to -3 dBFS so feature extraction lives in a stable amplitude band.
    peak = float(np.max(np.abs(filtered))) or 1.0
    return (filtered * (0.7 / peak)).astype(np.float32)
