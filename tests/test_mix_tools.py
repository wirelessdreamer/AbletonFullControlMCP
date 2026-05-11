"""Tests for the MCP tools registered by ``ableton_mcp.tools.mix``.

These wrap the L2-L5 + KB modules with the standard MCP error envelope
(``{"status": "ok" | "error", ...}``). The underlying module functions
are already heavily tested elsewhere — these tests verify:

1. Each tool registers and is callable through FastMCP.
2. Args are passed through with the right types.
3. The standard error envelope kicks in on the expected error types
   (KeyError for unknown intent, ValueError for focal-not-found, etc).
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from ableton_mcp.tools import mix as mix_tool


async def _invoke(mcp: FastMCP, name: str, **args: Any) -> Any:
    """Call a registered MCP tool and unwrap the structured content."""
    result = await mcp.call_tool(name, args)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, list):
        return result[-1] if result else {}
    return result


# ---------------------------------------------------------------------------
# Vocabulary tools — no Live needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_list_intents_returns_descriptor_list() -> None:
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(mcp, "mix_list_intents")
    assert result["status"] == "ok"
    names = {d["name"] for d in result["descriptors"]}
    # Plan-doc core vocabulary is present.
    for expected in ("cuts_through", "buried", "muddy", "harsh", "airy"):
        assert expected in names


@pytest.mark.asyncio
async def test_mix_describe_intent_resolves_canonical_name() -> None:
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(mcp, "mix_describe_intent", intent="cuts_through")
    assert result["status"] == "ok"
    assert result["name"] == "cuts_through"
    assert result["band_low_hz"] == 2000.0
    assert result["band_high_hz"] == 5000.0


@pytest.mark.asyncio
async def test_mix_describe_intent_resolves_alias_with_whitespace() -> None:
    """The descriptor resolver is case + whitespace tolerant — the
    MCP wrapper should preserve that."""
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(mcp, "mix_describe_intent", intent="Cut Through")
    assert result["status"] == "ok"
    assert result["name"] == "cuts_through"


@pytest.mark.asyncio
async def test_mix_describe_intent_unknown_returns_error_envelope() -> None:
    """Unknown intent should land as a ``status=error`` dict, NOT
    raise out of the tool call (the MCP client would see a server
    error rather than an actionable response)."""
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(mcp, "mix_describe_intent", intent="nonsense")
    assert result["status"] == "error"
    assert "unknown" in result["error"].lower()


@pytest.mark.asyncio
async def test_mix_classify_track_by_name_matches_bass() -> None:
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(mcp, "mix_classify_track_by_name", track_name="Bass Gtr")
    assert result["status"] == "ok"
    assert result["matched"] is True
    assert result["name"] == "bass"


@pytest.mark.asyncio
async def test_mix_classify_track_by_name_unmatched() -> None:
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_classify_track_by_name", track_name="ZZZUnknown",
    )
    assert result["status"] == "ok"
    assert result["matched"] is False


# ---------------------------------------------------------------------------
# Analysis tools — monkeypatched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_spectrum_at_region_passes_args_and_wraps_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_spectrum(start_beats, end_beats, **kwargs):
        captured["start"] = start_beats
        captured["end"] = end_beats
        captured["sr"] = kwargs.get("target_sr")
        return {
            "region": {"start_beats": start_beats, "end_beats": end_beats},
            "bands": [], "tracks": [],
        }

    monkeypatch.setattr(mix_tool, "_mix_spectrum_at_region", fake_spectrum)
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_spectrum_at_region",
        start_beats=0.0, end_beats=8.0, target_sr=22050,
    )
    assert result["status"] == "ok"
    assert captured["start"] == 0.0
    assert captured["end"] == 8.0
    assert captured["sr"] == 22050


@pytest.mark.asyncio
async def test_mix_masking_at_region_wraps_value_error_into_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the underlying function raises ValueError (focal not
    found in the bounce), the tool returns ``status=error`` rather
    than letting it propagate."""

    async def fake_masking(**_kw):
        raise ValueError("focal_track_index=99 not found")

    monkeypatch.setattr(mix_tool, "_mix_masking_at_region", fake_masking)
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_masking_at_region",
        focal_track_index=99, start_beats=0.0, end_beats=4.0,
    )
    assert result["status"] == "error"
    assert "focal_track_index" in result["error"]


