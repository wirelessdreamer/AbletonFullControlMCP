"""Tests for ``ableton_mcp.mix_apply`` — Layer 4.2 of the mix-aware
shaping stack.

The pure planner is testable against synthesized device snapshots (no
Live needed). The async wrapper is exercised with monkey-patched
service helpers so we don't need a real OSC connection.
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp import mix_apply
from ableton_mcp.mix_apply import (
    EQ8_FILTER_TYPE,
    DeviceStep,
    apply_step,
    filter_type_for_action,
    find_eq_eight,
    mix_apply_proposal,
    pick_free_band,
    plan_application,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proposal(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of action dicts in a proposal envelope shaped like the
    output of ``mix_propose.mix_propose_at_region``."""
    return {
        "intent": "cuts_through",
        "descriptor": {
            "name": "cuts_through",
            "band_low_hz": 2000.0, "band_high_hz": 5000.0,
            "sign": +1, "action_class": "cut_competitors",
            "description": "test",
        },
        "focal_track": 0, "focal_name": "Lead",
        "actions": actions,
        "region": {"start_beats": 0.0, "end_beats": 8.0,
                   "duration_sec": 4.0, "tempo": 120.0},
    }


def _eq_action(
    track_index: int = 1, kind: str = "eq_cut",
    freq_hz: float = 3000.0, gain_db: float = -3.0, q: float = 1.5,
) -> dict[str, Any]:
    return {
        "track_index": track_index,
        "kind": kind,
        "device_hint": "EQ Eight",
        "freq_hz": freq_hz,
        "q": q,
        "gain_db": gain_db,
        "rationale": "test action",
    }


def _device(name: str = "EQ Eight", class_name: str = "Eq8",
            device_index: int = 0) -> dict[str, Any]:
    return {
        "device_index": device_index,
        "name": name,
        "type": "audio_effect",
        "class_name": class_name,
    }


# ---------------------------------------------------------------------------
# filter_type_for_action
# ---------------------------------------------------------------------------


def test_filter_type_for_action_eq_cut_is_bell() -> None:
    assert filter_type_for_action("eq_cut") == EQ8_FILTER_TYPE["bell"]


def test_filter_type_for_action_eq_boost_is_bell() -> None:
    assert filter_type_for_action("eq_boost") == EQ8_FILTER_TYPE["bell"]


def test_filter_type_for_action_high_pass_is_hp12() -> None:
    """High-pass should default to the gentler HP12 slope, not HP48."""
    assert filter_type_for_action("high_pass") == EQ8_FILTER_TYPE["hp12"]


def test_filter_type_for_action_high_shelf() -> None:
    assert filter_type_for_action("high_shelf") == EQ8_FILTER_TYPE["high_shelf"]


def test_filter_type_for_action_low_shelf() -> None:
    assert filter_type_for_action("low_shelf") == EQ8_FILTER_TYPE["low_shelf"]


def test_filter_type_for_action_unknown_returns_none() -> None:
    """An unsupported action kind returns None so the planner can skip it."""
    assert filter_type_for_action("de_ess") is None
    assert filter_type_for_action("compress_attack") is None


# ---------------------------------------------------------------------------
# find_eq_eight
# ---------------------------------------------------------------------------


def test_find_eq_eight_returns_first_match() -> None:
    devices = [
        _device("Compressor", "Compressor2", device_index=0),
        _device("EQ Eight", "Eq8", device_index=1),
        _device("Reverb", "Reverb", device_index=2),
        _device("EQ Eight", "Eq8", device_index=3),
    ]
    assert find_eq_eight(devices) == 1


def test_find_eq_eight_returns_none_when_absent() -> None:
    devices = [
        _device("Compressor", "Compressor2", device_index=0),
        _device("Reverb", "Reverb", device_index=1),
    ]
    assert find_eq_eight(devices) is None


def test_find_eq_eight_handles_empty_list() -> None:
    assert find_eq_eight([]) is None


