"""WAV → MP3 encoding via ffmpeg subprocess.

We shell out to ffmpeg rather than vendoring an encoder because:
- It's universally available and well-known.
- libmp3lame produces the modern industry-standard mp3.
- It handles all sample rate / channel layouts without us re-implementing.

Detects ffmpeg on PATH; if missing, raises FFmpegMissing with a clear hint.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class FFmpegMissing(RuntimeError):
    """ffmpeg is not on PATH or is not callable."""


def find_ffmpeg() -> str:
    """Locate the ffmpeg executable. Returns absolute path or raises."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FFmpegMissing(
            "ffmpeg not found on PATH. Install from https://ffmpeg.org/ "
            "(Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`). "
            "We need libmp3lame to encode mp3."
        )
    return exe


def encode_wav_to_mp3(
    wav_path: str | os.PathLike,
    mp3_path: str | os.PathLike,
    bitrate_kbps: int = 192,
    quality: int | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Encode a wav (or any ffmpeg-readable audio) to mp3 using libmp3lame.

    Args:
        wav_path: input file (wav/aif/flac/etc.)
        mp3_path: output mp3 path
        bitrate_kbps: target bitrate in kbps. Common values: 128, 192, 256, 320.
                      Ignored if `quality` is set.
        quality: VBR quality 0-9 (0 = best). When given, overrides bitrate_kbps.
        overwrite: if True, replaces an existing output file.

    Returns dict with `output_path`, `size_bytes`, and `ffmpeg_stderr` (last
    20 lines of progress output for debugging).
    """
    exe = find_ffmpeg()
    src = Path(wav_path).resolve()
    dst = Path(mp3_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"input audio not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"{dst} exists; pass overwrite=True to replace")

    cmd = [exe, "-y" if overwrite else "-n", "-i", str(src), "-codec:a", "libmp3lame"]
    if quality is not None:
        cmd.extend(["-q:a", str(int(quality))])
    else:
        cmd.extend(["-b:a", f"{int(bitrate_kbps)}k"])
    cmd.append(str(dst))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}):\n{tail}")
    return {
        "output_path": str(dst),
        "size_bytes": dst.stat().st_size,
        "bitrate_kbps": bitrate_kbps if quality is None else None,
        "vbr_quality": quality,
        "ffmpeg_stderr_tail": "\n".join(proc.stderr.strip().splitlines()[-5:]),
    }
