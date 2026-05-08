"""Natural-language sound shaping — the headline UX layer.

Take free-text descriptions like "make this brighter and punchier", parse out
the descriptors + intensities, translate them into target feature deltas
(via the optional ``semantics`` package, or a small built-in fallback
vocabulary), then use the existing :mod:`ableton_mcp.sound.matcher` kNN to
recommend / apply parameter changes.

Pieces:

- :mod:`.parser` — :class:`ShapeRequest` plus :func:`parse_shape_request`.
- :mod:`.fallback_vocab` — hardcoded ~25 descriptor → feature-delta mapping
  used when the optional ``semantics`` package is missing.
- :mod:`.planner` — turns a :class:`ShapeRequest` + current features into a
  ``{feature_name: target_value}`` dict.
- :mod:`.applier` — wraps :func:`sound.matcher.find_nearest` and (optionally)
  pushes the chosen params onto a real Live device via OSC.
"""

from __future__ import annotations

from .parser import ShapeRequest, parse_shape_request
from .planner import plan_target_features, semantics_source
from .applier import apply_to_live_device, find_params_matching_target

__all__ = [
    "ShapeRequest",
    "apply_to_live_device",
    "find_params_matching_target",
    "parse_shape_request",
    "plan_target_features",
    "semantics_source",
]
