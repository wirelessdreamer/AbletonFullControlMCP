"""Tests for the curated sound-design rule set + applier + introspect.

Covers:

- rule schema integrity (param names exist on the canonical schemas, weights
  in [0,1], directions ±1, every required descriptor is covered for every
  catalogued device)
- ``apply_descriptor`` against a fake-OSC fixture: pushes the right params
  for "brighter" on Drift
- ``apply_descriptor`` cancels opposing descriptors
- ``apply_descriptor`` reports unsupported_device for unknown class_name
- ``explain_descriptor`` returns the recorded note text
- ``summarise_track_sound`` builds a sensible musician summary
- ``register(mcp)`` registers ≥ 6 tools on a FastMCP instance
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import pytest

from ableton_mcp.device_schemas import lookup_schema
from ableton_mcp.sound_design import (
    DEVICE_RULES,
    ParamRule,
    REQUIRED_DESCRIPTORS,
    apply_descriptor,
    coverage_table,
    explain_descriptor,
    get_descriptor_rules,
    list_descriptors_for_device,
    normalize_descriptor,
    supported_classes,
    supported_descriptors_for,
)


# ---------------------------------------------------------------------------
# Schema integrity
# ---------------------------------------------------------------------------


def test_every_supported_device_has_required_descriptors() -> None:
    """Every device in DEVICE_RULES must define a rule list (possibly empty,
    by intent for descriptors that don't apply) for every required descriptor."""
    for cls, rules in DEVICE_RULES.items():
        for desc in REQUIRED_DESCRIPTORS:
            assert desc in rules, f"{cls!r} missing key for required descriptor {desc!r}"


def test_each_device_has_meaningful_descriptor_coverage() -> None:
    """Coverage threshold tiered by device kind.

    Instruments span the full musical concept space (envelope, harmonic, etc.)
    so they need broad coverage. FX/utility devices like Cabinet are honestly
    simpler — a mic-simulator has no envelope or harmonic concept, and the
    rules document that with intentional empty entries. We accept lower
    coverage for those rather than fake rules.
    """
    fx_only = {"Cabinet"}  # devices with intentionally limited descriptor space
    for cls, rules in DEVICE_RULES.items():
        non_empty = sum(1 for desc in REQUIRED_DESCRIPTORS if rules.get(desc))
        threshold = 5 if cls in fx_only else 9
        assert non_empty >= threshold, (
            f"{cls!r}: only {non_empty}/{len(REQUIRED_DESCRIPTORS)} required "
            f"descriptors have rules — below threshold of {threshold}"
        )


def test_rule_param_names_match_schema_when_schema_known() -> None:
    """Every param_name in a rule must exist on the device's canonical schema."""
    failures: list[str] = []
    for cls in supported_classes():
        schema = lookup_schema(cls)
        if schema is None:
            failures.append(f"{cls!r} has rules but no schema in device_schemas")
            continue
        schema_names = {p.name.strip().lower() for p in schema.parameters}
        for desc, rules in DEVICE_RULES[cls].items():
            for r in rules:
                if r.param_name.strip().lower() not in schema_names:
                    failures.append(
                        f"{cls!r}/{desc}: param {r.param_name!r} not on schema "
                        f"(have {sorted(schema_names)[:10]}...)"
                    )
    assert not failures, "\n".join(failures)


def test_rule_directions_and_weights_are_well_formed() -> None:
    for cls, rules in DEVICE_RULES.items():
        for desc, rule_list in rules.items():
            for r in rule_list:
                assert r.direction in (-1, +1), f"{cls}/{desc}: bad direction {r.direction}"
                assert 0.0 <= r.weight <= 1.0, f"{cls}/{desc}: bad weight {r.weight}"


def test_devices_covered_minimum_set() -> None:
    """The catalogue must cover the 11 devices the spec requires."""
    required = {
        "Drift",
        "Operator",
        "InstrumentVector",  # Wavetable
        "Tension",
        "AnalogDevice",
        "Compressor2",
        "Reverb",
        "AutoFilter",
        "Saturator",
        "Amp",
        "Cabinet",
    }
    missing = required - set(DEVICE_RULES.keys())
    assert not missing, f"missing required devices: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def test_normalize_descriptor_resolves_aliases() -> None:
    assert normalize_descriptor("brighter") == "bright"
    assert normalize_descriptor("BRIGHTER") == "bright"
    assert normalize_descriptor("punchier") == "punchy"
    assert normalize_descriptor("snappy") == "plucky"
    assert normalize_descriptor("dark") == "dark"
    assert normalize_descriptor("") == ""


def test_get_descriptor_rules_returns_list() -> None:
    rules = get_descriptor_rules("Drift", "bright")
    assert rules, "Drift+bright should have rules"
    assert all(isinstance(r, ParamRule) for r in rules)


def test_get_descriptor_rules_unknown_returns_empty_list() -> None:
    assert get_descriptor_rules("Nonexistent", "bright") == []
    # Unknown descriptor on known device:
    assert get_descriptor_rules("Drift", "spaghetti") == []


def test_supported_descriptors_for_lists_only_non_empty() -> None:
    descs = supported_descriptors_for("Drift")
    # Drift covers all required descriptors.
    for required in ("bright", "dark", "warm", "punchy"):
        assert required in descs


# ---------------------------------------------------------------------------
# explain_descriptor
# ---------------------------------------------------------------------------


def test_explain_descriptor_returns_note_text() -> None:
    """explain_descriptor must return the literal note from the rule."""
    out = explain_descriptor("Drift", "bright")
    assert out["status"] == "ok"
    assert out["class_name"] == "Drift"
    assert out["descriptor"] == "bright"
    rules = out["rules"]
    assert any(
        r["param_name"] == "Filter Frequency"
        and r["direction"] == +1
        and "cutoff" in r["note"].lower()
        for r in rules
    ), f"expected Filter Frequency+1 rule with 'cutoff' in note, got {rules}"


def test_explain_descriptor_unknown_device() -> None:
    out = explain_descriptor("ThisIsNotARealDevice", "bright")
    assert out["status"] == "unsupported_device"
    assert "ThisIsNotARealDevice" in out["class_name"]


def test_explain_descriptor_unknown_descriptor() -> None:
    out = explain_descriptor("Drift", "spaghetti")
    assert out["status"] == "unknown_descriptor"
    assert out["descriptor"] == "spaghetti"
    assert "supported_descriptors" in out


# ---------------------------------------------------------------------------
# list_descriptors_for_device
# ---------------------------------------------------------------------------


def test_list_descriptors_for_device_drift() -> None:
    out = list_descriptors_for_device("Drift")
    assert out["status"] == "ok"
    assert out["class_name"] == "Drift"
    assert out["device_display_name"] == "Drift"
    assert "bright" in out["descriptors"]


def test_list_descriptors_for_device_unsupported() -> None:
    out = list_descriptors_for_device("RandomVST")
    assert out["status"] == "unsupported_device"


def test_coverage_table_has_an_entry_per_supported_device() -> None:
    table = coverage_table()
    assert len(table) == len(DEVICE_RULES)
    classes = {row["class_name"] for row in table}
    assert "Drift" in classes
    assert "Reverb" in classes


# ---------------------------------------------------------------------------
# Fake-OSC fixture: lets apply_descriptor run without Live
# ---------------------------------------------------------------------------


class FakeOSCClient:
    """In-memory OSC stand-in.

    Backs ``request`` with a per-address dict of canned replies and
    records every ``send`` call so tests can assert on parameter writes.
    """

    def __init__(self, *, replies: dict[str, list[Any]]) -> None:
        self._replies = replies
        self.sent: list[tuple[str, tuple[Any, ...]]] = []

    async def request(self, address: str, *args: Any) -> tuple[Any, ...]:
        if address not in self._replies:
            raise KeyError(f"FakeOSCClient: no reply registered for {address!r}")
        return tuple(self._replies[address])

    def send(self, address: str, *args: Any) -> None:
        self.sent.append((address, tuple(args)))


def _drift_replies(track_index: int = 0) -> dict[str, list[Any]]:
    """Canned OSC replies for a single Drift instrument on track 0, device 0.

    Built to mirror the Drift schema in :mod:`device_schemas.instruments`.
    Filter Frequency starts at the schema default (8000 Hz) — well below the
    schema max (18000) so "brighter" should push it up.
    """
    schema = lookup_schema("Drift")
    assert schema is not None, "Drift schema must exist in catalogue"
    names = [p.name for p in schema.parameters]
    values = [float(p.default) for p in schema.parameters]
    mins = [float(p.min) for p in schema.parameters]
    maxs = [float(p.max) for p in schema.parameters]

    return {
        "/live/track/get/devices/class_name": [track_index, "Drift"],
        "/live/track/get/devices/name": [track_index, "Drift"],
        "/live/track/get/devices/type": [track_index, "instrument"],
        "/live/track/get/num_devices": [track_index, 1],
        "/live/song/get/track_names": ["Bass"],
        "/live/song/get/num_tracks": [1],
        "/live/track/get/mute": [track_index, 0],
        "/live/device/get/parameters/name": [track_index, 0, *names],
        "/live/device/get/parameters/value": [track_index, 0, *values],
        "/live/device/get/parameters/min": [track_index, 0, *mins],
        "/live/device/get/parameters/max": [track_index, 0, *maxs],
    }


def _patch_get_client(monkeypatch, fake: FakeOSCClient) -> None:
    """Make every ``get_client()`` call return our fake."""
    async def _fake_get_client(*_a: Any, **_k: Any) -> FakeOSCClient:
        return fake

    import ableton_mcp.osc_client as oc

    monkeypatch.setattr(oc, "get_client", _fake_get_client)


# ---------------------------------------------------------------------------
# apply_descriptor — happy paths
# ---------------------------------------------------------------------------


def test_apply_descriptor_brighter_on_drift_pushes_filter_up(monkeypatch) -> None:
    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)

    result = asyncio.run(
        apply_descriptor(track_index=0, device_index=0, descriptor="brighter", intensity=0.6)
    )

    assert result["status"] in ("ok", "partial"), result
    assert result["class_name"] == "Drift"
    assert "bright" in result["descriptors_applied"]

    # Find the Filter Frequency change.
    by_name = {a["param_name"]: a for a in result["applied"]}
    assert "Filter Frequency" in by_name, f"missing Filter Frequency in {by_name}"
    ff = by_name["Filter Frequency"]
    assert ff["to_value"] > ff["from_value"], (
        f"Filter Frequency should go UP for 'brighter': {ff}"
    )

    # The corresponding OSC send must have happened.
    sends = [s for s in fake.sent if s[0] == "/live/device/set/parameter/value"]
    assert sends, "expected an OSC parameter set call"
    # Verify at least one of those sends was for Filter Frequency at the right index.
    schema = lookup_schema("Drift")
    assert schema is not None
    ff_index = next(
        i for i, p in enumerate(schema.parameters) if p.name == "Filter Frequency"
    )
    matching = [s for s in sends if s[1][2] == ff_index]
    assert matching, f"no send for Filter Frequency (index {ff_index}); sends={sends}"


def test_apply_descriptor_dry_run_does_not_send(monkeypatch) -> None:
    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)

    result = asyncio.run(
        apply_descriptor(0, 0, "brighter", intensity=0.6, dry_run=True)
    )
    assert result["dry_run"] is True
    set_sends = [s for s in fake.sent if s[0] == "/live/device/set/parameter/value"]
    assert not set_sends, f"dry_run shouldn't push: {set_sends}"
    # But the report still reports planned changes.
    assert any(a["param_name"] == "Filter Frequency" for a in result["applied"])


def test_apply_descriptor_darker_pushes_filter_down(monkeypatch) -> None:
    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)
    result = asyncio.run(
        apply_descriptor(0, 0, "darker", intensity=0.7, dry_run=True)
    )
    by_name = {a["param_name"]: a for a in result["applied"]}
    assert "Filter Frequency" in by_name
    assert by_name["Filter Frequency"]["to_value"] < by_name["Filter Frequency"]["from_value"]


def test_apply_descriptor_intensity_zero_no_meaningful_change(monkeypatch) -> None:
    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)
    result = asyncio.run(
        apply_descriptor(0, 0, "brighter", intensity=0.0, dry_run=True)
    )
    # All applied changes should be zero (skipped to "applied" only when sign != 0;
    # but at intensity 0 the signed_amount is 0, so they end up in "skipped").
    assert not result["applied"], f"intensity=0 should produce no applied changes: {result['applied']}"
    assert result["skipped"], "expected entries in 'skipped' for cancelled rules"


