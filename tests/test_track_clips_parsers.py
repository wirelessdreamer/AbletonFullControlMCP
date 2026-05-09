"""Regression tests for the AbletonOSC reply-shape parsers in
``tools/arrangement.py`` and ``tools/clips.py``.

The bug we're guarding against: earlier versions of these parsers expected
an interleaved ``(track_id, idx_0, val_0, idx_1, val_1, ...)`` shape, but
AbletonOSC actually returns a flat ``(track_id, val_0, val_1, ...)`` —
no indices. With the broken parser, ``arrangement_clips_list`` reported
0 clips even when ``track.arrangement_clips`` had entries (parser falls
through ``range(1, 1, 2)`` for a 1-clip reply), and ``clip_list`` raised
on empty session tracks (``int(None)`` from the ``None`` placeholder).

These tests pin AbletonOSC's actual reply shape so the parsers stay
correct as the surrounding code evolves.

Reply shapes verified against AbletonOSC `track.py` 2026-05-09:

- /live/track/get/arrangement_clips/{name,length,start_time}
    handler: ``tuple(getattr(c, prop) for c in track.arrangement_clips)``
    wrapper: prepends track_id
    → ``(track_id, val_0, val_1, ..., val_M)``  (M = number of clips)
- /live/track/get/clips/{name,length,color}
    handler: ``tuple(slot.clip.<prop> if slot.clip else None for slot in track.clip_slots)``
    wrapper: prepends track_id
    → ``(track_id, val_0_or_None, ..., val_N_or_None)``  (N = total slots)
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP


class _CannedOSC:
    """Returns a dict-driven set of canned OSC replies."""

    def __init__(self, replies: dict[str, tuple[Any, ...]]) -> None:
        self.replies = dict(replies)
        self.requests: list[tuple[str, tuple[Any, ...]]] = []

    async def request(self, addr: str, *args: Any) -> tuple:
        self.requests.append((addr, args))
        if addr in self.replies:
            return self.replies[addr]
        raise AssertionError(f"unmocked OSC address: {addr} {args!r}")

    def send(self, *_args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# arrangement_clips_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arrangement_clips_list_one_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug from the song-flow validation run: a single arrangement clip
    should NOT be lost. AbletonOSC emits ``(track_id, name_0)`` — flat
    tuple of length 2. The old parser saw 0 clips here (range(1, 1, 2) is
    empty); the new parser must surface 1 clip.
    """
    from ableton_mcp.tools import arrangement as arr_module

    canned = _CannedOSC({
        "/live/track/get/arrangement_clips/name": (2, "Psalm 13 - Long Enough"),
        "/live/track/get/arrangement_clips/length": (2, 195.84),
        "/live/track/get/arrangement_clips/start_time": (2, 0.0),
    })

    async def fake_get_client():
        return canned

    monkeypatch.setattr(arr_module, "get_client", fake_get_client)

    mcp = FastMCP("test")
    arr_module.register(mcp)
    tools = await mcp.list_tools()
    assert any(t.name == "arrangement_clips_list" for t in tools)

    # FastMCP exposes registered async functions through call_tool by name.
    result = await mcp.call_tool("arrangement_clips_list", {"track_index": 2})
    # Newer FastMCP returns (content, structured) — extract the structured payload.
    payload = result[1] if isinstance(result, tuple) and len(result) >= 2 else result

    assert payload == [{
        "track_index": 2,
        "arrangement_clip_index": 0,
        "name": "Psalm 13 - Long Enough",
        "length_beats": 195.84,
        "start_time_beats": 0.0,
    }] or payload == {"result": [{
        "track_index": 2,
        "arrangement_clip_index": 0,
        "name": "Psalm 13 - Long Enough",
        "length_beats": 195.84,
        "start_time_beats": 0.0,
    }]}


