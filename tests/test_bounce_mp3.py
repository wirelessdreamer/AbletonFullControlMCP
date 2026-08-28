"""Tests for bounce.mp3 — especially ffmpeg's inherited-stdin hazard.

Regression context: ffmpeg reads stdin for its interactive controls and a
spawned child inherits the parent's stdin. Inside an MCP server that stdin
is the JSON-RPC protocol pipe, so an undetached ffmpeg blocks on it —
observed live as a 10 s mp3 encode taking 11 MINUTES with ffmpeg burning
no CPU and the disk idle — and can steal bytes from the protocol stream.
Both guards (``-nostdin`` and ``stdin=DEVNULL``) are pinned here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.bounce import mp3


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch):
    """Capture the argv + kwargs of the ffmpeg call without running it."""
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(mp3, "find_ffmpeg", lambda: "ffmpeg")

    class FakeProc:
        returncode = 0
        stderr = "size=  1234kB time=00:00:10.00 bitrate= 192.0kbits/s"

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        Path(cmd[-1]).write_bytes(b"ID3fake")  # the "encoded" mp3
        return FakeProc()

    monkeypatch.setattr(mp3.subprocess, "run", fake_run)
    return calls


def _src(tmp_path: Path) -> Path:
    p = tmp_path / "in.wav"
    p.write_bytes(b"RIFFfake")
    return p


def test_nostdin_flag_present(fake_ffmpeg, tmp_path: Path) -> None:
    mp3.encode_wav_to_mp3(_src(tmp_path), tmp_path / "out.mp3")
    cmd = fake_ffmpeg[0]["cmd"]
    assert "-nostdin" in cmd, "ffmpeg must not read the parent's stdin"
    # Must precede the input so it applies to the whole invocation.
    assert cmd.index("-nostdin") < cmd.index("-i")


def test_stdin_is_devnull(fake_ffmpeg, tmp_path: Path) -> None:
    mp3.encode_wav_to_mp3(_src(tmp_path), tmp_path / "out.mp3")
    assert fake_ffmpeg[0]["kwargs"].get("stdin") is subprocess.DEVNULL


def test_encode_returns_metadata(fake_ffmpeg, tmp_path: Path) -> None:
    out = mp3.encode_wav_to_mp3(_src(tmp_path), tmp_path / "out.mp3", bitrate_kbps=256)
    assert out["bitrate_kbps"] == 256
    assert out["size_bytes"] > 0
    assert "-b:a" in fake_ffmpeg[0]["cmd"]


def test_vbr_quality_overrides_bitrate(fake_ffmpeg, tmp_path: Path) -> None:
    out = mp3.encode_wav_to_mp3(_src(tmp_path), tmp_path / "out.mp3", quality=2)
    cmd = fake_ffmpeg[0]["cmd"]
    assert "-q:a" in cmd and "-b:a" not in cmd
    assert out["vbr_quality"] == 2 and out["bitrate_kbps"] is None


def test_missing_input_raises(fake_ffmpeg, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        mp3.encode_wav_to_mp3(tmp_path / "nope.wav", tmp_path / "out.mp3")


def test_ffmpeg_failure_raises_with_stderr_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mp3, "find_ffmpeg", lambda: "ffmpeg")

    class FailProc:
        returncode = 1
        stderr = "line1\nEncoder libmp3lame not found"

    monkeypatch.setattr(mp3.subprocess, "run", lambda cmd, **kw: FailProc())
    with pytest.raises(RuntimeError, match="libmp3lame not found"):
        mp3.encode_wav_to_mp3(_src(tmp_path), tmp_path / "out.mp3")