# ---------------------------------------------------------------------------
# pick_free_band
# ---------------------------------------------------------------------------


def test_pick_free_band_picks_first_off_band() -> None:
    """Should pick the first band whose ``Filter On`` flag is 0."""
    # Bands 1 and 2 are on, band 3 is off, band 4 is on.
    band_states = [
        {"index": 1, "on": True}, {"index": 2, "on": True},
        {"index": 3, "on": False}, {"index": 4, "on": True},
        {"index": 5, "on": True}, {"index": 6, "on": True},
        {"index": 7, "on": True}, {"index": 8, "on": True},
    ]
    assert pick_free_band(band_states) == 3


def test_pick_free_band_returns_first_when_none_off() -> None:
    """If every band is on, fall back to band 1 — the user can refine."""
    band_states = [
        {"index": i, "on": True} for i in range(1, 9)
    ]
    assert pick_free_band(band_states) == 1


def test_pick_free_band_returns_first_when_states_unknown() -> None:
    """Empty band states (we couldn't read them) → default to band 1."""
    assert pick_free_band([]) == 1


# ---------------------------------------------------------------------------
# plan_application — pure data
# ---------------------------------------------------------------------------


def test_plan_application_eq_present_emits_set_steps() -> None:
    """EQ Eight already on the track → no insert step, just set_band."""
    proposal = _proposal([_eq_action(track_index=1, kind="eq_cut")])
    devices_by_track = {1: [_device(device_index=2)]}
    steps, skipped = plan_application(
        proposal, devices_by_track, band_states={(1, 2): []},
    )
    assert skipped == []
    assert len(steps) == 1
    step = steps[0]
    assert step.op == "set_band"
    assert step.track_index == 1
    assert step.device_index == 2
    assert step.params["filter_type"] == EQ8_FILTER_TYPE["bell"]
    assert step.params["frequency"] == 3000.0
    assert step.params["gain"] == -3.0


def test_plan_application_no_eq_emits_insert_then_set() -> None:
    """No EQ Eight on the track → planner emits insert first, then a
    placeholder set_band that references a sentinel device_index of -1
    (the apply layer resolves it after the insert returns the real index)."""
    proposal = _proposal([_eq_action(track_index=1, kind="eq_cut")])
    devices_by_track = {1: []}  # nothing on the track
    steps, skipped = plan_application(proposal, devices_by_track, band_states={})
    assert skipped == []
    assert len(steps) == 2
    assert steps[0].op == "insert_eq_eight"
    assert steps[0].track_index == 1
    assert steps[1].op == "set_band"
    assert steps[1].track_index == 1


def test_plan_application_skips_unknown_action_kinds() -> None:
    """``de_ess`` / ``compress_attack`` aren't applied in v1 — they should
    land in the ``skipped`` list with a reason, NOT in the steps."""
    proposal = _proposal([
        _eq_action(kind="eq_cut"),
        {"track_index": 1, "kind": "de_ess",
         "device_hint": "X", "rationale": "test"},
        {"track_index": 1, "kind": "compress_attack",
         "device_hint": "X", "rationale": "test"},
    ])
    devices_by_track = {1: [_device(device_index=0)]}
    steps, skipped = plan_application(
        proposal, devices_by_track, band_states={(1, 0): []},
    )
    set_steps = [s for s in steps if s.op == "set_band"]
    assert len(set_steps) == 1
    assert {s["kind"] for s in skipped} == {"de_ess", "compress_attack"}
    assert all("reason" in s for s in skipped)


def test_plan_application_high_pass_uses_hp12_filter_type() -> None:
    proposal = _proposal([_eq_action(kind="high_pass", freq_hz=120.0)])
    devices_by_track = {1: [_device(device_index=0)]}
    steps, _skipped = plan_application(
        proposal, devices_by_track, band_states={(1, 0): []},
    )
    assert steps[0].params["filter_type"] == EQ8_FILTER_TYPE["hp12"]
    assert steps[0].params["frequency"] == 120.0


