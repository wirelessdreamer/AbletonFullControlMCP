"""MCP tools for bouncing the current set to wav/mp3 (full mix + stems).

These wrap ``ableton_mcp.bounce.*``. All realtime capture goes through Live's
built-in Resampling input — no Max for Live, no loopback driver.

Realtime (one playback pass, ``duration_sec`` of wall-clock):
    bounce_song(output_path, duration_sec)         master mix → wav (+ optional mp3)
    bounce_tracks(track_indices, output_dir, ...)  per-track stems in parallel
    bounce_enabled(output_dir, duration_sec, ...)  every un-muted track + master

Every realtime tool accepts ``background=True``: the call returns a
``job_id`` immediately and the render continues server-side (poll
``bounce_job_status``). Use it for anything longer than a couple of
minutes — MCP clients cap or background long tool calls — and for batch
renders where the agent orchestrates song after song.

Offline (work on any wavs already on disk):
    bounce_encode_mp3(wav_path, mp3_path, bitrate_kbps)
    bounce_mix_stems(stem_paths, output_path)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP

from ..bounce import (
    FreezeBounceError,
    bounce_enabled_via_freeze,
    bounce_enabled_via_resampling,
    bounce_region_all_active_via_resampling,
    bounce_region_via_resampling,
    bounce_song_via_resampling,
    bounce_tracks_via_freeze,
    bounce_tracks_via_resampling,
    encode_wav_to_mp3,
    mix_stems_to_master,
)
from ..bounce import jobs as _jobs
from ..bounce.mp3 import FFmpegMissing

_ProgressCB = Callable[[float, str], Awaitable[None]]


def _start_background(
    operation: str, runner: "_jobs.JobRunner", *, exclusive: bool = True
) -> dict[str, Any]:
    """Launch a long operation as a background job.

    Shared by the realtime bounce tools (exclusive — they drive Live's
    transport) and by pure file-math tools like song_make_variations
    (``exclusive=False``, so they never queue behind a bounce).
    """
    try:
        job = _jobs.start_job(operation, runner, exclusive=exclusive)
    except _jobs.BounceJobError as e:
        active = _jobs.active_exclusive_job()
        return {
            "status": "busy",
            "error": str(e),
            "active_job": active.snapshot() if active else None,
        }
    return {
        "status": "started",
        "job_id": job.job_id,
        "operation": operation,
        "hint": "poll bounce_job_status(job_id) — the result dict appears there "
                "when state becomes 'done'",
    }


async def _note(progress_cb: _ProgressCB | None, progress: float, message: str) -> None:
    """Best-effort coarse progress for engines without per-second callbacks."""
    if progress_cb is None:
        return
    try:
        await progress_cb(progress, message)
    except Exception:  # pragma: no cover — a broken notifier must not kill a bounce
        pass


def register(mcp: FastMCP) -> None:

    # ============================================================
    # The "just give me wavs" surface — Live's resampling track.
    # No M4L, no loopback driver. Realtime; one playback pass.
    # ============================================================

    @mcp.tool()
    async def bounce_song(
        output_path: str,
        duration_sec: float,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
        warmup_sec: float = 0.0,
        background: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Bounce the entire song (master mix) to a wav (and optionally mp3).

        Realtime — takes ``duration_sec`` of wall-clock + ~1s overhead. Creates
        a temp audio track with input = Resampling, arms it, records the
        arrangement for the requested duration, copies the resulting wav to
        ``output_path``, then deletes the temp track. The user's existing
        tracks are not modified.

        If ``encode_mp3`` is True and ffmpeg is on PATH, also writes an mp3
        next to the wav at ``bitrate_kbps``.

        ``warmup_sec`` runs a brief no-record playback before the real
        capture to prime samplers + the audio engine. Use 0.3-0.5 s if the
        first second of your bounce comes out silent on fresh sessions
        (Live's samplers lazy-load on first trigger). Default 0 (off).

        ``background=True`` returns ``{status: "started", job_id}``
        immediately and renders in a server-side job — use for pieces
        longer than ~2 minutes or unattended batch renders; poll
        ``bounce_job_status(job_id)`` for progress and the final result.
        Only one bounce job can run at a time (Live has one transport).

        **Progress + cancellation** (foreground path): when invoked
        through an MCP client that supports ``notifications/progress``,
        this tool emits per-second progress updates during the recording
        phase (e.g. "recording 12.0/240.0 s") plus phase-boundary
        notifications at setup/harvest/cleanup boundaries. Cancellation
        via ``notifications/cancelled`` (or ``bounce_job_cancel`` for
        jobs) is handled cleanly: transport is stopped, record mode
        disabled, the temp track deleted, and the partial bounce raised
        as a CancelledError without leaving the user's session in a
        half-armed state.
        """
        async def _run(progress_cb: _ProgressCB | None) -> dict[str, Any]:
            try:
                wav_result = await bounce_song_via_resampling(
                    output_path, duration_sec, warmup_sec=warmup_sec,
                    progress_callback=progress_cb,
                )
            except Exception as e:
                return {"status": "error", "error": f"{type(e).__name__}: {e}"}
            if not wav_result.get("copied"):
                return {"status": "error", **wav_result}
            out: dict[str, Any] = {"status": "ok", "wav": wav_result}
            if encode_mp3:
                mp3_path = output_path[:-4] + ".mp3" if output_path.lower().endswith(".wav") else output_path + ".mp3"
                try:
                    out["mp3"] = encode_wav_to_mp3(output_path, mp3_path, bitrate_kbps=bitrate_kbps)
                except FFmpegMissing as e:
                    out["mp3_skipped"] = str(e)
                except Exception as e:
                    out["mp3_skipped"] = f"{type(e).__name__}: {e}"
            return out

        if background:
            return _start_background("bounce_song", _run)

        # Foreground: bind progress to the MCP ctx (None when absent — the
        # bounce function just no-ops the notifier in that case).
        async def _on_progress(progress: float, message: str) -> None:
            if ctx is not None:
                await ctx.report_progress(progress, 1.0, message=message)

        return await _run(_on_progress if ctx is not None else None)

    @mcp.tool()
    async def bounce_tracks(
        track_indices: list[int],
        output_dir: str,
        duration_sec: float,
        include_master: bool = False,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
        warmup_sec: float = 0.0,
        mode: str = "resampling",
        keep_frozen: bool = False,
        background: bool = False,
    ) -> dict[str, Any]:
        """Bounce specific tracks to per-track wav stems.

        Two pipelines, picked via ``mode``:

        - ``mode="resampling"`` (default) — realtime, one playback pass.
          Live records all listed tracks in parallel during a single
          playback pass; wall-clock is ``duration_sec`` regardless of
          track count. Captures each track's post-master-bus signal via a
          Resampling track. ``duration_sec`` and ``warmup_sec`` apply.
        - ``mode="freeze"`` — offline via ``Track.freeze()``, faster than
          realtime (~2-4× on a modern machine). Per-track wavs come
          straight out of Live's ``Samples/Freezing/`` folder.
          ``duration_sec`` is ignored (Live writes whatever length the
          track's clips span). Master-bus FX are NOT in the output —
          freeze captures pre-master signal. Requires the project to be
          saved (Live needs ``<project>/Samples/Freezing/``).
          ``keep_frozen=True`` leaves tracks frozen after the bounce;
          default unfreezes them to restore session state.

        Set ``include_master=True`` to also capture the master mix.
        ``include_master`` is only honored when ``mode="resampling"`` —
        freeze has no master equivalent.

        ``background=True`` returns ``{status: "started", job_id}``
        immediately and renders in a server-side job (coarse progress
        only for this operation); poll ``bounce_job_status(job_id)``.
        """
        if mode not in ("resampling", "freeze"):
            return {
                "status": "error",
                "error": f"unknown mode {mode!r}; must be 'resampling' or 'freeze'",
            }
        if mode == "freeze" and include_master:
            return {
                "status": "error",
                "error": "include_master is not supported with mode='freeze' "
                         "(freeze captures per-track pre-master signal only). "
                         "Use mode='resampling' for a master-aware bounce.",
            }

        async def _run(progress_cb: _ProgressCB | None) -> dict[str, Any]:
            await _note(progress_cb, 0.0, f"bounce_tracks mode={mode} running "
                                          "(coarse progress only)")
            try:
                if mode == "freeze":
                    r = await bounce_tracks_via_freeze(
                        track_indices, output_dir,
                        keep_frozen=keep_frozen,
                    )
                else:
                    r = await bounce_tracks_via_resampling(
                        track_indices, output_dir, duration_sec,
                        include_master=include_master,
                        warmup_sec=warmup_sec,
                    )
            except FreezeBounceError as e:
                return {"status": "error", "error": str(e), "mode": mode}
            except Exception as e:
                return {"status": "error", "error": f"{type(e).__name__}: {e}", "mode": mode}
            await _note(progress_cb, 0.9, "capture complete; encoding mp3s"
                        if encode_mp3 else "capture complete")
            if encode_mp3:
                r["mp3s"] = []
                for s in r.get("stems", []):
                    if not s.get("copied"):
                        continue
                    wav = s["output_path"]
                    mp3 = wav[:-4] + ".mp3"
                    try:
                        r["mp3s"].append(encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps))
                    except FFmpegMissing as e:
                        r["mp3s"].append({"error": str(e), "skipped_for": wav})
                        break
                    except Exception as e:
                        r["mp3s"].append({"error": f"{type(e).__name__}: {e}", "skipped_for": wav})
                if r.get("master") and r["master"].get("copied"):
                    wav = r["master"]["output_path"]
                    mp3 = wav[:-4] + ".mp3"
                    try:
                        r["mp3s"].append(encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps))
                    except Exception as e:
                        r["mp3s"].append({"error": f"{type(e).__name__}: {e}", "skipped_for": wav})
            r.setdefault("status", "ok")
            return r

        if background:
            return _start_background("bounce_tracks", _run)
        return await _run(None)

    @mcp.tool()
    async def bounce_enabled(
        output_dir: str,
        duration_sec: float,
        include_master: bool = True,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
        warmup_sec: float = 0.5,
        mode: str = "resampling",
        keep_frozen: bool = False,
        background: bool = False,
    ) -> dict[str, Any]:
        """Bounce every un-muted track (+ optionally master) as separate stems.

        Two pipelines, picked via ``mode``:

        - ``mode="resampling"`` (default) — realtime, one playback pass.
          ``warmup_sec`` defaults to 0.5 s here to prime samplers on
          fresh sessions. ``include_master=True`` adds a Resampling
          track for the full mix.
        - ``mode="freeze"`` — offline via ``Track.freeze()``, faster than
          realtime. ``duration_sec`` is ignored. ``include_master`` is
          not supported (freeze has no master equivalent). Requires the
          project to be saved. ``keep_frozen=True`` leaves tracks frozen
          after the bounce.

        Queries every track's mute state, skips muted ones and tracks
        without audio output, then runs the chosen pipeline on the rest.

        ``background=True`` returns ``{status: "started", job_id}``
        immediately and renders in a server-side job (coarse progress
        only for this operation); poll ``bounce_job_status(job_id)``.
        """
        if mode not in ("resampling", "freeze"):
            return {
                "status": "error",
                "error": f"unknown mode {mode!r}; must be 'resampling' or 'freeze'",
            }
        if mode == "freeze" and include_master:
            return {
                "status": "error",
                "error": "include_master is not supported with mode='freeze' "
                         "(freeze captures per-track pre-master signal only). "
                         "Use mode='resampling' for a master-aware bounce.",
            }

        async def _run(progress_cb: _ProgressCB | None) -> dict[str, Any]:
            await _note(progress_cb, 0.0, f"bounce_enabled mode={mode} running "
                                          "(coarse progress only)")
            try:
                if mode == "freeze":
                    r = await bounce_enabled_via_freeze(
                        output_dir, keep_frozen=keep_frozen,
                    )
                else:
                    r = await bounce_enabled_via_resampling(
                        output_dir, duration_sec,
                        include_master=include_master,
                        warmup_sec=warmup_sec,
                    )
            except FreezeBounceError as e:
                return {"status": "error", "error": str(e), "mode": mode}
            except Exception as e:
                return {"status": "error", "error": f"{type(e).__name__}: {e}", "mode": mode}
            await _note(progress_cb, 0.9, "capture complete; encoding mp3s"
                        if encode_mp3 else "capture complete")
            if encode_mp3:
                r["mp3s"] = []
                for s in r.get("stems", []):
                    if not s.get("copied"):
                        continue
                    wav = s["output_path"]
                    try:
                        r["mp3s"].append(encode_wav_to_mp3(wav, wav[:-4] + ".mp3", bitrate_kbps=bitrate_kbps))
                    except FFmpegMissing as e:
                        r["mp3s"].append({"error": str(e), "skipped_for": wav})
                        break
                    except Exception as e:
                        r["mp3s"].append({"error": f"{type(e).__name__}: {e}", "skipped_for": wav})
                if r.get("master") and r["master"].get("copied"):
                    wav = r["master"]["output_path"]
                    try:
                        r["mp3s"].append(encode_wav_to_mp3(wav, wav[:-4] + ".mp3", bitrate_kbps=bitrate_kbps))
                    except Exception as e:
                        r["mp3s"].append({"error": f"{type(e).__name__}: {e}", "skipped_for": wav})
            r.setdefault("status", "ok")
            return r

        if background:
            return _start_background("bounce_enabled", _run)
        return await _run(None)

    @mcp.tool()
    async def bounce_region(
        output_dir: str,
        start_beats: float,
        end_beats: float,
        track_indices: list[int] | None = None,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
        warmup_sec: float = 0.0,
        background: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Bounce a specific beat range of the arrangement.

        Region-bounded sibling of ``bounce_song`` / ``bounce_tracks``. The
        building block for the mix-aware shaping stack (see
        ``docs/MIX_AWARE_SHAPING.md``) — section analysis, masking
        diagnosis, and A/B verification all bounce a region rather than
        the whole song.

        - ``track_indices=None`` (default) → bounce the master mix for
          the region to ``<output_dir>/master_<start>-<end>.wav``.
        - ``track_indices=[...]`` → bounce per-track stems for the
          region to ``<output_dir>/stem_<idx>_<name>.wav``, captured in
          one playback pass.

        Wall-clock cost is real-time: the region's duration in seconds
        (computed from beats + Live's current tempo). 32 bars at 120 BPM
        is ~64 s of wall clock. For ultra-fast iteration on a saved
        project use ``bounce_tracks(mode="freeze")`` for whole-track
        stems and slice them post-hoc.

        Args:
            output_dir: directory to write the captured wav(s).
            start_beats: region start in beats (>= 0).
            end_beats: region end in beats (> start_beats).
            track_indices: tracks to capture, or None for master.
            encode_mp3 / bitrate_kbps / warmup_sec — see ``bounce_song``.
            background: return ``{status: "started", job_id}`` immediately
                and render in a server-side job with per-second progress;
                poll ``bounce_job_status(job_id)``.
        """
        async def _run(progress_cb: _ProgressCB | None) -> dict[str, Any]:
            try:
                r = await bounce_region_via_resampling(
                    output_dir, float(start_beats), float(end_beats),
                    track_indices=track_indices, warmup_sec=warmup_sec,
                    progress_callback=progress_cb,
                )
            except ValueError as e:
                return {"status": "error", "error": str(e)}
            except Exception as e:
                return {"status": "error", "error": f"{type(e).__name__}: {e}"}
            if encode_mp3 and r.get("kind") == "region_master":
                # Master path: one wav.
                wav = r.get("output_path")
                if wav:
                    mp3 = wav[:-4] + ".mp3" if wav.lower().endswith(".wav") else wav + ".mp3"
                    try:
                        r["mp3"] = encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps)
                    except FFmpegMissing as e:
                        r["mp3_skipped"] = str(e)
                    except Exception as e:
                        r["mp3_skipped"] = f"{type(e).__name__}: {e}"
            elif encode_mp3 and r.get("kind") == "region_stems":
                r["mp3s"] = []
                for s in r.get("stems", []):
                    if not s.get("copied"):
                        continue
                    wav = s["output_path"]
                    mp3 = wav[:-4] + ".mp3"
                    try:
                        r["mp3s"].append(encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps))
                    except FFmpegMissing as e:
                        r["mp3s"].append({"error": str(e), "skipped_for": wav})
                        break
                    except Exception as e:
                        r["mp3s"].append({"error": f"{type(e).__name__}: {e}", "skipped_for": wav})
            r.setdefault("status", "ok")
            return r

        if background:
            return _start_background("bounce_region", _run)

        async def _on_progress(progress: float, message: str) -> None:
            if ctx is not None:
                await ctx.report_progress(progress, 1.0, message=message)

        return await _run(_on_progress if ctx is not None else None)

    @mcp.tool()
    async def bounce_region_all_active(
        output_dir: str,
        start_beats: float,
        end_beats: float,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
        warmup_sec: float = 0.0,
        background: bool = False,
    ) -> dict[str, Any]:
        """Bounce every un-muted, audio-producing track for a beat region.

        Layer 1.3 of the mix-aware shaping stack (see
        ``docs/MIX_AWARE_SHAPING.md``). Combines the track-selection
        logic of ``bounce_enabled`` with the region-bounded primitive
        ``bounce_region``. Produces per-track stems for the requested
        region — the input format Layer 2's spectral / masking analysis
        needs.

        Skips muted tracks, temp tracks from previous crashed bounces,
        and tracks without audio output (group folders, MIDI without
        instrument).

        ``background=True`` returns ``{status: "started", job_id}``
        immediately and renders in a server-side job (coarse progress
        only for this operation); poll ``bounce_job_status(job_id)``.
        """
        async def _run(progress_cb: _ProgressCB | None) -> dict[str, Any]:
            await _note(progress_cb, 0.0, "bounce_region_all_active running "
                                          "(coarse progress only)")
            try:
                r = await bounce_region_all_active_via_resampling(
                    output_dir, float(start_beats), float(end_beats),
                    warmup_sec=warmup_sec,
                )
            except ValueError as e:
                return {"status": "error", "error": str(e)}
            except Exception as e:
                return {"status": "error", "error": f"{type(e).__name__}: {e}"}
            await _note(progress_cb, 0.9, "capture complete; encoding mp3s"
                        if encode_mp3 else "capture complete")
            if encode_mp3:
                r["mp3s"] = []
                for s in r.get("stems", []):
                    if not s.get("copied"):
                        continue
                    wav = s["output_path"]
                    mp3 = wav[:-4] + ".mp3"
                    try:
                        r["mp3s"].append(encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps))
                    except FFmpegMissing as e:
                        r["mp3s"].append({"error": str(e), "skipped_for": wav})
                        break
                    except Exception as e:
                        r["mp3s"].append({"error": f"{type(e).__name__}: {e}", "skipped_for": wav})
            r.setdefault("status", "ok")
            return r

        if background:
            return _start_background("bounce_region_all_active", _run)
        return await _run(None)

    # ============================================================
    # Offline post-processing — operate on wav files already on disk.
    # ============================================================

    @mcp.tool()
    async def bounce_encode_mp3(
        wav_path: str,
        mp3_path: str,
        bitrate_kbps: int = 192,
        quality: int | None = None,
    ) -> dict[str, Any]:
        """Encode a wav (or any ffmpeg-readable file) to mp3 via libmp3lame.

        Common bitrates: 128 (small), 192 (default — transparent for most music),
        256, 320 (max). Set `quality` to use VBR (0=best, 9=worst); overrides
        bitrate. Requires ffmpeg on PATH (https://ffmpeg.org/).
        """
        try:
            return encode_wav_to_mp3(wav_path, mp3_path, bitrate_kbps=bitrate_kbps, quality=quality)
        except FFmpegMissing as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:  # surface ffmpeg errors as data, not exceptions
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    @mcp.tool()
    async def bounce_mix_stems(
        stem_paths: list[str],
        output_path: str,
        normalize: bool = True,
        headroom_db: float = 0.1,
    ) -> dict[str, Any]:
        """Sum N stem WAVs into a single mix WAV. Pure offline operation.

        All stems must share a sample rate. Normalisation peak-limits to
        -headroom_db dBFS. Use this when you have stems on disk (from any
        source — `bounce_tracks`, freeze export, manual export from Live)
        and you also want a single full-mix file.
        """
        try:
            return mix_stems_to_master(
                stem_paths, output_path,
                normalize=normalize, headroom_db=headroom_db,
            )
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    # ============================================================
    # Background bounce jobs — start with background=True on any
    # realtime bounce tool, then manage with these.
    # ============================================================

    @mcp.tool()
    async def bounce_job_status(job_id: str) -> dict[str, Any]:
        """Status of a background bounce job.

        Returns `{status: "ok", job: {job_id, operation, state, progress,
        message, elapsed_sec, result, error}}`. ``state`` is one of
        ``running`` / ``done`` / ``error`` / ``cancelled``; when ``done``,
        ``result`` holds exactly the dict the foreground tool would have
        returned (wav paths, mp3s, ...).

        Batch pattern (e.g. 100-song render): load song → start bounce
        with ``background=True`` → poll this every ~10 s → on ``done``,
        verify ``result`` and start the next song. The registry is
        process-local: it empties if the MCP server restarts.
        """
        try:
            job = _jobs.get_job(job_id)
        except _jobs.BounceJobError as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "job": job.snapshot()}

    @mcp.tool()
    async def bounce_job_cancel(job_id: str) -> dict[str, Any]:
        """Cancel a running background bounce job and wait for cleanup.

        The engine's cancellation contract restores Live's state (stops
        transport, disables record mode, deletes the temp track) before
        the job is marked ``cancelled``. Cancelling a finished job is a
        no-op that returns its final state.
        """
        try:
            job = await _jobs.cancel_job(job_id)
        except _jobs.BounceJobError as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "job": job.snapshot()}

    @mcp.tool()
    async def bounce_job_list() -> dict[str, Any]:
        """List background bounce jobs, newest first.

        Includes the running job (at most one — Live has a single
        transport) and up to the last 20 finished ones. Use to recover a
        job_id after losing track of it mid-batch.
        """
        jobs = _jobs.list_jobs()
        return {"status": "ok", "jobs": jobs, "count": len(jobs)}

