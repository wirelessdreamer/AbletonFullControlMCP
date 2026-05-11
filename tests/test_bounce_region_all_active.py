"""Tests for ``bounce_region_all_active_via_resampling`` — L1.3 of the
mix-aware shaping stack.

Combines the active-track selection logic from
``bounce_enabled_via_resampling`` with the region primitive from L1.1.
Tests verify the selection (mute / temp-track / has_audio_output
filtering) routes through correctly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.bounce import resampling


class FakeOSC:
    def __init__(
        self,
        *,
        tempo: float = 120.0,
        track_names: list[str] | None = None,
        muted: set[int] | None = None,
        non_audio: set[int] | None = None,
    ) -> None:
        self.tempo = tempo
        self.track_names = list(track_names or [])
        self.muted = muted or set()
        self.non_audio = non_audio or set()
        self.input_routing: dict[int, str] = {}
        self.arm: dict[int, int] = {}
        self.sent: list[tuple[str, tuple]] = []

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/tempo":
            return (self.tempo,)
        if addr == "/live/song/get/num_tracks":
            return (len(self.track_names),)
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/track/get/mute":
            ti = int(args[0])
            return (ti, 1 if ti in self.muted else 0)
        if addr == "/live/track/get/has_audio_output":
            ti = int(args[0])
            return (ti, 0 if ti in self.non_audio else 1)
        if addr == "/live/track/get/input_routing_type":
            ti = int(args[0])
            return (ti, self.input_routing.get(ti, "No Input"))
        if addr == "/live/track/get/arm":
            ti = int(args[0])
            return (ti, self.arm.get(ti, 0))
        raise AssertionError(f"unexpected OSC: {addr}")

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
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        if op == "clip.arrangement_clip_info":
            return {"file_path": None}
        return {}


def _wire(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge) -> None:
    async def fake_get_client() -> FakeOSC:
        return osc

    def fake_get_bridge() -> FakeBridge:
        return bridge

    monkeypatch.setattr(resampling, "get_client", fake_get_client)
    monkeypatch.setattr(resampling, "get_bridge_client", fake_get_bridge)


@pytest.mark.asyncio
async def test_bounce_region_all_active_includes_unmuted_audio_tracks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    osc = FakeOSC(track_names=["Drums", "Bass", "Vocals"])
    bridge = FakeBridge()
    _wire(monkeypatch, osc, bridge)
    result = await resampling.bounce_region_all_active_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0,
    )
    # All 3 tracks selected → per-track stems for the region.
    assert result["kind"] == "region_stems"
    indices = {s["source_track_index"] for s in result["stems"]}
    assert indices == {0, 1, 2}


@pytest.mark.asyncio
async def test_bounce_region_all_active_skips_muted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    osc = FakeOSC(
        track_names=["A", "B (muted)", "C"], muted={1},
    )
    bridge = FakeBridge()
    _wire(monkeypatch, osc, bridge)
    result = await resampling.bounce_region_all_active_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0,
    )
    indices = {s["source_track_index"] for s in result["stems"]}
    assert indices == {0, 2}  # 1 was muted


@pytest.mark.asyncio
async def test_bounce_region_all_active_skips_non_audio_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    osc = FakeOSC(
        track_names=["A", "Group (no audio)", "B"], non_audio={1},
    )
    bridge = FakeBridge()
    _wire(monkeypatch, osc, bridge)
    result = await resampling.bounce_region_all_active_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0,
    )
    indices = {s["source_track_index"] for s in result["stems"]}
    assert indices == {0, 2}


@pytest.mark.asyncio
async def test_bounce_region_all_active_skips_orphan_temp_tracks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Temp tracks from previous crashed bounces (suffix marker) are
    deleted by the pre-cleanup pass before track selection happens, so
    they DO NOT show up in the bounced stems. After cleanup, the
    remaining real tracks are renumbered."""
    osc = FakeOSC(
        track_names=["Real", f"Old Bounce{resampling.TEMP_TRACK_SUFFIX}", "Also Real"],
    )
    bridge = FakeBridge()
    _wire(monkeypatch, osc, bridge)
    result = await resampling.bounce_region_all_active_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0,
    )
    # Pre-cleanup ran first, deleted the orphan at index 1.
    # "Also Real" shifted from index 2 → 1. So we bounce indices {0, 1}.
    indices = {s["source_track_index"] for s in result["stems"]}
    assert indices == {0, 1}
    # No stem named like an orphan got captured.
    names = {s["source_track_name"] for s in result["stems"]}
    assert "Real" in names and "Also Real" in names
    assert not any(resampling.TEMP_TRACK_SUFFIX in n for n in names)


@pytest.mark.asyncio
async def test_bounce_region_all_active_carries_region_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The L1.1 region metadata (start_beats, end_beats, tempo) should
    pass through to L1.3's result so downstream layers can see it."""
    osc = FakeOSC(track_names=["A"], tempo=100.0)
    bridge = FakeBridge()
    _wire(monkeypatch, osc, bridge)
    result = await resampling.bounce_region_all_active_via_resampling(
        tmp_path, start_beats=8.0, end_beats=12.0,
    )
    assert result["region_start_beats"] == 8.0
    assert result["region_end_beats"] == 12.0
    assert result["tempo_at_bounce"] == 100.0
