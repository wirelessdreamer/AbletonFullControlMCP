"""Tests for the high-level op_* tools that wrap bridge handlers.

Focuses on the three Phase 1 stubs that PR-this completes: ``op_reverse_clip``,
``op_slice_clip_to_midi``, ``op_new_set``. The reverse op gained MIDI clip
support via the bridge handler; the other two remain stubs (no Live LOM
hook for them) but ship with improved workaround messaging.

We also cover a pure-math note-reversal helper that mirrors the inline
logic the bridge uses; the bridge runs inside Live so it can't be
imported here directly, but the helper here documents what the bridge
does and would catch a math bug in code review.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp.server.fastmcp import FastMCP

from ableton_mcp.tools import high_level as high_level_tool


# ---------------------------------------------------------------------------
# Pure-math helper: same logic as bridge `_reverse_midi_clip` uses inline.
# ---------------------------------------------------------------------------


def _reverse_note_times(notes: list[tuple], length: float) -> list[tuple]:
    """Pure-math reflection of the bridge's MIDI reverse formula.

    A note at (start=t, duration=d) in a length-L clip becomes
    (start=max(0, L - t - d), duration=d). Pitch / velocity / mute are
    preserved. Notes that ran past the clip end (t + d > L) get pinned
    to the new beginning.

    Kept as a Python helper here (not imported from the bridge — the
    bridge module lives in live_remote_script/ and depends on Live's
    runtime, so it's not importable from tests) so the math is
    independently testable.
    """
    return [
        (p, max(0.0, length - t - d), d, v, m)
        for (p, t, d, v, m) in notes
    ]


def test_reverse_math_simple_two_notes_in_4beat_clip() -> None:
    notes = [(60, 0.0, 1.0, 100, False), (64, 2.0, 1.0, 100, False)]
    out = _reverse_note_times(notes, 4.0)
    # First note (start 0, dur 1) → new start = 4 - 0 - 1 = 3
    # Second note (start 2, dur 1) → new start = 4 - 2 - 1 = 1
    assert out[0] == (60, 3.0, 1.0, 100, False)
    assert out[1] == (64, 1.0, 1.0, 100, False)


def test_reverse_math_preserves_pitch_velocity_mute() -> None:
    notes = [(72, 1.5, 0.5, 80, True)]
    out = _reverse_note_times(notes, 4.0)
    assert out[0] == (72, 2.0, 0.5, 80, True)  # pitch/dur/vel/mute unchanged


def test_reverse_math_clamps_notes_running_past_clip_end() -> None:
    """A note that extends past the clip end (t + d > L) ends up at start=0
    after reversal — we clamp negative starts to 0."""
    notes = [(60, 3.5, 1.0, 100, False)]  # ends at 4.5 in a length-4 clip
    out = _reverse_note_times(notes, 4.0)
    assert out[0][1] == 0.0  # clamped to 0


def test_reverse_math_round_trip_is_identity() -> None:
    """Reversing twice should land at the original note times."""
    notes = [
        (60, 0.0, 1.0, 100, False),
        (64, 2.0, 1.0, 100, False),
        (67, 3.5, 0.5, 90, True),
    ]
    once = _reverse_note_times(notes, 4.0)
    twice = _reverse_note_times(once, 4.0)
    assert twice == notes


def test_reverse_math_empty_notes_list() -> None:
    assert _reverse_note_times([], 4.0) == []


# ---------------------------------------------------------------------------
# Tool-level: op_reverse_clip dispatches to bridge
# ---------------------------------------------------------------------------


class _FakeBridge:
    """Records calls, returns a canned reply for clip.reverse."""

    def __init__(self, reply: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reply = reply or {
            "track_index": 0, "clip_index": 0,
            "reversed": True, "via": "midi-notes-extended",
            "kind": "midi", "note_count": 3,
        }

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        return self.reply


async def _invoke(mcp: FastMCP, name: str, **args: Any) -> Any:
    """Call a registered tool and return its structured result."""
    result = await mcp.call_tool(name, args)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, list):
        return result[-1] if result else {}
    return result


@pytest.mark.asyncio
async def test_op_reverse_clip_forwards_to_bridge_reverse_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _FakeBridge()
    monkeypatch.setattr(
        "ableton_mcp.tools.high_level.get_bridge_client", lambda: bridge,
    )
    mcp = FastMCP("t")
    high_level_tool.register(mcp)
    result = await _invoke(mcp, "op_reverse_clip", track_index=2, clip_index=3)
    assert ("clip.reverse", {"track_index": 2, "clip_index": 3}) in bridge.calls
    assert result["reversed"] is True
    assert result["kind"] == "midi"


@pytest.mark.asyncio
async def test_op_reverse_clip_surfaces_not_supported_for_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bridge returns supported=false (audio clip case), the tool
    passes that result through unchanged."""
    bridge = _FakeBridge(reply={
        "track_index": 0, "clip_index": 0,
        "reversed": False, "supported": False, "kind": "audio",
        "workaround": "Audio clip reverse is UI-only...",
    })
    monkeypatch.setattr(
        "ableton_mcp.tools.high_level.get_bridge_client", lambda: bridge,
    )
    mcp = FastMCP("t")
    high_level_tool.register(mcp)
    result = await _invoke(mcp, "op_reverse_clip", track_index=0, clip_index=0)
    assert result["reversed"] is False
    assert result["supported"] is False
    assert "workaround" in result


# ---------------------------------------------------------------------------
# Stubs that stay stubs (no LOM hook): slice_to_midi, new_set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_op_slice_clip_to_midi_returns_actionable_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _FakeBridge()
    monkeypatch.setattr(
        "ableton_mcp.tools.high_level.get_bridge_client", lambda: bridge,
    )
    mcp = FastMCP("t")
    high_level_tool.register(mcp)
    result = await _invoke(
        mcp, "op_slice_clip_to_midi",
        track_index=0, clip_index=0, slicing_preset="16th",
    )
    # Should NOT have hit the bridge — pure stub.
    assert bridge.calls == []
    # Standard stub shape: {ok: False, status: "not_implemented", ...}.
    assert result.get("ok") is False
    assert result.get("status") == "not_implemented"
    assert "reason" in result
    # The message should now reference the workaround paths.
    msg = result.get("reason", "")
    assert "UI" in msg or "manual" in msg.lower() or "Max for Live" in msg


@pytest.mark.asyncio
async def test_op_new_set_returns_actionable_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _FakeBridge()
    monkeypatch.setattr(
        "ableton_mcp.tools.high_level.get_bridge_client", lambda: bridge,
    )
    mcp = FastMCP("t")
    high_level_tool.register(mcp)
    result = await _invoke(mcp, "op_new_set")
    assert bridge.calls == []  # pure stub
    assert result.get("ok") is False
    assert result.get("status") == "not_implemented"
    # Message should mention the UI + alternatives.
    msg = result.get("reason", "")
    assert "File" in msg or "track_delete" in msg or "UI" in msg
