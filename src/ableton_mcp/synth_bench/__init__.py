"""In-process synth bench — a free testing surface for the Phase 3 NL→params pipeline.

Each renderer is a numpy/scipy implementation that mirrors the architecture of
one of Ableton's stock instruments (Analog/Drift, Operator, Wavetable, ...). A
shared :class:`SYNTH_REGISTRY` lets tools and tests look up renderers by name.

Public API:
    SYNTH_REGISTRY      — dict[name, RendererClass]
    list_synths()       — sorted list of registered names
    get(name)           — instantiate the renderer class for ``name``
    BenchSynthRenderer  — common abstract base
    FXChain + FX        — composable post-processing
"""

from __future__ import annotations

from .additive import AdditiveRenderer
from .base import BenchSynthRenderer
from .fm_2op import FM2OpRenderer
from .fm_4op import FM4OpRenderer
from .fx_chain import DelayFX, FilterFX, FXChain, ReverbFX, SaturatorFX
from .granular import GranularRenderer
from .subtractive import SubtractiveRenderer
from .wavetable import WavetableRenderer

SYNTH_REGISTRY: dict[str, type[BenchSynthRenderer]] = {
    "subtractive": SubtractiveRenderer,
    "fm_2op": FM2OpRenderer,
    "fm_4op": FM4OpRenderer,
    "wavetable": WavetableRenderer,
    "additive": AdditiveRenderer,
    "granular": GranularRenderer,
}


def list_synths() -> list[str]:
    """All registered synth names, sorted."""
    return sorted(SYNTH_REGISTRY.keys())


def get(
    name: str,
    *,
    sample_rate: int = 22050,
    duration_sec: float = 2.0,
    midi_note: int = 60,
    seed: int | None = 0,
) -> BenchSynthRenderer:
    """Instantiate the renderer class for ``name`` with the given render config."""
    if name not in SYNTH_REGISTRY:
        raise KeyError(
            f"unknown synth {name!r}; have {sorted(SYNTH_REGISTRY)}"
        )
    cls = SYNTH_REGISTRY[name]
    return cls(
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        midi_note=midi_note,
        seed=seed,
    )


__all__ = [
    "AdditiveRenderer",
    "BenchSynthRenderer",
    "DelayFX",
    "FM2OpRenderer",
    "FM4OpRenderer",
    "FXChain",
    "FilterFX",
    "GranularRenderer",
    "ReverbFX",
    "SaturatorFX",
    "SYNTH_REGISTRY",
    "SubtractiveRenderer",
    "WavetableRenderer",
    "get",
    "list_synths",
]
