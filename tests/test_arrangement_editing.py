"""Tests for the arrangement-editing tools: ``arrangement_insert_midi_clip``
and ``arrangement_move_clip``.

Both tools wrap bridge handlers (`clip.create_arrangement_midi_clip` and
`clip.move_arrangement_clip`, added in bridge 1.4.0). The bridge handlers
themselves run inside Live and aren't unit-testable; these tests verify
the Python tool layer wires up correctly.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp.server.fastmcp import FastMCP

from ableton_mcp.bridge_client import (
    AbletonBridgeError,
    AbletonBridgeOutdated,
    EXPECTED_BRIDGE_VERSION,
)
from ableton_mcp.tools import arrangement as arrangement_tool


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeBridge:
    """Records calls + answers require_handler from a static handler list."""

    def __init__(
        self,
        *,
        handlers: list[str] | None = None,
        call_reply: dict[str, Any] | None = None,
        call_raises: AbletonBridgeError | None = None,
    ) -> None:
        self.handlers = handlers or [
            "clip.create_arrangement_midi_clip",
            "clip.move_arrangement_clip",
        ]
        self.call_reply = call_reply
        self.call_raises = call_raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def version_info(self, *, refresh: bool = False) -> dict[str, Any]:
        return {
            "bridge_version": EXPECTED_BRIDGE_VERSION,
            "live_version": "11.3.43",
            "handlers": self.handlers,
            "expected_bridge_version": EXPECTED_BRIDGE_VERSION,
            "compatible": True,
            "outdated": False,
        }

    async def require_handler(self, op: str) -> None:
        if op not in self.handlers:
            raise AbletonBridgeOutdated(
                f"Bridge handler {op!r} not available. install_bridge."
            )

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        if self.call_raises is not None:
            raise self.call_raises
        return self.call_reply or {}


async def _invoke(mcp: FastMCP, name: str, **args: Any) -> Any:
    result = await mcp.call_tool(name, args)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, list):
        return result[-1] if result else {}
    return result


# ---------------------------------------------------------------------------
# arrangement_insert_midi_clip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_midi_clip_forwards_to_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge(call_reply={
        "track_index": 2, "clip_index": 0,
        "position": 8.0, "length": 4.0, "end": 12.0,
        "name": None, "pre_count": 0, "post_count": 1,
        "created": True,
    })
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    result = await _invoke(
        mcp, "arrangement_insert_midi_clip",
        track_index=2, position_beats=8.0, length_beats=4.0,
    )
    assert result["status"] == "ok"
    assert result["track_index"] == 2
    assert result["clip_index"] == 0
    assert result["position"] == 8.0
    # Verify the bridge call had the right args.
    assert bridge.calls[0][0] == "clip.create_arrangement_midi_clip"
    assert bridge.calls[0][1] == {"track_index": 2, "position": 8.0, "length": 4.0}


@pytest.mark.asyncio
async def test_insert_midi_clip_default_length_is_four_beats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting length_beats should default to 4 beats (one bar in 4/4)."""
    bridge = FakeBridge(call_reply={"track_index": 1, "clip_index": 0, "created": True})
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    await _invoke(
        mcp, "arrangement_insert_midi_clip",
        track_index=1, position_beats=0.0,
    )
    assert bridge.calls[0][1]["length"] == 4.0


@pytest.mark.asyncio
async def test_insert_midi_clip_outdated_bridge_returns_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge(handlers=["clip.list_arrangement_clips"])  # no create
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    result = await _invoke(
        mcp, "arrangement_insert_midi_clip",
        track_index=0, position_beats=0.0,
    )
    assert result["status"] == "error"
    assert result["stage"] == "version_check"
    assert "install_bridge" in result["error"]


@pytest.mark.asyncio
async def test_insert_midi_clip_surfaces_bridge_call_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Live rejects the create (e.g. overlapping clip), the tool
    returns status=error with the underlying message — doesn't raise."""
    bridge = FakeBridge(
        call_raises=AbletonBridgeError("simulated overlap rejection")
    )
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    result = await _invoke(
        mcp, "arrangement_insert_midi_clip",
        track_index=0, position_beats=0.0, length_beats=4.0,
    )
    assert result["status"] == "error"
    assert result["stage"] == "create"
    assert "overlap rejection" in result["error"]


# ---------------------------------------------------------------------------
# arrangement_move_clip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_clip_forwards_to_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge(call_reply={
        "track_index": 2, "clip_index": 1,
        "old_position": 0.0, "new_position": 16.0,
        "requested_position": 16.0, "delta_beats": 16.0,
        "via": "Clip.move", "moved": True,
    })
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    result = await _invoke(
        mcp, "arrangement_move_clip",
        track_index=2, clip_index=1, new_position_beats=16.0,
    )
    assert result["status"] == "ok"
    assert result["new_position"] == 16.0
    assert bridge.calls[0][0] == "clip.move_arrangement_clip"
    assert bridge.calls[0][1] == {
        "track_index": 2, "clip_index": 1, "new_position": 16.0,
    }


@pytest.mark.asyncio
async def test_move_clip_returns_error_when_move_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Clip.move isn't on the running Live build (very old), the
    bridge returns moved=False with a workaround. The tool surfaces
    that as status=error so the LLM can present the workaround."""
    bridge = FakeBridge(call_reply={
        "track_index": 0, "clip_index": 0,
        "old_position": 0.0, "requested_position": 8.0, "delta_beats": 8.0,
        "moved": False, "supported": False,
        "error": "Clip.move not available",
        "workaround": "Move manually in the arrangement view.",
    })
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    result = await _invoke(
        mcp, "arrangement_move_clip",
        track_index=0, clip_index=0, new_position_beats=8.0,
    )
    assert result["status"] == "error"
    assert result["moved"] is False
    assert "workaround" in result


@pytest.mark.asyncio
async def test_move_clip_outdated_bridge_returns_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge(handlers=["clip.list_arrangement_clips"])
    monkeypatch.setattr(arrangement_tool, "get_bridge_client", lambda: bridge)
    mcp = FastMCP("t")
    arrangement_tool.register(mcp)
    result = await _invoke(
        mcp, "arrangement_move_clip",
        track_index=0, clip_index=0, new_position_beats=8.0,
    )
    assert result["status"] == "error"
    assert result["stage"] == "version_check"
