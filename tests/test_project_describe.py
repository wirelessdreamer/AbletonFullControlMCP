"""Tests for the project_describe tool.

The tool composes existing OSC + bridge queries, so the tests mock both
and verify shape + detail-level behaviour rather than the underlying
Live state reads (those are covered by the lower-level tool tests).
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp.server.fastmcp import FastMCP

from ableton_mcp.tools import project as project_tool


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeOSC:
    """Returns canned values for the addresses project_describe reads."""

    def __init__(
        self,
        *,
        tempo: float = 120.0,
        sig_num: int = 4,
        sig_den: int = 4,
        song_length: float = 32.0,
        num_tracks: int = 0,
        num_scenes: int = 4,
        is_playing: bool = False,
        track_names: list[str] | None = None,
        scene_names: list[str] | None = None,
        per_track: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.tempo = tempo
        self.sig_num = sig_num
        self.sig_den = sig_den
        self.song_length = song_length
        self.num_tracks = num_tracks
        self.num_scenes = num_scenes
        self.is_playing = is_playing
        self.track_names = track_names or []
        self.scene_names = scene_names or []
        # Per-track override values: {track_idx: {field: value}}
        self.per_track = per_track or {}

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/tempo":
            return (self.tempo,)
        if addr == "/live/song/get/signature_numerator":
            return (self.sig_num,)
        if addr == "/live/song/get/signature_denominator":
            return (self.sig_den,)
        if addr == "/live/song/get/song_length":
            return (self.song_length,)
        if addr == "/live/song/get/num_tracks":
            return (self.num_tracks,)
        if addr == "/live/song/get/num_scenes":
            return (self.num_scenes,)
        if addr == "/live/song/get/current_song_time":
            return (0.0,)
        if addr == "/live/song/get/is_playing":
            return (1 if self.is_playing else 0,)
        if addr == "/live/song/get/root_note":
            return (0,)
        if addr == "/live/song/get/scale_name":
            return ("Major",)
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/song/get/scenes/name":
            return tuple(self.scene_names)
        # Per-track reads. AbletonOSC reply shape is (track_id, value).
        if addr.startswith("/live/track/get/") and args:
            ti = int(args[0])
            field = addr.removeprefix("/live/track/get/")
            return (ti, self._field(ti, field))
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")

    def _field(self, ti: int, field: str) -> Any:
        defaults = {
            "mute": 0, "solo": 0, "arm": 0,
            "is_foldable": 0, "is_grouped": 0,
            "has_audio_input": 0, "has_audio_output": 1,
            "has_midi_input": 0, "has_midi_output": 0,
            "color_index": 0,
            "volume": 0.85, "panning": 0.0,
        }
        return self.per_track.get(ti, {}).get(field, defaults.get(field, 0))


class FakeBridge:
    """Bridge double for version_info + track.list_devices."""

    def __init__(
        self,
        *,
        version_info: dict[str, Any] | None = None,
        devices_per_track: dict[int, list[dict[str, Any]]] | None = None,
        version_raises: bool = False,
        list_devices_raises: bool = False,
    ) -> None:
        from ableton_mcp.bridge_client import EXPECTED_BRIDGE_VERSION
        self._version = version_info or {
            "bridge_version": EXPECTED_BRIDGE_VERSION,
            "live_version": "11.3.43",
            "handlers": ["track.list_devices"],
            "expected_bridge_version": EXPECTED_BRIDGE_VERSION,
            "compatible": True,
            "outdated": False,
        }
        self._devices = devices_per_track or {}
        self._version_raises = version_raises
        self._list_devices_raises = list_devices_raises

    async def version_info(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._version_raises:
            from ableton_mcp.bridge_client import AbletonBridgeError
            raise AbletonBridgeError("simulated bridge unreachable")
        return self._version

    async def call(self, op: str, **kwargs: Any) -> Any:
        if op == "track.list_devices":
            if self._list_devices_raises:
                from ableton_mcp.bridge_client import AbletonBridgeError
                raise AbletonBridgeError("simulated track.list_devices failure")
            ti = int(kwargs.get("track_index", 0))
            return {"track_index": ti, "devices": self._devices.get(ti, [])}
        raise AssertionError(f"unexpected bridge op: {op}")


def _patch(monkeypatch: pytest.MonkeyPatch, osc: FakeOSC, bridge: FakeBridge) -> None:
    async def fake_get_client() -> FakeOSC:
        return osc

    def fake_get_bridge() -> FakeBridge:
        return bridge

    monkeypatch.setattr(project_tool, "get_client", fake_get_client)
    monkeypatch.setattr(project_tool, "get_bridge_client", fake_get_bridge)


async def _call_tool(detail: str = "tracks") -> dict[str, Any]:
    """Helper: register the tool on a fresh FastMCP and invoke it.

    Mirrors how the existing test_song_flow tests exercise tools via the
    server registration path.
    """
    mcp = FastMCP("t")
    project_tool.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "project_describe" in names
    # Invoke directly through the tool callable; FastMCP's tool() decorator
    # wraps the function but the original is retrievable.
    # Simpler: call the underlying function via the registered handler.
    result = await mcp.call_tool("project_describe", {"detail": detail})
    # FastMCP returns the result as a list of content blocks; the structured
    # data is on the result's `structuredContent` or last block. Newer
    # versions return a CallToolResult with `.structuredContent`; older
    # returned a list. Handle both.
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if isinstance(result, tuple) and len(result) == 2:
        # FastMCP returns (content_blocks, structured_content) tuple
        return result[1]
    if isinstance(result, list):
        return result[-1] if result else {}
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tool registration smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_describe_is_registered() -> None:
    mcp = FastMCP("t")
    project_tool.register(mcp)
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "project_describe" in names


# ---------------------------------------------------------------------------
# detail="summary"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_returns_project_and_track_names_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(
        track_names=["Drums", "Bass", "Vocals"],
        num_tracks=3,
        scene_names=["Intro", "Verse"],
        num_scenes=2,
    )
    bridge = FakeBridge()
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool("summary")
    assert result["status"] == "ok"
    assert result["detail"] == "summary"
    assert result["project"]["tempo"] == 120.0
    assert result["project"]["num_tracks"] == 3
    tracks = result["tracks"]
    assert len(tracks) == 3
    assert {t["index"] for t in tracks} == {0, 1, 2}
    assert {t["name"] for t in tracks} == {"Drums", "Bass", "Vocals"}
    # Summary level shouldn't include mixer state.
    assert "muted" not in tracks[0]
    assert "devices" not in tracks[0]


# ---------------------------------------------------------------------------
# detail="tracks" (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracks_detail_includes_mixer_state(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC(
        track_names=["A", "B"],
        num_tracks=2,
        per_track={0: {"mute": 1, "solo": 0}, 1: {"mute": 0, "solo": 1}},
    )
    bridge = FakeBridge()
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool()  # default is "tracks"
    assert result["detail"] == "tracks"
    tracks = result["tracks"]
    assert tracks[0]["muted"] is True
    assert tracks[0]["solo"] is False
    assert tracks[1]["muted"] is False
    assert tracks[1]["solo"] is True
    # No devices at "tracks" level.
    assert "devices" not in tracks[0]


@pytest.mark.asyncio
async def test_tracks_detail_handles_per_track_read_failure_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If one OSC read fails mid-track, that field is null but the row stays."""
    osc = FakeOSC(track_names=["Broken"], num_tracks=1)
    real_request = osc.request

    async def fail_one(addr: str, *args: Any) -> tuple:
        if addr == "/live/track/get/solo":
            raise RuntimeError("simulated OSC failure")
        return await real_request(addr, *args)

    osc.request = fail_one  # type: ignore[assignment]
    bridge = FakeBridge()
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool()
    track = result["tracks"][0]
    assert track["name"] == "Broken"
    assert track["solo"] is None  # failed read → null
    assert track["muted"] is False  # other reads succeed


