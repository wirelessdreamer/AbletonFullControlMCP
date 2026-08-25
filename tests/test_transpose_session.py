"""Tests for transpose_session_clip — the arrangement-safe transpose path —
and the bounce silence guard (wav_peak_dbfs)."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ableton_mcp.bounce.resampling import wav_peak_dbfs
from ableton_mcp.song_flow import transpose as tr


# ---------------------------------------------------------------------------
# wav_peak_dbfs
# ---------------------------------------------------------------------------


def _write_wav(path: Path, amplitude: float, duration_sec: float = 0.5, sr: int = 44100) -> None:
    t = np.arange(int(sr * duration_sec)) / sr
    data = (amplitude * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def test_peak_dbfs_full_scale(tmp_path: Path) -> None:
    p = tmp_path / "loud.wav"
    _write_wav(p, 1.0)
    peak = wav_peak_dbfs(p)
    assert peak is not None and peak > -1.0


def test_peak_dbfs_silence(tmp_path: Path) -> None:
    p = tmp_path / "silent.wav"
    _write_wav(p, 0.0)
    peak = wav_peak_dbfs(p)
    assert peak is not None and peak < -90.0


def test_peak_dbfs_unreadable_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "not_audio.wav"
    p.write_text("junk")
    assert wav_peak_dbfs(p) is None


# ---------------------------------------------------------------------------
# transpose_session_clip
# ---------------------------------------------------------------------------


class FakeOSC:
    def __init__(self, tempo: float = 120.0, clip_length_beats: float = 8.0):
        self.tempo = tempo
        self.clip_length_beats = clip_length_beats
        self.sent: list[tuple] = []

    async def request(self, addr: str, *args: Any):
        if addr == "/live/song/get/tempo":
            return (self.tempo,)
        if addr == "/live/clip/get/length":
            return (args[0], args[1], self.clip_length_beats)
        raise AssertionError(f"unexpected request {addr}")

    def send(self, addr: str, *args: Any) -> None:
        self.sent.append((addr, *args))


class FakeBridge:
    """Session-scope pitch-state handlers for one audio clip."""

    def __init__(self, is_empty: bool = False):
        self.is_empty = is_empty
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.pitch_coarse = 0

    async def call(self, op: str, **kwargs: Any):
        self.calls.append((op, kwargs))
        if op == "clip.get_session_pitch_state":
            if self.is_empty:
                return {"is_empty": True}
            return {
                "is_midi_clip": False,
                "warping": False,
                "warp_mode": 0,
                "pitch_coarse": self.pitch_coarse,
                "pitch_fine": 0,
            }
        if op == "clip.set_session_pitch":
            self.pitch_coarse = kwargs["coarse"]
            return {}
        if op in ("clip.set_session_warp", "clip.set_session_warp_mode"):
            return {}
        raise AssertionError(f"unexpected bridge op {op}")


def _wire(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge,
          bounce_result: dict[str, Any]):
    async def fake_get_client():
        return osc

    def fake_get_bridge():
        return bridge

    bounce_calls: list[dict[str, Any]] = []

    async def fake_bounce(output_path: str, duration_sec: float, **kw: Any):
        bounce_calls.append({"output_path": output_path, "duration_sec": duration_sec})
        return dict(bounce_result)

    monkeypatch.setattr(tr, "get_client", fake_get_client)
    monkeypatch.setattr(tr, "get_bridge_client", fake_get_bridge)
    monkeypatch.setattr(tr, "bounce_song_via_resampling", fake_bounce)
    return bounce_calls


async def test_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    osc, bridge = FakeOSC(), FakeBridge()
    bounce_calls = _wire(monkeypatch, osc, bridge,
                         {"copied": True, "peak_dbfs": -12.3, "output_path": "x"})
    r = await tr.transpose_session_clip(
        3, 0, "E", "F", output_path=tmp_path / "out.wav",
    )
    assert r["status"] == "ok"
    assert r["semitone_delta"] == -1
    assert r["peak_dbfs"] == -12.3
    # Clip was fired before the bounce and stopped after.
    assert ("/live/clip/fire", 3, 0) in osc.sent
    assert ("/live/clip/stop", 3, 0) in osc.sent
    # Mutation went through the SESSION handlers and was restored.
    ops = [op for op, _ in bridge.calls]
    assert "clip.set_session_warp" in ops
    assert "clip.set_session_warp_mode" in ops
    assert ops.count("clip.set_session_pitch") == 2  # shift + restore
    assert bridge.pitch_coarse == 0  # restored
    # Duration derived from clip length: 8 beats @120bpm = 4s + 2s tail.
    assert bounce_calls[0]["duration_sec"] == pytest.approx(6.0)


async def test_silence_guard_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    osc, bridge = FakeOSC(), FakeBridge()
    _wire(monkeypatch, osc, bridge,
          {"copied": True, "peak_dbfs": -180.0, "output_path": "x"})
    r = await tr.transpose_session_clip(3, 0, "E", "F", output_path=tmp_path / "o.wav")
    assert r["status"] == "error"
    assert r["stage"] == "silence_guard"
    assert bridge.pitch_coarse == 0  # restore still ran


async def test_empty_slot_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    osc, bridge = FakeOSC(), FakeBridge(is_empty=True)
    _wire(monkeypatch, osc, bridge, {"copied": True, "peak_dbfs": -12.0})
    r = await tr.transpose_session_clip(3, 0, "E", "F", output_path=tmp_path / "o.wav")
    assert r["status"] == "error"
    assert r["stage"] == "clip"
    # Nothing was fired for an empty slot.
    assert not any(s[0] == "/live/clip/fire" for s in osc.sent)


async def test_same_key_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    osc, bridge = FakeOSC(), FakeBridge()
    _wire(monkeypatch, osc, bridge, {"copied": True})
    r = await tr.transpose_session_clip(3, 0, "F", "F")
    assert r["status"] == "noop"
    assert osc.sent == []


async def test_bounce_failure_restores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    osc, bridge = FakeOSC(), FakeBridge()
    _wire(monkeypatch, osc, bridge, {"copied": False, "error": "copy failed"})
    r = await tr.transpose_session_clip(3, 0, "E", "F", output_path=tmp_path / "o.wav")
    assert r["status"] == "error"
    assert r["stage"] == "bounce"
    assert bridge.pitch_coarse == 0
    assert ("/live/clip/stop", 3, 0) in osc.sent
