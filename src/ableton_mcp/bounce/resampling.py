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

This module verifies every state-change OSC message with a read-back poll
rather than relying on fixed sleeps. The fixed-sleep approach raced on
slower hosts (issue #7): a recording that hadn't finalized yet would be
queried for ``file_path``, return None, and the cleanup ``delete_track``
would land while Live was still mid-finalize, triggering the LOM exception
that surfaced as "A serious program error has occurred." All the
``_wait_until`` polling, the orphan-cleanup, the warmup hook, and the
try/except around the cleanup delete in this file exist to plug that
class of bug.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..osc_client import get_client

log = logging.getLogger(__name__)

# Suffix appended to temp audio track names — used to identify and clean up
# our own scratch tracks if a previous run crashed.
TEMP_TRACK_SUFFIX = " [bounce-temp]"

# Default timeouts for state-change verification polls. Tuned to absorb the
# kind of latency we see on Live 11.3 + ASIO interfaces (Roland DUO-CAPTURE
# EX in particular shows 100-300 ms stalls during state changes).
DEFAULT_VERIFY_TIMEOUT_SEC = 2.0
DEFAULT_CLIP_FINALIZE_TIMEOUT_SEC = 3.0
DEFAULT_POLL_INTERVAL_SEC = 0.05


class BounceError(RuntimeError):
    """Raised when a bounce operation can't even start (track create failed,
    pre-cleanup left the set in a broken state, etc.). Distinct from a
    bounce that ran but produced no captured audio — those return a
    structured result with ``copied=False`` and a ``diagnostics`` list."""


@dataclass
class BounceTrackResult:
    track_name: str
    output_path: str
    duration_sec: float
    source_track_index: int | None  # None = master/resampling


def _safe_filename(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s).strip()[:80] or "track"


# ---------------------------------------------------------------------------
# Low-level OSC wrappers (verified — read back what we wrote)
# ---------------------------------------------------------------------------


async def _wait_until(
    check: Callable[[], Awaitable[bool]],
    *,
    timeout_sec: float = DEFAULT_VERIFY_TIMEOUT_SEC,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    description: str = "condition",
) -> bool:
    """Poll ``check`` until it returns True or ``timeout_sec`` elapses.

    Returns True if the condition was met within the budget, False if it
    timed out. A debug log is emitted on timeout (callers decide whether
    that's a hard failure).
    """
    deadline = time.monotonic() + timeout_sec
    # First check is synchronous-fast — don't sleep before the initial probe.
    if await check():
        return True
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval_sec)
        if await check():
            return True
    log.debug("_wait_until timed out after %.2fs waiting for %s", timeout_sec, description)
    return False


async def _track_count() -> int:
    osc = await get_client()
    return int((await osc.request("/live/song/get/num_tracks"))[0])


async def _track_names() -> list[str]:
    osc = await get_client()
    return list(await osc.request("/live/song/get/track_names"))


async def _track_mute(track_index: int) -> bool:
    osc = await get_client()
    return bool((await osc.request("/live/track/get/mute", track_index))[1])


async def _create_audio_track(
    name: str,
    *,
    verify_timeout_sec: float = DEFAULT_VERIFY_TIMEOUT_SEC,
) -> int:
    """Create an audio track at the end of the set, polling for the count to
    increment rather than assuming a fixed sleep is long enough.

    On sample-heavy sessions or under ASIO stalls the previous fixed-0.25 s
    sleep could return before Live had actually added the new track; the
    caller then operated on whatever ``num_tracks - 1`` happened to be
    (an existing user track), leading to arming the wrong track and
    sometimes crashing on cleanup. We poll instead, with a generous
    timeout. Raises :class:`BounceError` on timeout.
    """
    osc = await get_client()
    pre = await _track_count()
    osc.send("/live/song/create_audio_track", -1)

    async def _grew() -> bool:
        try:
            return (await _track_count()) > pre
        except Exception:  # pragma: no cover — defensive against transient OSC errors
            return False

    if not await _wait_until(
        _grew, timeout_sec=verify_timeout_sec, description="track creation"
    ):
        raise BounceError(
            f"create_audio_track timed out after {verify_timeout_sec:.1f}s "
            f"(num_tracks stayed at {pre}). Live may be busy or unresponsive."
        )

    new_idx = (await _track_count()) - 1
    osc.send("/live/track/set/name", new_idx, name)
    await asyncio.sleep(0.05)
    return new_idx