# ---------------------------------------------------------------------------
# Proposal / apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_propose_at_region_wraps_unknown_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling with an unknown intent should land as a status=error
    response. The underlying function raises KeyError BEFORE bouncing,
    so this tests the error path is mapped correctly."""
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_propose_at_region",
        focal_track_index=0, intent="not_a_word",
        start_beats=0.0, end_beats=4.0,
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_mix_propose_at_region_returns_ok_with_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: monkeypatch the propose function, verify the tool
    wraps its result with status=ok."""

    async def fake_propose(**_kw):
        return {
            "intent": "cuts_through",
            "actions": [{"track_index": 1, "kind": "eq_cut",
                         "freq_hz": 3000.0, "gain_db": -3.0,
                         "rationale": "test"}],
            "region": {"start_beats": 0.0, "end_beats": 8.0},
        }

    monkeypatch.setattr(mix_tool, "_mix_propose_at_region", fake_propose)
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_propose_at_region",
        focal_track_index=0, intent="cuts_through",
        start_beats=0.0, end_beats=8.0,
    )
    assert result["status"] == "ok"
    assert len(result["actions"]) == 1


@pytest.mark.asyncio
async def test_mix_apply_proposal_passes_dry_run_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dry_run`` should reach the underlying function, and the
    structured plan should be returned."""
    captured: dict[str, Any] = {}

    async def fake_apply(proposal, *, dry_run):
        captured["proposal"] = proposal
        captured["dry_run"] = dry_run
        return {
            "dry_run": dry_run, "intent": proposal.get("intent"),
            "plan": [], "skipped": [], "results": [],
        }

    monkeypatch.setattr(mix_tool, "_mix_apply_proposal", fake_apply)
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    proposal = {"intent": "cuts_through", "actions": []}
    result = await _invoke(
        mcp, "mix_apply_proposal",
        proposal=proposal, dry_run=True,
    )
    assert result["status"] == "ok"
    assert captured["dry_run"] is True
    assert captured["proposal"]["intent"] == "cuts_through"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_snapshot_for_verification_wraps_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_snap(**_kw):
        return {"focal_track": 0, "focal_money_bands": []}

    monkeypatch.setattr(
        mix_tool, "_mix_snapshot_for_verification", fake_snap,
    )
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_snapshot_for_verification",
        focal_track_index=0, start_beats=0.0, end_beats=8.0,
    )
    assert result["status"] == "ok"
    assert result["focal_track"] == 0


@pytest.mark.asyncio
async def test_mix_verify_intent_with_baseline_returns_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_verify(**kwargs):
        # Verify baseline_snapshot was passed through.
        assert kwargs.get("baseline_snapshot") is not None
        return {
            "intent": "cuts_through",
            "intent_achieved": True,
            "regressed": False,
            "summary": "intent=cuts_through; ...",
        }

    monkeypatch.setattr(mix_tool, "_mix_verify_intent", fake_verify)
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    result = await _invoke(
        mcp, "mix_verify_intent",
        focal_track_index=0, intent="cuts_through",
        start_beats=0.0, end_beats=8.0,
        baseline_snapshot={"focal_track": 0, "focal_money_bands": []},
    )
    assert result["status"] == "ok"
    assert result["intent_achieved"] is True


@pytest.mark.asyncio
async def test_mix_verify_intent_unknown_intent_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-fast path: unknown intent raises KeyError before
    bouncing — the tool should surface that as status=error."""
    bounce_calls = []

    async def fake_verify(**kwargs):
        bounce_calls.append(kwargs)
        return {}

    # If we get here we don't want the underlying function to be called.
    monkeypatch.setattr(mix_tool, "_mix_verify_intent", fake_verify)
    mcp = FastMCP("t")
    mix_tool.register(mcp)

    # We bypass the fake — instead, let the real underlying function
    # run the resolve check by NOT patching it. But our fake takes
    # over... so we need a different approach. Patch fake to delegate.
    from ableton_mcp.mix_descriptors import resolve_descriptor

    async def fake_verify_strict(focal_track_index, intent, start_beats,
                                 end_beats, **_):
        resolve_descriptor(intent)  # will raise KeyError on unknown
        return {}

    monkeypatch.setattr(
        mix_tool, "_mix_verify_intent", fake_verify_strict,
    )
    result = await _invoke(
        mcp, "mix_verify_intent",
        focal_track_index=0, intent="zzz",
        start_beats=0.0, end_beats=8.0,
    )
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Registration shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_mix_tools_registered() -> None:
    """Every documented tool name shows up in the FastMCP tool list."""
    mcp = FastMCP("t")
    mix_tool.register(mcp)
    listed = await mcp.list_tools()
    names = {t.name for t in listed}
    expected = {
        "mix_list_intents",
        "mix_describe_intent",
        "mix_classify_track_by_name",
        "mix_spectrum_at_region",
        "mix_masking_at_region",
        "mix_propose_at_region",
        "mix_apply_proposal",
        "mix_snapshot_for_verification",
        "mix_verify_intent",
    }
    missing = expected - names
    assert missing == set(), f"unregistered tools: {missing}"
