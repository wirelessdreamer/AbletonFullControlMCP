"""Unit tests for the Phase 6 generator + stems plumbing.

These tests do NOT make any network calls; the Suno HTTP layer is stubbed.
Demucs and audiocraft are not assumed to be installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.generators import (
    GenResult,
    Generator,
    GeneratorNotConfigured,
)
from ableton_mcp.generators import registry as gen_registry
from ableton_mcp.generators.suno import SunoGenerator
from ableton_mcp.stems import demucs as stems_demucs


# --- Registry --------------------------------------------------------------


def test_registry_lists_all_known_providers() -> None:
    names = gen_registry.list_names()
    assert "suno" in names
    assert "musicgen" in names
    assert "stable_audio" in names


def test_registry_get_known_provider() -> None:
    inst = gen_registry.get("suno")
    assert isinstance(inst, Generator)
    assert inst.name == "suno"


def test_registry_unknown_name_lists_available() -> None:
    with pytest.raises(KeyError) as excinfo:
        gen_registry.get("does_not_exist")
    msg = str(excinfo.value)
    assert "suno" in msg
    assert "musicgen" in msg
    assert "stable_audio" in msg


# --- Suno: missing key -----------------------------------------------------


def test_suno_not_configured_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUNO_API_KEY", raising=False)
    monkeypatch.delenv("SUNO_API_BASE", raising=False)
    gen = SunoGenerator()
    assert gen.is_configured() is False


@pytest.mark.asyncio
async def test_suno_generate_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUNO_API_KEY", raising=False)
    gen = SunoGenerator()
    with pytest.raises(GeneratorNotConfigured) as excinfo:
        await gen.generate("a calm piano piece")
    assert "SUNO_API_KEY" in str(excinfo.value)


# --- Suno: mocked HTTP -----------------------------------------------------


@pytest.mark.asyncio
async def test_suno_generate_with_mocked_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Synchronous (no-poll) happy path: the API returns audio_url immediately."""
    monkeypatch.setenv("SUNO_API_KEY", "fake-key")
    out_dir = tmp_path / "generated"
    gen = SunoGenerator(output_dir=out_dir)

    job_payload = {
        "id": "job-123",
        "audio_url": "https://example.invalid/audio.mp3",
        "lyrics": "la la la",
        "duration": 42.5,
    }

    async def fake_post(self, client, prompt, lyrics, duration, extra):  # noqa: ARG001
        return job_payload

    async def fake_download(self, client, url, dest: Path):  # noqa: ARG001
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"FAKEAUDIO")

    monkeypatch.setattr(SunoGenerator, "_post_generate", fake_post, raising=True)
    monkeypatch.setattr(SunoGenerator, "_download", fake_download, raising=True)

    result = await gen.generate("lo-fi hip hop with rain")

    assert isinstance(result, GenResult)
    assert result.provider == "suno"
    assert result.lyrics == "la la la"
    assert result.duration == pytest.approx(42.5)
    assert Path(result.audio_path).exists()
    assert Path(result.audio_path).read_bytes() == b"FAKEAUDIO"
    assert result.metadata["job_id"] == "job-123"


@pytest.mark.asyncio
async def test_suno_generate_polls_until_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Async path: initial response has only a job_id; polling finishes it."""
    monkeypatch.setenv("SUNO_API_KEY", "fake-key")
    gen = SunoGenerator(output_dir=tmp_path / "generated")

    poll_calls: list[int] = []

    async def fake_post(self, client, prompt, lyrics, duration, extra):  # noqa: ARG001
        return {"id": "job-xyz"}  # no audio_url yet

    async def fake_poll(self, client, job_id):  # noqa: ARG001
        poll_calls.append(1)
        if len(poll_calls) < 2:
            return {"status": "running"}
        return {
            "status": "complete",
            "audio_url": "https://example.invalid/a.mp3",
            "lyrics": "verse",
            "duration": 30.0,
        }

    async def fake_download(self, client, url, dest: Path):  # noqa: ARG001
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"OK")

    # Skip the real sleep between polls.
    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(SunoGenerator, "_post_generate", fake_post, raising=True)
    monkeypatch.setattr(SunoGenerator, "_poll_status", fake_poll, raising=True)
    monkeypatch.setattr(SunoGenerator, "_download", fake_download, raising=True)
    monkeypatch.setattr("ableton_mcp.generators.suno.asyncio.sleep", fast_sleep)

    result = await gen.generate("ambient pad", duration=30.0)
    assert result.duration == pytest.approx(30.0)
    assert result.lyrics == "verse"
    assert len(poll_calls) >= 2


# --- Demucs ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_demucs_missing_reports_clean_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audio = tmp_path / "in.wav"
    audio.write_bytes(b"not-real-audio")
    monkeypatch.setattr(stems_demucs, "_demucs_available", lambda: False)

    with pytest.raises(stems_demucs.DemucsNotInstalled) as excinfo:
        await stems_demucs.split_stems(str(audio), out_dir=tmp_path / "stems")
    assert "pip install demucs" in str(excinfo.value)


@pytest.mark.asyncio
async def test_demucs_missing_input_file(tmp_path: Path) -> None:
    with pytest.raises(stems_demucs.StemsError):
        await stems_demucs.split_stems(
            str(tmp_path / "nope.wav"), out_dir=tmp_path / "stems"
        )


# --- gen_list_providers / unknown provider in tools ------------------------


@pytest.mark.asyncio
async def test_gen_list_providers_includes_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUNO_API_KEY", raising=False)
    monkeypatch.delenv("STABLE_AUDIO_API_KEY", raising=False)
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import suno as suno_tools

    mcp = FastMCP("t")
    suno_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "gen_list_providers",
        "gen_generate",
        "stems_split",
        "stems_import_to_live",
        "suno_generate",
        "suno_import_stems",
    } <= names
