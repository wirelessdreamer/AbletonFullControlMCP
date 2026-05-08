"""Curated + auto-discovered preset library.

Two halves:

- **Curated:** hand-authored ``Preset`` rows in :mod:`.library` — real
  audio-designer starting points (warm pad, plucky lead, sub bass, ...).
  Tagged with descriptive search terms.
- **Discovered:** :func:`.clusterer.discover_presets_from_dataset` runs
  KMeans over a probe dataset's feature vectors and picks the
  centroid-closest cell as a new preset, auto-naming it from dominant
  features.

Storage: sqlite at ``data/presets/library.sqlite`` (see :mod:`.storage`).
Application: :func:`.applier.apply_preset_to_live` pushes params via OSC.
MCP surface: see :mod:`ableton_mcp.tools.presets`.
"""

from __future__ import annotations

from .library import LIBRARY, Preset
from .storage import (
    DEFAULT_DB_PATH,
    add_preset,
    find_by_name,
    list_presets,
    search_by_tag,
    search_by_text,
    seed_curated,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "LIBRARY",
    "Preset",
    "add_preset",
    "find_by_name",
    "list_presets",
    "search_by_tag",
    "search_by_text",
    "seed_curated",
]
