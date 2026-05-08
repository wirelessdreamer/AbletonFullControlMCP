"""Common base for the in-process synth bench.

Each bench synth subclasses :class:`BenchSynthRenderer` (which itself extends
:class:`ableton_mcp.sound.renderer.Renderer`) and exposes:

- ``param_ranges``    — dict[name, (min, max)]
- ``param_defaults``  — dict[name, default_value]
- ``render(params)``  — mono float32 numpy array

The base resolves param dicts (defaults + clamping), produces ADSR envelopes,
midi-note → frequency conversion, and a soft-normalisation helper so every
synth in the bench produces audio in roughly the same amplitude band.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

from ..sound.renderer import Renderer


def midi_to_hz(midi_note: float) -> float:
    return float(440.0 * (2.0 ** ((float(midi_note) - 69.0) / 12.0)))


def adsr_envelope(
    n: int,
    sr: int,
    attack: float,
    decay: float,
    sustain: float,
    release: float,
) -> np.ndarray:
    """Build an ADSR envelope of length ``n`` samples (clamped to fit the buffer)."""
    sustain = float(np.clip(sustain, 0.0, 1.0))
    a = max(0, int(round(attack * sr)))
    d = max(0, int(round(decay * sr)))
    r = max(0, int(round(release * sr)))
    if a + d + r >= n:
        scale = (n - 1) / max(1, a + d + r)
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
    return env


def soft_normalise(audio: np.ndarray, target: float = 0.7) -> np.ndarray:
    """Peak-normalise to ``target`` (default ~-3 dBFS)."""
    audio = np.asarray(audio, dtype=np.float32)
    if not np.any(np.isfinite(audio)):
        return np.zeros_like(audio, dtype=np.float32)
    audio = np.nan_to_num(audio, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(audio))) or 1.0
    if peak < 1e-9:
        return audio.astype(np.float32, copy=False)
    return (audio * (target / peak)).astype(np.float32, copy=False)


class BenchSynthRenderer(Renderer):
    """Abstract base for the synth bench.

    Concrete subclasses must define :attr:`param_ranges` and :attr:`param_defaults`
    as class attributes (or properties) and implement :meth:`_render`. The base
    class handles default-fill + clamping before calling :meth:`_render`.
    """

    # Concrete subclasses fill these.
    PARAM_RANGES: dict[str, tuple[float, float]] = {}
    PARAM_DEFAULTS: dict[str, float] = {}

    sample_rate: int = 22050
    duration_sec: float = 2.0

    def __init__(
        self,
        *,
        sample_rate: int = 22050,
        duration_sec: float = 2.0,
        midi_note: int = 60,
        seed: int | None = 0,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.duration_sec = float(duration_sec)
        self.midi_note = int(midi_note)
        self.seed = seed

    # ---- Renderer hooks ------------------------------------------------------
    @property
    def param_ranges(self) -> dict[str, tuple[float, float]]:
        return dict(self.PARAM_RANGES)

    @property
    def param_defaults(self) -> dict[str, float]:
        return dict(self.PARAM_DEFAULTS)

    @property
    def default_params(self) -> dict[str, float]:
        return self.param_defaults

    # ---- helpers -------------------------------------------------------------
    def _resolve(self, params: Mapping[str, float]) -> dict[str, float]:
        out: dict[str, float] = dict(self.PARAM_DEFAULTS)
        for k, v in params.items():
            if k in self.PARAM_DEFAULTS:
                out[k] = float(v)
        for name, (lo, hi) in self.PARAM_RANGES.items():
            out[name] = float(np.clip(out[name], lo, hi))
        return out

    def _n_samples(self) -> int:
        return max(1, int(round(self.duration_sec * self.sample_rate)))

    def _time_axis(self) -> np.ndarray:
        return np.arange(self._n_samples(), dtype=np.float32) / float(self.sample_rate)

    def _rng(self) -> np.random.Generator:
        return np.random.default_rng(self.seed if self.seed is not None else 0)

    # ---- final entry point ---------------------------------------------------
    def render(self, params: Mapping[str, float]) -> np.ndarray:
        p = self._resolve(params)
        audio = self._render(p)
        return soft_normalise(audio)

    def _render(self, params: dict[str, float]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError
