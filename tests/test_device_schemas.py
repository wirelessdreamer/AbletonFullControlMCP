"""Tests for the canonical Live 11 device-schema library."""

from __future__ import annotations

import pytest

from ableton_mcp.device_schemas import (
    DEVICE_SCHEMAS,
    DEVICE_SCHEMAS_BY_CLASS,
    DeviceSchema,
    Parameter,
    closest_class_name,
    lookup_schema,
)
from ableton_mcp.tools import device_schemas as device_schemas_tools


VALID_TYPES = {"continuous", "quantized", "enum"}
VALID_DEVICE_TYPES = {
    "instrument",
    "audio_effect",
    "midi_effect",
    "drum",
    "utility",
    "rack",
}


def test_catalog_is_non_trivial() -> None:
    """At least 30 instrument + audio-effect schemas combined."""
    instr_and_fx = [
        s for s in DEVICE_SCHEMAS if s.device_type in ("instrument", "audio_effect", "drum")
    ]
    assert len(instr_and_fx) >= 30, (
        f"expected ≥30 instrument+effect schemas, got {len(instr_and_fx)}"
    )


def test_no_duplicate_class_names() -> None:
    seen: set[str] = set()
    for s in DEVICE_SCHEMAS:
        assert s.class_name not in seen, f"duplicate class_name: {s.class_name}"
        seen.add(s.class_name)
    # And the lookup index agrees.
    assert len(DEVICE_SCHEMAS_BY_CLASS) == len(DEVICE_SCHEMAS)


def test_device_schema_basic_fields() -> None:
    """Every schema has the required identification fields populated."""
    for s in DEVICE_SCHEMAS:
        assert isinstance(s, DeviceSchema)
        assert s.class_name and isinstance(s.class_name, str)
        assert s.display_name and isinstance(s.display_name, str)
        assert s.device_type in VALID_DEVICE_TYPES, (
            f"{s.class_name}: bad device_type {s.device_type}"
        )
        assert s.description, f"{s.class_name} missing description"
        assert isinstance(s.categories, list)
        assert isinstance(s.parameters, list)


def test_every_parameter_has_required_fields() -> None:
    """Every Parameter has name, type, default, min, max, category, description, sweep flag."""
    for s in DEVICE_SCHEMAS:
        for p in s.parameters:
            assert isinstance(p, Parameter)
            assert p.name and isinstance(p.name, str), f"{s.class_name}: empty param name"
            assert p.type in VALID_TYPES, f"{s.class_name}.{p.name}: bad type {p.type}"
            assert isinstance(p.default, (int, float)), (
                f"{s.class_name}.{p.name}: default not numeric"
            )
            assert isinstance(p.min, (int, float))
            assert isinstance(p.max, (int, float))
            assert p.min <= p.max, (
                f"{s.class_name}.{p.name}: min {p.min} > max {p.max}"
            )
            assert p.min <= p.default <= p.max, (
                f"{s.class_name}.{p.name}: default {p.default} outside [{p.min}, {p.max}]"
            )
            assert isinstance(p.category, str) and p.category, (
                f"{s.class_name}.{p.name}: empty category"
            )
            assert isinstance(p.recommended_for_sweep, bool)
            assert isinstance(p.description, str) and p.description, (
                f"{s.class_name}.{p.name}: empty description"
            )


def test_lookup_schema_known_and_unknown() -> None:
    s = lookup_schema("Operator")
    assert s is not None
    assert s.display_name == "Operator"

    assert lookup_schema("DefinitelyNotAClassName") is None
    assert lookup_schema("") is None


def test_closest_class_name_fuzzy_match() -> None:
    # Typo on a real display name should resolve.
    near = closest_class_name("Wavtable")
    assert near in {"InstrumentVector"}, f"expected Wavetable's class_name, got {near}"
    # A real class_name returns itself.
    assert closest_class_name("Operator") == "Operator"


