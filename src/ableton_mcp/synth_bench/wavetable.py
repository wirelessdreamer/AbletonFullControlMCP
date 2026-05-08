"""Morphing wavetable synth — Wavetable family.

Four built-in single-cycle tables (sine, saw, square, noise). The ``position``
param morphs between two of the four tables (controlled by ``table_a`` and
``table_b``). A simple lowpass applied for tone shaping.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .base import BenchSynthRenderer, adsr_envelope

_WT_LEN = 1024


def _build_wavetables() -> np.ndarray:
    """Return (4, _WT_LEN) array of single-cycle tables."""
    phase = np.linspace(0.0, 1.0, _WT_LEN, endpoint=False, dtype=np.float32)
    sine = np.sin(2.0 * np.pi * phase).astype(np.float32)
    saw = (2.0 * phase - 1.0).astype(np.float32)
    square = np.where(phase < 0.5, 1.0, -1.0).astype(np.float32)
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(_WT_LEN).astype(np.float32)
    noise /= max(1e-6, float(np.max(np.abs(noise))))
    return np.stack([sine, saw, square, noise], axis=0)


_TABLES = _build_wavetables()


def _table_lookup(table: np.ndarray, phase: np.ndarray) -> np.ndarray:
    """Linear-interp lookup of a single-cycle table indexed by phase ∈ [0, 1)."""
    idx_f = (phase % 1.0) * _WT_LEN
    i0 = np.floor(idx_f).astype(np.int64) % _WT_LEN
    frac = (idx_f - np.floor(idx_f)).astype(np.float32)
    i1 = (i0 + 1) % _WT_LEN
    return ((1.0 - frac) * table[i0] + frac * table[i1]).astype(np.float32)


def _onepole_lp(x: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    cutoff = float(np.clip(cutoff, 20.0, sr * 0.49))
    rc = 1.0 / (2.0 * np.pi * cutoff)
    dt = 1.0 / sr
    alpha = dt / (rc + dt)
    b = np.array([alpha], dtype=np.float64)
    a = np.array([1.0, -(1.0 - alpha)], dtype=np.float64)
    return lfilter(b, a, x.astype(np.float64)).astype(np.float32)


class WavetableRenderer(BenchSynthRenderer):
    """Morphing wavetable synth across {sine, saw, square, noise}."""

    PARAM_RANGES = {
        "freq": (40.0, 1500.0),
        "table_a": (0.0, 3.0),     # which built-in for end A
        "table_b": (0.0, 3.0),     # which built-in for end B
        "position": (0.0, 1.0),    # morph A↔B
        "tone": (200.0, 8000.0),   # 1-pole LP cutoff
        "attack": (0.001, 1.0),
        "decay": (0.01, 1.0),
        "sustain": (0.0, 1.0),
        "release": (0.01, 1.0),
        "unison_detune": (0.0, 25.0),  # cents
    }
    PARAM_DEFAULTS = {
        "freq": 220.0,
        "table_a": 0.0,
        "table_b": 1.0,
        "position": 0.0,
        "tone": 4000.0,
        "attack": 0.01,
        "decay": 0.3,
        "sustain": 0.7,
        "release": 0.2,
        "unison_detune": 5.0,
    }

    def _render(self, p: dict[str, float]) -> np.ndarray:
        sr = self.sample_rate
        n = self._n_samples()
        t = self._time_axis()

        a_idx = int(round(np.clip(p["table_a"], 0, 3)))
        b_idx = int(round(np.clip(p["table_b"], 0, 3)))
        wt_a = _TABLES[a_idx]
        wt_b = _TABLES[b_idx]
        morph = float(np.clip(p["position"], 0.0, 1.0))

        f = float(p["freq"])
        det = float(p["unison_detune"])
        f2 = f * (2.0 ** (det / 1200.0))
        phase1 = (f * t) % 1.0
        phase2 = (f2 * t) % 1.0

        v1a = _table_lookup(wt_a, phase1)
        v1b = _table_lookup(wt_b, phase1)
        v2a = _table_lookup(wt_a, phase2)
        v2b = _table_lookup(wt_b, phase2)
        sig = 0.5 * (
            (1.0 - morph) * (v1a + v2a) + morph * (v1b + v2b)
        )

        env = adsr_envelope(n, sr, p["attack"], p["decay"], p["sustain"], p["release"])
        voiced = (sig * env).astype(np.float32)
        return _onepole_lp(voiced, sr, p["tone"])
