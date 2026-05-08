"""Orchestrate realtime bounce capture using AbletonFullControlTape.

Two flows:

1. ``bounce_master_realtime`` — places playback at beat 0, fires the tape
   device on the Master track for `duration_sec`, returns the captured wav.
   Requires AbletonFullControlTape to be installed AND saved as `.amxd` AND dropped on
   the Master track. The user does the Save-As-Device step in Max once;
   `install_tape.py` puts the source `.maxpat` in place.

2. ``bounce_stems_realtime`` — same as above but iterates: for each track
   `t` in `[track_indices]`, set solo on `t` (auto-mutes others), capture,
   restore solo state. Final pass without solo for the full mix (optional).

Both flows respect the project's loop region: they set `loop_start=0` and
`loop_length=duration_sec_in_beats` so the arrangement plays the captured
window once. The user's previous loop state is restored afterwards.

If the M4L tape device isn't reachable (the user hasn't done the Save-As-
Device step yet, or hasn't dropped the .amxd on Master), every call returns
a structured error explaining the prerequisite. Nothing crashes.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Sequence

from ..osc_client import get_client
from ..tape.client import TapeClient
from ..tape.config import CaptureConfig


class BounceError(RuntimeError):
    """A bounce step failed in a way the caller can act on."""


def _safe_filename(s: str) -> str:
    """Strip illegal filename chars."""
    return re.sub(r'[<>:"/\\|?*]', "_", s).strip()[:80]


async def _project_tempo() -> float:
    osc = await get_client()
    return float((await osc.request("/live/song/get/tempo"))[0])


async def _ensure_loop_region(start_beats: float, length_beats: float) -> dict[str, Any]:
    """Set arrangement loop to (start, length). Returns the previous state for restore."""
    osc = await get_client()
    prev = {
        "loop": (await osc.request("/live/song/get/loop"))[0],
        "loop_start": (await osc.request("/live/song/get/loop_start"))[0],
        "loop_length": (await osc.request("/live/song/get/loop_length"))[0],
    }
    osc.send("/live/song/set/loop", 1)
    osc.send("/live/song/set/loop_start", float(start_beats))
    osc.send("/live/song/set/loop_length", float(length_beats))
    return prev


async def _restore_loop_region(prev: dict[str, Any]) -> None:
    osc = await get_client()
    osc.send("/live/song/set/loop_start", float(prev["loop_start"]))
    osc.send("/live/song/set/loop_length", float(prev["loop_length"]))
    osc.send("/live/song/set/loop", int(prev["loop"]))


async def _solo_only(track_indices: list[int], all_track_indices: list[int]) -> dict[int, bool]:
    """Solo only the listed tracks; un-solo others. Returns prior solo states."""
    osc = await get_client()
    prev: dict[int, bool] = {}
    target = set(int(i) for i in track_indices)
    for ti in all_track_indices:
        prev[ti] = bool((await osc.request("/live/track/get/solo", ti))[1])
        osc.send("/live/track/set/solo", ti, 1 if ti in target else 0)
    return prev


async def _restore_solo(prev: dict[int, bool]) -> None:
    osc = await get_client()
    for ti, was in prev.items():
        osc.send("/live/track/set/solo", ti, 1 if was else 0)


async def _stop_and_jump_to_zero() -> None:
    osc = await get_client()
    osc.send("/live/song/stop_playing")
    await asyncio.sleep(0.1)
    osc.send("/live/song/set/current_song_time", 0.0)
    await asyncio.sleep(0.1)


async def _all_track_indices() -> list[int]:
    osc = await get_client()
    n = int((await osc.request("/live/song/get/num_tracks"))[0])
    return list(range(n))


async def _verify_tape_reachable(tape: TapeClient) -> None:
    if not await tape.ping():
        raise BounceError(
            "AbletonFullControlTape did not respond on the configured backend. "
            "Either: (a) finish the Save-As-Device step in Max so the .amxd "
            "loads in Live, AND drop the .amxd on the track you want to "
            "capture (or Master, for full mix); or (b) switch to the "
            "loopback backend by setting ABLETON_MCP_CAPTURE_BACKEND=loopback "
            "and configuring a loopback driver (VB-Cable on Windows / "
            "BlackHole on macOS)."
        )


async def bounce_master_realtime(
    output_path: str | os.PathLike,
    duration_sec: float,
    *,
    cfg: CaptureConfig | None = None,
    pre_roll_sec: float = 0.5,
    post_roll_sec: float = 0.3,
) -> dict[str, Any]:
    """Capture the Master track output to a wav file via the tape device.

    Prerequisites:
      - AbletonFullControlTape compiled as `.amxd` and dropped on the **Master** track
        in Live.
      - Live in the foreground; arrangement contains the content you want
        captured starting at beat 0.

    Strategy:
      1. Stop playback, jump to beat 0.
      2. Set arrangement loop to (0, duration_in_beats) so playback plays
         the window cleanly without auto-restarting.
      3. Trigger tape recording for `duration_sec + post_roll_sec`.
      4. After ~`pre_roll_sec`, start playback.
      5. Wait for the tape `/tape/done` reply.
      6. Restore loop state, stop playback.
    """
    cfg = cfg or CaptureConfig.from_env()
    tape = TapeClient(cfg)
    await tape.start()
    try:
        await _verify_tape_reachable(tape)
        tempo = await _project_tempo()
        beats_per_sec = tempo / 60.0
        duration_beats = duration_sec * beats_per_sec
        prev_loop = await _ensure_loop_region(0.0, duration_beats)
        try:
            await _stop_and_jump_to_zero()
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            # Schedule the recording task; the tape device records for the
            # supplied duration. Our orchestrator starts playback after a
            # short pre-roll so the first downbeat actually lands inside the
            # capture window.
            record_duration = float(duration_sec + post_roll_sec)
            record_task = asyncio.create_task(
                tape.record(str(out), duration_sec=record_duration)
            )
            await asyncio.sleep(max(0.05, pre_roll_sec))
            osc = await get_client()
            osc.send("/live/song/start_playing")
            try:
                try:
                    result = await asyncio.wait_for(
                        record_task, timeout=record_duration + 5.0
                    )
                except asyncio.TimeoutError as exc:
                    record_task.cancel()
                    # Tell Max to stop sfrecord~ so it doesn't run forever.
                    try:
                        await tape.stop_recording()
                    except Exception:  # pragma: no cover — best-effort
                        pass
                    raise BounceError(
                        f"tape recording did not complete in time: {exc}"
                    ) from exc
                return {
                    "output_path": str(out),
                    "duration_sec": duration_sec,
                    "tape_result": result,
                }
            finally:
                # ALWAYS halt Live transport — including on timeout.
                osc.send("/live/song/stop_playing")
        finally:
            await _restore_loop_region(prev_loop)
    finally:
        await tape.stop()


async def bounce_stems_realtime(
    output_dir: str | os.PathLike,
    duration_sec: float,
    track_indices: Sequence[int],
    *,
    track_names: Sequence[str] | None = None,
    cfg: CaptureConfig | None = None,
    pre_roll_sec: float = 0.5,
    post_roll_sec: float = 0.3,
    include_full_mix: bool = True,
) -> dict[str, Any]:
    """Capture stems by soloing each track in turn, recording the Master output.

    Requires AbletonFullControlTape on the Master track. We solo one track at a time;
    the Master capture then contains only that track's audio (post-FX, post-
    send-routing).

    `track_indices` are the tracks you want as stems. `track_names` optional;
    used in output file names. If omitted, names are queried from Live.

    Returns a dict with paths for each stem and (optionally) the full mix.
    """
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if track_names is None:
        osc = await get_client()
        all_names = list(await osc.request("/live/song/get/track_names"))
        track_names = [
            all_names[ti] if ti < len(all_names) else f"track_{ti}"
            for ti in track_indices
        ]

    all_indices = await _all_track_indices()
    prev_solo: dict[int, bool] | None = None
    stems: list[dict[str, Any]] = []
    full_mix: dict[str, Any] | None = None
    try:
        for ti, tname in zip(track_indices, track_names):
            prev_solo = await _solo_only([int(ti)], all_indices)
            stem_path = out_root / f"stem_{int(ti):02d}_{_safe_filename(tname)}.wav"
            try:
                rec = await bounce_master_realtime(
                    stem_path,
                    duration_sec,
                    cfg=cfg,
                    pre_roll_sec=pre_roll_sec,
                    post_roll_sec=post_roll_sec,
                )
                stems.append({
                    "track_index": int(ti),
                    "track_name": tname,
                    "path": rec["output_path"],
                })
            finally:
                if prev_solo is not None:
                    await _restore_solo(prev_solo)
                    prev_solo = None
        if include_full_mix:
            mix_path = out_root / "full_mix.wav"
            full_mix = await bounce_master_realtime(
                mix_path,
                duration_sec,
                cfg=cfg,
                pre_roll_sec=pre_roll_sec,
                post_roll_sec=post_roll_sec,
            )
    finally:
        if prev_solo is not None:
            await _restore_solo(prev_solo)

    return {
        "output_dir": str(out_root),
        "duration_sec": duration_sec,
        "stems": stems,
        "full_mix": full_mix,
    }