def test_plan_application_picks_free_band_when_states_known() -> None:
    """If band-state info is supplied, the planner picks the next free
    band instead of always landing on band 1."""
    band_states_list = [
        {"index": 1, "on": True}, {"index": 2, "on": True},
        {"index": 3, "on": False}, {"index": 4, "on": True},
        {"index": 5, "on": True}, {"index": 6, "on": True},
        {"index": 7, "on": True}, {"index": 8, "on": True},
    ]
    proposal = _proposal([_eq_action(kind="eq_cut")])
    devices_by_track = {1: [_device(device_index=0)]}
    steps, _ = plan_application(
        proposal, devices_by_track, band_states={(1, 0): band_states_list},
    )
    assert steps[0].params["band_index"] == 3


def test_plan_application_two_tracks_two_steps() -> None:
    """Each per-track action produces its own step."""
    proposal = _proposal([
        _eq_action(track_index=1),
        _eq_action(track_index=2),
    ])
    devices_by_track = {
        1: [_device(device_index=0)],
        2: [_device(device_index=0)],
    }
    steps, _ = plan_application(
        proposal, devices_by_track,
        band_states={(1, 0): [], (2, 0): []},
    )
    set_steps = [s for s in steps if s.op == "set_band"]
    assert {s.track_index for s in set_steps} == {1, 2}


# ---------------------------------------------------------------------------
# DeviceStep serialization
# ---------------------------------------------------------------------------


def test_device_step_to_dict() -> None:
    s = DeviceStep(
        op="set_band", track_index=1, device_index=0,
        params={"filter_type": 3, "frequency": 3000.0, "gain": -3.0},
        rationale="why",
    )
    d = s.to_dict()
    assert d["op"] == "set_band"
    assert d["params"]["frequency"] == 3000.0


# ---------------------------------------------------------------------------
# apply_step — set_band executor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_step_set_band_sends_expected_osc_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``apply_step`` of a set_band step should issue OSC parameter
    sets for filter type, frequency, gain, resonance, AND turn the
    band on."""
    calls: list[tuple[int, int, str, Any]] = []

    async def fake_set_param(track, device, name, value):
        calls.append((track, device, name, value))
        return {"ok": True}

    monkeypatch.setattr(mix_apply, "_set_device_param", fake_set_param)

    step = DeviceStep(
        op="set_band", track_index=1, device_index=2,
        params={
            "filter_type": EQ8_FILTER_TYPE["bell"],
            "frequency": 3000.0, "gain": -3.0, "q": 1.5,
            "band_index": 4,
        },
    )
    await apply_step(step)

    names = {c[2] for c in calls}
    # Per-band parameter names use the "<N> Filter ... A" template.
    assert "4 Filter Type A" in names
    assert "4 Frequency A" in names
    assert "4 Gain A" in names
    assert "4 Resonance A" in names
    assert "4 Filter On A" in names


@pytest.mark.asyncio
async def test_apply_step_insert_eq_eight_calls_browser_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    async def fake_insert_eq(track_index: int) -> int:
        calls.append(track_index)
        return 5

    monkeypatch.setattr(mix_apply, "_insert_eq_eight", fake_insert_eq)
    step = DeviceStep(op="insert_eq_eight", track_index=2, device_index=None)
    result = await apply_step(step)
    assert calls == [2]
    assert result["new_device_index"] == 5


# ---------------------------------------------------------------------------
# mix_apply_proposal — async wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_apply_proposal_dry_run_makes_no_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In dry_run mode, the wrapper should plan but emit zero OSC writes."""
    writes: list[Any] = []

    async def fake_read_devices(track):
        return [_device(device_index=0)]

    async def fake_read_band_states(track, device):
        return []

    async def fake_set_param(*args):
        writes.append(args)

    async def fake_insert(*args):
        writes.append(("insert",) + args)
        return 0

    monkeypatch.setattr(mix_apply, "_read_track_devices", fake_read_devices)
    monkeypatch.setattr(mix_apply, "_read_eq_band_states", fake_read_band_states)
    monkeypatch.setattr(mix_apply, "_set_device_param", fake_set_param)
    monkeypatch.setattr(mix_apply, "_insert_eq_eight", fake_insert)

    proposal = _proposal([_eq_action(track_index=1, kind="eq_cut")])
    result = await mix_apply_proposal(proposal, dry_run=True)

    assert result["dry_run"] is True
    assert writes == []
    # Plan is still produced.
    plan = result["plan"]
    assert len(plan) == 1
    assert plan[0]["op"] == "set_band"


