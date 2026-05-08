"""Bounce via Live's resampling-track / track-as-input pattern.

This is the practical "automate the last step" approach. We use only LOM
operations that already work in Live 11.3.43 — no Max for Live, no audio
loopback drivers, no UI automation. The cost is realtime (Live records at
1×); the win is that one playback pass captures every requested track in
parallel into separate wavs.

Workflow (per call):

1. For each track to bounce, create a fresh audio track at the end of the
   set with input routing set to either ``Resampling`` (master mix) or the
   source track's name (per-track stem).
2. Arm the temp audio tracks. Stop transport, jump to beat 0.
3. Enable arrangement record (``Song.record_mode = 1``).
4. Start playback for ``duration_sec``. Live captures audio into each
   armed temp track's arrangement timeline.
5. Stop playback + record.
6. For each temp track: query its first arrangement clip's ``file_path``
   via the AbletonFullControlBridge (``clip.arrangement_clip_info``), copy
   the wav from Live's ``Samples/Recorded/`` folder to the user's
   destination.
7. Delete the temp audio tracks (highest index first to keep indices
   stable).

The set must be playing FROM beat 0 — i.e., the arrangement should already
hold the content you want recorded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..osc_client import get_client

log = logging.getLogger(__name__)

# Suffix appended to temp audio track names — used to identify and clean up
# our own scratch tracks if a previous run crashed.
TEMP_TRACK_SUFFIX = " [bounce-temp]"


@dataclass
class BounceTrackResult:
    track_name: str
    output_path: str
    duration_sec: float
    source_track_index: int | None  # None = master/resampling


def _safe_filename(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s).strip()[:80] or "track"


async def _track_count() -> int:
    osc = await get_client()
    return int((await osc.request("/live/song/get/num_tracks"))[0])


async def _track_names() -> list[str]:
    osc = await get_client()
    return list(await osc.request("/live/song/get/track_names"))


async def _track_mute(track_index: int) -> bool:
    osc = await get_client()
    return bool((await osc.request("/live/track/get/mute", track_index))[1])


async def _create_audio_track(name: str) -> int:
    """Create an audio track at the end of the set. Return its index."""
    osc = await get_client()
    osc.send("/live/song/create_audio_track", -1)
    await asyncio.sleep(0.25)
    n = await _track_count()
    new_idx = n - 1
    osc.send("/live/track/set/name", new_idx, name)
    await asyncio.sleep(0.05)
    return new_idx


async def _set_input_routing(track_index: int, type_name: str, channel_name: str | None = None) -> None:
    """Set input routing on a track. ``type_name`` examples:
    'Resampling', '5-Drums' (track-as-input), 'Ext. In', 'No Input'."""
    osc = await get_client()
    osc.send("/live/track/set/input_routing_type", track_index, type_name)
    await asyncio.sleep(0.05)
    if channel_name is not None:
        osc.send("/live/track/set/input_routing_channel", track_index, channel_name)
        await asyncio.sleep(0.05)


async def _arm(track_index: int, on: bool = True) -> None:
    osc = await get_client()
    osc.send("/live/track/set/arm", track_index, 1 if on else 0)
    await asyncio.sleep(0.03)


async def _delete_track(track_index: int) -> None:
    osc = await get_client()
    osc.send("/live/song/delete_track", track_index)
    await asyncio.sleep(0.15)


async def _record_arrangement(duration_sec: float, settle_sec: float = 0.4) -> None:
    """Stop, jump to 0, enable record, start playback, wait, stop, disable record."""
    osc = await get_client()
    osc.send("/live/song/stop_playing")
    await asyncio.sleep(0.1)
    osc.send("/live/song/set/current_song_time", 0.0)
    await asyncio.sleep(0.1)
    # Enable arrangement record (the global record button at top of transport).
    osc.send("/live/song/set/record_mode", 1)
    await asyncio.sleep(0.1)
    osc.send("/live/song/start_playing")
    # Real-time wait. settle_sec gives sfrecord-style trailing silence buffer.
    await asyncio.sleep(float(duration_sec) + float(settle_sec))
    osc.send("/live/song/stop_playing")
    await asyncio.sleep(0.1)
    osc.send("/live/song/set/record_mode", 0)
    await asyncio.sleep(0.1)


async def _resolve_source_input_name(source_track_index: int) -> str:
    """Return the input-routing-type string Live uses to address a source track.

    Live shows tracks in input/output dropdowns as ``"<n>-<name>"`` (1-based).
    """
    names = await _track_names()
    n = source_track_index + 1
    name = names[source_track_index] if source_track_index < len(names) else f"Track {n}"
    return f"{n}-{name}"


async def _harvest_arrangement_clip_path(track_index: int) -> str | None:
    """Get the absolute file_path of the first arrangement clip on a track."""
    bridge = get_bridge_client()
    try:
        info = await bridge.call("clip.arrangement_clip_info", track_index=track_index, clip_index=0)
    except AbletonBridgeError as exc:
        log.warning("could not query arrangement clip on track %d: %s", track_index, exc)
        return None
    return info.get("file_path")


async def _copy_or_skip(src: str | None, dst: str, max_retries: int = 6, retry_delay: float = 0.4) -> dict[str, Any]:
    """Copy src→dst with retries — Live may briefly hold a lock after closing the clip."""
    if not src:
        return {"copied": False, "error": "no source path returned by bridge"}
    if not os.path.exists(src):
        return {"copied": False, "error": f"source wav not found: {src}"}
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            shutil.copy2(src, dst)
            return {
                "copied": True,
                "source": src,
                "output_path": dst,
                "size_bytes": os.path.getsize(dst),
                "attempts": attempt + 1,
            }
        except (PermissionError, OSError) as e:
            last_err = e
            await asyncio.sleep(retry_delay)
    return {
        "copied": False,
        "source": src,
        "error": f"copy failed after {max_retries} attempts: {last_err}",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def bounce_song_via_resampling(
    output_path: str | os.PathLike,
    duration_sec: float,
    *,
    settle_sec: float = 0.4,
    cleanup_temp_track: bool = True,
) -> dict[str, Any]:
    """Capture the master mix to ``output_path`` via a Resampling track.

    Creates one fresh audio track with input = Resampling, arms it, records
    the arrangement for ``duration_sec`` seconds, copies the recorded wav to
    ``output_path``, then deletes the temp track.
    """
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    temp_name = "Master Bounce" + TEMP_TRACK_SUFFIX

    pre_count = await _track_count()
    new_idx = await _create_audio_track(temp_name)
    src: str | None = None
    try:
        await _set_input_routing(new_idx, "Resampling", "Post Mixer")
        await _arm(new_idx, True)
        await _record_arrangement(duration_sec, settle_sec=settle_sec)
        src = await _harvest_arrangement_clip_path(new_idx)
    finally:
        # Delete the temp track BEFORE copying — Live holds a write/exclusive
        # lock on the wav while the clip exists in the set. Track delete drops
        # the clip and releases the lock.
        if cleanup_temp_track:
            await _delete_track(new_idx)
            await asyncio.sleep(0.3)  # let the OS release the file handle
    result = await _copy_or_skip(src, str(out)) if src else {"copied": False, "error": "no recorded clip found"}
    return {
        "what": "master_via_resampling",
        "duration_sec": duration_sec,
        "temp_track_index": new_idx,
        "tracks_before": pre_count,
        **result,
    }


async def bounce_tracks_via_resampling(
    track_indices: Sequence[int],
    output_dir: str | os.PathLike,
    duration_sec: float,
    *,
    settle_sec: float = 0.4,
    include_master: bool = False,
    cleanup_temp_tracks: bool = True,
) -> dict[str, Any]:
    """Capture each source track to its own wav by routing into a temp audio track.

    Bounces all listed tracks IN PARALLEL during a single playback pass —
    total wall-clock = ``duration_sec + settle_sec``, regardless of how many
    tracks. Returns a dict mapping each source track to its output path.
    """
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    names = await _track_names()
    source_routings: list[tuple[int, str, str]] = []  # (source_idx, source_name, input_routing_string)
    for ti in track_indices:
        if ti < 0 or ti >= len(names):
            raise ValueError(f"track_index {ti} out of range (have {len(names)})")
        rt = await _resolve_source_input_name(ti)
        source_routings.append((ti, names[ti], rt))

    # Create one temp audio track per source, arm them all.
    temp_indices: list[tuple[int, int, str]] = []  # (temp_track_index, source_index, source_name)
    for source_idx, source_name, routing_type in source_routings:
        temp_name = f"Stem {source_name}{TEMP_TRACK_SUFFIX}"
        new_idx = await _create_audio_track(temp_name)
        await _set_input_routing(new_idx, routing_type, "Post Mixer")
        await _arm(new_idx, True)
        temp_indices.append((new_idx, source_idx, source_name))

    master_temp_idx: int | None = None
    if include_master:
        master_temp_idx = await _create_audio_track("Master Bounce" + TEMP_TRACK_SUFFIX)
        await _set_input_routing(master_temp_idx, "Resampling", "Post Mixer")
        await _arm(master_temp_idx, True)

    # One pass — records all in parallel.
    sources_for_copy: list[tuple[int, str, Path, str | None]] = []  # (source_idx, source_name, dst, src_path)
    master_src: str | None = None
    try:
        await _record_arrangement(duration_sec, settle_sec=settle_sec)
        # Harvest file paths BEFORE deleting tracks (file_path is on the clip).
        for temp_idx, source_idx, source_name in temp_indices:
            src = await _harvest_arrangement_clip_path(temp_idx)
            dst = out_root / f"stem_{source_idx:02d}_{_safe_filename(source_name)}.wav"
            sources_for_copy.append((source_idx, source_name, dst, src))
        if master_temp_idx is not None:
            master_src = await _harvest_arrangement_clip_path(master_temp_idx)
    finally:
        if cleanup_temp_tracks:
            # Delete temp tracks in REVERSE index order so earlier indices stay stable.
            all_temp = list(temp_indices)
            if master_temp_idx is not None:
                all_temp.append((master_temp_idx, -1, "master"))
            for temp_idx, _, _ in sorted(all_temp, key=lambda t: t[0], reverse=True):
                try:
                    await _delete_track(temp_idx)
                except Exception as exc:  # pragma: no cover — cleanup best-effort
                    log.warning("failed to delete temp track %d: %s", temp_idx, exc)
            await asyncio.sleep(0.3)  # let the OS release file handles

    # Now copy after Live has released its locks.
    results: list[dict[str, Any]] = []
    for source_idx, source_name, dst, src in sources_for_copy:
        result = await _copy_or_skip(src, str(dst)) if src else {"copied": False, "error": "no recorded clip"}
        results.append({
            "source_track_index": source_idx,
            "source_track_name": source_name,
            **result,
        })
    master_result: dict[str, Any] | None = None
    if master_temp_idx is not None:
        master_dst = out_root / "master.wav"
        master_result = (
            await _copy_or_skip(master_src, str(master_dst))
            if master_src else {"copied": False, "error": "no recorded clip"}
        )

    return {
        "what": "stems_via_resampling",
        "duration_sec": duration_sec,
        "output_dir": str(out_root),
        "stems": results,
        "master": master_result,
    }


async def bounce_enabled_via_resampling(
    output_dir: str | os.PathLike,
    duration_sec: float,
    *,
    include_master: bool = True,
    settle_sec: float = 0.4,
) -> dict[str, Any]:
    """Bounce every track that's currently un-muted, plus the master mix.

    Convenience wrapper around bounce_tracks_via_resampling. Skips muted
    tracks, group tracks (which produce no signal of their own), and tracks
    with no audio output.
    """
    osc = await get_client()
    n = await _track_count()
    keep: list[int] = []
    names = await _track_names()
    for i in range(n):
        # Skip muted.
        if (await osc.request("/live/track/get/mute", i))[1]:
            continue
        # Skip the temp tracks our own previous runs may have left behind.
        if names[i].endswith(TEMP_TRACK_SUFFIX):
            continue
        # Skip tracks with no audio output (e.g. group/folder, MIDI without instrument).
        try:
            has_audio_out = bool((await osc.request("/live/track/get/has_audio_output", i))[1])
        except Exception:
            has_audio_out = True
        if not has_audio_out:
            continue
        keep.append(i)

    return await bounce_tracks_via_resampling(
        keep,
        output_dir,
        duration_sec=duration_sec,
        settle_sec=settle_sec,
        include_master=include_master,
    )
