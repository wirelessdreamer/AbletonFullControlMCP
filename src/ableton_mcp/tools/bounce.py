"""MCP tools for bouncing the current set to wav/mp3 (full mix + stems).

These wrap ``ableton_mcp.bounce.*``. All realtime capture goes through Live's
built-in Resampling input — no Max for Live, no loopback driver.

Realtime (one playback pass, ``duration_sec`` of wall-clock):
    bounce_song(output_path, duration_sec)         master mix → wav (+ optional mp3)
    bounce_tracks(track_indices, output_dir, ...)  per-track stems in parallel
    bounce_enabled(output_dir, duration_sec, ...)  every un-muted track + master

Offline (work on any wavs already on disk):
    bounce_encode_mp3(wav_path, mp3_path, bitrate_kbps)
    bounce_mix_stems(stem_paths, output_path)
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bounce import (
    bounce_enabled_via_resampling,
    bounce_song_via_resampling,
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
        warmup_sec: float = 0.0,
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
        """
        try:
            wav_result = await bounce_song_via_resampling(
                output_path, duration_sec, warmup_sec=warmup_sec,
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

    @mcp.tool()
    async def bounce_tracks(
        track_indices: list[int],
        output_dir: str,
        duration_sec: float,
        include_master: bool = False,
        encode_mp3: bool = True,
        bitrate_kbps: int = 192,
        warmup_sec: float = 0.0,
    ) -> dict[str, Any]:
        """Bounce specific tracks to per-track wav stems in ONE realtime pass.

        Live records all listed tracks in parallel during a single playback
        pass — wall-clock is ``duration_sec`` regardless of how many tracks.
        For each source track, a temp audio track is created with input
        routed to that source, armed, recorded; the resulting wav is copied
        to ``output_dir/stem_<idx>_<name>.wav`` and the temp track deleted.

        Set ``include_master=True`` to also capture the master mix in the
        same pass (extra resampling track).

        ``warmup_sec`` runs a brief no-record playback before the real
        capture to prime samplers + the audio engine. Use 0.3-0.5 s if your
        first bounce of a fresh session shows silent leading audio.
        """
        try:
            r = await bounce_tracks_via_resampling(
                track_indices, output_dir, duration_sec,
                include_master=include_master,
                warmup_sec=warmup_sec,
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
        warmup_sec: float = 0.5,
    ) -> dict[str, Any]:
        """Bounce every un-muted track (+ master) as separate stems in one pass.

        Convenience wrapper: queries every track's mute state, skips muted
        ones and tracks without audio output, then runs bounce_tracks on the
        rest. ``include_master=True`` adds a Resampling track for the full mix.

        ``warmup_sec`` defaults to 0.5 here (higher than the other bounce_*
        tools) because this entrypoint is most often used on fresh sessions
        — exactly where the first-bounce-of-fresh-sampler silence bug
        bites hardest. Set to 0 to disable.
        """
        try:
            r = await bounce_enabled_via_resampling(
                output_dir, duration_sec,
                include_master=include_master,
                warmup_sec=warmup_sec,
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

