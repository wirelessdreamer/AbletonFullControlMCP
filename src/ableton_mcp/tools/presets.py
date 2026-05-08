"""MCP tools surfacing the curated + discovered preset library.

Six tools:

- ``preset_list``         — filter by device_class / tag.
- ``preset_get``          — full preset by name.
- ``preset_search``       — fuzzy match across name / tags / description.
- ``preset_apply_to_live`` — push the preset's params to a Live device via OSC.
- ``preset_render_synth_bench`` — render a preview wav for synth_stub /
  synth_bench presets (no-op for real-Live device classes; falls back to
  ``synth_stub.synth_render`` when synth_bench isn't available).
- ``preset_discover``     — KMeans cluster a probe dataset and add the
  discovered presets to the library.

The first call to any tool implicitly seeds the curated library — see
:func:`_ensure_seeded`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..presets import (
    LIBRARY,
    DEFAULT_DB_PATH,
    find_by_name,
    list_presets,
    search_by_text,
    seed_curated,
)
from ..presets.applier import apply_preset_to_live
from ..presets.clusterer import discover_presets_from_dataset


_seeded: bool = False


def _ensure_seeded() -> None:
    """Idempotent first-call seed of the curated library into sqlite."""
    global _seeded
    if _seeded:
        return
    try:
        seed_curated()
    except Exception:  # pragma: no cover — DB-init failures should still allow tools to load
        pass
    _seeded = True


def _try_render_synth_bench(
    device_class: str, params: dict[str, float], output_path: Path
) -> dict[str, Any]:
    """Render a preview wav. Tries synth_bench first; falls back to synth_stub.

    Returns a status dict — never raises for synth-bench-missing or unsupported
    device classes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. synth_bench attempt.
    audio = None
    sample_rate = 22050
    used_engine = None
    try:
        from .. import synth_bench as sb  # noqa: F401

        # Several plausible API shapes; try each in order of preference.
        getter = (
            getattr(sb, "get", None)
            or getattr(sb, "get_renderer", None)
            or getattr(sb, "get_synth", None)
        )
        if callable(getter):
            try:
                synth = getter(device_class)
                if hasattr(synth, "render"):
                    audio = synth.render(params)
                    used_engine = f"synth_bench.{device_class}"
            except Exception:
                audio = None
    except Exception:
        audio = None

    # 2. synth_stub fallback (only sane for synth_stub or for params that look
    #    like the stub schema — i.e. "freq" present).
    if audio is None and (device_class == "synth_stub" or "freq" in params):
        from ..sound.synth_stub import synth_render

        audio = synth_render(params, sr=sample_rate, dur=2.0)
        used_engine = "synth_stub"

    if audio is None:
        return {
            "status": "skipped",
            "reason": (
                f"no in-process renderer for device_class={device_class!r}; "
                "synth_bench is not available and params don't match the synth_stub schema"
            ),
            "device_class": device_class,
        }

    import numpy as np
    import soundfile as sf  # librosa already pulls this in

    sf.write(str(output_path), np.asarray(audio, dtype=np.float32), sample_rate)
    return {
        "status": "ok",
        "engine": used_engine,
        "device_class": device_class,
        "output_path": str(output_path.resolve()),
        "sample_rate": sample_rate,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def preset_list(
        device_class: str | None = None,
        tag: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """List presets, optionally filtered by device_class / tag / source."""
        _ensure_seeded()
        rows = list_presets(device_class=device_class, tag=tag, source=source)
        return {
            "status": "ok",
            "filter": {
                "device_class": device_class,
                "tag": tag,
                "source": source,
            },
            "count": len(rows),
            "presets": [p.to_dict() for p in rows],
        }

    @mcp.tool()
    async def preset_get(name: str) -> dict[str, Any]:
        """Return the full preset definition by name (case-insensitive)."""
        _ensure_seeded()
        preset = find_by_name(name)
        if preset is None:
            return {"status": "not_found", "name": name}
        return {"status": "ok", "preset": preset.to_dict()}

    @mcp.tool()
    async def preset_search(query: str, limit: int = 20) -> dict[str, Any]:
        """Fuzzy match ``query`` against preset names, tags, and descriptions.

        Tokens are split on whitespace; a preset's score is the count of
        matched tokens (substring, case-insensitive) across name + tags +
        description, with a name-match bonus.
        """
        _ensure_seeded()
        rows = search_by_text(query, limit=limit)
        return {
            "status": "ok",
            "query": query,
            "count": len(rows),
            "matches": [p.to_dict() for p in rows],
        }

    @mcp.tool()
    async def preset_apply_to_live(
        name: str, track_index: int, device_index: int
    ) -> dict[str, Any]:
        """Push the named preset's params to (track_index, device_index) via OSC.

        Synth_stub presets are skipped (in-process — nothing to apply).
        Unmatched param names are reported, not errored.
        """
        _ensure_seeded()
        return await apply_preset_to_live(name, track_index, device_index)

    @mcp.tool()
    async def preset_render_synth_bench(
        name: str, output_path: str
    ) -> dict[str, Any]:
        """Render a preview wav for a synth_stub / synth_bench preset.

        Falls back to ``synth_stub.synth_render`` for synth_stub presets
        (and any preset whose params look like the stub schema). Returns a
        ``skipped`` status for real-Live device classes — nothing to render
        offline.
        """
        _ensure_seeded()
        preset = find_by_name(name)
        if preset is None:
            return {"status": "not_found", "name": name}
        return _try_render_synth_bench(
            preset.device_class, dict(preset.params), Path(output_path)
        )

    @mcp.tool()
    async def preset_discover(
        dataset_path: str,
        k: int = 8,
        device_id: str | None = None,
        name_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Cluster a probe dataset into ``k`` discovered presets and add them to the library.

        Each cluster contributes one preset:
          - device_class = the source device_id from the dataset row
          - params       = the centroid-closest cell's params
          - tags         = top descriptors from the centroid feature vector
          - source       = ``"discovered"``
        """
        _ensure_seeded()
        try:
            discovered = discover_presets_from_dataset(
                dataset_path,
                k=int(k),
                device_id=device_id,
                name_prefix=name_prefix,
            )
        except FileNotFoundError as exc:
            return {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "dataset_path": str(Path(dataset_path).resolve()),
            "k": int(k),
            "discovered": [p.to_dict() for p in discovered],
            "count": len(discovered),
            "library_size": len(LIBRARY)
            + len(list_presets(source="discovered")),
        }
