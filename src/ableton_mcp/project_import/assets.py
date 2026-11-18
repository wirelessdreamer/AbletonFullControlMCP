"""Bounded source discovery and lossless media collection."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import soundfile as sf

AUDIO = {".wav", ".flac", ".aif", ".aiff", ".mp3"}
MIDI = {".mid", ".midi", ".smf"}
MAX_FILES = 4096
MAX_BYTES = 20 * 1024**3


class ImportError(ValueError):
    """An input cannot be imported without losing information."""


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(name: str) -> str:
    value = re.sub(r'[^\w .()-]', "_", name).strip(" .")[:100]
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(
        f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
    )}:
        value = "_" + value
    return value or "Untitled"


def extract_zip(source: Path, destination: Path) -> list[Path]:
    """Reject traversal, links, duplicate names and excessive expansion before writing."""
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES or sum(x.file_size for x in members) > MAX_BYTES:
            raise ImportError("ZIP exceeds import limits (4096 files / 20 GiB)")
        seen: set[str] = set()
        for member in members:
            name = member.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or ":" in name
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or any(part.rstrip(" .") != part for part in path.parts)):
                raise ImportError(f"Unsafe ZIP entry: {name}")
            folded = name.casefold()
            if folded in seen:
                raise ImportError(f"Duplicate ZIP entry: {name}")
            seen.add(folded)
            target = destination.joinpath(*path.parts).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ImportError(f"ZIP entry escapes extraction folder: {name}")
        archive.extractall(destination)
    return sorted(p for p in destination.rglob("*") if p.is_file())


def discover(sources: list[str], staging: Path) -> tuple[list[Path], list[dict[str, str]]]:
    if not sources:
        raise ImportError("Provide at least one source path")
    files: list[Path] = []
    excluded: list[dict[str, str]] = []
    for i, source in enumerate(sources):
        path = Path(source).expanduser().resolve()
        if not path.exists() or path.is_symlink():
            raise ImportError(f"Missing or linked source: {path}")
        if path.is_dir():
            candidates = sorted(path.rglob("*"))
            if len(candidates) > MAX_FILES:
                raise ImportError("Source folder exceeds 4096 entries")
        elif path.suffix.lower() == ".zip":
            candidates = extract_zip(path, staging / f"archive_{i}")
        else:
            candidates = [path]
        for item in candidates:
            if not item.is_file():
                continue
            if item.is_symlink() or not item.resolve().is_relative_to(
                path if path.is_dir() else item.parent.resolve()
            ):
                raise ImportError(f"Linked source is not supported: {item}")
            if item.suffix.lower() in AUDIO | MIDI:
                files.append(item)
            else:
                excluded.append({"path": str(item), "reason": "unsupported_format"})
    files = sorted(set(files))
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise ImportError("Source files exceed import limits")
    return files, excluded


def audio_info(path: Path) -> dict[str, Any]:
    info = sf.info(path)
    if info.frames <= 0 or info.samplerate <= 0 or info.channels not in (1, 2):
        raise ImportError(f"Expected nonempty mono/stereo audio: {path}")
    peak = 0.0
    energy = 0.0
    for block in sf.blocks(path, blocksize=65536, dtype="float32", always_2d=True):
        if not np.isfinite(block).all():
            raise ImportError(f"Audio contains NaN or infinity: {path}")
        peak = max(peak, float(np.max(np.abs(block))))
        energy += float(np.sum(block.astype(np.float64) ** 2))
    return {
        "frames": info.frames, "sample_rate": info.samplerate, "channels": info.channels,
        "duration_sec": info.frames / info.samplerate, "subtype": info.subtype,
        "peak": peak, "rms": math.sqrt(energy / (info.frames * info.channels)),
    }


def collect(path: Path, project: Path, role: str) -> dict[str, Any]:
    sha = digest(path)
    folder = project / ("MIDI" if path.suffix.lower() in MIDI else "Samples/Imported")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sha[:16]}_{safe_name(path.name)}"
    if not target.exists():
        shutil.copy2(path, target)
    if digest(target) != sha:
        raise ImportError(f"Media changed during collection: {path}")
    asset: dict[str, Any] = {
        "name": path.stem, "source_path": str(path), "sha256": sha, "role": role,
        "path": target.relative_to(project).as_posix(), "offset_sec": 0.0,
    }
    if path.suffix.lower() == ".mp3":
        # Keep the exact download. FFmpeg applies the MP3 stream's gapless
        # skip/discard metadata; no additional trim, offset or resampling occurs.
        originals = project / "Sources/Original"
        originals.mkdir(parents=True, exist_ok=True)
        original = originals / target.name
        target.replace(original)
        target = folder / f"{sha[:16]}_{safe_name(path.stem)}.wav"
        try:
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
                "-show_entries", "packet=side_data_list:packet_side_data=side_data_type,skip_samples,discard_padding",
                "-of", "json", str(original),
            ], capture_output=True, text=True, check=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            packets = json.loads(probe.stdout).get("packets", [])
            gapless = [side for packet in packets for side in packet.get("side_data_list", [])
                       if side.get("side_data_type") == "Skip Samples"]
            if not target.exists():
                subprocess.run([
                    "ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(original),
                    "-map", "0:a:0", "-c:a", "pcm_f32le", str(target),
                ], capture_output=True, text=True, check=True, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise ImportError(f"MP3 decode requires FFmpeg and ffprobe: {path}: {exc}") from exc
        asset.update(path=target.relative_to(project).as_posix(), sha256=digest(target),
                     original_path=original.relative_to(project).as_posix(), original_sha256=sha,
                     decode={"decoder": "ffmpeg", "format": "pcm_f32le",
                             "gapless_side_data": gapless, "additional_trim_samples": 0})
    if role != "midi":
        asset.update(audio_info(target))
    return asset


def sum_peak(project: Path, assets: list[dict[str, Any]]) -> float:
    """Measure the complete sum on one timebase, without loading entire songs."""
    from scipy.signal import resample_poly
    from math import gcd

    rate = max(a["sample_rate"] for a in assets)
    # Resample individual media to temporary float files, bounded to one source at a time.
    # The block summation below bounds memory independently of song duration at common rates.
    paths: list[tuple[Path, int]] = []
    temps: list[Path] = []
    try:
        for i, asset in enumerate(assets):
            path = project / asset["path"]
            if asset["sample_rate"] != rate:
                audio, sr = sf.read(path, always_2d=True, dtype="float32")
                factor = gcd(rate, sr)
                audio = resample_poly(audio, rate // factor, sr // factor, axis=0)
                path = project / f".sum_{i}.wav"
                sf.write(path, audio, rate, subtype="FLOAT")
                temps.append(path)
            paths.append((path, round(asset["offset_sec"] * rate)))
        end = max(offset + sf.info(path).frames for path, offset in paths)
        peak = 0.0
        for start in range(0, end, 65536):
            mix = np.zeros((min(65536, end - start), 2), dtype=np.float64)
            for path, offset in paths:
                left, right = max(start, offset), min(start + len(mix), offset + sf.info(path).frames)
                if right <= left:
                    continue
                with sf.SoundFile(path) as stream:
                    stream.seek(left - offset)
                    data = stream.read(right - left, dtype="float32", always_2d=True)
                mix[left - start:right - start] += data
            peak = max(peak, float(np.max(np.abs(mix))))
        return peak
    finally:
        for path in temps:
            path.unlink(missing_ok=True)
