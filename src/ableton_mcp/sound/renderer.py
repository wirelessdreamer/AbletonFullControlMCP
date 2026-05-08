"""Renderer interface — abstract bridge between params and rendered audio.

The matcher and probe loop only need ``render(params) -> np.ndarray`` so they
can be exercised with the in-process ``SynthStubRenderer`` today.

Two concrete implementations live here:

- :class:`SynthStubRenderer` — wraps ``synth_stub.synth_render`` for tests + demos.
- :class:`LiveRenderer` — stub for capturing real Live device output. Raises
  ``NotImplementedError`` on ``render()``: the previous tape-based capture path
  was removed; rebuild on Live's Resampling input (see
  ``ableton_mcp.bounce.resampling``) when sound-modeling against real devices
  is needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping

import numpy as np

from .synth_stub import SYNTH_STUB_DEFAULTS, SYNTH_STUB_PARAM_RANGES, synth_render


class Renderer(ABC):
    """Abstract render interface.

    Implementations should be deterministic for a given param dict so that the
    feature distance landscape used by the optimiser is well-defined.
    """

    sample_rate: int = 22050
    duration_sec: float = 2.0

    @abstractmethod
    def render(self, params: Mapping[str, float]) -> np.ndarray:
        """Render mono float32 audio for the given param dict."""

    @property
    def param_ranges(self) -> dict[str, tuple[float, float]]:
        """Optional: expose the legal range for each param the renderer accepts."""
        return {}

    @property
    def default_params(self) -> dict[str, float]:
        """Optional: a sensible starting cell for refinement."""
        return {}


@dataclass
class SynthStubRenderer(Renderer):
    """Renders the in-process numpy synth from ``synth_stub``."""

    sample_rate: int = 22050
    duration_sec: float = 2.0
    seed: int | None = 0

    def render(self, params: Mapping[str, float]) -> np.ndarray:
        return synth_render(
            params,
            sr=self.sample_rate,
            dur=self.duration_sec,
            seed=self.seed,
        )

    @property
    def param_ranges(self) -> dict[str, tuple[float, float]]:
        return dict(SYNTH_STUB_PARAM_RANGES)

    @property
    def default_params(self) -> dict[str, float]:
        return dict(SYNTH_STUB_DEFAULTS)


class LiveRenderer(Renderer):
    """Stub renderer for real Live devices.

    Construction is cheap and dependency-free, so the offline matcher /
    sweep planner / synth_stub pipeline keeps working. ``render()`` raises
    ``NotImplementedError`` — the prior tape-based capture path was removed
    and a Resampling-based replacement (see
    ``ableton_mcp.bounce.resampling``) hasn't been wired in yet.

    ``tools/sound_modeling.py`` catches the NotImplementedError and returns
    a structured ``{"status": "not_implemented"}`` to callers.
    """

    def __init__(
        self,
        track_index: int,
        device_index: int,
        *,
        sample_rate: int = 44100,
        duration_sec: float = 2.0,
        midi_note: int = 60,
        velocity: int = 100,
    ) -> None:
        self.track_index = int(track_index)
        self.device_index = int(device_index)
        self.sample_rate = int(sample_rate)
        self.duration_sec = float(duration_sec)
        self.midi_note = int(midi_note)
        self.velocity = int(velocity)

    def render(self, params: Mapping[str, float]) -> np.ndarray:
        raise NotImplementedError(
            "LiveRenderer.render() is currently a stub. Real-device capture "
            "needs a resampling-track-based render pipeline (see "
            "ableton_mcp/bounce/resampling.py for the bouncing equivalent). "
            "Use device_id='synth_stub' to run the in-process pipeline."
        )
