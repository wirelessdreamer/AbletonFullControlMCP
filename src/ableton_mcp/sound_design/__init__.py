"""Lightweight curated mapping from natural-language descriptors to device knobs.

Public surface:

- :class:`ParamRule` — one descriptor → param contribution.
- :data:`DEVICE_RULES` — master ``{class_name: {descriptor: [ParamRule, ...]}}``.
- :func:`get_rules`, :func:`get_descriptor_rules`, :func:`supported_classes`,
  :func:`supported_descriptors_for` — lookup helpers.
- :func:`apply_descriptor`, :func:`apply_descriptors`,
  :func:`apply_descriptors_to_track` — async appliers (push to a Live device).
- :func:`summarise_track_sound`, :func:`summarise_all_tracks` — async track
  introspection helpers.
- :func:`explain_descriptor`, :func:`list_descriptors_for_device` — pure
  lookup helpers; no OSC required.

This is the lightweight complement to
:mod:`ableton_mcp.tools.sound_modeling`: those tools require a probe
dataset built by sweeping the device, capturing audio and extracting
features. The rules in this package apply instantly without any audio
capture, at the cost of being generic across presets.
"""

from .applier import (
    apply_descriptor,
    apply_descriptors,
    apply_descriptors_to_track,
)
from .device_rules import (
    DESCRIPTOR_ALIASES,
    DEVICE_RULES,
    ParamRule,
    REQUIRED_DESCRIPTORS,
    all_descriptors,
    coverage_table,
    get_descriptor_rules,
    get_rules,
    iter_rules,
    normalize_descriptor,
    supported_classes,
    supported_descriptors_for,
)
from .introspect import (
    explain_descriptor,
    list_descriptors_for_device,
    summarise_all_tracks,
    summarise_device,
    summarise_track_sound,
)

__all__ = [
    # data + types
    "ParamRule",
    "DEVICE_RULES",
    "DESCRIPTOR_ALIASES",
    "REQUIRED_DESCRIPTORS",
    # rule lookups
    "get_rules",
    "get_descriptor_rules",
    "supported_classes",
    "supported_descriptors_for",
    "all_descriptors",
    "coverage_table",
    "iter_rules",
    "normalize_descriptor",
    # appliers
    "apply_descriptor",
    "apply_descriptors",
    "apply_descriptors_to_track",
    # introspection
    "summarise_track_sound",
    "summarise_all_tracks",
    "summarise_device",
    "explain_descriptor",
    "list_descriptors_for_device",
]