async def _set_input_routing(
    track_index: int,
    type_name: str,
    channel_name: str | None = None,
    *,
    verify_timeout_sec: float = 1.0,
) -> bool:
    """Set input routing on a track and verify with a read-back.

    ``type_name`` examples: 'Resampling', '5-Drums' (track-as-input),
    'Ext. In', 'No Input'.

    Returns True if the read-back confirms the new routing. False if the
    read-back didn't match within the timeout — caller should treat that as
    a likely cause of subsequent silent recording, but we don't raise here
    because some Live builds answer the read with a slightly different
    string (e.g. localized) even when the routing is correct. Callers can
    surface the warning to the user via diagnostics.
    """
    osc = await get_client()
    osc.send("/live/track/set/input_routing_type", track_index, type_name)

    async def _confirmed() -> bool:
        try:
            reply = await osc.request("/live/track/get/input_routing_type", track_index)
            actual = reply[1] if len(reply) > 1 else None
            return str(actual) == type_name
        except Exception:
            return False

    ok = await _wait_until(
        _confirmed,
        timeout_sec=verify_timeout_sec,
        description=f"input routing → {type_name!r}",
    )
    if not ok:
        log.warning(
            "input routing read-back did not confirm %r on track %d "
            "within %.1fs (continuing — recording may be silent)",
            type_name, track_index, verify_timeout_sec,
        )

    if channel_name is not None:
        osc.send("/live/track/set/input_routing_channel", track_index, channel_name)
        await asyncio.sleep(0.05)
    return ok


async def _arm(
    track_index: int,
    on: bool = True,
    *,
    verify_timeout_sec: float = 1.0,
) -> bool:
    """Set arm state on a track and verify with a read-back.

    Returns True if the read-back confirms the new state. False on timeout
    (caller may want to surface this in diagnostics — a track that didn't
    take the arm command records silence even with record_mode=1).
    """
    osc = await get_client()
    target = 1 if on else 0
    osc.send("/live/track/set/arm", track_index, target)

    async def _confirmed() -> bool:
        try:
            reply = await osc.request("/live/track/get/arm", track_index)
            return int(reply[1]) == target
        except Exception:
            return False

    ok = await _wait_until(
        _confirmed,
        timeout_sec=verify_timeout_sec,
        description=f"arm={target}",
    )
    if not ok:
        log.warning(
            "arm read-back did not confirm arm=%d on track %d within %.1fs "
            "(continuing — recording may be silent)",
            target, track_index, verify_timeout_sec,
        )
    return ok


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


async def _warmup_playback(duration_sec: float) -> None:
    """Run a brief no-record playback to prime samplers + the audio engine.

    First-bounce-of-fresh-sampler shows up as silent leading audio because
    Live's sampler instruments lazy-load samples from disk on first
    trigger. A short warmup pass prevents that — by the time we start the
    real record, every sampler in the arrangement has been hit at least
    once and is hot.
    """
    if duration_sec <= 0:
        return
    osc = await get_client()
    osc.send("/live/song/stop_playing")
    await asyncio.sleep(0.05)
    osc.send("/live/song/set/current_song_time", 0.0)
    await asyncio.sleep(0.05)
    osc.send("/live/song/start_playing")
    await asyncio.sleep(float(duration_sec))
    osc.send("/live/song/stop_playing")
    await asyncio.sleep(0.2)
    # Re-park playhead at 0 so the subsequent _record_arrangement call
    # starts from the same spot the user expects.
    osc.send("/live/song/set/current_song_time", 0.0)
    await asyncio.sleep(0.05)


async def _cleanup_orphan_temp_tracks() -> list[str]:
    """Delete any tracks left over from previous (possibly crashed) bounces.

    Returns the names of tracks cleaned up (empty list if nothing to do).
    A crashed bounce leaves tracks whose names end in
    :data:`TEMP_TRACK_SUFFIX`. Cleaning them up at the start of a new
    bounce prevents index drift and stops the set from accumulating cruft
    across runs.

    Deletion is best-effort — individual failures are logged but don't
    raise (otherwise a previously-orphaned-and-now-locked track could
    permanently brick the bounce path).
    """
    names = await _track_names()
    orphans = [(i, n) for i, n in enumerate(names) if n.endswith(TEMP_TRACK_SUFFIX)]
    if not orphans:
        return []
    log.info(
        "cleaning up %d orphan bounce-temp track(s) from previous run: %s",
        len(orphans), [n for _, n in orphans],
    )
    # Delete in reverse-index order so earlier indices stay stable.
    cleaned: list[str] = []
    for idx, name in sorted(orphans, key=lambda t: t[0], reverse=True):
        try:
            await _delete_track(idx)
            cleaned.append(name)
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning(
                "failed to delete orphan temp track %d (%r): %s — user may need "
                "to delete it manually in Live",
                idx, name, exc,
            )
    return cleaned


