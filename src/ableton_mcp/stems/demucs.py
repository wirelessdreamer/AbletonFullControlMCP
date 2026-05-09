"""Subprocess wrapper around Meta's Demucs for source separation.

Demucs is invoked as `python -m demucs ...` so we don't need a CLI shim on
PATH. We rely on the *current* Python interpreter (`sys.executable`) so the
user only has to `pip install demucs` into the same venv.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class StemsError(RuntimeError):
    pass


class DemucsNotInstalled(StemsError):
    pass


@dataclass
class StemFile:
    name: str  # vocals / drums / bass / other
    path: str


def _demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


async def split_stems(
    audio_path: str,
    model: str = "htdemucs",
    out_dir: str | os.PathLike[str] = "data/stems",
    python_executable: str | None = None,
) -> list[StemFile]:
    """Split `audio_path` into stems using Demucs.

    Demucs writes its outputs to `<out_dir>/<model>/<track_name>/<stem>.wav`.
    Returns the list of stems it actually produced (`htdemucs` ships
    vocals/drums/bass/other; `htdemucs_6s` adds piano + guitar).

    Raises DemucsNotInstalled if the package can't be found in this venv.
    """
    src = Path(audio_path)
    if not src.exists():
        raise StemsError(f"Audio file not found: {audio_path}")

    if not _demucs_available():
        raise DemucsNotInstalled(
            "Demucs is missing from this venv even though it's a base "
            "dependency. The venv probably predates the dependency change "
            "— rerun `pip install -e .` from the project root to pull it "
            "(adds torch + torchaudio, ~2 GB). If the install fails, "
            "manual fallback: `pip install demucs>=4.0.1`."
        )

    py = python_executable or sys.executable
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        py, "-m", "demucs", "-n", model, "-o", str(out), str(src),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise StemsError(
            f"Demucs failed (exit {proc.returncode}): "
            f"{stderr.decode(errors='replace')[:2000]}"
        )

    track_dir = out / model / src.stem
    if not track_dir.exists():
        # Some Demucs versions skip the model dir when only one model exists.
        candidates = [p for p in out.rglob(src.stem) if p.is_dir()]
        if not candidates:
            raise StemsError(
                f"Demucs did not produce an output directory for {src.stem!r} "
                f"under {out}. stdout tail: {stdout.decode(errors='replace')[-500:]}"
            )
        track_dir = candidates[0]

    stems: list[StemFile] = []
    for wav in sorted(track_dir.glob("*.wav")):
        stems.append(StemFile(name=wav.stem, path=str(wav)))
    if not stems:
        raise StemsError(f"No stem .wav files found in {track_dir}")
    return stems
