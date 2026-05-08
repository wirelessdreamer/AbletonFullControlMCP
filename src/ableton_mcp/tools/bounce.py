"""MCP tools for bouncing the current set to wav/mp3 (full mix + stems).

These wrap `ableton_mcp.bounce.*`. Two classes of tool:

Offline (work today, no extra setup):
    bounce_encode_mp3(wav_path, mp3_path, bitrate_kbps)
    bounce_mix_stems(stem_paths, output_path)

Realtime (require AbletonFullControlTape on Master, or a configured loopback driver):
    bounce_full_mix(output_path, duration_sec)
    bounce_stems(output_dir, duration_sec, track_indices)
    bounce_full_pipeline(output_dir, duration_sec, track_indices, formats)

If the tape device isn't reachable, every realtime tool returns a structured
error explaining how to finish setup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bounce import (
    BounceError,
    bounce_enabled_via_resampling,
    bounce_master_realtime,
    bounce_song_via_resampling,
    bounce_stems_realtime,
    bounce_tracks_via_resampling,
    encode_wav_to_mp3,
    mix_stems_to_master,
)
from ..bounce.mp3 import FFmpegMissing


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
    ) -> dict[str, Any]:
        """Bounce the entire song (master mix) to a wav (and optionally mp3).

        Realtime — takes ``duration_sec`` of wall-clock + ~1s overhead. Creates
        a temp audio track with input = Resampling, arms it, records the
        arrangement for the requested duration, copies the resulting wav to
        ``output_path``, then deletes the temp track. The user's existing
        tracks are not modified.

        If ``encode_mp3`` is True and ffmpeg is on PATH, also writes an mp3
        next to the wav at ``bitrate_kbps``.
        """
        try:
            wav_result = await bounce_song_via_resampling(output_path, duration_sec)
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

    @mcp.tool()
    async def bounce_tracks(
        track_indices: list[int],
        output_dir: str,
        duration_sec: float,
        include_master: bool = False,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
    ) -> dict[str, Any]:
        """Bounce specific tracks to per-track wav stems in ONE realtime pass.

        Live records all listed tracks in parallel during a single playback
        pass — wall-clock is ``duration_sec`` regardless of how many tracks.
        For each source track, a temp audio track is created with input
        routed to that source, armed, recorded; the resulting wav is copied
        to ``output_dir/stem_<idx>_<name>.wav`` and the temp track deleted.

        Set ``include_master=True`` to also capture the master mix in the
        same pass (extra resampling track).
        """
        try:
            r = await bounce_tracks_via_resampling(
                track_indices, output_dir, duration_sec, include_master=include_master,
            )
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}
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

    @mcp.tool()
    async def bounce_enabled(
        output_dir: str,
        duration_sec: float,
        include_master: bool = True,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
    ) -> dict[str, Any]:
        """Bounce every un-muted track (+ master) as separate stems in one pass.

        Convenience wrapper: queries every track's mute state, skips muted
        ones and tracks without audio output, then runs bounce_tracks on the
        rest. ``include_master=True`` adds a Resampling track for the full mix.
        """
        try:
            r = await bounce_enabled_via_resampling(
                output_dir, duration_sec, include_master=include_master,
            )
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}
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

    # ============================================================
    # Pre-existing tools below (offline post-processing + tape device).
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
        source — tape capture, freeze export, manual export from Live) and
        you also want a single full-mix file.
        """
        try:
            return mix_stems_to_master(
                stem_paths, output_path,
                normalize=normalize, headroom_db=headroom_db,
            )
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    @mcp.tool()
    async def bounce_full_mix(
        output_path: str,
        duration_sec: float,
        pre_roll_sec: float = 0.5,
        post_roll_sec: float = 0.3,
    ) -> dict[str, Any]:
        """Capture the Master output to a wav. Realtime; takes ~duration_sec.

        Prerequisites:
          - AbletonFullControlTape compiled as `.amxd` (Save As Device in Max once
            after `install_tape`).
          - The .amxd dropped on Live's **Master** track.
          - Live in the foreground; the arrangement starts at beat 0.
        """
        try:
            return await bounce_master_realtime(
                output_path,
                duration_sec=duration_sec,
                pre_roll_sec=pre_roll_sec,
                post_roll_sec=post_roll_sec,
            )
        except BounceError as e:
            return {"status": "not_configured", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    @mcp.tool()
    async def bounce_stems(
        output_dir: str,
        duration_sec: float,
        track_indices: list[int],
        include_full_mix: bool = True,
    ) -> dict[str, Any]:
        """Capture per-track stems by soloing each in turn while recording Master.

        Realtime: total time ≈ `duration_sec * (len(track_indices) + 1)` with
        the optional final full-mix pass. Same prereqs as `bounce_full_mix`.
        """
        try:
            return await bounce_stems_realtime(
                output_dir,
                duration_sec=duration_sec,
                track_indices=track_indices,
                include_full_mix=include_full_mix,
            )
        except BounceError as e:
            return {"status": "not_configured", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    @mcp.tool()
    async def bounce_full_pipeline(
        output_dir: str,
        duration_sec: float,
        track_indices: list[int],
        formats: list[str] | None = None,
        bitrate_kbps: int = 192,
    ) -> dict[str, Any]:
        """End-to-end: stems → wav → optional mp3, plus a full-mix wav and mp3.

        `formats` defaults to ["wav", "mp3"]. Total wall-clock ~= duration *
        (n_tracks + 1) seconds for the realtime stem capture, plus a few
        seconds for ffmpeg encoding.
        """
        formats = formats or ["wav", "mp3"]
        out_root = Path(output_dir)
        try:
            stems_result = await bounce_stems_realtime(
                str(out_root),
                duration_sec=duration_sec,
                track_indices=track_indices,
                include_full_mix=True,
            )
        except BounceError as e:
            return {"status": "not_configured", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

        encoded: list[dict[str, Any]] = []
        if "mp3" in formats:
            try:
                # Stems
                for s in stems_result.get("stems", []):
                    wav = s["path"]
                    mp3 = wav[:-4] + ".mp3" if wav.lower().endswith(".wav") else wav + ".mp3"
                    encoded.append(encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps))
                fm = stems_result.get("full_mix")
                if fm:
                    wav = fm["output_path"]
                    mp3 = wav[:-4] + ".mp3" if wav.lower().endswith(".wav") else wav + ".mp3"
                    encoded.append(encode_wav_to_mp3(wav, mp3, bitrate_kbps=bitrate_kbps))
            except FFmpegMissing as e:
                return {
                    "status": "wav_only",
                    "wavs": stems_result,
                    "mp3_skipped_reason": str(e),
                }
            except Exception as e:
                return {
                    "status": "wav_only",
                    "wavs": stems_result,
                    "mp3_skipped_reason": f"{type(e).__name__}: {e}",
                }

        return {
            "status": "ok",
            "output_dir": str(out_root.resolve()),
            "wavs": stems_result,
            "mp3s": encoded,
            "formats": formats,
        }
