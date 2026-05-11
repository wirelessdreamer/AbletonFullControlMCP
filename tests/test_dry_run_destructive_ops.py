"""Tests for dry-run support on the three destructive `delete` tools.

Pattern: ``dry_run=True`` returns a structured "would do" summary
without issuing the underlying OSC send. ``dry_run=False`` (default)
issues the send and returns the original delete confirmation shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp.server.fastmcp import FastMCP

from ableton_mcp.tools import clips as clips_tool
from ableton_mcp.tools import scenes as scenes_tool
from ableton_mcp.tools import tracks as tracks_tool


class FakeOSC:
    """Minimal OSC double for the dry-run tests.

    Records every ``send`` so tests can assert that destructive sends DON'T
    happen in dry-run mode. ``request`` returns canned data for the info
    queries dry-run uses (track_names, num_scenes, etc.).
    """

    def __init__(
        self,
        *,
        track_names: list[str] | None = None,
        num_scenes: int = 0,
        scene_names: dict[int, str] | None = None,
        clip_names: dict[tuple[int, int], str] | None = None,
    ) -> None:
        self.track_names = track_names or []
        self.num_scenes = num_scenes
        self.scene_names = scene_names or {}
        self.clip_names = clip_names or {}
        self.sent: list[tuple[str, tuple]] = []

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/song/get/num_scenes":
            return (self.num_scenes,)
        if addr == "/live/scene/get/name":
            si = int(args[0])
            return (si, self.scene_names.get(si, f"Scene {si}"))
        if addr == "/live/clip/get/name":
            ti, ci = int(args[0]), int(args[1])
            return (ti, self.clip_names.get((ti, ci), f"clip-{ti}-{ci}"))
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")

    def send(self, addr: str, *args: Any) -> None:
        self.sent.append((addr, args))


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
# track_delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_delete_dry_run_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC(track_names=["Drums", "Bass", "Vocals"])

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(tracks_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    tracks_tool.register(mcp)
    result = await _invoke(mcp, "track_delete", track_index=1, dry_run=True)
    # Critical: no send happened.
    assert osc.sent == []
    # Result describes what would happen.
    assert result["dry_run"] is True
    assert result["would_delete"] is True
    assert result["track_index"] == 1
    assert result["track_name"] == "Bass"
    assert result["num_tracks_before"] == 3
    assert result["num_tracks_after"] == 2
    assert result["actual_state_unchanged"] is True


@pytest.mark.asyncio
async def test_track_delete_real_sends_osc(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC(track_names=["A"])

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(tracks_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    tracks_tool.register(mcp)
    result = await _invoke(mcp, "track_delete", track_index=0, dry_run=False)
    # The actual send happened.
    assert ("/live/song/delete_track", (0,)) in osc.sent
    assert result["deleted_index"] == 0
    assert result["dry_run"] is False


@pytest.mark.asyncio
async def test_track_delete_real_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting dry_run should default to False (real delete)."""
    osc = FakeOSC(track_names=["A"])

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(tracks_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    tracks_tool.register(mcp)
    result = await _invoke(mcp, "track_delete", track_index=0)
    assert ("/live/song/delete_track", (0,)) in osc.sent
    assert result.get("dry_run") is False


@pytest.mark.asyncio
async def test_track_delete_dry_run_handles_out_of_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out-of-range index in dry-run mode → track_name=None but no crash."""
    osc = FakeOSC(track_names=["A"])

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(tracks_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    tracks_tool.register(mcp)
    result = await _invoke(mcp, "track_delete", track_index=99, dry_run=True)
    assert result["dry_run"] is True
    assert result["track_name"] is None
    assert osc.sent == []


# ---------------------------------------------------------------------------
# track_delete_return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_delete_return_dry_run_skips_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC()

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(tracks_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    tracks_tool.register(mcp)
    result = await _invoke(
        mcp, "track_delete_return", return_track_index=2, dry_run=True,
    )
    assert osc.sent == []
    assert result["dry_run"] is True
    assert result["kind"] == "return_track"
    assert result["return_track_index"] == 2


# ---------------------------------------------------------------------------
# clip_delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clip_delete_dry_run_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(clip_names={(0, 2): "Verse Loop"})

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(clips_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    clips_tool.register(mcp)
    result = await _invoke(mcp, "clip_delete", track_index=0, clip_index=2,
                           dry_run=True)
    assert osc.sent == []
    assert result["dry_run"] is True
    assert result["track_index"] == 0
    assert result["clip_index"] == 2
    assert result["clip_name"] == "Verse Loop"


@pytest.mark.asyncio
async def test_clip_delete_real_sends_osc(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC()

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(clips_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    clips_tool.register(mcp)
    await _invoke(mcp, "clip_delete", track_index=1, clip_index=0)
    assert ("/live/clip_slot/delete_clip", (1, 0)) in osc.sent


# ---------------------------------------------------------------------------
# scene_delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scene_delete_dry_run_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(num_scenes=4, scene_names={2: "Bridge"})

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(scenes_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    scenes_tool.register(mcp)
    result = await _invoke(mcp, "scene_delete", scene_index=2, dry_run=True)
    assert osc.sent == []
    assert result["dry_run"] is True
    assert result["scene_index"] == 2
    assert result["scene_name"] == "Bridge"
    assert result["num_scenes_before"] == 4
    assert result["num_scenes_after"] == 3


@pytest.mark.asyncio
async def test_scene_delete_real_sends_osc(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC(num_scenes=2)

    async def fake_get_client() -> FakeOSC:
        return osc

    monkeypatch.setattr(scenes_tool, "get_client", fake_get_client)
    mcp = FastMCP("t")
    scenes_tool.register(mcp)
    result = await _invoke(mcp, "scene_delete", scene_index=1)
    assert ("/live/song/delete_scene", (1,)) in osc.sent
    assert result["deleted_index"] == 1
    assert result["dry_run"] is False
