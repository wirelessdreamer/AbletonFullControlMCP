"""Tests for the freeze-mode bounce path.

Live + bridge are mocked. The bridge double simulates ``Track.freeze()``
side-effects (state transitions + new files appearing in the Freezing
folder) so we can test the harvest-and-copy loop without launching Live.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.bounce import freeze as freeze_mod
from ableton_mcp.bridge_client import AbletonBridgeError


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeOSC:
    """Minimal OSC stub — freeze path only touches track_count / track_names /
    mute / has_audio_output."""

    def __init__(self, track_names: list[str], muted: set[int] | None = None,
                 no_audio_out: set[int] | None = None) -> None:
        self.track_names = list(track_names)
        self.muted = muted or set()
        self.no_audio_out = no_audio_out or set()

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/num_tracks":
            return (len(self.track_names),)
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/track/get/mute":
            ti = int(args[0])
            return (ti, 1 if ti in self.muted else 0)
        if addr == "/live/track/get/has_audio_output":
            ti = int(args[0])
            return (ti, 0 if ti in self.no_audio_out else 1)
        raise AssertionError(f"unexpected OSC: {addr} {args!r}")

    def send(self, *_args: Any, **_kwargs: Any) -> None:
        pass


class FakeBridge:
    """Bridge stub that simulates the freeze lifecycle.

    Per-track state is held in ``self.freeze_state`` (0=normal, 1=frozen).
    Calling ``track.freeze`` transitions a track to frozen AND writes a
    fake wav into ``self.freezing_dir`` so the harvest path can find it.
    ``track.unfreeze`` reverses both (but leaves the wav on disk — Live
    does the same).
    """

    def __init__(self, freezing_dir: Path | None,
                 initial_files: list[Path] | None = None,
                 freeze_raises_for: set[int] | None = None,
                 freeze_hangs_for: set[int] | None = None,
                 list_freezing_raises: bool = False) -> None:
        self.freezing_dir = str(freezing_dir) if freezing_dir else None
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.freeze_state: dict[int, int] = {}
        self.freeze_raises_for = freeze_raises_for or set()
        self.freeze_hangs_for = freeze_hangs_for or set()
        self.list_freezing_raises = list_freezing_raises
        if freezing_dir:
            freezing_dir.mkdir(parents=True, exist_ok=True)
        # Seed any pre-existing files (e.g. from a previous freeze).
        if initial_files:
            for p in initial_files:
                p.write_bytes(b"prev")

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        if op == "project.list_freezing_dir":
            if self.list_freezing_raises:
                raise AbletonBridgeError("simulated list_freezing_dir failure")
            return self._list_freezing()
        if op == "track.is_frozen":
            ti = int(kwargs["track_index"])
            return {
                "track_index": ti,
                "freezing_state": self.freeze_state.get(ti, 0),
            }
        if op == "track.freeze":
            ti = int(kwargs["track_index"])
            if ti in self.freeze_raises_for:
                raise AbletonBridgeError(f"simulated freeze failure on track {ti}")
            if ti in self.freeze_hangs_for:
                # Stay in state 0 — _wait_for_frozen will time out.
                return {"track_index": ti, "frozen": True}
            self.freeze_state[ti] = 1
            # Simulate Live writing a new wav to Freezing/.
            if self.freezing_dir:
                wav = Path(self.freezing_dir) / f"freeze_{ti}_{int(time.time()*1000)}.wav"
                # Sleep just long enough for filesystem mtime resolution to be reliable
                time.sleep(0.005)
                wav.write_bytes(b"RIFF....WAVEfake-freeze-payload")
            return {"track_index": ti, "frozen": True}
        if op == "track.unfreeze":
            ti = int(kwargs["track_index"])
            self.freeze_state[ti] = 0
            return {"track_index": ti, "unfrozen": True}
        raise AssertionError(f"unexpected bridge op: {op} {kwargs!r}")

    def _list_freezing(self) -> dict[str, Any]:
        if not self.freezing_dir:
            return {"freezing_dir": None, "files": []}
        files = []
        for name in sorted(os.listdir(self.freezing_dir)):
            full = os.path.join(self.freezing_dir, name)
            if not os.path.isfile(full):
                continue
            st = os.stat(full)
            files.append({
                "path": full,
                "mtime": float(st.st_mtime),
                "size": int(st.st_size),
            })
        return {"freezing_dir": self.freezing_dir, "files": files}


def _patch(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge) -> None:
    async def _fake_get_client() -> FakeOSC:
        return osc

    def _fake_get_bridge() -> FakeBridge:
        return bridge

    monkeypatch.setattr(freeze_mod, "get_client", _fake_get_client)
    monkeypatch.setattr(freeze_mod, "get_bridge_client", _fake_get_bridge)


# ---------------------------------------------------------------------------
# Happy path: bounce_tracks_via_freeze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_bounce_happy_path_two_tracks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(track_names=["Drums", "Bass", "Vocals"])
    bridge = FakeBridge(freezing_dir=freezing)
    _patch(monkeypatch, osc, bridge)

    out = tmp_path / "out"
    result = await freeze_mod.bounce_tracks_via_freeze([0, 1], out)
    assert result["what"] == "stems_via_freeze"
    assert len(result["stems"]) == 2
    for stem in result["stems"]:
        assert stem["copied"] is True, stem
        assert stem["froze_now"] is True
        assert Path(stem["output_path"]).is_file()
    # Naming convention.
    names = sorted(Path(s["output_path"]).name for s in result["stems"])
    assert names[0] == "stem_00_Drums.wav"
    assert names[1] == "stem_01_Bass.wav"


@pytest.mark.asyncio
async def test_freeze_bounce_unfreezes_tracks_we_froze_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(track_names=["A", "B"])
    bridge = FakeBridge(freezing_dir=freezing)
    _patch(monkeypatch, osc, bridge)

    await freeze_mod.bounce_tracks_via_freeze([0, 1], tmp_path / "out")
    # State should be back to normal on both tracks.
    assert bridge.freeze_state[0] == 0
    assert bridge.freeze_state[1] == 0
    # The unfreeze calls should be in the bridge log.
    unfreeze_calls = [c for c in bridge.calls if c[0] == "track.unfreeze"]
    assert {c[1]["track_index"] for c in unfreeze_calls} == {0, 1}


@pytest.mark.asyncio
async def test_freeze_bounce_keep_frozen_skips_unfreeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(track_names=["A"])
    bridge = FakeBridge(freezing_dir=freezing)
    _patch(monkeypatch, osc, bridge)

    await freeze_mod.bounce_tracks_via_freeze(
        [0], tmp_path / "out", keep_frozen=True,
    )
    assert bridge.freeze_state[0] == 1
    assert not any(c[0] == "track.unfreeze" for c in bridge.calls)


# ---------------------------------------------------------------------------
# Pre-existing frozen state should NOT be unfrozen on cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_bounce_leaves_already_frozen_tracks_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When a track is already frozen at call time we don't re-freeze, and on
    cleanup we don't unfreeze it (we didn't freeze it, so we don't undo).
    """
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    # Seed an existing frozen-output wav so the harvest finds it.
    seed_wav = freezing / "preexisting_freeze.wav"
    osc = FakeOSC(track_names=["AlreadyFrozen"])
    bridge = FakeBridge(freezing_dir=freezing, initial_files=[seed_wav])
    bridge.freeze_state[0] = 1  # pre-frozen
    _patch(monkeypatch, osc, bridge)

    result = await freeze_mod.bounce_tracks_via_freeze([0], tmp_path / "out")
    assert result["stems"][0]["was_frozen"] is True
    assert result["stems"][0].get("froze_now") in (False, None)
    # No freeze call should have been made (already frozen) and NO unfreeze.
    assert not any(c[0] == "track.freeze" for c in bridge.calls)
    assert not any(c[0] == "track.unfreeze" for c in bridge.calls)
    # Track stays frozen.
    assert bridge.freeze_state[0] == 1


