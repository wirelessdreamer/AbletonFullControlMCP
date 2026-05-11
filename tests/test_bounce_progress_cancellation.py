"""Tests for the streaming-progress + cancellation behaviour added to
``bounce_song_via_resampling``.

These extend the existing test_bounce_resampling suite with the new
opt-in ``progress_callback`` parameter and the asyncio.CancelledError
contract (transport stopped, record_mode off, temp track deleted, error
re-raised).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.bounce import resampling


# Reuse the test doubles from test_bounce_resampling. Importing through
# the fully-qualified path here so this file stays self-contained — the
# fakes are simple enough to redeclare inline rather than reach across
# test files.


class FakeOSC:
    def __init__(self) -> None:
        self.track_names: list[str] = ["UserTrack"]
        self.input_routing: dict[int, str] = {}
        self.arm: dict[int, int] = {}
        self.sent: list[tuple[str, tuple]] = []

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/num_tracks":
            return (len(self.track_names),)
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/track/get/input_routing_type":
            ti = int(args[0])
            return (ti, self.input_routing.get(ti, "No Input"))
        if addr == "/live/track/get/arm":
            ti = int(args[0])
            return (ti, self.arm.get(ti, 0))
        if addr == "/live/track/get/mute":
            return (int(args[0]), 0)
        if addr == "/live/track/get/has_audio_output":
            return (int(args[0]), 1)
        raise AssertionError(f"unexpected OSC request: {addr}")

    def send(self, addr: str, *args: Any) -> None:
        self.sent.append((addr, args))
        if addr == "/live/song/create_audio_track":
            self.track_names.append("temp")
        elif addr == "/live/track/set/name":
            ti, name = int(args[0]), str(args[1])
            while len(self.track_names) <= ti:
                self.track_names.append("")
            self.track_names[ti] = name
        elif addr == "/live/track/set/input_routing_type":
            self.input_routing[int(args[0])] = str(args[1])
        elif addr == "/live/track/set/arm":
            self.arm[int(args[0])] = int(args[1])
        elif addr == "/live/song/delete_track":
            ti = int(args[0])
            if 0 <= ti < len(self.track_names):
                del self.track_names[ti]


class FakeBridge:
    def __init__(self, file_path: str | None = "/tmp/recorded.wav") -> None:
        self.file_path = file_path
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        if op == "clip.arrangement_clip_info":
            return {"file_path": self.file_path}
        return {}


def _wire_fakes(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge) -> None:
    async def fake_get_client() -> FakeOSC:
        return osc

    def fake_get_bridge() -> FakeBridge:
        return bridge

    monkeypatch.setattr(resampling, "get_client", fake_get_client)
    monkeypatch.setattr(resampling, "get_bridge_client", fake_get_bridge)


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_emits_progress_at_phase_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A simple progress callback should receive a sequence of (progress,
    message) pairs covering the major phases of the bounce."""
    osc = FakeOSC()
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    progress_events: list[tuple[float, str]] = []

    async def on_progress(progress: float, message: str) -> None:
        progress_events.append((progress, message))

    await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05, settle_sec=0.02,
        clip_finalize_timeout_sec=0.5,
        progress_callback=on_progress,
    )

    # Verify the headline phases all fired.
    msgs = [m for _, m in progress_events]
    assert any("starting" in m for m in msgs)
    assert any("pre-cleanup" in m for m in msgs)
    assert any("armed" in m or "recording" in m for m in msgs)
    assert any("harvest" in m for m in msgs)
    assert any("cleaned up" in m for m in msgs)
    # Final 1.0 event marks success (file did get copied; FakeBridge
    # returns a path, but it doesn't actually exist on disk → result is
    # {"copied": False}). So progress caps at 0.95 in this fake setup.
    final_progress = max(p for p, _ in progress_events)
    assert final_progress >= 0.85


@pytest.mark.asyncio
async def test_bounce_reaches_full_progress_when_copy_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When the source wav exists and copy succeeds, progress hits 1.0."""
    real_wav = tmp_path / "source.wav"
    real_wav.write_bytes(b"RIFF....WAVEfake-content")

    osc = FakeOSC()
    bridge = FakeBridge(file_path=str(real_wav))
    _wire_fakes(monkeypatch, osc, bridge)

    progress_events: list[tuple[float, str]] = []

    async def on_progress(progress: float, message: str) -> None:
        progress_events.append((progress, message))

    out_path = tmp_path / "out.wav"
    result = await resampling.bounce_song_via_resampling(
        out_path,
        duration_sec=0.05, settle_sec=0.02,
        clip_finalize_timeout_sec=0.5,
        progress_callback=on_progress,
    )
    assert result["copied"] is True
    # Last progress is 1.0 with "complete".
    final_progress, final_msg = progress_events[-1]
    assert final_progress == 1.0
    assert "complete" in final_msg


@pytest.mark.asyncio
async def test_bounce_without_callback_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Callback is opt-in; not passing one shouldn't change behaviour."""
    osc = FakeOSC()
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)
    result = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05, settle_sec=0.02,
        clip_finalize_timeout_sec=0.5,
    )
    # The bounce ran end-to-end without a callback.
    assert "what" in result


