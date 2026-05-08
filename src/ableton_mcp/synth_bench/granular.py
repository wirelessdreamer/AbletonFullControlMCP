"""Granular synth — chops a procedurally generated source into windowed grains.

The "source" is a deterministic mix of detuned sines (so we own it without
needing a sample file). Grains of length ``grain_size_ms`` are scattered
across the buffer with controllable density, position spread, pitch jitter,
and an envelope-shaped read window.
"""

from __future__ import annotations

import numpy as np

from .base import BenchSynthRenderer

_SOURCE_SECONDS = 4.0  # length of the procedural source we sample from
_SOURCE_SR = 22050


def _build_source() -> np.ndarray:
    sr = _SOURCE_SR
    n = int(_SOURCE_SECONDS * sr)
    t = np.arange(n, dtype=np.float32) / float(sr)
    # A bright pad of detuned harmonic sines + slow noise.
    base = 220.0
    sig = np.zeros(n, dtype=np.float32)
    for k, det in enumerate([0.0, 7.0, 12.0, 19.0]):
        sig += (1.0 / (k + 1)) * np.sin(2.0 * np.pi * (base * (2 ** (det / 12.0))) * t)
    rng = np.random.default_rng(123)
    sig += 0.05 * rng.standard_normal(n).astype(np.float32)
    sig /= max(1e-6, float(np.max(np.abs(sig))))
    return sig.astype(np.float32)


_SOURCE = _build_source()


class GranularRenderer(BenchSynthRenderer):
    """Granular resynthesis from a fixed procedural source."""

    PARAM_RANGES = {
        "grain_size_ms": (5.0, 200.0),
        "density": (5.0, 80.0),         # grains/sec
        "position": (0.0, 1.0),         # position within source [0..1]
        "position_jitter": (0.0, 0.5),  # +/- jitter as fraction of source
        "pitch": (0.5, 2.0),            # playback rate
        "pitch_jitter": (0.0, 0.5),     # +/- semitones jitter
        "spread": (0.0, 1.0),           # 0=center, 1=full random pan (here: amp jitter)
        "attack": (0.001, 0.5),         # global amp attack
        "release": (0.01, 1.0),         # global amp release
    }
    PARAM_DEFAULTS = {
        "grain_size_ms": 50.0,
        "density": 25.0,
        "position": 0.5,
        "position_jitter": 0.1,
        "pitch": 1.0,
        "pitch_jitter": 0.0,
        "spread": 0.2,
        "attack": 0.05,
        "release": 0.4,
    }

    def _render(self, p: dict[str, float]) -> np.ndarray:
        sr = self.sample_rate
        n = self._n_samples()
        rng = self._rng()

        grain_n = max(2, int(round(p["grain_size_ms"] * 1e-3 * sr)))
        density = max(1.0, float(p["density"]))
        n_grains = max(1, int(round(density * self.duration_sec)))

        out = np.zeros(n + grain_n + 8, dtype=np.float32)

        # Hann window for grain envelope.
        window = (0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(grain_n) / max(1, grain_n - 1))))
        window = window.astype(np.float32)

        src = _SOURCE
        src_len = src.shape[0]
        pos_base = float(np.clip(p["position"], 0.0, 1.0))
        pos_jit = float(np.clip(p["position_jitter"], 0.0, 0.5))
        pitch = float(np.clip(p["pitch"], 0.25, 4.0))
        pitch_jit = float(np.clip(p["pitch_jitter"], 0.0, 1.0))  # in semitones-equivalent
        spread = float(np.clip(p["spread"], 0.0, 1.0))

        for _ in range(n_grains):
            # When does the grain start in the output buffer?
            t0 = int(rng.integers(0, max(1, n)))
            # Where in the source do we read from?
            src_pos = pos_base + (rng.uniform(-1.0, 1.0) * pos_jit)
            src_pos = float(np.clip(src_pos, 0.0, 1.0))
            src_start = int(src_pos * (src_len - grain_n - 4))
            # Pitched read with linear interp.
            this_pitch = pitch * (2.0 ** (rng.uniform(-pitch_jit, pitch_jit) / 12.0))
            read_idx = src_start + np.arange(grain_n, dtype=np.float32) * this_pitch
            read_idx = np.clip(read_idx, 0, src_len - 2)
            i0 = read_idx.astype(np.int64)
            frac = (read_idx - i0).astype(np.float32)
            grain = ((1.0 - frac) * src[i0] + frac * src[i0 + 1]).astype(np.float32)
            grain *= window
            # Amplitude jitter mimics stereo spread in mono.
            amp = 1.0 - spread * float(rng.uniform(0.0, 1.0))
            end = t0 + grain_n
            out[t0:end] += amp * grain

        out = out[:n]
        # Soft global attack/release shape on top.
        a = max(1, int(p["attack"] * sr))
        r = max(1, int(p["release"] * sr))
        env = np.ones(n, dtype=np.float32)
        if a < n:
            env[:a] = np.linspace(0.0, 1.0, a, dtype=np.float32)
        if r < n:
            env[-r:] *= np.linspace(1.0, 0.0, r, dtype=np.float32)
        return (out * env).astype(np.float32)
