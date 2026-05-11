"""Mix proposal applier — Layer 4.2 of the mix-aware shaping stack.

Takes a proposal from :mod:`mix_propose` (L4.1) and applies it to Live:

1. For each per-track action, locate (or insert) an EQ Eight device.
2. Pick a free band on that EQ Eight.
3. Set the band's filter type, frequency, gain, and Q.

The module is split into:

- **Service helpers** (``_read_track_devices``, ``_read_eq_band_states``,
  ``_insert_eq_eight``, ``_set_device_param``) — the actual OSC + bridge
  calls. Module-level so tests can monkeypatch.

- **Pure planner** (``plan_application``) — given a proposal and
  snapshots of each track's devices + band states, returns a list of
  concrete ``DeviceStep``s. No I/O. Trivial to unit-test.

- **Step executor** (``apply_step``) — runs one step against Live.

- **Top-level wrapper** (``mix_apply_proposal``) — chains snapshot →
  plan → execute (or dry-run).

``dry_run=True`` returns the plan without making any state changes —
same pattern as the dry-run mode shipped in PR #15.

Filter-type mapping (EQ Eight enum)::

    eq_cut, eq_boost  -> Bell           (3)
    high_pass         -> HP12           (1)   — gentler than HP48
    high_shelf        -> HighShelf      (5)
    low_shelf         -> LowShelf       (2)

V1 only handles those five action kinds. ``de_ess`` and
``compress_attack`` from L4.1 are listed in the ``skipped`` output with
a reason rather than acted on — they'll get dedicated apply paths
later.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

log = logging.getLogger(__name__)


# EQ Eight ``Filter Type`` parameter enum. Indices match the values
# Live's ``Eq8`` device exposes via LOM. See device_schemas/audio_effects.py.
EQ8_FILTER_TYPE: dict[str, int] = {
    "hp48": 0,
    "hp12": 1,
    "low_shelf": 2,
    "bell": 3,
    "notch": 4,
    "high_shelf": 5,
    "lp12": 6,
    "lp48": 7,
}


# Map our action-kind vocabulary (from mix_propose) onto EQ Eight filter
# types. Returns None for action kinds we don't know how to map -- the
# planner skips those with a reason rather than guessing.
_ACTION_TO_FILTER_TYPE: dict[str, int] = {
    "eq_cut": EQ8_FILTER_TYPE["bell"],
    "eq_boost": EQ8_FILTER_TYPE["bell"],
    "high_pass": EQ8_FILTER_TYPE["hp12"],
    "high_shelf": EQ8_FILTER_TYPE["high_shelf"],
    "low_shelf": EQ8_FILTER_TYPE["low_shelf"],
}


def filter_type_for_action(action_kind: str) -> int | None:
    """Look up the EQ Eight filter-type enum for an action kind.
    Returns None for unsupported kinds so the planner can ``skip``."""
    return _ACTION_TO_FILTER_TYPE.get(action_kind)


def find_eq_eight(devices: Sequence[dict[str, Any]]) -> int | None:
    """Return the ``device_index`` of the first EQ Eight on a track,
    or None if the track has none."""
    for d in devices:
        if d.get("class_name") == "Eq8":
            return int(d["device_index"])
    return None


def pick_free_band(band_states: Sequence[dict[str, Any]]) -> int:
    """Pick a band (1-8) to use on the EQ Eight.

    Strategy: prefer a band that's currently OFF (no user changes to
    overwrite). If we don't know which bands are off, or every band is
    on, fall back to band 1 — the user can fix it.

    ``band_states`` entries have ``{"index": 1-8, "on": bool}``."""
    for state in band_states:
        if not state.get("on"):
            return int(state.get("index", 1))
    return 1


@dataclass(frozen=True)
class DeviceStep:
    """One concrete step in applying a proposal."""

    op: str  # "set_band", "insert_eq_eight"
    track_index: int
    device_index: int | None
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pure planner
# ---------------------------------------------------------------------------


# Sentinel device_index used by the planner when an EQ Eight will be
# inserted just before the set_band step. The async wrapper resolves
# this to the real index returned by ``_insert_eq_eight``.
DEVICE_INDEX_PENDING_INSERT: int = -1


def plan_application(
    proposal: dict[str, Any],
    devices_by_track: dict[int, list[dict[str, Any]]],
    *,
    band_states: dict[tuple[int, int], list[dict[str, Any]]] | None = None,
) -> tuple[list[DeviceStep], list[dict[str, Any]]]:
    """Translate a proposal into concrete ``DeviceStep``s. Pure function.

    Args:
        proposal: the dict from ``mix_propose.propose_actions`` (or
            ``mix_propose_at_region``).
        devices_by_track: for each track referenced by an action, the
            list of devices currently on that track. Used to decide
            whether to insert an EQ Eight or reuse an existing one.
        band_states: optional ``{(track, device): [band_state, ...]}``
            map. Used to pick a free band on an existing EQ Eight.
            Missing entries are treated as "I don't know" — the planner
            falls back to band 1.

    Returns:
        ``(steps, skipped)`` where:
        - ``steps`` is the in-order list of operations the apply layer
          will execute.
        - ``skipped`` is a list of action dicts that the planner
          couldn't handle, each with an added ``reason`` string.
    """
    if band_states is None:
        band_states = {}

    steps: list[DeviceStep] = []
    skipped: list[dict[str, Any]] = []

    # Track which tracks we've already emitted an insert for, so we don't
    # double-insert when a proposal has multiple EQ actions per track.
    inserted_into: set[int] = set()

    for action in proposal.get("actions", []):
        kind = action.get("kind")
        track = action.get("track_index")
        ft = filter_type_for_action(kind)
        if ft is None:
            skipped.append({
                **action,
                "reason": f"action kind {kind!r} has no apply handler in v1",
            })
            continue
        if track is None:
            skipped.append({
                **action,
                "reason": "action has no track_index",
            })
            continue

        # Find or schedule-insert an EQ Eight.
        track_devices = devices_by_track.get(track, [])
        eq_index = find_eq_eight(track_devices)
        if eq_index is None and track not in inserted_into:
            steps.append(DeviceStep(
                op="insert_eq_eight",
                track_index=track,
                device_index=None,
                rationale=f"no EQ Eight on track {track}; inserting one",
            ))
            inserted_into.add(track)
            eq_index = DEVICE_INDEX_PENDING_INSERT
        elif eq_index is None:
            # Insert already scheduled earlier in the same plan.
            eq_index = DEVICE_INDEX_PENDING_INSERT

        # Pick the band to use.
        states = band_states.get((track, eq_index), []) if eq_index >= 0 else []
        band_index = pick_free_band(states)

        steps.append(DeviceStep(
            op="set_band",
            track_index=track,
            device_index=eq_index,
            params={
                "band_index": band_index,
                "filter_type": ft,
                "frequency": float(action.get("freq_hz") or 1000.0),
                "gain": float(action.get("gain_db") or 0.0),
                "q": float(action.get("q") or 1.0),
            },
            rationale=action.get("rationale", ""),
        ))

    return steps, skipped


# ---------------------------------------------------------------------------
# Service helpers (real OSC calls)
# ---------------------------------------------------------------------------


async def _read_track_devices(track_index: int) -> list[dict[str, Any]]:
    """Return the list of devices on a track via OSC.

    Each dict has ``device_index``, ``name``, ``type``, ``class_name``.
    Mirrors what :func:`ableton_mcp.tools.devices.device_list` returns
    (without the MCP wrapper)."""
    from .tools.devices import device_list as _device_list
    result = await _device_list(track_index)
    if isinstance(result, dict):
        return list(result.get("devices", []))
    if isinstance(result, list):
        return list(result)
    return []


async def _read_eq_band_states(
    track_index: int, device_index: int,
) -> list[dict[str, Any]]:
    """Read the on/off state of each EQ Eight band.

    Returns a list of ``{"index": 1-8, "on": bool}`` dicts. On read
    failure, returns ``[]`` (the planner falls back to band 1)."""
    from .tools.devices import device_get_parameter_string
    states: list[dict[str, Any]] = []
    for band_n in range(1, 9):
        param_name = f"{band_n} Filter On A"
        try:
            res = await device_get_parameter_string(
                track_index, device_index, param_name,
            )
            # Normalise to bool. Live exposes 0/1 or string "0"/"1".
            value = res.get("value") if isinstance(res, dict) else res
            on = bool(int(float(value))) if value not in (None, "") else False
        except Exception:
            on = False
        states.append({"index": band_n, "on": on})
    return states


async def _insert_eq_eight(track_index: int) -> int:
    """Insert an EQ Eight device on the track. Returns the new
    ``device_index``."""
    from .tools.browser import browser_load_device
    from .tools.devices import device_list as _device_list

    before = await _device_list(track_index)
    before_list = (
        before.get("devices", []) if isinstance(before, dict)
        else before if isinstance(before, list) else []
    )

    await browser_load_device(
        uri_or_path="audio_effects/EQ Eight",
        track_index=track_index,
    )

    after = await _device_list(track_index)
    after_list = (
        after.get("devices", []) if isinstance(after, dict)
        else after if isinstance(after, list) else []
    )

    # The new device is the first Eq8 not in ``before``.
    before_indices = {d.get("device_index") for d in before_list}
    for d in after_list:
        if (
            d.get("class_name") == "Eq8"
            and d.get("device_index") not in before_indices
        ):
            return int(d["device_index"])
    # Fallback: last device on the track.
    if after_list:
        return int(after_list[-1].get("device_index", 0))
    return 0


async def _set_device_param(
    track_index: int, device_index: int, name: str, value: Any,
) -> Any:
    """Set one parameter on a device by name. Thin wrapper around
    ``device_set_parameter_by_name`` so tests can monkeypatch."""
    from .tools.devices import device_set_parameter_by_name
    return await device_set_parameter_by_name(
        track_index, device_index, name, value,
    )


# ---------------------------------------------------------------------------
# Step executor
# ---------------------------------------------------------------------------


async def apply_step(step: DeviceStep) -> dict[str, Any]:
    """Execute one ``DeviceStep`` against Live.

    Returns a dict summarizing what was done; the wrapper accumulates
    these for the final result.
    """
    if step.op == "insert_eq_eight":
        new_index = await _insert_eq_eight(step.track_index)
        return {"op": "insert_eq_eight", "track_index": step.track_index,
                "new_device_index": new_index}

    if step.op == "set_band":
        band = int(step.params["band_index"])
        track = step.track_index
        device = step.device_index
        if device is None:
            raise ValueError("set_band step missing device_index")

        param_writes: list[dict[str, Any]] = []
        # Set filter type FIRST so frequency/gain/Q land on the right curve.
        for param_name, value in (
            (f"{band} Filter Type A", int(step.params["filter_type"])),
            (f"{band} Frequency A", float(step.params["frequency"])),
            (f"{band} Gain A", float(step.params["gain"])),
            (f"{band} Resonance A", float(step.params["q"])),
            (f"{band} Filter On A", 1),
        ):
            res = await _set_device_param(track, device, param_name, value)
            param_writes.append({"name": param_name, "value": value, "result": res})

        return {
            "op": "set_band", "track_index": track,
            "device_index": device, "band_index": band,
            "writes": param_writes,
        }

    raise ValueError(f"unknown DeviceStep op: {step.op}")


# ---------------------------------------------------------------------------
# Top-level wrapper
# ---------------------------------------------------------------------------


async def mix_apply_proposal(
    proposal: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a mix proposal to Live (or plan it, with ``dry_run=True``).

    Args:
        proposal: the dict from ``mix_propose.mix_propose_at_region`` or
            ``mix_propose.propose_actions``.
        dry_run: when True, gather snapshots and emit the plan but do
            NOT execute any OSC writes. Useful for showing the user
            exactly what will change before committing.

    Returns:
        {
            "dry_run": bool,
            "intent": str,
            "plan": list[DeviceStep dict],    # the steps
            "skipped": list[action dict],     # actions we couldn't handle
            "results": list[result dict],     # one per executed step
                                              # (empty when dry_run)
        }
    """
    actions = proposal.get("actions", [])
    tracks_involved = sorted({
        int(a["track_index"]) for a in actions
        if a.get("track_index") is not None
    })

    # 1. Snapshot devices on every involved track.
    devices_by_track: dict[int, list[dict[str, Any]]] = {}
    band_states: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for track in tracks_involved:
        devices = await _read_track_devices(track)
        devices_by_track[track] = devices
        eq_idx = find_eq_eight(devices)
        if eq_idx is not None:
            band_states[(track, eq_idx)] = await _read_eq_band_states(
                track, eq_idx,
            )

    # 2. Plan.
    steps, skipped = plan_application(
        proposal, devices_by_track, band_states=band_states,
    )

    # 3. Execute (unless dry-run).
    results: list[dict[str, Any]] = []
    if not dry_run:
        # Walk the steps; resolve the PENDING_INSERT sentinel after each
        # successful insert.
        pending_index_by_track: dict[int, int] = {}
        for step in steps:
            # Resolve sentinel.
            if step.device_index == DEVICE_INDEX_PENDING_INSERT:
                resolved_idx = pending_index_by_track.get(step.track_index)
                if resolved_idx is None:
                    raise RuntimeError(
                        f"set_band step for track {step.track_index} "
                        "references PENDING_INSERT but no insert ran first"
                    )
                step = DeviceStep(
                    op=step.op, track_index=step.track_index,
                    device_index=resolved_idx, params=step.params,
                    rationale=step.rationale,
                )
            result = await apply_step(step)
            results.append(result)
            if step.op == "insert_eq_eight":
                pending_index_by_track[step.track_index] = (
                    result["new_device_index"]
                )

    return {
        "dry_run": dry_run,
        "intent": proposal.get("intent"),
        "plan": [s.to_dict() for s in steps],
        "skipped": skipped,
        "results": results,
    }