# ---------------------------------------------------------------------------
# detail="full"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_detail_includes_devices_per_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(track_names=["Synth"], num_tracks=1)
    bridge = FakeBridge(
        devices_per_track={
            0: [
                {"index": 0, "name": "Operator", "class_name": "OperatorDevice", "type": 1},
                {"index": 1, "name": "EQ Eight", "class_name": "Eq8", "type": 2},
            ],
        },
    )
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool("full")
    devices = result["tracks"][0]["devices"]
    assert isinstance(devices, list)
    assert len(devices) == 2
    assert devices[0]["name"] == "Operator"
    assert devices[1]["name"] == "EQ Eight"


@pytest.mark.asyncio
async def test_full_detail_surfaces_bridge_error_as_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    osc = FakeOSC(track_names=["Broken"], num_tracks=1)
    bridge = FakeBridge(list_devices_raises=True)
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool("full")
    devices = result["tracks"][0]["devices"]
    assert isinstance(devices, str)
    assert "bridge error" in devices.lower()


# ---------------------------------------------------------------------------
# Bridge unavailable → still returns useful data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_unavailable_does_not_break_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bridge isn't reachable, ``bridge.available`` is False but
    we still return project + tracks (no devices). Critical for sessions
    where only AbletonOSC is running but not the bridge."""
    osc = FakeOSC(track_names=["A"], num_tracks=1)
    bridge = FakeBridge(version_raises=True)
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool("full")
    assert result["status"] == "ok"
    assert result["bridge"]["available"] is False
    assert "error" in result["bridge"]
    # Tracks still populated (mixer reads come from OSC).
    assert len(result["tracks"]) == 1
    # Devices skipped because bridge unavailable.
    assert "devices" not in result["tracks"][0]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_detail_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    osc = FakeOSC(track_names=[], num_tracks=0)
    bridge = FakeBridge()
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool("bogus")
    assert result["status"] == "error"
    assert "unknown detail" in result["error"]


@pytest.mark.asyncio
async def test_empty_project_returns_clean_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh empty Live set should still produce a valid response."""
    osc = FakeOSC(track_names=[], num_tracks=0, scene_names=[], num_scenes=0)
    bridge = FakeBridge()
    _patch(monkeypatch, osc, bridge)

    result = await _call_tool("full")
    assert result["status"] == "ok"
    assert result["tracks"] == []
    assert result["scenes"] == []
    assert result["project"]["num_tracks"] == 0