@pytest.mark.asyncio
async def test_progress_callback_failure_does_not_break_bounce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A buggy callback that raises should not abort the bounce — progress
    notifications are best-effort."""
    osc = FakeOSC()
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    async def bad_callback(progress: float, message: str) -> None:
        raise RuntimeError("simulated callback bug")

    # Should NOT raise.
    result = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05, settle_sec=0.02,
        clip_finalize_timeout_sec=0.5,
        progress_callback=bad_callback,
    )
    assert "what" in result


# ---------------------------------------------------------------------------
# Periodic in-recording progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_arrangement_with_progress_emits_periodic_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long-ish record window should emit several progress notifications
    during the recording phase mapped onto [progress_start, progress_end]."""
    osc = FakeOSC()
    _wire_fakes(monkeypatch, osc, FakeBridge())

    events: list[tuple[float, str]] = []

    async def on_progress(progress: float, message: str) -> None:
        events.append((progress, message))

    # 0.3 s record, poll every 0.1 s → ~3 progress events.
    await resampling._record_arrangement_with_progress(
        duration_sec=0.3, settle_sec=0.05,
        progress_callback=on_progress,
        progress_start=0.1, progress_end=0.9,
        poll_interval_sec=0.1,
    )
    # All progress values fall inside the [start, end] window.
    for p, _ in events:
        assert 0.1 <= p <= 0.9 + 0.001
    # Messages describe the recording phase.
    assert any("recording" in m for m in (m for _, m in events))
    # Multiple updates (at least 2).
    assert len(events) >= 2


@pytest.mark.asyncio
async def test_record_arrangement_short_does_not_emit_intermediate_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record window shorter than the poll interval just does one wait,
    no intermediate progress."""
    osc = FakeOSC()
    _wire_fakes(monkeypatch, osc, FakeBridge())

    events: list[tuple[float, str]] = []

    async def on_progress(progress: float, message: str) -> None:
        events.append((progress, message))

    # 0.05 s record window with 1.0 s poll interval → single wait, no
    # intermediate progress (the loop only kicks in when total > interval).
    await resampling._record_arrangement_with_progress(
        duration_sec=0.05, settle_sec=0.0,
        progress_callback=on_progress,
        progress_start=0.0, progress_end=1.0,
        poll_interval_sec=1.0,
    )
    # No intermediate progress events.
    assert events == []


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_cancellation_stops_transport_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If the bounce task is cancelled mid-record, the OSC stop_playing
    + record_mode=0 should still fire, and the temp track should be
    deleted in the finally block. The CancelledError must propagate."""
    osc = FakeOSC()
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    async def run_and_cancel():
        task = asyncio.create_task(
            resampling.bounce_song_via_resampling(
                tmp_path / "out.wav",
                duration_sec=5.0,  # long enough that we cancel mid-record
                settle_sec=0.0,
                clip_finalize_timeout_sec=0.1,
            )
        )
        # Give the task time to enter the recording phase.
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await run_and_cancel()
    # Transport stop + record_mode=0 must have been sent.
    sent_addrs = [s[0] for s in osc.sent]
    assert "/live/song/stop_playing" in sent_addrs
    assert "/live/song/set/record_mode" in sent_addrs
    record_mode_writes = [s for s in osc.sent if s[0] == "/live/song/set/record_mode"]
    # The last record_mode write should be 0 (disable record).
    assert record_mode_writes[-1][1] == (0,)
    # Temp track should have been deleted (cleanup in finally ran).
    assert "/live/song/delete_track" in sent_addrs


@pytest.mark.asyncio
async def test_bounce_cancellation_emits_progress_up_to_point_of_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When cancelled mid-record, the progress callback should have
    fired for at least the setup phases and possibly some recording
    progress — never reaching 1.0."""
    osc = FakeOSC()
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    events: list[tuple[float, str]] = []

    async def on_progress(progress: float, message: str) -> None:
        events.append((progress, message))

    async def run_and_cancel():
        task = asyncio.create_task(
            resampling.bounce_song_via_resampling(
                tmp_path / "out.wav",
                duration_sec=5.0, settle_sec=0.0,
                clip_finalize_timeout_sec=0.1,
                progress_callback=on_progress,
            )
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await run_and_cancel()
    # We should have seen at least the setup phase notifications.
    msgs = [m for _, m in events]
    assert any("pre-cleanup" in m for m in msgs)
    # Final progress is below 1.0 (cancelled before complete).
    if events:
        assert max(p for p, _ in events) < 1.0
