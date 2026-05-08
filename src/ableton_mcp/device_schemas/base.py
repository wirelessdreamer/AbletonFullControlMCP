"""Dataclasses for the canonical Ableton Live 11 device-schema library.

A ``Parameter`` describes a single knob / button / dropdown on a device:
its UI label (``name``), what kind of control it is (``type``), the
default value, the legal range, an optional unit, a coarse semantic
``category`` (oscillator / envelope / filter / mix / fx / ...), whether
the Phase 3 sweep planner should consider it interesting
(``recommended_for_sweep``), and a short human-readable description.

A ``DeviceSchema`` bundles a list of parameters with metadata about the
host device: its LOM ``class_name`` (the string returned by
``/live/track/get/devices/class_name``), its display name in Live's
browser, what kind of device it is, a short description, and any caveats
captured in ``notes`` (e.g. "schema partial; verified params: X, Y, Z").

These objects are deliberately lightweight: they exist so an LLM can ask
"which knob is the filter cutoff on Operator?" or "what is sweep-worthy
on Wavetable?" without needing the device to actually be loaded in Live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


ParamType = Literal["continuous", "quantized", "enum"]
DeviceType = Literal[
    "instrument",
    "audio_effect",
    "midi_effect",
    "drum",
    "utility",
    "rack",
]


@dataclass(frozen=True)
class Parameter:
    """Canonical description of a single Live device parameter."""

    name: str
    type: ParamType
    default: float
    min: float
    max: float
    unit: Optional[str] = None
    category: str = "general"
    recommended_for_sweep: bool = False
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "unit": self.unit,
            "category": self.category,
            "recommended_for_sweep": self.recommended_for_sweep,
            "description": self.description,
        }


@dataclass(frozen=True)
class DeviceSchema:
    """Canonical description of a Live built-in device."""

    class_name: str
    display_name: str
    device_type: DeviceType
    description: str
    categories: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    notes: str = ""
    manual_url: Optional[str] = None

    def recommended_sweep_params(self) -> list[Parameter]:
        return [p for p in self.parameters if p.recommended_for_sweep]

    def find(self, param_name: str) -> Optional[Parameter]:
        target = param_name.strip().lower()
        for p in self.parameters:
            if p.name.strip().lower() == target:
                return p
        return None

    def to_dict(self) -> dict:
        return {
            "class_name": self.class_name,
            "display_name": self.display_name,
            "device_type": self.device_type,
            "description": self.description,
            "categories": list(self.categories),
            "parameters": [p.to_dict() for p in self.parameters],
            "notes": self.notes,
            "manual_url": self.manual_url,
        }