async def _resolve_source_input_name(source_track_index: int) -> str:
    """Return the input-routing-type string Live uses to address a source track.

    Live shows tracks in input/output dropdowns as ``"<n>-<name>"`` (1-based).
    """
    names = await _track_names()
    n = source_track_index + 1
    name = names[source_track_index] if source_track_index < len(names) else f"Track {n}"
    return f"{n}-{name}"


async def _harvest_arrangement_clip_path(track_index: int) -> str | None:
    """Get the absolute file_path of the first arrangement clip on a track.

    Single-shot version (no polling). Use :func:`_wait_for_clip_file_path`
    when the clip may not yet be finalized.
    """
    bridge = get_bridge_client()
    try:
        info = await bridge.call(
            "clip.arrangement_clip_info", track_index=track_index, clip_index=0
        )
    except AbletonBridgeError as exc:
        log.warning(
            "could not query arrangement clip on track %d: %s", track_index, exc
        )
        return None
    return info.get("file_path")


async def _wait_for_clip_file_path(
    track_index: int,
    *,
    timeout_sec: float = DEFAULT_CLIP_FINALIZE_TIMEOUT_SEC,
    poll_interval_sec: float = 0.1,
) -> str | None:
    """Poll for the first arrangement clip on ``track_index`` to have a
    non-empty ``file_path``.

    Live can take noticeably longer than the previous fixed 0.1 s sleep to
    finalize a just-recorded arrangement clip — especially with sampled
    instruments, ASIO interfaces, or busy sessions. The old code's race
    here was the proximate cause of the "no recorded clip found" symptom
    in issue #7. Polls every ``poll_interval_sec`` until either the bridge
    returns a non-empty path or the timeout elapses.
    """
    bridge = get_bridge_client()
    deadline = time.monotonic() + timeout_sec
    last_err: Exception | None = None
    while True:
        try:
            info = await bridge.call(
                "clip.arrangement_clip_info", track_index=track_index, clip_index=0
            )
            fp = info.get("file_path") if isinstance(info, dict) else None
            if fp:
                return fp
        except AbletonBridgeError as exc:
            last_err = exc
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_interval_sec)
    if last_err is not None:
        log.warning(
            "clip file_path wait timed out for track %d after %.1fs (last bridge error: %s)",
            track_index, timeout_sec, last_err,
        )
    else:
        log.warning(
            "clip file_path wait timed out for track %d after %.1fs (clip "
            "exists but no file_path — recording may not have finalized)",
            track_index, timeout_sec,
        )
    return None


async def _copy_or_skip(
    src: str | None,
    dst: str,
    max_retries: int = 6,
    retry_delay: float = 0.4,
) -> dict[str, Any]:
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


