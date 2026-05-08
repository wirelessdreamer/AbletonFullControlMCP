"""Apply a stored preset's params to a real Live device via OSC.

Mirrors ``_push_params_via_osc`` from :mod:`ableton_mcp.tools.sound_modeling`.
Lookups are by preset name; the preset's ``device_class`` is reported back
in the result so the caller can sanity-check it against the actual device
on the chosen track.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Mapping

from .storage import find_by_name


async def _push_params_via_osc_async(
    track_index: int, device_index: int, params: Mapping[str, float]
) -> dict[str, Any]:
    """Async core: match param names case-insensitively against device params.

    Mirrors :func:`ableton_mcp.tools.sound_modeling._push_params_via_osc` but
    only the async branch — this module is always called from FastMCP tool
    handlers, which run inside an event loop.
    """
    from ..osc_client import get_client  # local: avoid OSC dep at test time

    client = await get_client()
    names = (
        await client.request(
            "/live/device/get/parameters/name", int(track_index), int(device_index)
        )
    )[2:]
    applied: list[dict[str, Any]] = []
    unmatched: list[str] = []
    lowered = [str(n).strip().lower() for n in names]
    for name, value in params.items():
        try:
            idx = lowered.index(name.strip().lower())
        except ValueError:
            unmatched.append(name)
            continue
        client.send(
            "/live/device/set/parameter/value",
            int(track_index),
            int(device_index),
            int(idx),
            float(value),
        )
        applied.append(
            {"name": str(names[idx]), "index": idx, "value": float(value)}
        )
    return {"applied": applied, "unmatched": unmatched}


async def apply_preset_to_live(
    preset_name: str,
    track_index: int,
    device_index: int,
    *,
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Look up the named preset and push its params to (track, device) via OSC.

    Returns a dict with ``status``, the preset metadata, and per-param
    ``applied`` / ``unmatched`` lists. Never raises for unmatched param
    names — those are reported in the result.
    """
    preset = find_by_name(preset_name, db_path=db_path)
    if preset is None:
        return {
            "status": "not_found",
            "preset_name": preset_name,
        }

    # Synth_stub presets have no Live device to push to.
    if preset.device_class == "synth_stub":
        return {
            "status": "skipped",
            "reason": "synth_stub presets are in-process; nothing to push to Live",
            "preset_name": preset.name,
            "device_class": preset.device_class,
            "params": dict(preset.params),
        }

    try:
        push = await _push_params_via_osc_async(track_index, device_index, preset.params)
    except Exception as exc:  # pragma: no cover — OSC failures surface here
        return {
            "status": "error",
            "preset_name": preset.name,
            "device_class": preset.device_class,
            "track_index": int(track_index),
            "device_index": int(device_index),
            "error": repr(exc),
        }

    return {
        "status": "ok",
        "preset_name": preset.name,
        "device_class": preset.device_class,
        "track_index": int(track_index),
        "device_index": int(device_index),
        "applied": push["applied"],
        "unmatched": push["unmatched"],
    }


def apply_preset_to_live_sync(
    preset_name: str,
    track_index: int,
    device_index: int,
    *,
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Sync wrapper for callers outside an asyncio loop."""
    return asyncio.run(
        apply_preset_to_live(
            preset_name, track_index, device_index, db_path=db_path
        )
    )


__all__ = ["apply_preset_to_live", "apply_preset_to_live_sync"]
