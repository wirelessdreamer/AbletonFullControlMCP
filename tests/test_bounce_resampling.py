"""Tests for ``ableton_mcp.bounce.resampling`` — the bounce paths that
hand-shake with Live to capture audio via the Resampling track.

The tests here cover the issue #7 fixes:

- ``_wait_until`` polling helper (happy path + timeout)
- ``_create_audio_track`` polls for ``num_tracks`` to increment instead of
  trusting a fixed sleep
- ``_set_input_routing`` / ``_arm`` verify via read-back
- ``_wait_for_clip_file_path`` polls bridge for the just-recorded clip's
  ``file_path`` rather than assuming 0.1 s is enough
- ``_cleanup_orphan_temp_tracks`` finds and removes leftover tracks from
  crashed previous runs
- ``bounce_song_via_resampling`` surfaces diagnostics when no clip
  materialized and survives a delete failure during cleanup

OSC + bridge are mocked. Real timing isn't exercised heavily — tests use
small timeouts so they stay fast.
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.bounce import resampling
from ableton_mcp.bridge_client import AbletonBridgeError


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeOSC:
    """In-memory OSC double for the addresses the bounce path touches.

    Each test composes the bits it needs; the constructor takes optional
    starting state but everything else is mutable on the instance.
    """

    def __init__(
        self,
        *,
        track_names: list[str] | None = None,
        delete_raises_for: set[int] | None = None,
        create_grows_after_n_polls: int = 0,
        routing_confirms: bool = True,
        arm_confirms: bool = True,
    ) -> None:
        self.track_names: list[str] = list(track_names or [])
        self.input_routing: dict[int, str] = {}
        self.arm: dict[int, int] = {}
        self.sent: list[tuple[str, tuple]] = []
        self.delete_raises_for = delete_raises_for or set()
        # When set > 0, num_tracks queries return the old count for the
        # first N polls after a create_audio_track send, then jump.
        self.create_grows_after_n_polls = create_grows_after_n_polls
        self._pending_create_polls = 0
        self._pending_new_track_name: str | None = None
        # When False, reading back input_routing_type returns a sentinel that
        # doesn't match what was written — simulates a Live build that
        # answers reads with a different format than writes accept.
        self.routing_confirms = routing_confirms
        self.arm_confirms = arm_confirms

    @property
    def track_count(self) -> int:
        return len(self.track_names)

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/num_tracks":
            if self._pending_create_polls > 0:
                self._pending_create_polls -= 1
                # Report pre-create count
                return (self.track_count - 1 if self._pending_new_track_name is not None
                        else self.track_count,)
            # If a deferred create is now visible, materialize it
            if self._pending_new_track_name is not None:
                self.track_names.append(self._pending_new_track_name)
                self._pending_new_track_name = None
            return (self.track_count,)
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/track/get/input_routing_type":
            ti = int(args[0])
            actual = self.input_routing.get(ti, "No Input")
            if not self.routing_confirms:
                return (ti, f"<unconfirmable:{actual}>")
            return (ti, actual)
        if addr == "/live/track/get/arm":
            ti = int(args[0])
            actual = self.arm.get(ti, 0)
            if not self.arm_confirms:
                return (ti, 1 - actual)  # always wrong
            return (ti, actual)
        if addr == "/live/track/get/mute":
            return (int(args[0]), 0)
        if addr == "/live/track/get/has_audio_output":
            return (int(args[0]), 1)
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")

    def send(self, addr: str, *args: Any) -> None:
        self.sent.append((addr, args))
        if addr == "/live/song/create_audio_track":
            new_name = "Audio"  # placeholder; set/name typically follows
            if self.create_grows_after_n_polls > 0:
                self._pending_new_track_name = new_name
                self._pending_create_polls = self.create_grows_after_n_polls
            else:
                self.track_names.append(new_name)
        elif addr == "/live/track/set/name":
            ti, name = int(args[0]), str(args[1])
            while len(self.track_names) <= ti:
                self.track_names.append("")
            self.track_names[ti] = name
        elif addr == "/live/track/set/input_routing_type":
            ti, type_name = int(args[0]), str(args[1])
            self.input_routing[ti] = type_name
        elif addr == "/live/track/set/arm":
            ti, val = int(args[0]), int(args[1])
            self.arm[ti] = val
        elif addr == "/live/song/delete_track":
            ti = int(args[0])
            if ti in self.delete_raises_for:
                raise RuntimeError(f"simulated delete failure for track {ti}")
            if 0 <= ti < len(self.track_names):
                del self.track_names[ti]
                # shift routing/arm keys
                self.input_routing = {
                    (k if k < ti else k - 1): v
                    for k, v in self.input_routing.items() if k != ti
                }
                self.arm = {
                    (k if k < ti else k - 1): v
                    for k, v in self.arm.items() if k != ti
                }


class FakeBridge:
    """Bridge double for ``clip.arrangement_clip_info`` responses.

    ``file_path_after_n_calls`` defers the first call's success: returns
    ``{}`` (no file_path) for the first N calls, then returns the configured
    path on the (N+1)th call. Use this to exercise the polling path in
    ``_wait_for_clip_file_path``.
    """

    def __init__(
        self,
        *,
        file_path: str | None = "/fake/path/recorded.wav",
        file_path_after_n_calls: int = 0,
        raise_for_calls: int = 0,
    ) -> None:
        self.file_path = file_path
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._defer_until = file_path_after_n_calls
        self._raise_remaining = raise_for_calls

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        if op != "clip.arrangement_clip_info":
            return {}
        if self._raise_remaining > 0:
            self._raise_remaining -= 1
            raise AbletonBridgeError("simulated bridge transient error")
        if self._defer_until > 0:
            self._defer_until -= 1
            return {"file_path": None}
        if self.file_path is None:
            return {"file_path": None}
        return {"file_path": self.file_path}


def _patch(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge) -> None:
    """Wire fakes into the resampling module."""
    async def _fake_get_client() -> FakeOSC:
        return osc

    def _fake_get_bridge() -> FakeBridge:
        return bridge

    monkeypatch.setattr(resampling, "get_client", _fake_get_client)
    monkeypatch.setattr(resampling, "get_bridge_client", _fake_get_bridge)


# ---------------------------------------------------------------------------
# _wait_until
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_until_returns_true_when_condition_met() -> None:
    """A check that returns True on first poll should short-circuit."""
    calls = 0

    async def check() -> bool:
        nonlocal calls
        calls += 1
        return True

    ok = await resampling._wait_until(check, timeout_sec=0.5, poll_interval_sec=0.01)
    assert ok is True
    assert calls == 1


@pytest.mark.asyncio
async def test_wait_until_returns_false_after_timeout() -> None:
    """When the condition never becomes true, returns False after timeout."""
    calls = 0

    async def check() -> bool:
        nonlocal calls
        calls += 1
        return False

    ok = await resampling._wait_until(check, timeout_sec=0.1, poll_interval_sec=0.02)
    assert ok is False
    assert calls >= 2  # at least the initial probe + one more


@pytest.mark.asyncio
async def test_wait_until_polls_until_condition_flips() -> None:
    """A check that flips to True after N calls should still succeed."""
    state = {"calls": 0}

    async def check() -> bool:
        state["calls"] += 1
        return state["calls"] >= 3

    ok = await resampling._wait_until(check, timeout_sec=1.0, poll_interval_sec=0.01)
    assert ok is True
    assert state["calls"] == 3


# ---------------------------------------------------------------------------
# _create_audio_track
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_audio_track_polls_until_count_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When num_tracks doesn't increment immediately, poll until it does."""
    osc = FakeOSC(track_names=["Existing"], create_grows_after_n_polls=2)
    _patch(monkeypatch, osc, FakeBridge())
    new_idx = await resampling._create_audio_track("My Bounce", verify_timeout_sec=1.0)
    assert new_idx == 1  # was 1 track, now 2
    assert osc.track_names[1] == "My Bounce"