def test_recommended_for_sweep_filter_works() -> None:
    """Every device with parameters has at least one sweep-recommended param;
    the filter returns ONLY those marked as such."""
    devices_with_params = [s for s in DEVICE_SCHEMAS if s.parameters]
    no_sweep = [s for s in devices_with_params if not s.recommended_sweep_params()]
    # A few measurement-only devices (Tuner / Spectrum) might legitimately have none.
    assert len(no_sweep) <= 3, (
        f"too many devices without any recommended sweep params: "
        f"{[s.class_name for s in no_sweep]}"
    )

    # Operator should specifically have a sweep set.
    op = lookup_schema("Operator")
    assert op is not None
    sweep = op.recommended_sweep_params()
    assert len(sweep) >= 5, "Operator should have ≥5 sweep-worthy params"
    for p in sweep:
        assert p.recommended_for_sweep is True


def test_at_least_one_schema_per_device_type() -> None:
    """We catalogue at least one device of each major type."""
    seen_types = {s.device_type for s in DEVICE_SCHEMAS}
    expected = {"instrument", "audio_effect", "midi_effect", "rack", "utility"}
    missing = expected - seen_types
    assert not missing, f"missing schemas for device types: {missing}"


def test_to_dict_round_trip() -> None:
    """to_dict() returns a JSON-friendly snapshot with all the right keys."""
    s = lookup_schema("Reverb")
    assert s is not None
    d = s.to_dict()
    assert d["class_name"] == "Reverb"
    assert d["display_name"] == "Reverb"
    assert d["device_type"] == "audio_effect"
    assert isinstance(d["parameters"], list)
    assert d["parameters"], "Reverb should have parameters"
    p0 = d["parameters"][0]
    for key in (
        "name",
        "type",
        "default",
        "min",
        "max",
        "category",
        "recommended_for_sweep",
        "description",
    ):
        assert key in p0


# --- MCP tool registration -------------------------------------------------- #


@pytest.mark.asyncio
async def test_register_registers_at_least_5_tools() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-device-schemas")
    device_schemas_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "device_schema_list",
        "device_schema_get",
        "device_schema_for_track_device",
        "device_schema_recommended_sweep_params",
        "device_schema_search",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"
    assert len(names) >= 5


# --- Tool-level smoke tests (no Live needed for these four) ----------------- #


@pytest.mark.asyncio
async def test_tool_list_filters_by_device_type() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-device-schemas")
    device_schemas_tools.register(mcp)

    # Use call_tool to exercise the tools the same way the LLM would.
    instr = await mcp.call_tool("device_schema_list", {"device_type": "instrument"})
    # FastMCP returns (content, structured) — we only need the structured part.
    structured = instr[1] if isinstance(instr, tuple) else instr
    if isinstance(structured, dict) and "result" in structured:
        rows = structured["result"]
    else:
        rows = structured
    assert isinstance(rows, list)
    assert all(r["device_type"] == "instrument" for r in rows)
    assert len(rows) >= 5


@pytest.mark.asyncio
async def test_tool_get_known_and_unknown() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-device-schemas")
    device_schemas_tools.register(mcp)

    out = await mcp.call_tool("device_schema_get", {"class_name": "Operator"})
    structured = out[1] if isinstance(out, tuple) else out
    assert structured.get("display_name") == "Operator" or (
        isinstance(structured, dict)
        and structured.get("result", {}).get("display_name") == "Operator"
    )

    bad = await mcp.call_tool(
        "device_schema_get", {"class_name": "NotARealDeviceClass"}
    )
    bad_struct = bad[1] if isinstance(bad, tuple) else bad
    if isinstance(bad_struct, dict) and "result" in bad_struct:
        bad_struct = bad_struct["result"]
    assert "error" in bad_struct


@pytest.mark.asyncio
async def test_tool_search_finds_filter_devices() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-device-schemas")
    device_schemas_tools.register(mcp)

    out = await mcp.call_tool("device_schema_search", {"query": "filter cutoff"})
    structured = out[1] if isinstance(out, tuple) else out
    if isinstance(structured, dict) and "result" in structured:
        rows = structured["result"]
    else:
        rows = structured
    assert isinstance(rows, list)
    assert rows, "search for 'filter cutoff' should match at least one device"
    class_names = {r["class_name"] for r in rows}
    # Auto Filter is the obvious hit.
    assert "AutoFilter" in class_names
