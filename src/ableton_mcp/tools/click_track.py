"""MCP tools for click track generation."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..click_track import (
    detect_beats as _detect_beats,
    generate_click_track as _generate_click_track,
)


def _err(exc: Exception) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def song_click_track(
        audio_path: str,
        output_path: str | None = None,
        checkpoint: str = "final0",
        device: str | None = None,
        use_dbn: bool = False,
        beat_freq_hz: float = 1000.0,
        downbeat_freq_hz: float = 1500.0,
    ) -> dict[str, Any]:
        """Generate a click track WAV from an audio file.

        Uses beat_this (Foscarin et al., JKU) for beat + downbeat
        detection, then synthesizes sine-burst clicks at each beat with
        a higher-pitched accent on downbeats. Output is a mono WAV
        matching the source audio's length and samplerate so it can
        play in perfect sync in any DAW / audio player.

        Args:
            audio_path: source audio (wav/mp3/etc).
            output_path: destination WAV path. Defaults to
                ``<audio_dir>/click.wav``.
            checkpoint: beat_this checkpoint name (default ``"final0"``).
            device: torch device (``"cuda"``, ``"cpu"``, or None to
                auto-detect).
            use_dbn: enable the DBN post-processor for messier audio at
                ~2x inference cost.
            beat_freq_hz / downbeat_freq_hz: click pitch overrides.

        Returns ``{status, click_wav_path, duration_sec, samplerate,
        beat_count, downbeat_count, bpm_estimate}``.
        """
        try:
            r = _generate_click_track(
                audio_path,
                output_path=output_path,
                checkpoint=checkpoint,
                device=device,
                use_dbn=use_dbn,
                beat_freq_hz=beat_freq_hz,
                downbeat_freq_hz=downbeat_freq_hz,
            )
        except FileNotFoundError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return _err(e)
        return {
            "status": "ok",
            "click_wav_path": r.click_wav_path,
            "duration_sec": r.duration_sec,
            "samplerate": r.samplerate,
            "beat_count": r.beat_count,
            "downbeat_count": r.downbeat_count,
            "bpm_estimate": r.bpm_estimate,
        }

    @mcp.tool()
    async def song_detect_beats(
        audio_path: str,
        checkpoint: str = "final0",
        device: str | None = None,
        use_dbn: bool = False,
    ) -> dict[str, Any]:
        """Detect beat + downbeat timestamps in an audio file.

        Returns timestamps in seconds + a median-derived BPM estimate.
        No WAV is written — useful when you want to visualise or
        transform the beat grid without generating clicks.

        Args:
            audio_path: source audio.
            checkpoint: beat_this checkpoint (default ``"final0"``).
            device: torch device (``"cuda"``, ``"cpu"``, or None).
            use_dbn: DBN post-processor toggle.

        Returns ``{status, beats_sec, downbeats_sec, bpm_estimate,
        beat_count, downbeat_count}``.
        """
        try:
            d = _detect_beats(
                audio_path,
                checkpoint=checkpoint,
                device=device,
                use_dbn=use_dbn,
            )
        except FileNotFoundError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return _err(e)
        return {
            "status": "ok",
            "beats_sec": list(d.beats_sec),
            "downbeats_sec": list(d.downbeats_sec),
            "bpm_estimate": d.bpm_estimate,
            "beat_count": len(d.beats_sec),
            "downbeat_count": len(d.downbeats_sec),
        }