@pytest.mark.asyncio
async def test_create_audio_track_times_out_with_bounce_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Live never adds the track, raise BounceError with a helpful message."""
    osc = FakeOSC(track_names=["Existing"])
    # Override send to ignore create commands (simulating a stuck Live).
    real_send = osc.send

    def deaf_send(addr: str, *args: Any) -> None:
        if addr == "/live/song/create_audio_track":
            osc.sent.append((addr, args))
            return  # don't actually create
        real_send(addr, *args)

    osc.send = deaf_send  # type: ignore[assignment]
    _patch(monkeypatch, osc, FakeBridge())
    with pytest.raises(resampling.BounceError) as excinfo:
        await resampling._create_audio_track("x", verify_timeout_sec=0.1)
    assert "timed out" in str(excinfo.value)


# ---------------------------------------------------------------------------
# _set_input_routing / _arm — verify with read-back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_input_routing_returns_true_on_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(track_names=["t0"])
    _patch(monkeypatch, osc, FakeBridge())
    ok = await resampling._set_input_routing(0, "Resampling", verify_timeout_sec=0.5)
    assert ok is True
    assert osc.input_routing[0] == "Resampling"


@pytest.mark.asyncio
async def test_set_input_routing_returns_false_on_no_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-back returns something different from what we wrote → False (not a raise)."""
    osc = FakeOSC(track_names=["t0"], routing_confirms=False)
    _patch(monkeypatch, osc, FakeBridge())
    ok = await resampling._set_input_routing(0, "Resampling", verify_timeout_sec=0.1)
    assert ok is False


