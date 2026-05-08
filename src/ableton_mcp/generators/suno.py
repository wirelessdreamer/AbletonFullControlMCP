"""Suno generator (unofficial API)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .base import GenResult, Generator, GeneratorError, GeneratorNotConfigured


class SunoGenerator(Generator):
    """Wrapper around the unofficial Suno API.

    Reads SUNO_API_KEY from the environment. Optionally honours SUNO_API_BASE
    (defaults to a community wrapper endpoint). The actual HTTP call is
    isolated in `_post_generate` and `_poll_status` so tests can monkeypatch
    them without touching the network.
    """

    name = "suno"
    DEFAULT_BASE = "https://api.suno.ai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        output_dir: str | os.PathLike[str] = "data/generated",
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("SUNO_API_KEY")
        self._base_url = (base_url or os.environ.get("SUNO_API_BASE") or self.DEFAULT_BASE).rstrip("/")
        self._output_dir = Path(output_dir)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> str:
        if not self._api_key:
            raise GeneratorNotConfigured(
                "Suno generator is not configured: set the SUNO_API_KEY environment "
                "variable (and optionally SUNO_API_BASE) to enable it."
            )
        return self._api_key

    # --- HTTP layer (mockable) -------------------------------------------------

    async def _post_generate(
        self,
        client: httpx.AsyncClient,
        prompt: str,
        lyrics: str | None,
        duration: float | None,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"prompt": prompt}
        if lyrics is not None:
            payload["lyrics"] = lyrics
        if duration is not None:
            payload["duration"] = duration
        payload.update(extra)
        resp = await client.post(
            f"{self._base_url}/v1/generate",
            json=payload,
            headers={"Authorization": f"Bearer {self._require_key()}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_status(self, client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
        resp = await client.get(
            f"{self._base_url}/v1/jobs/{job_id}",
            headers={"Authorization": f"Bearer {self._require_key()}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _download(self, client: httpx.AsyncClient, url: str, dest: Path) -> None:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)

    # --- Generator API ---------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        lyrics: str | None = None,
        duration: float | None = None,
        **kwargs: Any,
    ) -> GenResult:
        self._require_key()
        output_path = kwargs.pop("output_path", None)
        poll_interval = float(kwargs.pop("poll_interval", 3.0))
        timeout = float(kwargs.pop("timeout", 300.0))

        async with httpx.AsyncClient(timeout=60.0) as client:
            job = await self._post_generate(client, prompt, lyrics, duration, kwargs)
            job_id = job.get("id") or job.get("job_id")
            audio_url = job.get("audio_url")
            returned_lyrics = job.get("lyrics", lyrics)
            returned_duration = float(job.get("duration") or duration or 0.0)

            # If the job is async, poll until done.
            if not audio_url and job_id:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    await asyncio.sleep(poll_interval)
                    status = await self._poll_status(client, job_id)
                    state = status.get("status") or status.get("state")
                    if state in ("complete", "completed", "succeeded", "done"):
                        audio_url = status.get("audio_url")
                        returned_lyrics = status.get("lyrics", returned_lyrics)
                        returned_duration = float(
                            status.get("duration") or returned_duration or 0.0
                        )
                        break
                    if state in ("failed", "error"):
                        raise GeneratorError(
                            f"Suno job {job_id} failed: {status.get('error') or status}"
                        )
                else:
                    raise GeneratorError(
                        f"Suno job {job_id} did not finish within {timeout}s"
                    )

            if not audio_url:
                raise GeneratorError(
                    f"Suno response did not contain an audio_url: {job!r}"
                )

            dest = Path(output_path) if output_path else (
                self._output_dir / f"suno_{job_id or int(time.time())}.mp3"
            )
            await self._download(client, audio_url, dest)

        return GenResult(
            audio_path=str(dest),
            duration=returned_duration,
            lyrics=returned_lyrics,
            provider=self.name,
            metadata={"job_id": job_id, "audio_url": audio_url},
        )