@pytest.mark.asyncio
async def test_arrangement_clips_list_three_clips(monkeypatch: pytest.MonkeyPatch) -> None:
    from ableton_mcp.tools import arrangement as arr_module

    canned = _CannedOSC({
        "/live/track/get/arrangement_clips/name": (0, "intro", "verse", "chorus"),
        "/live/track/get/arrangement_clips/length": (0, 16.0, 32.0, 32.0),
        "/live/track/get/arrangement_clips/start_time": (0, 0.0, 16.0, 48.0),
    })

    async def fake_get_client():
        return canned

    monkeypatch.setattr(arr_module, "get_client", fake_get_client)
    mcp = FastMCP("test")
    arr_module.register(mcp)

    result = await mcp.call_tool("arrangement_clips_list", {"track_index": 0})
    payload = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]

    assert len(payload) == 3
    assert [c["arrangement_clip_index"] for c in payload] == [0, 1, 2]
    assert [c["name"] for c in payload] == ["intro", "verse", "chorus"]
    assert [c["length_beats"] for c in payload] == [16.0, 32.0, 32.0]
    assert [c["start_time_beats"] for c in payload] == [0.0, 16.0, 48.0]


@pytest.mark.asyncio
async def test_arrangement_clips_list_empty_track(monkeypatch: pytest.MonkeyPatch) -> None:
    """Track with no arrangement clips: AbletonOSC emits ``(track_id,)``."""
    from ableton_mcp.tools import arrangement as arr_module

    canned = _CannedOSC({
        "/live/track/get/arrangement_clips/name": (3,),
        "/live/track/get/arrangement_clips/length": (3,),
        "/live/track/get/arrangement_clips/start_time": (3,),
    })

    async def fake_get_client():
        return canned

    monkeypatch.setattr(arr_module, "get_client", fake_get_client)
    mcp = FastMCP("test")
    arr_module.register(mcp)

    result = await mcp.call_tool("arrangement_clips_list", {"track_index": 3})
    payload = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    assert payload == []


# ---------------------------------------------------------------------------
# clip_list (session view)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clip_list_filled_and_empty_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session reply emits one entry per slot; empty slots are ``None``.
    The parser should keep slot indices stable while filtering empties.
    """
    from ableton_mcp.tools import clips as clip_module

    canned = _CannedOSC({
        "/live/track/get/clips/name": (1, None, "loop A", None, None, "loop B", None, None, None),
        "/live/track/get/clips/length": (1, None, 4.0, None, None, 8.0, None, None, None),
        "/live/track/get/clips/color": (1, None, 0xFF0000, None, None, 0x00FF00, None, None, None),
    })

    async def fake_get_client():
        return canned

    monkeypatch.setattr(clip_module, "get_client", fake_get_client)
    mcp = FastMCP("test")
    clip_module.register(mcp)

    result = await mcp.call_tool("clip_list", {"track_index": 1})
    payload = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]

    assert len(payload) == 2
    assert payload[0]["clip_index"] == 1   # filled slot at index 1
    assert payload[0]["name"] == "loop A"
    assert payload[0]["length_beats"] == 4.0
    assert payload[1]["clip_index"] == 4   # filled slot at index 4
    assert payload[1]["name"] == "loop B"
    assert payload[1]["length_beats"] == 8.0


@pytest.mark.asyncio
async def test_clip_list_all_empty_no_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old parser raised ``int(None)`` on empty tracks. The new parser
    should return ``[]`` cleanly.
    """
    from ableton_mcp.tools import clips as clip_module

    canned = _CannedOSC({
        "/live/track/get/clips/name": (2, None, None, None, None, None, None, None, None),
        "/live/track/get/clips/length": (2, None, None, None, None, None, None, None, None),
        "/live/track/get/clips/color": (2, None, None, None, None, None, None, None, None),
    })

    async def fake_get_client():
        return canned

    monkeypatch.setattr(clip_module, "get_client", fake_get_client)
    mcp = FastMCP("test")
    clip_module.register(mcp)

    result = await mcp.call_tool("clip_list", {"track_index": 2})
    payload = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    assert payload == []
