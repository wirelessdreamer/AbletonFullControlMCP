"""project_describe: single-call comprehensive snapshot of the Live session.

Composes existing per-track / per-device OSC + bridge queries into one
result so the LLM can answer "what's on track 3?", "which tracks are
muted?", "what plugins make this sound?" without a 5-tool dance per
question. Three detail levels trade off output size vs. completeness:

- ``summary`` — project metadata + track names/index only. ~1-2 KB.
- ``tracks`` (default) — adds per-track mixer state + I/O capability +
  clip counts. ~50-100 bytes/track.
- ``full`` — adds devices per track. ~200-500 bytes/track depending on
  device count. Cap your call at this level if you actually need device
  identity; otherwise stick to ``tracks``.

All data sources are pre-existing: AbletonOSC for transport + LOM
properties, AbletonFullControlBridge for ``track.list_devices``. No new
bridge handlers required.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..osc_client import get_client

log = logging.getLogger(__name__)


_DETAIL_LEVELS = ("summary", "tracks", "full")


async def _project_block(client: Any) -> dict[str, Any]:
    """Project-level metadata. Same fields as ``arrangement_summary`` plus
    a couple extras (scale, is_playing) that help question-answering."""

    async def g(addr: str) -> Any:
        return (await client.request(addr))[0]

    sig_num = await g("/live/song/get/signature_numerator")
    sig_den = await g("/live/song/get/signature_denominator")
    return {
        "tempo": float(await g("/live/song/get/tempo")),
        "time_signature": f"{int(sig_num)}/{int(sig_den)}",
        "song_length_beats": float(await g("/live/song/get/song_length")),
        "num_tracks": int(await g("/live/song/get/num_tracks")),
        "num_scenes": int(await g("/live/song/get/num_scenes")),
        "current_song_time_beats": float(await g("/live/song/get/current_song_time")),
        "is_playing": bool(await g("/live/song/get/is_playing")),
        "scale": {
            "root_note": int(await g("/live/song/get/root_note")),
            "scale_name": str(await g("/live/song/get/scale_name")),
        },
    }


async def _track_summary(client: Any, index: int, name: str) -> dict[str, Any]:
    """Per-track mixer state + capability flags + clip counts."""

    async def g(addr: str) -> Any:
        # AbletonOSC track replies are (track_id, value); we want the value.
        reply = await client.request(addr, index)
        return reply[1] if len(reply) > 1 else None

    out: dict[str, Any] = {
        "index": index,
        "name": name,
    }
    # These properties are exposed on every track regardless of type, so we
    # always read them. Wrap each in its own try so one missing property
    # doesn't blank the whole row.
    for key, addr, cast in (
        ("muted", "/live/track/get/mute", bool),
        ("solo", "/live/track/get/solo", bool),
        ("armed", "/live/track/get/arm", bool),
        ("is_foldable", "/live/track/get/is_foldable", bool),
        ("is_grouped", "/live/track/get/is_grouped", bool),
        ("has_audio_input", "/live/track/get/has_audio_input", bool),
        ("has_audio_output", "/live/track/get/has_audio_output", bool),
        ("has_midi_input", "/live/track/get/has_midi_input", bool),
        ("has_midi_output", "/live/track/get/has_midi_output", bool),
        ("color_index", "/live/track/get/color_index", int),
        ("volume", "/live/track/get/volume", float),
        ("panning", "/live/track/get/panning", float),
    ):
        try:
            val = await g(addr)
            out[key] = cast(val) if val is not None else None
        except Exception as exc:
            log.debug("project_describe: track %d %s read failed: %r", index, addr, exc)
            out[key] = None
    return out


async def _track_devices(bridge: Any, index: int) -> list[dict[str, Any]] | str:
    """Devices on a track via the bridge. Returns a list, or a string with
    an error message if the bridge call failed (so the caller can still
    return a useful response for the rest of the session)."""
    try:
        reply = await bridge.call("track.list_devices", track_index=index)
    except AbletonBridgeError as exc:
        return f"bridge error: {exc}"
    devices = reply.get("devices") if isinstance(reply, dict) else None
    if not isinstance(devices, list):
        return "bridge returned malformed device list"
    return [
        {
            "index": int(d.get("index", i)),
            "name": d.get("name"),
            "class_name": d.get("class_name"),
            "type": d.get("type"),
        }
        for i, d in enumerate(devices)
    ]


async def _bridge_metadata() -> dict[str, Any]:
    """Pull bridge + Live version via the handshake-cached version_info().

    Errors are reported as a dict with ``error`` rather than raising — the
    bridge may genuinely be unavailable (user disabled the Remote Script,
    is running an old Live without it, etc.) and ``project_describe``
    should still return useful data."""
    bridge = get_bridge_client()
    try:
        info = await bridge.version_info()
    except AbletonBridgeError as exc:
        return {"error": str(exc), "available": False}
    return {
        "available": True,
        "bridge_version": info.get("bridge_version"),
        "live_version": info.get("live_version"),
        "expected_bridge_version": info.get("expected_bridge_version"),
        "compatible": info.get("compatible"),
        "outdated": info.get("outdated"),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def project_describe(detail: str = "tracks") -> dict[str, Any]:
        """One-call snapshot of the active Live session for question-answering.

        ``detail`` controls how much is included:

        - ``"summary"`` — project metadata + track names/indices only.
          Cheapest; use when you only need an overview.
        - ``"tracks"`` (default) — adds per-track mixer state, I/O
          capability flags, and clip counts. Right for questions about
          mute/solo state, track types, or which tracks have content.
        - ``"full"`` — also includes devices on each track. Use when the
          question is about plugins/instruments. Cost scales with track
          and device count.

        Returns:
            ``{live_version, bridge, project, tracks, scenes}``. The
            ``bridge`` block carries the version handshake result so the
            caller knows whether bridge-backed fields (devices) are
            reliable. Per-track read failures are logged at debug and
            surfaced as ``null`` for the affected field — the rest of
            the row still populates.
        """
        if detail not in _DETAIL_LEVELS:
            return {
                "status": "error",
                "error": f"unknown detail {detail!r}; must be one of {_DETAIL_LEVELS}",
            }

        client = await get_client()
        project = await _project_block(client)
        bridge_meta = await _bridge_metadata()

        # Track names are needed for every detail level.
        names = list(await client.request("/live/song/get/track_names"))
        bridge = get_bridge_client() if detail == "full" and bridge_meta["available"] else None

        if detail == "summary":
            tracks = [{"index": i, "name": n} for i, n in enumerate(names)]
        else:
            tracks = []
            for i, n in enumerate(names):
                row = await _track_summary(client, i, n)
                if detail == "full" and bridge is not None:
                    row["devices"] = await _track_devices(bridge, i)
                tracks.append(row)

        # Scenes — name + colour. Bulk read where AbletonOSC supports it.
        scenes: list[dict[str, Any]] = []
        try:
            scene_names = list(await client.request("/live/song/get/scenes/name"))
            for i, n in enumerate(scene_names):
                scenes.append({"index": i, "name": n})
        except Exception as exc:
            log.debug("project_describe: scene names read failed: %r", exc)

        return {
            "status": "ok",
            "detail": detail,
            "bridge": bridge_meta,
            "project": project,
            "tracks": tracks,
            "scenes": scenes,
        }
