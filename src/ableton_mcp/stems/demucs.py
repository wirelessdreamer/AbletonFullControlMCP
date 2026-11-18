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
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..paths import data_dir


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


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
    content_hash = _file_hash(src)
    from importlib.metadata import version
    settings = {"model": model_name, "demucs_version": version("demucs"),
                "torch_version": torch.__version__, "format": "FLOAT", "seed": 0,
                "shifts": 1, "overlap": 0.25}
    settings_hash = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    parent = out_dir / model_name
    parent.mkdir(parents=True, exist_ok=True)
    track_dir = parent / f"{content_hash}_{settings_hash}"
    marker = track_dir / "separation.json"
    if marker.is_file():
        try:
            cached = json.loads(marker.read_text(encoding="utf-8"))
            expected_sources = ({"drums", "bass", "other", "vocals", "guitar", "piano"}
                                if model_name == "htdemucs_6s" else {"drums", "bass", "other", "vocals"})
            names = cached["sources"]
            if (len(names) != len(expected_sources) or set(names) != expected_sources
                    or cached["source_sha256"] != content_hash
                    or any(cached.get(key) != value for key, value in settings.items())):
                raise ValueError("Cache provenance/source categories differ")
            paths = [StemFile(name=name, path=str(track_dir / f"{name}.wav")) for name in names]
            if all(Path(s.path).is_file() and sf.info(s.path).frames == cached["frames"]
                   and sf.info(s.path).channels == 2 and sf.info(s.path).subtype == "FLOAT"
                   and sf.info(s.path).samplerate == cached["sample_rate"]
                   and _file_hash(Path(s.path)) == cached.get("output_hashes", {}).get(s.name)
                   for s in paths):
                return paths
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            # Partial/corrupt metadata is an invalid cache, not a fatal source error.
            # Preserve it and compute a fresh result in a new directory below.
            pass

    # Auto-detect device. CPU is the fallback for any environment without
    # CUDA torch installed; no opt-in flag required.
    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = get_model(model_name)
    model.eval()
    expected = {"drums", "bass", "other", "vocals", "guitar", "piano"}
    if model_name == "htdemucs_6s" and set(model.sources) != expected:
        raise StemsError("Six-stem model did not expose the six expected source names")
    if len(model.sources) != len(set(model.sources)):
        raise StemsError("Model contains duplicate source names")

    # Load audio at the model's native sample rate, force stereo (demucs
    # expects 2 channels).
    audio, _ = librosa.load(str(src), sr=model.samplerate, mono=False)
    if _file_hash(src) != content_hash:
        raise StemsError("Source recording changed while it was being decoded")
    if audio.ndim == 1:
        audio = np.stack([audio, audio])  # mono → stereo
    elif audio.shape[0] > 2:
        raise StemsError("Only mono/stereo recordings are supported; channels were not discarded")
    if audio.shape[-1] == 0 or not np.isfinite(audio).all():
        raise StemsError("Input audio is empty or contains non-finite samples")
    audio_tensor = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)
    reference = audio_tensor.mean(dim=1)
    mean, scale = reference.mean(), reference.std()
    if not torch.isfinite(scale) or float(scale) < 1e-8:
        scale = torch.tensor(1.0)
    normalized = (audio_tensor - mean) / scale
    torch.manual_seed(0)
    try:
        with torch.no_grad():
            separated = apply_model(model, normalized, device=device, progress=False,
                                    shifts=1, overlap=0.25) * scale + mean
    except torch.cuda.OutOfMemoryError:
        # Retry the same six-source model, never silently change the source count.
        device = "cpu"
        model.cpu()
        torch.cuda.empty_cache()
        torch.manual_seed(0)
        with torch.no_grad():
            separated = apply_model(model, normalized, device=device, progress=False,
                                    shifts=1, overlap=0.25) * scale + mean
    separated = separated[0]  # drop batch dim → (sources, channels, samples)
    if tuple(separated.shape) != (len(model.sources), 2, audio.shape[-1]):
        raise StemsError("Model output shape/duration does not match the decoded input")
    if not torch.isfinite(separated).all():
        raise StemsError("Model returned non-finite samples")
    staging = Path(tempfile.mkdtemp(prefix=".separation-", dir=parent))
    try:
        for i, name in enumerate(model.sources):
            out_path = staging / f"{name}.wav"
            sf.write(str(out_path), separated[i].detach().cpu().numpy().T,
                     model.samplerate, subtype="FLOAT")
        metadata = {**settings, "source_sha256": content_hash, "sources": list(model.sources),
                    "sample_rate": model.samplerate, "frames": audio.shape[-1], "device": device,
                    "output_hashes": {name: _file_hash(staging / f"{name}.wav")
                                      for name in model.sources}}
        (staging / "separation.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        if track_dir.exists():
            # An invalid previous result is retained for diagnostics, not overwritten.
            track_dir = parent / f"{content_hash}_{settings_hash}_{staging.name[12:]}"
        staging.rename(track_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return [StemFile(name=name, path=str(track_dir / f"{name}.wav")) for name in model.sources]


async def split_stems(
    audio_path: str,
    model: str = "htdemucs_6s",
    out_dir: str | os.PathLike[str] | None = None,
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

    Demucs writes float WAVs to content-addressed directories beneath ``out_dir/model``.
    The project-import workflow runs this function in a killable worker process;
    cancelling this low-level coroutine alone cannot stop its executor thread.

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

    # Default resolves through paths.data_dir — cwd-relative "data/stems"
    # broke under MCP hosts that spawn the server in a protected directory.
    out = Path(out_dir) if out_dir is not None else data_dir("stems")
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
