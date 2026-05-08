"""Canonical schema library for Ableton Live 11 built-in devices.

Public surface:

- ``DeviceSchema``, ``Parameter`` — dataclasses for the schema model.
- ``DEVICE_SCHEMAS`` — list[DeviceSchema] of every catalogued device.
- ``DEVICE_SCHEMAS_BY_CLASS`` — dict[class_name -> DeviceSchema] for O(1) lookup.
- ``lookup_schema(class_name)`` — case-sensitive class_name match, returns
  the schema or ``None``. Use ``closest_class_name(name)`` for the
  fuzzy fallback.
- ``closest_class_name(name)`` — best-guess class_name from a free-form
  string (uses difflib).

The catalogue covers Live 11 built-in instruments, audio effects, MIDI
effects, racks, and a couple of measurement utilities. Several schemas
are deliberately partial — see each device's ``notes`` attribute for the
specific gaps. Partial is honest; the alternative (faking the full
parameter surface) would mislead the planner.
"""

from __future__ import annotations

import difflib
from typing import Optional

from .audio_effects import AUDIO_EFFECT_SCHEMAS
from .base import DeviceSchema, Parameter
from .instruments import INSTRUMENT_SCHEMAS
from .midi_effects import MIDI_EFFECT_SCHEMAS
from .utilities import UTILITY_SCHEMAS


DEVICE_SCHEMAS: list[DeviceSchema] = [
    *INSTRUMENT_SCHEMAS,
    *AUDIO_EFFECT_SCHEMAS,
    *MIDI_EFFECT_SCHEMAS,
    *UTILITY_SCHEMAS,
]


DEVICE_SCHEMAS_BY_CLASS: dict[str, DeviceSchema] = {
    s.class_name: s for s in DEVICE_SCHEMAS
}


def lookup_schema(class_name: str) -> Optional[DeviceSchema]:
    """Return the schema for ``class_name`` (exact match) or None."""
    if not class_name:
        return None
    return DEVICE_SCHEMAS_BY_CLASS.get(class_name)


def closest_class_name(name: str, n: int = 1, cutoff: float = 0.6) -> Optional[str]:
    """Best-effort fuzzy match against the catalogue's class_names + display_names."""
    if not name:
        return None
    candidates: list[str] = []
    for s in DEVICE_SCHEMAS:
        candidates.append(s.class_name)
        candidates.append(s.display_name)
    matches = difflib.get_close_matches(name, candidates, n=n, cutoff=cutoff)
    if not matches:
        return None
    pick = matches[0]
    # Map a display-name match back to its class_name for callers.
    for s in DEVICE_SCHEMAS:
        if s.display_name == pick or s.class_name == pick:
            return s.class_name
    return None


__all__ = [
    "DeviceSchema",
    "Parameter",
    "DEVICE_SCHEMAS",
    "DEVICE_SCHEMAS_BY_CLASS",
    "lookup_schema",
    "closest_class_name",
]