# ---------------------------------------------------------------------------
# Project unsaved → clean precondition error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_bounce_raises_when_project_unsaved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    osc = FakeOSC(track_names=["A"])
    bridge = FakeBridge(freezing_dir=None)  # unsaved project → no dir
    _patch(monkeypatch, osc, bridge)

    with pytest.raises(freeze_mod.FreezeBounceError) as excinfo:
        await freeze_mod.bounce_tracks_via_freeze([0], tmp_path / "out")
    assert "saved" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Freeze failures: timeouts + bridge errors → diagnostics, no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_bounce_diagnostics_on_freeze_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(track_names=["StuckTrack"])
    bridge = FakeBridge(freezing_dir=freezing, freeze_hangs_for={0})
    _patch(monkeypatch, osc, bridge)

    result = await freeze_mod.bounce_tracks_via_freeze(
        [0], tmp_path / "out", freeze_timeout_sec=0.1,
    )
    assert result["stems"][0]["copied"] is False
    assert "timed out" in result["stems"][0]["error"]
    assert result["diagnostics"] is not None
    assert any("did not complete" in d for d in result["diagnostics"])


@pytest.mark.asyncio
async def test_freeze_bounce_diagnostics_on_freeze_bridge_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(track_names=["Broken", "OK"])
    bridge = FakeBridge(freezing_dir=freezing, freeze_raises_for={0})
    _patch(monkeypatch, osc, bridge)

    result = await freeze_mod.bounce_tracks_via_freeze([0, 1], tmp_path / "out")
    # Track 0 failed, track 1 should still succeed.
    assert result["stems"][0]["copied"] is False
    assert "freeze call failed" in result["stems"][0]["error"]
    assert result["stems"][1]["copied"] is True
    assert any("freeze call failed" in d for d in result["diagnostics"])


# ---------------------------------------------------------------------------
# bounce_enabled_via_freeze respects mute + has_audio_output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_bounce_enabled_skips_muted_and_no_audio_tracks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(
        track_names=["A", "B (muted)", "C (no audio out)", "D"],
        muted={1}, no_audio_out={2},
    )
    bridge = FakeBridge(freezing_dir=freezing)
    _patch(monkeypatch, osc, bridge)

    result = await freeze_mod.bounce_enabled_via_freeze(tmp_path / "out")
    indices = sorted(s["source_track_index"] for s in result["stems"])
    assert indices == [0, 3]


# ---------------------------------------------------------------------------
# Unfreeze failure on cleanup is logged but doesn't propagate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_bounce_unfreeze_failure_surfaces_in_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    freezing = tmp_path / "project" / "Samples" / "Freezing"
    osc = FakeOSC(track_names=["A"])
    bridge = FakeBridge(freezing_dir=freezing)

    real_call = bridge.call

    async def call_with_unfreeze_raising(op: str, **kwargs: Any) -> Any:
        if op == "track.unfreeze":
            raise AbletonBridgeError("simulated unfreeze failure")
        return await real_call(op, **kwargs)

    bridge.call = call_with_unfreeze_raising  # type: ignore[assignment]
    _patch(monkeypatch, osc, bridge)

    result = await freeze_mod.bounce_tracks_via_freeze([0], tmp_path / "out")
    # Bounce still succeeds at the copy level.
    assert result["stems"][0]["copied"] is True
    # But the cleanup failure is in diagnostics.
    assert result["diagnostics"] is not None
    assert any("unfreeze failed" in d for d in result["diagnostics"])