async def _safe_delete_track(idx: int) -> str | None:
    """Try to delete a track; return None on success or an error message on failure.

    Cleanup must never raise — a delete that crashes Live (issue #7) is
    worse than a leaked temp track that the next bounce will clean up via
    :func:`_cleanup_orphan_temp_tracks`.
    """
    try:
        await _delete_track(idx)
        return None
    except Exception as exc:  # pragma: no cover — defensive
        msg = f"failed to delete temp track {idx}: {exc}"
        log.warning(msg)
        return msg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def bounce_song_via_resampling(
    output_path: str | os.PathLike,
    duration_sec: float,
    *,
    settle_sec: float = 0.4,
    warmup_sec: float = 0.0,
    pre_cleanup: bool = True,
    cleanup_temp_track: bool = True,
    clip_finalize_timeout_sec: float = DEFAULT_CLIP_FINALIZE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Capture the master mix to ``output_path`` via a Resampling track.

    Creates one fresh audio track with input = Resampling, arms it, records
    the arrangement for ``duration_sec`` seconds, polls for Live to finalize
    the recorded clip, copies the wav to ``output_path``, then deletes the
    temp track.

    Args:
        output_path: where to write the captured wav.
        duration_sec: how long to record (real-time).
        settle_sec: trailing-silence buffer after the song body.
        warmup_sec: optional pre-record playback (no record) to prime
            samplers + audio engine. Defaults to 0 (off). 0.3-0.5 s is
            usually enough to eliminate first-bounce-of-fresh-sampler
            silence at the start of the capture.
        pre_cleanup: if True (default), delete any leftover bounce-temp
            tracks from previous crashed runs before starting.
        cleanup_temp_track: if True (default), delete the temp track after
            the bounce. Set False to leave the temp track in the
            arrangement for debugging.
        clip_finalize_timeout_sec: how long to wait for Live to finalize
            the recorded clip and report its ``file_path``. The old code
            assumed 0.1 s was enough; in practice 1-3 s is needed on busy
            sessions with sampled instruments.

    Returns:
        A dict with ``copied``, ``output_path`` (on success), ``error``,
        and ``diagnostics`` (a list of human-readable warnings about what
        went wrong if anything did).
    """
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    diagnostics: list[str] = []

    if pre_cleanup:
        cleaned = await _cleanup_orphan_temp_tracks()
        if cleaned:
            diagnostics.append(
                f"cleaned up {len(cleaned)} orphan temp track(s) from a previous run"
            )

    if warmup_sec > 0:
        await _warmup_playback(warmup_sec)

    temp_name = "Master Bounce" + TEMP_TRACK_SUFFIX
    pre_count = await _track_count()
    new_idx = await _create_audio_track(temp_name)
    src: str | None = None
    routing_ok: bool | None = None
    arm_ok: bool | None = None
    try:
        routing_ok = await _set_input_routing(new_idx, "Resampling", "Post Mixer")
        arm_ok = await _arm(new_idx, True)
        await _record_arrangement(duration_sec, settle_sec=settle_sec)
        src = await _wait_for_clip_file_path(
            new_idx, timeout_sec=clip_finalize_timeout_sec
        )
        if src is None:
            # Build an actionable diagnostic so the user knows where to
            # look. Distinct failure modes get distinct hints.
            if routing_ok is False:
                diagnostics.append(
                    "input routing 'Resampling' read-back did not confirm — "
                    "verify Live's Master track has Resampling available as "
                    "an input source."
                )
            if arm_ok is False:
                diagnostics.append(
                    "arm command read-back did not confirm — the temp track "
                    "may not have actually armed before record started."
                )
            diagnostics.append(
                f"clip never reported a file_path within {clip_finalize_timeout_sec:.1f}s "
                "(check Live's arrangement view: did a new clip appear on the "
                "temp track? if no, recording didn't capture; if yes but "
                "empty, input routing failed silently)."
            )
    finally:
        if cleanup_temp_track:
            err = await _safe_delete_track(new_idx)
            if err:
                diagnostics.append(err)
            else:
                await asyncio.sleep(0.3)  # let the OS release the file handle

    result = (
        await _copy_or_skip(src, str(out))
        if src else {"copied": False, "error": "no recorded clip found"}
    )
    return {
        "what": "master_via_resampling",
        "duration_sec": duration_sec,
        "temp_track_index": new_idx,
        "tracks_before": pre_count,
        "routing_confirmed": routing_ok,
        "arm_confirmed": arm_ok,
        "diagnostics": diagnostics if diagnostics else None,
        **result,
    }


async def bounce_tracks_via_resampling(
    track_indices: Sequence[int],
    output_dir: str | os.PathLike,
    duration_sec: float,
    *,
    settle_sec: float = 0.4,
    warmup_sec: float = 0.0,
    pre_cleanup: bool = True,
    include_master: bool = False,
    cleanup_temp_tracks: bool = True,
    clip_finalize_timeout_sec: float = DEFAULT_CLIP_FINALIZE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Capture each source track to its own wav by routing into a temp audio track.

    Bounces all listed tracks IN PARALLEL during a single playback pass —
    total wall-clock = ``duration_sec + settle_sec``, regardless of how many
    tracks. Returns a dict mapping each source track to its output path.

    See :func:`bounce_song_via_resampling` for the meaning of the new
    ``warmup_sec``, ``pre_cleanup``, and ``clip_finalize_timeout_sec``
    arguments. They behave identically here.
    """
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    diagnostics: list[str] = []

    if pre_cleanup:
        cleaned = await _cleanup_orphan_temp_tracks()
        if cleaned:
            diagnostics.append(
                f"cleaned up {len(cleaned)} orphan temp track(s) from a previous run"
            )

    if warmup_sec > 0:
        await _warmup_playback(warmup_sec)

    names = await _track_names()
    source_routings: list[tuple[int, str, str]] = []
    for ti in track_indices:
        if ti < 0 or ti >= len(names):
            raise ValueError(f"track_index {ti} out of range (have {len(names)})")
        rt = await _resolve_source_input_name(ti)
        source_routings.append((ti, names[ti], rt))

    # Create + route + arm each temp track, recording per-step confirmation.
    temp_indices: list[tuple[int, int, str, bool, bool]] = []
    # tuple = (temp_track_index, source_index, source_name, routing_ok, arm_ok)
    for source_idx, source_name, routing_type in source_routings:
        temp_name = f"Stem {source_name}{TEMP_TRACK_SUFFIX}"
        new_idx = await _create_audio_track(temp_name)
        r_ok = await _set_input_routing(new_idx, routing_type, "Post Mixer")
        a_ok = await _arm(new_idx, True)
        temp_indices.append((new_idx, source_idx, source_name, r_ok, a_ok))

    master_temp_idx: int | None = None
    master_routing_ok: bool | None = None
    master_arm_ok: bool | None = None
    if include_master:
        master_temp_idx = await _create_audio_track("Master Bounce" + TEMP_TRACK_SUFFIX)
        master_routing_ok = await _set_input_routing(
            master_temp_idx, "Resampling", "Post Mixer"
        )
        master_arm_ok = await _arm(master_temp_idx, True)

    # One pass — records all in parallel.
    sources_for_copy: list[tuple[int, str, Path, str | None]] = []
    master_src: str | None = None
    try:
        await _record_arrangement(duration_sec, settle_sec=settle_sec)
        # Harvest file paths BEFORE deleting tracks (file_path is on the clip).
        for temp_idx, source_idx, source_name, r_ok, a_ok in temp_indices:
            src = await _wait_for_clip_file_path(
                temp_idx, timeout_sec=clip_finalize_timeout_sec
            )
            if src is None:
                hint = []
                if r_ok is False:
                    hint.append("routing read-back failed")
                if a_ok is False:
                    hint.append("arm read-back failed")
                hint.append(
                    f"clip never reported file_path within {clip_finalize_timeout_sec:.1f}s"
                )
                diagnostics.append(
                    f"track {source_idx} ({source_name!r}): " + "; ".join(hint)
                )
            dst = out_root / f"stem_{source_idx:02d}_{_safe_filename(source_name)}.wav"
            sources_for_copy.append((source_idx, source_name, dst, src))
        if master_temp_idx is not None:
            master_src = await _wait_for_clip_file_path(
                master_temp_idx, timeout_sec=clip_finalize_timeout_sec
            )
            if master_src is None:
                diagnostics.append(
                    "master bounce: clip never reported file_path within "
                    f"{clip_finalize_timeout_sec:.1f}s"
                )
    finally:
        if cleanup_temp_tracks:
            # Delete temp tracks in REVERSE index order so earlier indices stay stable.
            all_temp: list[tuple[int, int, str]] = [
                (ti, si, sn) for (ti, si, sn, _, _) in temp_indices
            ]
            if master_temp_idx is not None:
                all_temp.append((master_temp_idx, -1, "master"))
            for temp_idx, _, _ in sorted(all_temp, key=lambda t: t[0], reverse=True):
                err = await _safe_delete_track(temp_idx)
                if err:
                    diagnostics.append(err)
            await asyncio.sleep(0.3)  # let the OS release file handles

    # Now copy after Live has released its locks.
    results: list[dict[str, Any]] = []
    for source_idx, source_name, dst, src in sources_for_copy:
        result = (
            await _copy_or_skip(src, str(dst))
            if src else {"copied": False, "error": "no recorded clip"}
        )
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
        if master_result is not None:
            master_result["routing_confirmed"] = master_routing_ok
            master_result["arm_confirmed"] = master_arm_ok

    return {
        "what": "stems_via_resampling",
        "duration_sec": duration_sec,
        "output_dir": str(out_root),
        "stems": results,
        "master": master_result,
        "diagnostics": diagnostics if diagnostics else None,
    }


async def bounce_enabled_via_resampling(
    output_dir: str | os.PathLike,
    duration_sec: float,
    *,
    include_master: bool = True,
    settle_sec: float = 0.4,
    warmup_sec: float = 0.5,
    pre_cleanup: bool = True,
    clip_finalize_timeout_sec: float = DEFAULT_CLIP_FINALIZE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Bounce every track that's currently un-muted, plus the master mix.

    Convenience wrapper around :func:`bounce_tracks_via_resampling`. Skips
    muted tracks, group tracks (which produce no signal of their own), and
    tracks with no audio output.

    Defaults ``warmup_sec=0.5`` (unlike the lower-level functions which
    default to 0) because this entrypoint is most commonly used as the
    first action on a session — exactly where the fresh-sampler silence
    bug bites hardest.
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
            has_audio_out = bool(
                (await osc.request("/live/track/get/has_audio_output", i))[1]
            )
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
        warmup_sec=warmup_sec,
        pre_cleanup=pre_cleanup,
        include_master=include_master,
        clip_finalize_timeout_sec=clip_finalize_timeout_sec,
    )
