"""Print a full snapshot of the current Live set using only the OSC client.

No MCP server, no LLM — direct UDP. Useful as a sanity check after a fresh
install, and as a template for whatever introspection you want to script.

Run::

    D:\\Code\\AbletonMCP\\.venv\\Scripts\\python.exe examples/live_set_introspection.py

Output is human-readable, not JSON. If you want JSON for piping into other
tools, swap the ``_print_*`` calls for ``json.dumps(snapshot)`` at the bottom
of ``main()``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ableton_mcp.config import Config
from ableton_mcp.osc_client import AbletonOSCClient, AbletonOSCTimeout


# ---------------------------------------------------------------------------
# Small helpers around the bare OSC client.
# ---------------------------------------------------------------------------

async def _maybe(client: AbletonOSCClient, addr: str, *args: Any, default: Any = None) -> Any:
    """Like client.request(addr, *args) but returns ``default`` on timeout."""
    try:
        return await client.request(addr, *args, timeout=1.5)
    except AbletonOSCTimeout:
        return default


async def _g0(client: AbletonOSCClient, addr: str, *args: Any) -> Any:
    """Get the first arg from a reply, skipping the selector echo."""
    result = await _maybe(client, addr, *args)
    if not result:
        return None
    # Replies echo the selectors. e.g. /live/track/get/mute → (track_id, value).
    return result[len(args)] if len(result) > len(args) else result[-1]


# ---------------------------------------------------------------------------
# Snapshot pieces.
# ---------------------------------------------------------------------------

async def song_snapshot(client: AbletonOSCClient) -> dict[str, Any]:
    return {
        "tempo": await _g0(client, "/live/song/get/tempo"),
        "is_playing": bool(await _g0(client, "/live/song/get/is_playing")),
        "song_time_beats": await _g0(client, "/live/song/get/current_song_time"),
        "song_length_beats": await _g0(client, "/live/song/get/song_length"),
        "time_signature": (
            f"{await _g0(client, '/live/song/get/signature_numerator')}/"
            f"{await _g0(client, '/live/song/get/signature_denominator')}"
        ),
        "metronome": bool(await _g0(client, "/live/song/get/metronome")),
        "session_record": bool(await _g0(client, "/live/song/get/session_record")),
        "loop": {
            "enabled": bool(await _g0(client, "/live/song/get/loop")),
            "start": await _g0(client, "/live/song/get/loop_start"),
            "length": await _g0(client, "/live/song/get/loop_length"),
        },
        "num_tracks": await _g0(client, "/live/song/get/num_tracks"),
        "num_scenes": await _g0(client, "/live/song/get/num_scenes"),
    }


async def tracks_snapshot(client: AbletonOSCClient, n_tracks: int) -> list[dict[str, Any]]:
    names_reply = await _maybe(client, "/live/song/get/track_names", default=())
    names = list(names_reply) if names_reply else []
    out: list[dict[str, Any]] = []
    for i in range(n_tracks):
        track: dict[str, Any] = {
            "index": i,
            "name": names[i] if i < len(names) else f"Track {i+1}",
            "mute": bool(await _g0(client, "/live/track/get/mute", i)),
            "solo": bool(await _g0(client, "/live/track/get/solo", i)),
            "arm": bool(await _g0(client, "/live/track/get/arm", i)),
            "is_grouped": bool(await _g0(client, "/live/track/get/is_grouped", i)),
            "is_foldable": bool(await _g0(client, "/live/track/get/is_foldable", i)),
        }
        # Devices on this track.
        n_dev = await _g0(client, "/live/track/get/num_devices", i)
        if n_dev:
            dev_names = await _maybe(client, "/live/track/get/devices/name", i, default=())
            track["devices"] = list(dev_names[1:]) if dev_names else []
        else:
            track["devices"] = []
        # Clips: count slots, list non-empty ones with name + length.
        clips_names = await _maybe(client, "/live/track/get/clips/name", i, default=())
        clips: list[dict[str, Any]] = []
        if clips_names:
            # Reply: (track_id, slot0, name0, slot1, name1, ...) — skip the leading id.
            payload = clips_names[1:]
            for j in range(0, len(payload) - 1, 2):
                slot, name = payload[j], payload[j + 1]
                if name is None:
                    continue
                length = await _g0(
                    client, "/live/clip/get/length", i, int(slot)
                )
                clips.append({"slot": int(slot), "name": name, "length_beats": length})
        track["clips"] = clips
        out.append(track)
    return out


async def scenes_snapshot(client: AbletonOSCClient, n_scenes: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n_scenes):
        out.append({
            "index": i,
            "name": await _g0(client, "/live/scene/get/name", i),
            "is_empty": bool(await _g0(client, "/live/scene/get/is_empty", i)),
            "is_triggered": bool(await _g0(client, "/live/scene/get/is_triggered", i)),
        })
    return out


async def cue_points_snapshot(client: AbletonOSCClient) -> list[dict[str, Any]]:
    reply = await _maybe(client, "/live/song/get/cue_points", default=())
    if not reply:
        return []
    out: list[dict[str, Any]] = []
    for i in range(0, len(reply) - 1, 2):
        out.append({"name": reply[i], "time_beats": float(reply[i + 1])})
    return out


async def selection_snapshot(client: AbletonOSCClient) -> dict[str, Any]:
    return {
        "selected_scene": await _g0(client, "/live/view/get/selected_scene"),
        "selected_track": await _g0(client, "/live/view/get/selected_track"),
        "selected_clip": await _maybe(client, "/live/view/get/selected_clip"),
        "selected_device": await _maybe(client, "/live/view/get/selected_device"),
    }


# ---------------------------------------------------------------------------
# Pretty printing.
# ---------------------------------------------------------------------------

def _print_section(title: str) -> None:
    print()
    print(f"=== {title} ===")


def _print_song(song: dict[str, Any]) -> None:
    _print_section("song")
    for k, v in song.items():
        print(f"  {k}: {v}")


def _print_tracks(tracks: list[dict[str, Any]]) -> None:
    _print_section(f"tracks ({len(tracks)})")
    for t in tracks:
        flags = []
        if t["mute"]:
            flags.append("M")
        if t["solo"]:
            flags.append("S")
        if t["arm"]:
            flags.append("R")
        if t["is_grouped"]:
            flags.append("g")
        if t["is_foldable"]:
            flags.append("F")
        flag_str = f" [{''.join(flags)}]" if flags else ""
        print(f"  [{t['index']:2d}] {t['name']!r}{flag_str}")
        if t["devices"]:
            print(f"       devices: {', '.join(str(d) for d in t['devices'])}")
        if t["clips"]:
            for c in t["clips"]:
                print(f"       clip slot {c['slot']}: {c['name']!r} ({c['length_beats']} beats)")


def _print_scenes(scenes: list[dict[str, Any]]) -> None:
    _print_section(f"scenes ({len(scenes)})")
    for s in scenes:
        marker = "▶" if s["is_triggered"] else (" " if not s["is_empty"] else "·")
        print(f"  {marker} [{s['index']:2d}] {s['name']!r}")


def _print_cues(cues: list[dict[str, Any]]) -> None:
    _print_section(f"cue points ({len(cues)})")
    if not cues:
        print("  (none)")
        return
    for c in cues:
        print(f"  @{c['time_beats']:7.2f}  {c['name']!r}")


def _print_selection(sel: dict[str, Any]) -> None:
    _print_section("selection")
    for k, v in sel.items():
        print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

async def main() -> int:
    cfg = Config.from_env()
    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        if not await client.ping():
            print("FAIL: AbletonOSC did not respond. Open Live and enable the control surface.")
            return 1

        version = await client.request("/live/application/get/version")
        print(f"Connected to Ableton Live {'.'.join(str(v) for v in version)}")

        song = await song_snapshot(client)
        n_tracks = int(song.get("num_tracks") or 0)
        n_scenes = int(song.get("num_scenes") or 0)
        tracks = await tracks_snapshot(client, n_tracks)
        scenes = await scenes_snapshot(client, n_scenes)
        cues = await cue_points_snapshot(client)
        selection = await selection_snapshot(client)

        _print_song(song)
        _print_tracks(tracks)
        _print_scenes(scenes)
        _print_cues(cues)
        _print_selection(selection)
        print()
        return 0
    finally:
        await client.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
