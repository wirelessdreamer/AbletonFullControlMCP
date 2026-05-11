"""Tests for region-bounded bounce — Layer 1.1 of the mix-aware shaping stack.

The MCP-side function ``bounce_region_via_resampling`` wraps the existing
``bounce_song`` / ``bounce_tracks`` paths with a beat→seconds conversion
and a starting-position parameter. Tests here verify:

1. The pure-math helper (`_beats_to_seconds`).
2. Range validation (start must be >= 0, end must be > start).
3. That the playhead is parked at the right beat before recording starts.
4. That master vs. stems routing both honor the new parameter.

Live + bridge are mocked. Real-time waits are kept short (region in
fractions of a beat) so the test suite stays fast.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.bounce import resampling


# ---------------------------------------------------------------------------
# _beats_to_seconds — pure math
# ---------------------------------------------------------------------------


def test_beats_to_seconds_canonical_tempo() -> None:
    """At 120 BPM, 1 beat = 0.5 s, 4 beats = 2 s, 16 beats = 8 s."""
    assert resampling._beats_to_seconds(1.0, 120.0) == pytest.approx(0.5)
    assert resampling._beats_to_seconds(4.0, 120.0) == pytest.approx(2.0)
    assert resampling._beats_to_seconds(16.0, 120.0) == pytest.approx(8.0)


def test_beats_to_seconds_other_tempos() -> None:
    """At 60 BPM, 1 beat = 1 s. At 240 BPM, 1 beat = 0.25 s."""
    assert resampling._beats_to_seconds(1.0, 60.0) == pytest.approx(1.0)
    assert resampling._beats_to_seconds(1.0, 240.0) == pytest.approx(0.25)


def test_beats_to_seconds_zero_beats() -> None:
    assert resampling._beats_to_seconds(0.0, 120.0) == 0.0


def test_beats_to_seconds_invalid_tempo() -> None:
    with pytest.raises(ValueError, match="tempo"):
        resampling._beats_to_seconds(4.0, 0.0)
    with pytest.raises(ValueError, match="tempo"):
        resampling._beats_to_seconds(4.0, -120.0)


# ---------------------------------------------------------------------------
# Range validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_region_rejects_negative_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="start_beats"):
        await resampling.bounce_region_via_resampling(
            tmp_path, start_beats=-1.0, end_beats=4.0,
        )


@pytest.mark.asyncio
async def test_bounce_region_rejects_end_le_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="end_beats"):
        await resampling.bounce_region_via_resampling(
            tmp_path, start_beats=4.0, end_beats=4.0,
        )
    with pytest.raises(ValueError, match="end_beats"):
        await resampling.bounce_region_via_resampling(
            tmp_path, start_beats=4.0, end_beats=2.0,
        )


# ---------------------------------------------------------------------------
# Playhead positioning + duration derivation
# ---------------------------------------------------------------------------


class FakeOSC:
    """Minimal OSC stub for region-bounce tests."""

    def __init__(self, *, tempo: float = 120.0, track_names: list[str] | None = None) -> None:
        self.tempo = tempo
        self.track_names = list(track_names or ["UserTrack"])
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
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        if op == "clip.arrangement_clip_info":
            return {"file_path": None}
        return {}


def _wire_fakes(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge) -> None:
    async def fake_get_client() -> FakeOSC:
        return osc

    def fake_get_bridge() -> FakeBridge:
        return bridge

    monkeypatch.setattr(resampling, "get_client", fake_get_client)
    monkeypatch.setattr(resampling, "get_bridge_client", fake_get_bridge)


@pytest.mark.asyncio
async def test_bounce_region_parks_playhead_at_start_beat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Recording phase should issue
    /live/song/set/current_song_time with the region's start_beats value,
    not 0.0 (which the whole-song bounce uses)."""
    osc = FakeOSC(tempo=120.0)
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    await resampling.bounce_region_via_resampling(
        tmp_path, start_beats=32.0, end_beats=33.0,  # 1 beat = 0.5 s at 120 BPM
    )
    # The recording-phase setup should have set current_song_time to 32.0.
    set_song_time = [
        s for s in osc.sent if s[0] == "/live/song/set/current_song_time"
    ]
    assert len(set_song_time) >= 1
    # At least one of those sets uses the region start beat. (The bounce
    # may also set it elsewhere on cleanup; we don't assert "only".)
    assert any(s[1] == (32.0,) for s in set_song_time), (
        f"expected at least one current_song_time write to 32.0; got {set_song_time!r}"
    )


@pytest.mark.asyncio
async def test_bounce_region_uses_zero_when_start_is_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """start_beats=0 keeps the playhead-at-zero behaviour of the
    pre-region API. Used both for master-bounce-from-top and as a
    backwards-compat sanity check."""
    osc = FakeOSC(tempo=120.0)
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    await resampling.bounce_region_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0,
    )
    set_song_time = [
        s for s in osc.sent if s[0] == "/live/song/set/current_song_time"
    ]
    assert any(s[1] == (0.0,) for s in set_song_time)


@pytest.mark.asyncio
async def test_bounce_region_duration_derived_from_tempo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Returned region_seconds = (end_beats - start_beats) * 60 / tempo."""
    osc = FakeOSC(tempo=140.0)  # non-default tempo
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    result = await resampling.bounce_region_via_resampling(
        tmp_path, start_beats=8.0, end_beats=16.0,  # 8 beats @ 140 BPM = ~3.43 s
    )
    expected = 8.0 * 60.0 / 140.0
    assert result["region_seconds"] == pytest.approx(expected, rel=1e-6)
    assert result["region_start_beats"] == 8.0
    assert result["region_end_beats"] == 16.0
    assert result["tempo_at_bounce"] == 140.0


# ---------------------------------------------------------------------------
# Routing: master vs. stems
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_region_master_when_track_indices_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """track_indices=None routes to bounce_song_via_resampling and
    captures the master mix."""
    osc = FakeOSC(tempo=120.0)
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    result = await resampling.bounce_region_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0, track_indices=None,
    )
    assert result["kind"] == "region_master"
    # The Resampling routing type would have been set on the temp track.
    assert any(
        s[0] == "/live/track/set/input_routing_type" and s[1][1] == "Resampling"
        for s in osc.sent
    )


@pytest.mark.asyncio
async def test_bounce_region_stems_when_track_indices_provided(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """track_indices=[0] routes to bounce_tracks_via_resampling — per-
    track routing strings, no Resampling."""
    osc = FakeOSC(tempo=120.0, track_names=["Source"])
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    result = await resampling.bounce_region_via_resampling(
        tmp_path, start_beats=0.0, end_beats=1.0, track_indices=[0],
    )
    assert result["kind"] == "region_stems"
    # Per-track routing uses the source track's name pattern "1-Source",
    # not "Resampling".
    routing_writes = [
        s for s in osc.sent if s[0] == "/live/track/set/input_routing_type"
    ]
    assert any("Source" in s[1][1] for s in routing_writes), routing_writes


@pytest.mark.asyncio
async def test_bounce_region_metadata_attached_to_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Result includes region_start_beats, region_end_beats,
    region_seconds, tempo_at_bounce — useful downstream for the mix
    analysis layer."""
    osc = FakeOSC(tempo=100.0)
    bridge = FakeBridge()
    _wire_fakes(monkeypatch, osc, bridge)

    result = await resampling.bounce_region_via_resampling(
        tmp_path, start_beats=4.0, end_beats=8.0,
    )
    for key in (
        "region_start_beats", "region_end_beats", "region_seconds",
        "tempo_at_bounce", "kind",
    ):
        assert key in result, f"missing metadata key {key!r}"
