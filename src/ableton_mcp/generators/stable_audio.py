"""Stable Audio generator (Stability AI hosted API)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

from .base import GenResult, Generator, GeneratorError, GeneratorNotConfigured


class StableAudioGenerator(Generator):
    """Wrapper around Stability AI's Stable Audio API."""

    name = "stable_audio"
    DEFAULT_BASE = "https://api.stability.ai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        output_dir: str | os.PathLike[str] = "data/generated",
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("STABLE_AUDIO_API_KEY")
        )
        self._base_url = (
            base_url or os.environ.get("STABLE_AUDIO_API_BASE") or self.DEFAULT_BASE
        ).rstrip("/")
        self._output_dir = Path(output_dir)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> str:
        if not self._api_key:
            raise GeneratorNotConfigured(
                "Stable Audio generator is not configured: set the "
                "STABLE_AUDIO_API_KEY environment variable to enable it."
            )
        return self._api_key

    async def _post_generate(
        self,
        client: httpx.AsyncClient,
        prompt: str,
        duration: float | None,
        extra: dict[str, Any],
    ) -> httpx.Response:
        payload: dict[str, Any] = {"prompt": prompt}
        if duration is not None:
            payload["duration"] = duration
        payload.update(extra)
        return await client.post(
            f"{self._base_url}/v2beta/audio/stable-audio-2/text-to-audio",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._require_key()}",
                "Accept": "audio/*",
            },
        )

    async def generate(
        self,
        prompt: str,
        lyrics: str | None = None,
        duration: float | None = None,
        **kwargs: Any,
    ) -> GenResult:
        self._require_key()
        output_path = kwargs.pop("output_path", None)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await self._post_generate(client, prompt, duration, kwargs)
            if resp.status_code >= 400:
                raise GeneratorError(
                    f"Stable Audio request failed (HTTP {resp.status_code}): "
                    f"{resp.text[:1000]}"
                )
            body = resp.content

        dest = Path(output_path) if output_path else (
            self._output_dir / f"stableaudio_{int(time.time())}.wav"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

        return GenResult(
            audio_path=str(dest),
            duration=float(duration or 0.0),
            lyrics=lyrics,
            provider=self.name,
            metadata={"content_type": resp.headers.get("content-type", "")},
        )
