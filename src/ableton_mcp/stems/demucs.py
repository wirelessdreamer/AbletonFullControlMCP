"""Demucs source separation via the Python API.

Uses ``demucs.pretrained.get_model`` + ``demucs.apply.apply_model`` directly,
and writes stems with ``soundfile`` rather than ``torchaudio.save``. Recent
``torchaudio`` releases route ``save`` through ``torchcodec``, which on
Windows requires system FFmpeg shared libs that the project doesn't ship —
the subprocess-based ``python -m demucs <file>`` invocation breaks at the
final write step in that environment. Calling the model in-process and
saving with ``soundfile`` sidesteps the whole stack.

Two other behaviour changes vs. the old subprocess version:

* **GPU when available** — ``apply_model`` runs on CUDA if
  ``torch.cuda.is_available()`` returns True, else CPU. No flag needed.
  Users without a GPU just get the slower path automatically.
* **6-stem default** — the default model is now ``htdemucs_6s`` (drums,
  bass, other, vocals, guitar, piano). The 4-stem ``htdemucs`` is still
  available by passing ``model="htdemucs"`` explicitly.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path


class StemsError(RuntimeError):
    pass


class DemucsNotInstalled(StemsError):
    pass


@dataclass
class StemFile:
    name: str  # vocals/drums/bass/other (htdemucs) + guitar/piano (htdemucs_6s)
    path: str


def _demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def _separate_sync(
    audio_path: str,
    model_name: str,
    out_dir: Path,
) -> list[StemFile]:
    """Synchronous demucs separation. Heavy imports happen here so that
    importing this module doesn't drag in torch + librosa for callers that
    only need the dataclasses or the availability check."""
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    src = Path(audio_path)
    track_dir = out_dir / model_name / src.stem
    track_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect device. CPU is the fallback for any environment without
    # CUDA torch installed; no opt-in flag required.
    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = get_model(model_name)
    model.eval()

    # Load audio at the model's native sample rate, force stereo (demucs
    # expects 2 channels).
    audio, _ = librosa.load(str(src), sr=model.samplerate, mono=False)
    if audio.ndim == 1:
        audio = np.stack([audio, audio])  # mono → stereo
    elif audio.shape[0] > 2:
        audio = audio[:2]  # downmix to stereo if multichannel
    audio_tensor = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)

    with torch.no_grad():
        separated = apply_model(model, audio_tensor, device=device, progress=False)
    separated = separated[0]  # drop batch dim → (sources, channels, samples)

    stems: list[StemFile] = []
    for i, name in enumerate(model.sources):
        stem_audio = separated[i].numpy()  # (channels, samples)
        out_path = track_dir / f"{name}.wav"
        sf.write(str(out_path), stem_audio.T, model.samplerate, subtype="PCM_16")
        stems.append(StemFile(name=name, path=str(out_path)))
    return stems


async def split_stems(
    audio_path: str,
    model: str = "htdemucs_6s",
    out_dir: str | os.PathLike[str] = "data/stems",
    python_executable: str | None = None,  # ignored; kept for backwards compat
) -> list[StemFile]:
    """Split ``audio_path`` into stems using Demucs.

    The default model is ``htdemucs_6s`` (6 stems: drums, bass, other,
    vocals, guitar, piano). Pass ``model="htdemucs"`` for the older
    4-stem version (drums/bass/other/vocals).

    Demucs runs on **GPU when available** (``torch.cuda.is_available()``)
    and falls back to CPU automatically. To enable GPU, install a CUDA
    torch build into this venv:

        # CUDA 12.6 (stable) — most NVIDIA GPUs
        pip uninstall -y torch torchaudio
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126

        # CUDA 12.8 nightly — required for sm_120 / Blackwell (RTX 50-series)
        pip install --pre torch torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128

    Demucs writes stems to ``<out_dir>/<model>/<basename>/<stem>.wav``.

    The ``python_executable`` argument is accepted for backwards
    compatibility but ignored — the new implementation invokes Demucs
    in-process.

    Raises :class:`DemucsNotInstalled` if the package can't be found in
    this venv. Raises :class:`StemsError` for any other failure.
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

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Run the synchronous demucs work in a thread executor so we don't
    # block the event loop.
    loop = asyncio.get_event_loop()
    try:
        stems = await loop.run_in_executor(
            None, _separate_sync, str(src), model, out
        )
    except DemucsNotInstalled:
        raise
    except StemsError:
        raise
    except Exception as exc:
        raise StemsError(f"Demucs separation failed: {exc!r}") from exc

    if not stems:
        raise StemsError(f"Demucs produced no stems for {src.name}")
    return stems
