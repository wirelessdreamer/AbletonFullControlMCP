"""MusicGen generator via Meta's audiocraft (subprocess)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .base import GenResult, Generator, GeneratorError, GeneratorNotConfigured


class MusicGenGenerator(Generator):
    """Run audiocraft's MusicGen CLI as a subprocess.

    Requires the optional dep `audiocraft` installed in the same Python
    environment. We don't import it directly to keep the server lightweight.
    """

    name = "musicgen"

    def __init__(
        self,
        model: str = "facebook/musicgen-small",
        output_dir: str | os.PathLike[str] = "data/generated",
        python_executable: str | None = None,
    ) -> None:
        self._model = model
        self._output_dir = Path(output_dir)
        self._python = python_executable or sys.executable

    def is_configured(self) -> bool:
        # Cheap probe: we look for the audiocraft package, not the model weights.
        return self._audiocraft_available()

    def _audiocraft_available(self) -> bool:
        # Prefer importlib.util so we don't import audiocraft at module load time.
        import importlib.util

        return importlib.util.find_spec("audiocraft") is not None

    async def generate(
        self,
        prompt: str,
        lyrics: str | None = None,
        duration: float | None = None,
        **kwargs: Any,
    ) -> GenResult:
        if not self._audiocraft_available():
            raise GeneratorNotConfigured(
                "MusicGen generator requires the `audiocraft` package. Install with "
                "`pip install audiocraft` (heavy: pulls torch + transformers)."
            )
        if not shutil.which(self._python):
            raise GeneratorNotConfigured(
                f"Python executable not found: {self._python}"
            )

        self._output_dir.mkdir(parents=True, exist_ok=True)
        out_path = Path(kwargs.pop("output_path", None) or (
            self._output_dir / f"musicgen_{int(time.time())}.wav"
        ))

        # Drive audiocraft via a tiny -c snippet so we don't require a CLI entry.
        dur = float(duration if duration is not None else 8.0)
        snippet = (
            "import sys, torchaudio\n"
            "from audiocraft.models import MusicGen\n"
            f"m = MusicGen.get_pretrained({self._model!r})\n"
            f"m.set_generation_params(duration={dur})\n"
            "wav = m.generate([sys.argv[1]])[0].cpu()\n"
            f"torchaudio.save({str(out_path)!r}, wav, m.sample_rate)\n"
        )
        proc = await asyncio.create_subprocess_exec(
            self._python, "-c", snippet, prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GeneratorError(
                f"MusicGen subprocess failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')[:2000]}"
            )

        return GenResult(
            audio_path=str(out_path),
            duration=dur,
            lyrics=lyrics,
            provider=self.name,
            metadata={"model": self._model, "stdout_tail": stdout.decode(errors="replace")[-500:]},
        )