# ---------------------------------------------------------------------------
# Multi-descriptor combine
# ---------------------------------------------------------------------------


def test_apply_descriptors_brighter_plus_aggressive_on_drift(monkeypatch) -> None:
    from ableton_mcp.sound_design import apply_descriptors

    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)

    result = asyncio.run(
        apply_descriptors(
            0, 0, ["brighter", "aggressive"], intensity=0.5, dry_run=True
        )
    )
    by_name = {a["param_name"]: a for a in result["applied"]}
    # Filter Resonance picks up contributions from both descriptors and goes up.
    if "Filter Resonance" in by_name:
        assert by_name["Filter Resonance"]["to_value"] >= by_name["Filter Resonance"]["from_value"]
    # Filter Frequency should also go up.
    assert "Filter Frequency" in by_name
    assert by_name["Filter Frequency"]["to_value"] > by_name["Filter Frequency"]["from_value"]


def test_apply_descriptors_opposing_cancels(monkeypatch) -> None:
    """warm vs bright on Drift both touch Filter Frequency in opposite directions."""
    from ableton_mcp.sound_design import apply_descriptors

    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)

    # Equal intensity → check that the |signed_amount| for Filter Frequency is
    # smaller than what a single 'brighter' call produces.
    bright_only = asyncio.run(
        apply_descriptors(0, 0, ["brighter"], intensity=0.5, dry_run=True)
    )
    bright_then_dark = asyncio.run(
        apply_descriptors(0, 0, ["brighter", "darker"], intensity=0.5, dry_run=True)
    )
    bright_amount = next(
        a["signed_amount"] for a in bright_only["applied"]
        if a["param_name"] == "Filter Frequency"
    )
    # darker has an equal-and-opposite Filter Frequency rule (weight 0.9 vs 0.9).
    cancelled = [a for a in bright_then_dark["applied"] if a["param_name"] == "Filter Frequency"]
    skipped = [s for s in bright_then_dark["skipped"] if s["param_name"] == "Filter Frequency"]
    # Either it cancelled into "skipped" or its signed_amount is much smaller.
    if cancelled:
        assert abs(cancelled[0]["signed_amount"]) < abs(bright_amount)
    else:
        assert skipped, "Filter Frequency should be in skipped if cancelled"


