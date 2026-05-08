"""Composable FX for the synth bench.

- :class:`FilterFX` — RBJ biquad LP/HP/BP, static cutoff/Q.
- :class:`DelayFX` — single-tap delay with feedback.
- :class:`ReverbFX` — 4-comb / 2-allpass Schroeder reverb.
- :class:`SaturatorFX` — tanh + asymmetric (tanh on positives, smooth-clip on negatives).

Wire one or more onto a base synth via :class:`FXChain` (which is a Renderer
itself, so it slots in anywhere a Renderer is expected).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.signal import lfilter

from ..sound.renderer import Renderer
from .base import soft_normalise


@dataclass
class FilterFX:
    cutoff: float = 1500.0
    q: float = 1.0
    kind: str = "lp"  # "lp" | "hp" | "bp"

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        cutoff = float(np.clip(self.cutoff, 20.0, sr * 0.49))
        q = float(max(self.q, 1e-3))
        w0 = 2.0 * np.pi * cutoff / sr
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * q)
        if self.kind == "lp":
            b0 = (1.0 - cos_w0) / 2.0
            b1 = 1.0 - cos_w0
            b2 = (1.0 - cos_w0) / 2.0
        elif self.kind == "hp":
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
        return lfilter(b, a, audio.astype(np.float64)).astype(np.float32)


@dataclass
class DelayFX:
    time_sec: float = 0.25
    feedback: float = 0.4
    mix: float = 0.35  # 0=dry, 1=wet

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        n = int(audio.shape[0])
        d = max(1, int(self.time_sec * sr))
        # Pad audio so echoes can ring out past the original length.
        tail = max(d * 4, sr // 2)
        padded = np.concatenate([audio, np.zeros(tail, dtype=np.float32)])
        out = padded.copy()
        fb = float(np.clip(self.feedback, 0.0, 0.95))
        for i in range(d, padded.shape[0]):
            out[i] = padded[i] + fb * out[i - d]
        wet = float(np.clip(self.mix, 0.0, 1.0))
        return ((1.0 - wet) * padded + wet * out).astype(np.float32)


@dataclass
class ReverbFX:
    """Cheap algorithmic Schroeder reverb (4 combs + 2 allpasses)."""

    room_size: float = 0.6  # 0..1, scales comb feedback
    damping: float = 0.4    # 0..1, lowpass strength inside combs
    mix: float = 0.3

    # Comb delays (samples at 22050) and allpass delays — Schroeder's classic ratios.
    _COMB_LENS = (1116, 1188, 1277, 1356)
    _ALLPASS_LENS = (556, 441)

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        n = int(audio.shape[0])
        tail = sr  # 1s tail
        x = np.concatenate([audio, np.zeros(tail, dtype=np.float32)]).astype(np.float32)
        N = x.shape[0]
        out = np.zeros(N, dtype=np.float32)

        fb = 0.7 + 0.28 * float(np.clip(self.room_size, 0.0, 1.0))  # 0.7..0.98
        damp = float(np.clip(self.damping, 0.0, 1.0))

        # Scale comb delays by sr / 44100 so it sounds vaguely consistent across SRs.
        scale = sr / 44100.0
        for raw_d in self._COMB_LENS:
            d = max(1, int(raw_d * scale))
            buf = np.zeros(d, dtype=np.float32)
            lp = 0.0
            for i in range(N):
                read = buf[i % d]
                lp = (1.0 - damp) * read + damp * lp
                buf[i % d] = x[i] + fb * lp
                out[i] += read

        # Two allpass stages on the comb sum.
        for raw_d in self._ALLPASS_LENS:
            d = max(1, int(raw_d * scale))
            buf = np.zeros(d, dtype=np.float32)
            g = 0.5
            new_out = np.zeros(N, dtype=np.float32)
            for i in range(N):
                read = buf[i % d]
                v = -g * out[i] + read
                buf[i % d] = out[i] + g * v
                new_out[i] = v
            out = new_out

        # Mix dry + wet.
        wet = float(np.clip(self.mix, 0.0, 1.0))
        # Scale wet down because comb sum gets loud.
        wet_scaled = (out / max(1.0, float(np.max(np.abs(out))))).astype(np.float32) * 0.7
        return ((1.0 - wet) * x + wet * wet_scaled).astype(np.float32)


@dataclass
class SaturatorFX:
    drive: float = 0.5    # 0..1
    asymmetry: float = 0.3  # 0..1, how much harder we clip negatives

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        gain = 1.0 + 8.0 * float(np.clip(self.drive, 0.0, 1.0))
        x = audio.astype(np.float32) * gain
        asym = float(np.clip(self.asymmetry, 0.0, 1.0))
        # tanh on positives, asymmetric soft-clip on negatives.
        pos = np.tanh(x).astype(np.float32)
        neg = (x / np.sqrt(1.0 + (x * (1.0 + 2.0 * asym)) ** 2)).astype(np.float32)
        out = np.where(x >= 0.0, pos, neg)
        return out.astype(np.float32)


class FXChain(Renderer):
    """Composes a base synth with a sequential FX chain.

    ``fx`` items must each have a ``.process(audio, sr) -> np.ndarray`` method.
    The chain is itself a :class:`Renderer` so it can be passed anywhere a
    renderer is expected (probe, match, refine, etc.).
    """

    def __init__(self, base: Renderer, fx: list, *, normalise: bool = True) -> None:
        self.base = base
        self.fx = list(fx)
        self.normalise = bool(normalise)
        self.sample_rate = int(getattr(base, "sample_rate", 22050))
        self.duration_sec = float(getattr(base, "duration_sec", 2.0))

    def render(self, params: Mapping[str, float]) -> np.ndarray:
        audio = self.base.render(params)
        sr = int(self.sample_rate)
        for fx in self.fx:
            audio = fx.process(audio, sr)
        if self.normalise:
            return soft_normalise(audio)
        return audio.astype(np.float32, copy=False)

    @property
    def param_ranges(self) -> dict[str, tuple[float, float]]:
        return getattr(self.base, "param_ranges", {})

    @property
    def default_params(self) -> dict[str, float]:
        return getattr(self.base, "default_params", {})
