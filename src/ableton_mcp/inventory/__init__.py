"""Bulk inventory of the user's Ableton Live browser.

This package walks Live's browser tree, optionally loads each instrument /
audio effect / MIDI effect onto a temporary throwaway track to introspect
its parameter list, matches the result against our canonical 57-device
schema library, and persists everything to a portable JSON manifest.

Public surface:

- :class:`BrowserItem` — one row of the browser walk.
- :class:`InstrumentSnapshot` — one row of the introspection step.
- :class:`Match` — one row of the schema-matching step.
- :class:`Manifest` — load/save the full bundle.
- :func:`scan_browser` — walk only (no Live mutation).
- :func:`load_and_introspect` — temp-track create/load/dump/delete.
- :func:`match_to_schemas` — fold canonical schemas into snapshots.
- :func:`build_coverage_summary` — high-level stats over a list of matches.

Tools that wrap these primitives live in
``ableton_mcp.tools.inventory`` and follow the FastMCP ``register(mcp)``
pattern.
"""

from __future__ import annotations

from .loader import (
    InstrumentSnapshot,
    InventoryError,
    load_and_introspect,
    PROBE_TRACK_NAME,
)
from .manifest import Manifest
from .matcher import Match, build_coverage_summary, match_to_schemas
from .scanner import (
    BROWSER_CATEGORIES,
    BrowserItem,
    scan_browser,
)


__all__ = [
    "BROWSER_CATEGORIES",
    "BrowserItem",
    "InstrumentSnapshot",
    "InventoryError",
    "Manifest",
    "Match",
    "PROBE_TRACK_NAME",
    "build_coverage_summary",
    "load_and_introspect",
    "match_to_schemas",
    "scan_browser",
]
