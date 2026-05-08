"""Tests for ``LiveRenderer.arender`` against fake tape + OSC servers.

The renderer's job is:

  1. Push named params via OSC (mirrors ``tools/sound_modeling`` helper).
  2. Trigger a MIDI note (best-effort; we don't assert on the message because
     the AbletonOSC note address has been a moving target between versions).
  3. Drive the configured tape backend to capture audio for ``duration_sec``.
  4. Load the resulting wav with ``soundfile`` and return mono float32 audio.

Tests use the async ``arender`` entry point so we stay on the pytest-asyncio
event loop instead of spawning a worker thread (which would also work but
adds noise).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from ableton_mcp.config import Config as OSCConfig
from ableton_mcp.sound import LiveRenderer
from ableton_mcp.tape import CaptureConfig


class _FakeAbletonOSC:
    """Stand-in for AbletonOSC: replies to /live/device/get/parameters/name."""

    def __init__(self, host: str, recv_port: int, param_names: list[str]) -> None:
        self._host = host
        self._reply_client = SimpleUDPClient(host, recv_port)
        self._param_names = param_names
        self.received: list[tuple[str, tuple]] = []
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self, listen_port: int) -> None:
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._handler)
        server = AsyncIOOSCUDPServer(
            (self._host, listen_port), dispatcher, asyncio.get_running_loop()
        )
        self._transport, _ = await server.create_serve_endpoint()

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _handler(self, address: str, *args: object) -> None:
        self.received.append((address, args))
        if address == "/live/device/get/parameters/name":
            track_id = args[0] if args else 0
            device_id = args[1] if len(args) > 1 else 0
            self._reply_client.send_message(
                "/live/device/get/parameters/name",
                [track_id, device_id, *self._param_names],
            )


class _FakeTapeServer:
    def __init__(self, host: str, send_port: int, recv_port: int) -> None:
        self._host = host
        self._send_port = send_port
        self._reply_client = SimpleUDPClient(host, recv_port)
        self.received: list[tuple[str, tuple]] = []
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._handler)
        server = AsyncIOOSCUDPServer(
            (self._host, self._send_port), dispatcher, asyncio.get_running_loop()
        )
        self._transport, _ = await server.create_serve_endpoint()

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _handler(self, address: str, *args: object) -> None:
        self.received.append((address, args))
        if address == "/tape/ping":
            self._reply_client.send_message("/tape/pong", [])
        elif address == "/tape/record":
            path = str(args[0])
            duration = float(args[1])
            try:
                import soundfile as sf  # type: ignore[import-not-found]
            except Exception:
                sf = None
            if sf is not None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                sr = 22050
                n = max(1, int(round(sr * duration)))
                t = np.arange(n, dtype=np.float32) / sr
                audio = 0.5 * np.sin(2.0 * np.pi * 440.0 * t)
                sf.write(path, audio.astype(np.float32), sr)
            asyncio.get_event_loop().call_later(
                0.01,
                lambda: self._reply_client.send_message("/tape/done", [path, duration]),
            )


@pytest.mark.asyncio
async def test_live_renderer_pushes_params_and_returns_audio(tmp_path: Path) -> None:
    """End-to-end: arender() pushes the right OSC, triggers tape, returns audio."""
    pytest.importorskip("soundfile")

    osc_send_port, osc_recv_port = 17310, 17311
    tape_send_port, tape_recv_port = 17312, 17313

    fake_osc = _FakeAbletonOSC(
        "127.0.0.1", osc_recv_port,
        param_names=["Cutoff", "Resonance", "Volume"],
    )
    await fake_osc.start(listen_port=osc_send_port)

    fake_tape = _FakeTapeServer("127.0.0.1", tape_send_port, tape_recv_port)
    await fake_tape.start()

    osc_cfg = OSCConfig(
        osc_host="127.0.0.1",
        osc_send_port=osc_send_port,
        osc_recv_port=osc_recv_port,
        request_timeout=2.0,
    )
    cap_cfg = CaptureConfig(
        backend="tape", tape_host="127.0.0.1",
        tape_send_port=tape_send_port, tape_recv_port=tape_recv_port,
        tape_timeout=2.0,
    )

    try:
        renderer = LiveRenderer(
            track_index=2, device_index=0,
            sample_rate=22050, duration_sec=0.05,
            capture_config=cap_cfg, osc_config=osc_cfg,
            tmp_dir=str(tmp_path),
        )
        audio = await renderer.arender({"cutoff": 0.5, "resonance": 0.9, "unknown": 1.0})
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert audio.size > 0

        assert any(m[0] == "/live/device/get/parameters/name" for m in fake_osc.received)
        sets = [m for m in fake_osc.received if m[0] == "/live/device/set/parameter/value"]
        assert len(sets) == 2  # cutoff + resonance matched; unknown didn't
        recs = [m for m in fake_tape.received if m[0] == "/tape/record"]
        assert len(recs) == 1
        assert float(recs[0][1][1]) == pytest.approx(0.05)
    finally:
        fake_osc.stop()
        fake_tape.stop()


@pytest.mark.asyncio
async def test_live_renderer_unmatched_params_are_skipped(tmp_path: Path) -> None:
    pytest.importorskip("soundfile")

    osc_send_port, osc_recv_port = 17320, 17321
    tape_send_port, tape_recv_port = 17322, 17323

    fake_osc = _FakeAbletonOSC("127.0.0.1", osc_recv_port, param_names=["Drive"])
    await fake_osc.start(listen_port=osc_send_port)
    fake_tape = _FakeTapeServer("127.0.0.1", tape_send_port, tape_recv_port)
    await fake_tape.start()

    osc_cfg = OSCConfig(
        osc_host="127.0.0.1", osc_send_port=osc_send_port,
        osc_recv_port=osc_recv_port, request_timeout=2.0,
    )
    cap_cfg = CaptureConfig(
        backend="tape", tape_host="127.0.0.1",
        tape_send_port=tape_send_port, tape_recv_port=tape_recv_port,
        tape_timeout=2.0,
    )
    try:
        renderer = LiveRenderer(
            track_index=0, device_index=0,
            sample_rate=22050, duration_sec=0.05,
            capture_config=cap_cfg, osc_config=osc_cfg,
            tmp_dir=str(tmp_path),
        )
        audio = await renderer.arender({"completely_unknown": 0.5})
        assert audio.size > 0
        sets = [m for m in fake_osc.received if m[0] == "/live/device/set/parameter/value"]
        assert sets == []
    finally:
        fake_osc.stop()
        fake_tape.stop()


def test_live_renderer_load_wav_is_mono_float32(tmp_path: Path) -> None:
    """Stereo wav must be downmixed to mono float32."""
    sf = pytest.importorskip("soundfile")
    path = tmp_path / "stereo.wav"
    sr = 22050
    n = sr // 10
    left = 0.5 * np.sin(2.0 * np.pi * 440.0 * np.arange(n) / sr)
    right = 0.3 * np.sin(2.0 * np.pi * 220.0 * np.arange(n) / sr)
    stereo = np.stack([left, right], axis=1).astype(np.float32)
    sf.write(str(path), stereo, sr)

    renderer = LiveRenderer(track_index=0, device_index=0)
    audio = renderer._load_wav(str(path))
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert audio.size == n


def test_live_renderer_construction_is_cheap_without_running_loop() -> None:
    """LiveRenderer should construct without touching the network."""
    renderer = LiveRenderer(
        track_index=0, device_index=0,
        sample_rate=22050, duration_sec=0.05,
    )
    assert renderer.track_index == 0
    assert renderer.sample_rate == 22050
    # Render not invoked, so no real Live needed.
