"""Subtractive synth — Analog/Drift family.

Saw / square / triangle oscillator → ADSR amp env → biquad LP/HP/BP with a
LFO modulating the cutoff. Two oscillators share the waveform; a second-osc
detune control widens the sound. Keeps to ~150 LOC.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .base import BenchSynthRenderer, adsr_envelope, midi_to_hz


def _saw(phase: np.ndarray) -> np.ndarray:
    # Phase in [0, 1). Naive saw is fine for testing.
    return (2.0 * (phase - np.floor(phase + 0.5))).astype(np.float32)


def _square(phase: np.ndarray) -> np.ndarray:
    return np.where((phase % 1.0) < 0.5, 1.0, -1.0).astype(np.float32)


def _triangle(phase: np.ndarray) -> np.ndarray:
    return (2.0 * np.abs(2.0 * (phase - np.floor(phase + 0.5))) - 1.0).astype(np.float32)


_WAVEFORMS = {0: _saw, 1: _square, 2: _triangle}


def _biquad_coeffs(filter_type: str, sr: int, cutoff: float, q: float):
    cutoff = float(np.clip(cutoff, 20.0, sr * 0.49))
    q = float(max(q, 1e-3))
    w0 = 2.0 * np.pi * cutoff / sr
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    if filter_type == "lp":
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
    elif filter_type == "hp":
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
    else:  # bp
        b0 = alpha
        b1 = 0.0
        b2 = -alpha
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return b, a


class SubtractiveRenderer(BenchSynthRenderer):
    """Two-osc subtractive synth with LFO-modulated filter."""

    # waveform: 0 saw, 1 square, 2 triangle (we round)
    # filter_type: 0 lp, 1 hp, 2 bp (we round)
    PARAM_RANGES = {
        "freq": (40.0, 1500.0),
        "waveform": (0.0, 2.0),
        "detune": (0.0, 30.0),         # cents on osc2
        "osc_mix": (0.0, 1.0),         # blend osc1/osc2
        "attack": (0.001, 1.0),
        "decay": (0.01, 1.0),
        "sustain": (0.0, 1.0),
        "release": (0.01, 1.0),
        "filter_type": (0.0, 2.0),
        "cutoff": (200.0, 8000.0),
        "resonance": (0.5, 8.0),
        "lfo_rate": (0.1, 10.0),       # Hz
        "lfo_amount": (0.0, 1.0),      # fraction of cutoff modulated
    }
    PARAM_DEFAULTS = {
        "freq": 220.0,
        "waveform": 0.0,
        "detune": 8.0,
        "osc_mix": 0.5,
        "attack": 0.01,
        "decay": 0.2,
        "sustain": 0.7,
        "release": 0.2,
        "filter_type": 0.0,
        "cutoff": 2000.0,
        "resonance": 1.2,
        "lfo_rate": 3.0,
        "lfo_amount": 0.3,
    }

    def _render(self, p: dict[str, float]) -> np.ndarray:
        sr = self.sample_rate
        n = self._n_samples()
        t = self._time_axis()

        wf_idx = int(round(np.clip(p["waveform"], 0, 2)))
        wave_fn = _WAVEFORMS[wf_idx]

        f1 = float(p["freq"])
        f2 = f1 * (2.0 ** (p["detune"] / 1200.0))

        phase1 = (f1 * t) % 1.0
        phase2 = (f2 * t) % 1.0
        osc1 = wave_fn(phase1)
        osc2 = wave_fn(phase2)
        mix = float(np.clip(p["osc_mix"], 0.0, 1.0))
        raw = (1.0 - mix) * osc1 + mix * osc2

        env = adsr_envelope(n, sr, p["attack"], p["decay"], p["sustain"], p["release"])
        voiced = (raw * env).astype(np.float32)

        # LFO sweeps cutoff between [cutoff*(1-amt), cutoff*(1+amt)].
        cutoff_base = float(p["cutoff"])
        amt = float(np.clip(p["lfo_amount"], 0.0, 1.0))
        lfo = np.sin(2.0 * np.pi * float(p["lfo_rate"]) * t).astype(np.float32)
        cutoff_track = cutoff_base * (1.0 + amt * lfo)
        cutoff_track = np.clip(cutoff_track, 20.0, sr * 0.49)

        # Block-process to apply time-varying cutoff cheaply.
        ft_idx = int(round(np.clip(p["filter_type"], 0, 2)))
        ftype = {0: "lp", 1: "hp", 2: "bp"}[ft_idx]
        block = max(1, n // 64)
        out = np.zeros(n, dtype=np.float32)
        zi = np.zeros(2, dtype=np.float64)
        for start in range(0, n, block):
            stop = min(n, start + block)
            mid = (start + stop) // 2
            cutoff = float(cutoff_track[min(mid, n - 1)])
            b, a = _biquad_coeffs(ftype, sr, cutoff, p["resonance"])
            chunk, zi = lfilter(b, a, voiced[start:stop].astype(np.float64), zi=zi)
            out[start:stop] = chunk.astype(np.float32)
        return out
