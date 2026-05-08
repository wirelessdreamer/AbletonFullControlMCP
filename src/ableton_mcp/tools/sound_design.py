"""MCP tools for the curated sound-design rule set.

Six tools, all under the ``sound_*`` prefix:

- ``sound_describe_track(track_index)`` — read a track's chain via OSC and
  return a musician-readable summary.
- ``sound_describe_all_tracks()`` — same, for every audible track.
- ``sound_apply_descriptor(track_index, device_index, descriptor, intensity, dry_run)``
  — apply one descriptor's curated rules to a single device.
- ``sound_apply_descriptors(track_index, descriptors, intensity, dry_run)``
  — apply multiple descriptors across every device on the track.
- ``sound_explain_descriptor(class_name, descriptor)`` — return the rule
  body so the LLM can explain why a particular knob was moved.
- ``sound_list_descriptors_for_device(class_name)`` — what descriptors does
  this device's rule set cover?

All tools are designed to fail gracefully when OSC is unreachable: they
return an ``error`` status rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from ..sound_design import (
    DEVICE_RULES,
    apply_descriptor as _apply_descriptor,
    apply_descriptors as _apply_descriptors,
    apply_descriptors_to_track as _apply_descriptors_to_track,
    coverage_table,
    explain_descriptor as _explain_descriptor,
    list_descriptors_for_device as _list_descriptors_for_device,
    normalize_descriptor,
    summarise_all_tracks as _summarise_all_tracks,
    summarise_track_sound as _summarise_track_sound,
)

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def sound_describe_track(track_index: int) -> dict[str, Any]:
        """Musician-readable summary of one track's instrument + effect chain.

        Reads the device chain via OSC, matches each device's class_name
        against the schema catalogue and curated rule set, and returns
        a one-paragraph summary plus per-device records (display name,
        synthesis-architecture blurb, descriptors that map to its knobs).
        """
        return await _summarise_track_sound(int(track_index))

    @mcp.tool()
    async def sound_describe_all_tracks() -> dict[str, Any]:
        """Scan every audible track and summarise its sound."""
        return await _summarise_all_tracks()

    @mcp.tool()
    async def sound_apply_descriptor(
        track_index: int,
        device_index: int,
        descriptor: str,
        intensity: float = 0.5,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply a single sound descriptor to a Live device using curated rules.

        Examples: ``descriptor="brighter"``, ``descriptor="aggressive"``,
        ``descriptor="warm"``. The rule set is keyed by the device's LOM
        class_name; if the device isn't covered (e.g. a third-party VST),
        returns ``status: "unsupported_device"``.

        ``intensity`` (0..1) scales how far each rule pushes the param.
        ``dry_run=True`` reports what would change without pushing OSC.
        """
        return await _apply_descriptor(
            track_index=int(track_index),
            device_index=int(device_index),
            descriptor=str(descriptor),
            intensity=float(intensity),
            dry_run=bool(dry_run),
        )

    @mcp.tool()
    async def sound_apply_descriptors(
        track_index: int,
        descriptors: list[str],
        intensity: float = 0.5,
        dry_run: bool = False,
        device_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Apply multiple descriptors across a track's devices.

        Pass ``device_index`` to limit to a single device, otherwise the
        rules walk the entire chain — devices without a rule set (VSTs,
        unsupported stocks) are noted in ``per_device[].status`` but don't
        fail the call.

        Opposing descriptors cancel by design ("warmer and brighter" with
        equal intensity yields a near-zero filter move).
        """
        if device_index is not None:
            single = await _apply_descriptors(
                track_index=int(track_index),
                device_index=int(device_index),
                descriptors=list(descriptors),
                intensity=float(intensity),
                dry_run=bool(dry_run),
            )
            return {
                "status": single.get("status", "ok"),
                "track_index": int(track_index),
                "device_index": int(device_index),
                "descriptors": [normalize_descriptor(d) for d in descriptors],
                "intensity": float(intensity),
                "per_device": [single],
            }
        return await _apply_descriptors_to_track(
            track_index=int(track_index),
            descriptors=list(descriptors),
            intensity=float(intensity),
            dry_run=bool(dry_run),
        )

    @mcp.tool()
    async def sound_explain_descriptor(
        class_name: str, descriptor: str
    ) -> dict[str, Any]:
        """Return the curated rule body for a (device class, descriptor) pair.

        Use this to explain *why* a parameter was changed. For example,
        "brighter on Drift" returns the rule list that includes Filter
        Frequency (+1, 0.9) — so the LLM can say "I lifted the cutoff".
        """
        return _explain_descriptor(str(class_name), str(descriptor))

    @mcp.tool()
    async def sound_list_descriptors_for_device(class_name: str) -> dict[str, Any]:
        """List the descriptors this device's rule set knows about."""
        return _list_descriptors_for_device(str(class_name))

    @mcp.tool()
    async def sound_design_coverage() -> dict[str, Any]:
        """Report which devices and descriptors the curated rule catalogue covers.

        Useful for diagnostics — surfaces gaps so the LLM knows when to
        fall back to the heavier ``shape_apply`` (probe-dataset) workflow.
        """
        return {
            "status": "ok",
            "device_count": len(DEVICE_RULES),
            "coverage": coverage_table(),
        }
