"""MCP tools for the bulk-inventory pipeline.

These wrap the primitives in :mod:`ableton_mcp.inventory` and expose them
as FastMCP tools. The bulk operation (``inventory_scan_all``) honours
``dry_run`` and ``max_items`` so users can stage runs against a huge
plugin library without losing their day to a runaway scan.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from ..inventory import (
    BROWSER_CATEGORIES,
    BrowserItem,
    InstrumentSnapshot,
    Manifest,
    build_coverage_summary,
    load_and_introspect,
    match_to_schemas,
    scan_browser,
)
from ..inventory.scanner import PROBEABLE_CATEGORIES


log = logging.getLogger(__name__)


DEFAULT_MANIFEST_PATH = "data/inventory/manifest.json"


def _serialize_items(items: list[BrowserItem]) -> list[dict[str, Any]]:
    return [it.to_dict() for it in items]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def inventory_scan_browser(category: Optional[str] = None) -> dict[str, Any]:
        """Walk Live's browser tree without loading anything.

        Returns every item the bridge can see — including folders so the
        client can render structure if it wants. ``category`` narrows the
        scope to one of: instruments, audio_effects, midi_effects, drums,
        sounds, samples, plugins, user_library, current_project, packs.
        """
        items = await scan_browser(category=category)
        return {
            "count": len(items),
            "loadable_count": sum(1 for it in items if it.is_loadable),
            "categories": sorted({it.category for it in items}),
            "items": _serialize_items(items),
        }

    @mcp.tool()
    async def inventory_introspect(uri: str, category: str) -> dict[str, Any]:
        """Load one device on a temp track, dump its parameters, delete the track.

        ``uri`` may be either a slash-delimited path or a Live URI.
        ``category`` controls whether a MIDI or audio probe track is
        created (instruments / midi_effects / drums / plugins → MIDI;
        audio_effects → audio).
        """
        snapshot = await load_and_introspect(uri, category)
        return snapshot.to_dict()

    @mcp.tool()
    async def inventory_scan_all(
        category: str = "instruments",
        out_path: str = DEFAULT_MANIFEST_PATH,
        dry_run: bool = False,
        max_items: Optional[int] = None,
    ) -> dict[str, Any]:
        """Bulk-walk the browser, introspect each loadable item, write a manifest.

        Set ``dry_run=True`` to skip the load-and-introspect step entirely
        — the result then lists what WOULD be probed without touching
        Live. Set ``max_items`` to cap the work for staged runs.

        Probe-able categories are instruments, audio_effects, midi_effects,
        drums, plugins. Other categories (samples, sounds) won't load
        usefully on a probe track and will return a clear error if asked.
        """
        if category not in BROWSER_CATEGORIES:
            return {
                "error": f"unknown category {category!r}",
                "expected": list(BROWSER_CATEGORIES),
            }
        if category not in PROBEABLE_CATEGORIES:
            return {
                "error": (
                    f"category {category!r} is not probeable; bulk introspection "
                    "only works for instruments / audio_effects / midi_effects / drums / plugins"
                ),
                "probeable": sorted(PROBEABLE_CATEGORIES),
            }

        items = await scan_browser(category=category)
        loadable = [it for it in items if it.is_loadable]
        if max_items is not None:
            loadable = loadable[: int(max_items)]

        if dry_run:
            return {
                "dry_run": True,
                "would_probe": len(loadable),
                "items": _serialize_items(loadable),
            }

        snapshots: list[InstrumentSnapshot] = []
        for it in loadable:
            uri_or_path = it.path or it.uri or ""
            if not uri_or_path:
                snapshots.append(
                    InstrumentSnapshot(
                        name=it.name,
                        uri=None,
                        category=it.category,
                        class_name="",
                        error="no resolvable uri or path",
                    )
                )
                continue
            snap = await load_and_introspect(uri_or_path, it.category, name=it.name)
            snapshots.append(snap)
            # Tiny pause between items so we don't queue hundreds of
            # `delete_track` ops at the bridge in the same display tick.
            await asyncio.sleep(0.1)

        matches = match_to_schemas(snapshots)
        coverage = build_coverage_summary(matches)
        manifest = Manifest(
            instruments=snapshots,
            coverage_summary=coverage,
        )
        saved_to = manifest.save(out_path)
        return {
            "dry_run": False,
            "scanned": len(snapshots),
            "manifest_path": str(saved_to),
            "totals": manifest.totals(),
            "coverage_summary": coverage,
            "errors": [s.name for s in snapshots if s.error],
        }

    @mcp.tool()
    async def inventory_match_manifest(manifest_path: str) -> dict[str, Any]:
        """Re-run the schema matcher against a saved manifest.

        Useful after adding new canonical schemas: load the existing
        manifest, run :func:`match_to_schemas`, and return the coverage
        report without re-touching Live.
        """
        manifest = Manifest.load(manifest_path)
        matches = match_to_schemas(manifest.instruments)
        coverage = build_coverage_summary(matches)
        return {
            "manifest_path": str(manifest_path),
            "coverage_summary": coverage,
            "matches": [m.to_dict() for m in matches],
        }

    @mcp.tool()
    async def inventory_load_manifest(path: str) -> dict[str, Any]:
        """Return the saved manifest as a dict (raw round-trip)."""
        manifest = Manifest.load(path)
        return manifest.to_dict()

    @mcp.tool()
    async def inventory_summary(manifest_path: str) -> dict[str, Any]:
        """High-level stats: counts by category, schema coverage %, top plugins.

        Returns:
            * ``totals`` per browser category
            * ``coverage_pct`` — share of items with a full canonical schema
            * ``top_param_rich`` — 10 most parameter-rich plugins
            * ``no_schema`` — plugins with no canonical schema (truncated)
        """
        manifest = Manifest.load(manifest_path)
        matches = match_to_schemas(manifest.instruments)
        coverage = build_coverage_summary(matches)
        total = max(coverage["total"], 1)
        coverage_pct = round(100.0 * coverage["by_coverage"].get("full", 0) / total, 2)

        # Sort plugins by parameter count, descending. Skip ones that
        # errored out so we don't surface stubs.
        rich = sorted(
            (s for s in manifest.instruments if not s.error),
            key=lambda s: len(s.parameters),
            reverse=True,
        )[:10]
        no_schema = [
            m.snapshot.name for m in matches if m.coverage == "unknown"
        ]
        return {
            "manifest_path": str(manifest_path),
            "created_at": manifest.created_at,
            "totals": manifest.totals(),
            "coverage_summary": coverage,
            "coverage_pct": coverage_pct,
            "top_param_rich": [
                {"name": s.name, "class_name": s.class_name, "parameters": len(s.parameters)}
                for s in rich
            ],
            "no_schema": no_schema[:50],
            "no_schema_total": len(no_schema),
        }