# ---------------------------------------------------------------------------
# Unsupported / error paths
# ---------------------------------------------------------------------------


def test_apply_descriptor_unsupported_device(monkeypatch) -> None:
    """If the live device's class isn't in DEVICE_RULES, return unsupported_device."""
    replies = dict(_drift_replies())
    replies["/live/track/get/devices/class_name"] = [0, "TotallyMadeUpVST"]
    fake = FakeOSCClient(replies=replies)
    _patch_get_client(monkeypatch, fake)

    result = asyncio.run(
        apply_descriptor(0, 0, "brighter", intensity=0.5, dry_run=True)
    )
    assert result["status"] == "unsupported_device"
    assert result["class_name"] == "TotallyMadeUpVST"
    set_sends = [s for s in fake.sent if s[0] == "/live/device/set/parameter/value"]
    assert not set_sends


def test_apply_descriptor_osc_unreachable(monkeypatch) -> None:
    async def _bad_client(*_a: Any, **_k: Any) -> Any:
        raise ConnectionRefusedError("OSC down")

    import ableton_mcp.osc_client as oc

    monkeypatch.setattr(oc, "get_client", _bad_client)

    result = asyncio.run(apply_descriptor(0, 0, "brighter"))
    assert result["status"] == "error"
    assert "ConnectionRefusedError" in result["error"]


# ---------------------------------------------------------------------------
# Track summary
# ---------------------------------------------------------------------------


def test_summarise_track_sound_drift(monkeypatch) -> None:
    from ableton_mcp.sound_design import summarise_track_sound

    fake = FakeOSCClient(replies=_drift_replies())
    _patch_get_client(monkeypatch, fake)

    out = asyncio.run(summarise_track_sound(0))
    assert out["status"] == "ok"
    assert out["track_index"] == 0
    assert out["track_name"] == "Bass"
    assert out["device_count"] == 1
    devices = out["devices"]
    assert len(devices) == 1
    assert devices[0]["class_name"] == "Drift"
    assert devices[0]["display_name"] == "Drift"
    assert devices[0]["rules_known"] is True
    assert "Filter Frequency" in devices[0]["character_params"]
    assert "Drift" in out["summary"] or "Bass" in out["summary"]


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_registers_at_least_six_tools() -> None:
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import sound_design as sd_tools

    mcp = FastMCP("test")
    sd_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "sound_describe_track",
        "sound_describe_all_tracks",
        "sound_apply_descriptor",
        "sound_apply_descriptors",
        "sound_explain_descriptor",
        "sound_list_descriptors_for_device",
    }
    assert expected <= names, f"missing: {expected - names}"
    assert len(names) >= 6
