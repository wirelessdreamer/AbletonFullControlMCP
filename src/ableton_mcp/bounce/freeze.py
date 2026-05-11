"""Bounce via Live's `Track.freeze()` — offline (faster than real-time).

When Live freezes a track, it offline-renders the track's audio output to a
wav under ``<project>/Samples/Freezing/`` and stays there until the track
is unfrozen or flattened. Freeze runs faster than real-time on modern
hardware (roughly 30-50% of song-length wall-clock for typical sessions)
because Live processes the track without going through audio hardware.

This module wraps the freeze + harvest-the-wav loop into a stem-bounce path
that's a drop-in alternative to :mod:`ableton_mcp.bounce.resampling` for
per-track output. The trade-off:

- **Faster than real-time**: 2-4× speedup over the Resampling path on a
  busy session with sampled instruments.
- **Per-track only**: there's no ``Song.freeze()``. Freeze captures each
  track's *post-track-FX, pre-master-bus* signal. If the user's mix
  depends on master-bus FX (mastering chain, master sidechains) the freeze
  output won't match what Live would play — use the Resampling path for
  master-aware captures.
- **Length is clip-driven, not song-driven**: the frozen wav covers the
  range of clips on that track. A track with clips only in bars 1-16 of
  a 32-bar song produces a 16-bar wav. The Resampling path always
  produces a wav of length ``duration_sec``.
- **Requires the project to be saved**: Live writes the freeze wav to
  ``<project>/Samples/Freezing/``. An unsaved project has no
  ``<project>`` and Live's freeze call fails. We surface this as a clear
  precondition error.

Implementation: snapshot the freezing folder, call ``track.freeze()`` via
the bridge, poll ``track.is_frozen`` until done (with timeout), diff the
folder listing to find the new wav, copy to ``output_dir``. Tracks that
were already frozen when we started are left as-is on cleanup; tracks we
froze are unfrozen (``keep_frozen=False`` default) to restore the user's
session state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Sequence

from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..osc_client import get_client

log = logging.getLogger(__name__)

# How long to wait for ``Track.freeze()`` to finish before giving up on a
# single track. Live's freeze is usually well under the song length, but
# busy sessions with frozen tail effects (long reverb tails) can push past
# 2× song-length. Default 5 minutes is generous; callers can override per-call.
DEFAULT_FREEZE_TIMEOUT_SEC = 300.0
DEFAULT_FREEZE_POLL_INTERVAL_SEC = 0.25

# Live.Track.freezing_state values.
FREEZING_STATE_NORMAL = 0
FREEZING_STATE_FROZEN = 1
FREEZING_STATE_FLATTENING = 2


class FreezeBounceError(RuntimeError):
    """Raised when the freeze-based bounce path can't proceed (project
    unsaved, freeze API missing, etc.)."""


def _safe_filename(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s).strip()[:80] or "track"


async def _track_count() -> int:
    osc = await get_client()
    return int((await osc.request("/live/song/get/num_tracks"))[0])


async def _track_names() -> list[str]:
    osc = await get_client()
    return list(await osc.request("/live/song/get/track_names"))


async def _is_frozen(track_index: int) -> int:
    """Return the freezing_state (0/1/2) for a track via the bridge."""
    bridge = get_bridge_client()
    reply = await bridge.call("track.is_frozen", track_index=int(track_index))
    return int(reply.get("freezing_state", 0))


async def _freezing_dir_snapshot() -> tuple[str | None, dict[str, float]]:
    """List the Freezing folder as ``(dir_path, {path: mtime})``.

    Returns ``(None, {})`` if the project hasn't been saved yet.
    """
    bridge = get_bridge_client()
    reply = await bridge.call("project.list_freezing_dir")
    freezing_dir = reply.get("freezing_dir")
    files = reply.get("files") or []
    return freezing_dir, {f["path"]: float(f["mtime"]) for f in files}


async def _freeze_track(track_index: int) -> None:
    bridge = get_bridge_client()
    await bridge.call("track.freeze", track_index=int(track_index))


async def _unfreeze_track(track_index: int) -> None:
    bridge = get_bridge_client()
    await bridge.call("track.unfreeze", track_index=int(track_index))


async def _wait_for_frozen(
    track_index: int,
    *,
    timeout_sec: float = DEFAULT_FREEZE_TIMEOUT_SEC,
    poll_interval_sec: float = DEFAULT_FREEZE_POLL_INTERVAL_SEC,
) -> bool:
    """Poll ``track.is_frozen`` until ``freezing_state == 1`` (frozen) or timeout."""
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            state = await _is_frozen(track_index)
            if state == FREEZING_STATE_FROZEN:
                return True
        except AbletonBridgeError as exc:  # transient bridge hiccup, keep polling
            log.debug("is_frozen poll error on track %d: %s", track_index, exc)
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_interval_sec)


def _new_file_in_dir(
    pre_snapshot: dict[str, float],
    post_listing: list[dict[str, Any]],
) -> str | None:
    """Pick the file in ``post_listing`` that's newest and not in
    ``pre_snapshot`` (or has a newer mtime than its pre snapshot).

    Returns None if no candidate. A "candidate" is a file whose path
    wasn't present before OR whose mtime increased.
    """
    candidates: list[tuple[float, str]] = []
    for f in post_listing:
        path = f["path"]
        mtime = float(f["mtime"])
        pre_mtime = pre_snapshot.get(path)
        if pre_mtime is None or mtime > pre_mtime + 0.001:
            candidates.append((mtime, path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


async def _copy_or_skip(
    src: str | None,
    dst: str,
    max_retries: int = 6,
    retry_delay: float = 0.4,
) -> dict[str, Any]:
    """Copy src→dst with retries — Live may briefly hold a lock just after freeze."""
    if not src:
        return {"copied": False, "error": "no freeze wav found in Samples/Freezing/"}
    if not os.path.exists(src):
        return {"copied": False, "error": f"freeze wav not found on disk: {src}"}
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
        except (PermissionError, OSError) as exc:
            last_err = exc
            await asyncio.sleep(retry_delay)
    return {
        "copied": False,
        "source": src,
        "error": f"copy failed after {max_retries} attempts: {last_err}",
    }


async def bounce_tracks_via_freeze(
    track_indices: Sequence[int],
    output_dir: str | os.PathLike,
    *,
    keep_frozen: bool = False,
    freeze_timeout_sec: float = DEFAULT_FREEZE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Bounce each listed track to its own wav via Live's offline freeze pass.

    The trade-offs vs. :func:`bounce_tracks_via_resampling` are documented
    in this module's header. Use the Resampling path when the master mix bus
    matters; this one when you want each track's pre-master-bus output as
    fast as Live can produce it.

    Args:
        track_indices: which source tracks to capture.
        output_dir: directory to copy the resulting wavs to. Each wav is
            named ``stem_<idx>_<track_name>.wav``.
        keep_frozen: if False (default), tracks we froze during this call
            are unfrozen on completion to restore the user's session
            state. Tracks that were already frozen before the call are
            never touched. Set True if you want to preserve the freezes
            (e.g. you're freezing in bulk anyway).
        freeze_timeout_sec: per-track timeout for ``Track.freeze()`` to
            complete.

    Returns:
        ``{"what": "stems_via_freeze", "stems": [...],
           "diagnostics": [...] | None}`` — same shape as the Resampling
        equivalent's ``stems`` field for drop-in compatibility.
    """
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    diagnostics: list[str] = []

    # 1. Confirm project is saved (freeze needs a directory to write to).
    pre_dir, pre_files = await _freezing_dir_snapshot()
    if pre_dir is None:
        raise FreezeBounceError(
            "Project must be saved before freeze-mode bouncing. Live writes "
            "freeze wavs to <project>/Samples/Freezing/, and an unsaved "
            "project has no <project> directory. Save the set (Ctrl-S) "
            "and retry, or use mode='resampling' for the realtime path."
        )

    # 2. Validate indices + grab names for filenames.
    names = await _track_names()
    for ti in track_indices:
        if ti < 0 or ti >= len(names):
            raise ValueError(f"track_index {ti} out of range (have {len(names)})")

    # 3. Per-track: snapshot pre-state, freeze if needed, poll, find new wav, copy.
    #    We accumulate "tracks we froze" so we can unfreeze them in cleanup.
    we_froze: list[int] = []
    results: list[dict[str, Any]] = []

    for ti in track_indices:
        source_name = names[ti]
        # Snapshot the dir BEFORE this track's freeze.
        per_track_pre_dir, per_track_pre_files = await _freezing_dir_snapshot()

        try:
            was_frozen = await _is_frozen(ti) == FREEZING_STATE_FROZEN
        except AbletonBridgeError as exc:
            diagnostics.append(
                f"track {ti} ({source_name!r}): could not query is_frozen "
                f"({exc}) — skipping"
            )
            results.append({
                "source_track_index": ti,
                "source_track_name": source_name,
                "copied": False,
                "error": f"is_frozen query failed: {exc}",
            })
            continue

        froze_now = False
        if not was_frozen:
            try:
                await _freeze_track(ti)
            except AbletonBridgeError as exc:
                diagnostics.append(
                    f"track {ti} ({source_name!r}): freeze call failed ({exc})"
                )
                results.append({
                    "source_track_index": ti,
                    "source_track_name": source_name,
                    "copied": False,
                    "error": f"freeze call failed: {exc}",
                })
                continue
            ok = await _wait_for_frozen(ti, timeout_sec=freeze_timeout_sec)
            if not ok:
                diagnostics.append(
                    f"track {ti} ({source_name!r}): freeze did not complete "
                    f"within {freeze_timeout_sec:.0f}s"
                )
                results.append({
                    "source_track_index": ti,
                    "source_track_name": source_name,
                    "copied": False,
                    "error": f"freeze timed out after {freeze_timeout_sec:.0f}s",
                })
                continue
            we_froze.append(ti)
            froze_now = True

        # 4. Locate the freeze wav by diffing the dir.
        _, post_files = await _freezing_dir_snapshot()
        post_listing = [
            {"path": p, "mtime": m} for p, m in post_files.items()
        ]
        new_wav = _new_file_in_dir(per_track_pre_files, post_listing)
        if new_wav is None and was_frozen:
            # Already-frozen tracks have an existing wav we should still
            # harvest. Pick the newest .wav whose path is associated with
            # this track. Live's filenames are random UUIDs, so we can't
            # match by name; we take the newest as a best-effort.
            wavs_only = [
                (m, p) for p, m in post_files.items() if p.lower().endswith(".wav")
            ]
            if wavs_only:
                wavs_only.sort(reverse=True)
                new_wav = wavs_only[0][1]
                diagnostics.append(
                    f"track {ti} ({source_name!r}): track was already frozen; "
                    f"harvested newest freeze wav as a best-effort match "
                    f"(may not be from this track)"
                )

        dst = out_root / f"stem_{ti:02d}_{_safe_filename(source_name)}.wav"
        copy_result = await _copy_or_skip(new_wav, str(dst))
        results.append({
            "source_track_index": ti,
            "source_track_name": source_name,
            "froze_now": froze_now,
            "was_frozen": was_frozen,
            **copy_result,
        })

    # 5. Cleanup: unfreeze only the tracks WE froze, unless keep_frozen.
    if not keep_frozen and we_froze:
        for ti in we_froze:
            try:
                await _unfreeze_track(ti)
            except AbletonBridgeError as exc:
                diagnostics.append(
                    f"track {ti}: unfreeze failed on cleanup ({exc}) — user "
                    f"may want to unfreeze manually in Live"
                )

    return {
        "what": "stems_via_freeze",
        "output_dir": str(out_root),
        "stems": results,
        "freezing_dir": pre_dir,
        "we_froze": we_froze,
        "kept_frozen": keep_frozen,
        "diagnostics": diagnostics if diagnostics else None,
    }


async def bounce_enabled_via_freeze(
    output_dir: str | os.PathLike,
    *,
    keep_frozen: bool = False,
    freeze_timeout_sec: float = DEFAULT_FREEZE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Freeze + harvest every un-muted, audio-producing track.

    Mirrors :func:`bounce_enabled_via_resampling`'s track selection logic
    but routes through the offline freeze path. There is no master-mix
    equivalent here — freeze is per-track only.
    """
    osc = await get_client()
    n = await _track_count()
    keep: list[int] = []
    for i in range(n):
        if (await osc.request("/live/track/get/mute", i))[1]:
            continue
        try:
            has_audio_out = bool(
                (await osc.request("/live/track/get/has_audio_output", i))[1]
            )
        except Exception:
            has_audio_out = True
        if not has_audio_out:
            continue
        keep.append(i)
    return await bounce_tracks_via_freeze(
        keep,
        output_dir,
        keep_frozen=keep_frozen,
        freeze_timeout_sec=freeze_timeout_sec,
    )