@pytest.mark.asyncio
async def test_arm_returns_true_on_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC(track_names=["t0"])
    _patch(monkeypatch, osc, FakeBridge())
    ok = await resampling._arm(0, True, verify_timeout_sec=0.5)
    assert ok is True
    assert osc.arm[0] == 1


@pytest.mark.asyncio
async def test_arm_returns_false_on_no_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(track_names=["t0"], arm_confirms=False)
    _patch(monkeypatch, osc, FakeBridge())
    ok = await resampling._arm(0, True, verify_timeout_sec=0.1)
    assert ok is False


# ---------------------------------------------------------------------------
# _wait_for_clip_file_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_clip_file_path_returns_path_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC()
    bridge = FakeBridge(file_path="/tmp/x.wav")
    _patch(monkeypatch, osc, bridge)
    fp = await resampling._wait_for_clip_file_path(0, timeout_sec=0.5)
    assert fp == "/tmp/x.wav"


@pytest.mark.asyncio
async def test_wait_for_clip_file_path_polls_until_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the clip finalizes after a few polls, return the path."""
    osc = FakeOSC()
    bridge = FakeBridge(file_path="/tmp/y.wav", file_path_after_n_calls=3)
    _patch(monkeypatch, osc, bridge)
    fp = await resampling._wait_for_clip_file_path(
        0, timeout_sec=1.0, poll_interval_sec=0.02
    )
    assert fp == "/tmp/y.wav"
    assert len(bridge.calls) >= 4  # 3 misses + 1 hit


@pytest.mark.asyncio
async def test_wait_for_clip_file_path_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC()
    bridge = FakeBridge(file_path=None)
    _patch(monkeypatch, osc, bridge)
    fp = await resampling._wait_for_clip_file_path(
        0, timeout_sec=0.1, poll_interval_sec=0.02
    )
    assert fp is None


@pytest.mark.asyncio
async def test_wait_for_clip_file_path_survives_transient_bridge_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient AbletonBridgeError shouldn't abort the poll loop."""
    osc = FakeOSC()
    bridge = FakeBridge(file_path="/tmp/z.wav", raise_for_calls=2)
    _patch(monkeypatch, osc, bridge)
    fp = await resampling._wait_for_clip_file_path(
        0, timeout_sec=1.0, poll_interval_sec=0.02
    )
    assert fp == "/tmp/z.wav"


# ---------------------------------------------------------------------------
# _cleanup_orphan_temp_tracks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_orphan_temp_tracks_no_orphans_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(track_names=["MIDI 1", "Bass", "Drums"])
    _patch(monkeypatch, osc, FakeBridge())
    cleaned = await resampling._cleanup_orphan_temp_tracks()
    assert cleaned == []


@pytest.mark.asyncio
async def test_cleanup_orphan_temp_tracks_deletes_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two orphans should be cleaned up; reverse order keeps indices stable."""
    osc = FakeOSC(track_names=[
        "Vocals",
        "Master Bounce" + resampling.TEMP_TRACK_SUFFIX,
        "Drums",
        "Stem Bass" + resampling.TEMP_TRACK_SUFFIX,
    ])
    _patch(monkeypatch, osc, FakeBridge())
    cleaned = await resampling._cleanup_orphan_temp_tracks()
    assert len(cleaned) == 2
    # After cleanup, only the user tracks remain
    assert osc.track_names == ["Vocals", "Drums"]
    # The delete order in `sent` should be reverse-index (3 first, then 1)
    deletes = [args for addr, args in osc.sent if addr == "/live/song/delete_track"]
    assert deletes == [(3,), (1,)]


@pytest.mark.asyncio
async def test_cleanup_orphan_temp_tracks_continues_on_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delete that raises shouldn't abort cleanup of the remaining orphans."""
    osc = FakeOSC(
        track_names=[
            "Master Bounce" + resampling.TEMP_TRACK_SUFFIX,
            "Drums",
            "Stem Bass" + resampling.TEMP_TRACK_SUFFIX,
        ],
        delete_raises_for={2},
    )
    _patch(monkeypatch, osc, FakeBridge())
    cleaned = await resampling._cleanup_orphan_temp_tracks()
    # Only the one that successfully deleted is reported. The other was
    # attempted (and logged), so the function still made progress.
    assert len(cleaned) == 1
    # Track at index 0 should still have been deleted (came second in
    # reverse order, after the raise on index 2)
    assert "Master Bounce" not in osc.track_names[0] if osc.track_names else True