@pytest.mark.asyncio
async def test_mix_apply_proposal_executes_when_not_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[Any] = []

    async def fake_read_devices(track):
        return [_device(device_index=0)]

    async def fake_read_band_states(track, device):
        return []

    async def fake_set_param(track, device, name, value):
        writes.append((track, device, name, value))
        return {"ok": True}

    async def fake_insert(track):
        return 0

    monkeypatch.setattr(mix_apply, "_read_track_devices", fake_read_devices)
    monkeypatch.setattr(mix_apply, "_read_eq_band_states", fake_read_band_states)
    monkeypatch.setattr(mix_apply, "_set_device_param", fake_set_param)
    monkeypatch.setattr(mix_apply, "_insert_eq_eight", fake_insert)

    proposal = _proposal([_eq_action(track_index=1, kind="eq_boost",
                                     freq_hz=8000.0, gain_db=+2.5)])
    result = await mix_apply_proposal(proposal, dry_run=False)

    assert result["dry_run"] is False
    # Several writes happened (one per band parameter we set).
    assert len(writes) > 0
    names = {w[2] for w in writes}
    assert any("Frequency" in n for n in names)


@pytest.mark.asyncio
async def test_mix_apply_proposal_inserts_eq_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the track has no EQ Eight, the apply layer should insert one
    before setting band parameters."""
    inserted: list[int] = []
    set_calls: list[Any] = []

    async def fake_read_devices(track):
        return []  # nothing on the track

    async def fake_read_band_states(track, device):
        return []

    async def fake_insert(track):
        inserted.append(track)
        return 7  # new device index

    async def fake_set_param(track, device, name, value):
        set_calls.append((track, device, name, value))

    monkeypatch.setattr(mix_apply, "_read_track_devices", fake_read_devices)
    monkeypatch.setattr(mix_apply, "_read_eq_band_states", fake_read_band_states)
    monkeypatch.setattr(mix_apply, "_insert_eq_eight", fake_insert)
    monkeypatch.setattr(mix_apply, "_set_device_param", fake_set_param)

    proposal = _proposal([_eq_action(track_index=4, kind="eq_cut")])
    result = await mix_apply_proposal(proposal, dry_run=False)

    assert inserted == [4]
    # All set_param calls land on the NEW device_index (7), not the
    # sentinel (-1) the planner used.
    assert all(c[1] == 7 for c in set_calls)
    assert result["dry_run"] is False


@pytest.mark.asyncio
async def test_mix_apply_proposal_reports_skipped_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read_devices(track):
        return [_device(device_index=0)]

    async def fake_read_band_states(track, device):
        return []

    async def fake_set_param(*args):
        return None

    async def fake_insert(track):
        return 0

    monkeypatch.setattr(mix_apply, "_read_track_devices", fake_read_devices)
    monkeypatch.setattr(mix_apply, "_read_eq_band_states", fake_read_band_states)
    monkeypatch.setattr(mix_apply, "_set_device_param", fake_set_param)
    monkeypatch.setattr(mix_apply, "_insert_eq_eight", fake_insert)

    proposal = _proposal([
        _eq_action(kind="eq_cut"),
        {"track_index": 1, "kind": "de_ess",
         "device_hint": "X", "rationale": "test"},
    ])
    result = await mix_apply_proposal(proposal, dry_run=True)
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["kind"] == "de_ess"