# ---------------------------------------------------------------------------
# bounce_song_via_resampling — end-to-end with mocked IO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_song_returns_diagnostics_when_clip_never_finalizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """The issue #7 happy-path fix: when no clip materializes, return a
    structured result with diagnostics rather than crashing.
    """
    osc = FakeOSC(track_names=["t0"])
    bridge = FakeBridge(file_path=None)  # never returns a path
    _patch(monkeypatch, osc, bridge)
    out = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05,
        settle_sec=0.02,
        clip_finalize_timeout_sec=0.1,
    )
    assert out["copied"] is False
    assert out["error"] == "no recorded clip found"
    # Diagnostics list should explain what went wrong.
    assert out["diagnostics"] is not None
    assert any("clip never reported" in d for d in out["diagnostics"])
    # The temp track should have been cleaned up.
    assert not any(n.endswith(resampling.TEMP_TRACK_SUFFIX) for n in osc.track_names)


@pytest.mark.asyncio
async def test_bounce_song_does_not_crash_on_cleanup_delete_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """The issue #7 crash fix: when the cleanup delete_track raises, the
    bounce returns a structured result with a diagnostic — never lets the
    exception propagate (which is how Live ended up crashing before).
    """
    osc = FakeOSC(track_names=["t0"])
    # Track will be at index 1 after creation; make delete on index 1 raise
    osc.delete_raises_for = {1}
    bridge = FakeBridge(file_path=None)
    _patch(monkeypatch, osc, bridge)
    out = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05,
        settle_sec=0.02,
        clip_finalize_timeout_sec=0.1,
    )
    # Should still return a result (no uncaught exception).
    assert out["copied"] is False
    assert out["diagnostics"] is not None
    # And the diagnostics should mention the cleanup failure.
    assert any("delete temp track" in d for d in out["diagnostics"])


@pytest.mark.asyncio
async def test_bounce_song_pre_cleanup_removes_orphan_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """Pre-cleanup should remove any leftover bounce-temp tracks before
    creating a new one. Verifies the orphan tracks gets deleted as the
    first action."""
    osc = FakeOSC(track_names=[
        "Master Bounce" + resampling.TEMP_TRACK_SUFFIX,  # orphan from prev run
        "Drums",
    ])
    bridge = FakeBridge(file_path=None)
    _patch(monkeypatch, osc, bridge)
    out = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05,
        settle_sec=0.02,
        clip_finalize_timeout_sec=0.1,
    )
    # Diagnostics should mention the orphan cleanup.
    assert out["diagnostics"] is not None
    assert any("orphan" in d.lower() for d in out["diagnostics"])
    # The first delete should target the orphan (index 0).
    deletes = [args for addr, args in osc.sent if addr == "/live/song/delete_track"]
    assert deletes[0] == (0,)


@pytest.mark.asyncio
async def test_bounce_song_records_arm_and_routing_confirmation_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """The result dict should include routing_confirmed / arm_confirmed flags
    so the caller can tell whether the read-back verifications succeeded.
    """
    osc = FakeOSC(track_names=["t0"])
    bridge = FakeBridge(file_path=None)
    _patch(monkeypatch, osc, bridge)
    out = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05,
        settle_sec=0.02,
        clip_finalize_timeout_sec=0.1,
    )
    assert out["routing_confirmed"] is True
    assert out["arm_confirmed"] is True


@pytest.mark.asyncio
async def test_bounce_song_diagnostics_flag_unconfirmed_routing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """When read-back didn't confirm routing AND no clip materialized,
    the diagnostics should specifically mention the routing issue."""
    osc = FakeOSC(track_names=["t0"], routing_confirms=False)
    bridge = FakeBridge(file_path=None)
    _patch(monkeypatch, osc, bridge)
    out = await resampling.bounce_song_via_resampling(
        tmp_path / "out.wav",
        duration_sec=0.05,
        settle_sec=0.02,
        clip_finalize_timeout_sec=0.1,
    )
    assert out["routing_confirmed"] is False
    assert out["diagnostics"] is not None
    assert any("routing" in d.lower() for d in out["diagnostics"])
