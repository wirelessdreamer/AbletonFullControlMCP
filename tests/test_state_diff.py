"""Tests for ``state_diff`` — snapshot + diff helpers."""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.state_diff import (
    SceneSnapshot,
    SongStateSnapshot,
    TrackSnapshot,
    diff_state,
    snapshot_song_state,
)


# ---------------------------------------------------------------------------
# snapshot_song_state — needs an OSC client mock
# ---------------------------------------------------------------------------


class FakeOSC:
    """Returns canned values for the addresses snapshot_song_state reads."""

    def __init__(
        self,
        *,
        tempo: float = 120.0,
        sig_num: int = 4, sig_den: int = 4,
        track_names: list[str] | None = None,
        scene_names: list[str] | None = None,
        per_track: dict[int, dict[str, Any]] | None = None,
        raise_on_addr: set[str] | None = None,
    ) -> None:
        self.tempo = tempo
        self.sig_num = sig_num
        self.sig_den = sig_den
        self.track_names = track_names or []
        self.scene_names = scene_names or []
        self.per_track = per_track or {}
        self.raise_on_addr = raise_on_addr or set()

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr in self.raise_on_addr:
            raise RuntimeError(f"simulated failure for {addr}")
        if addr == "/live/song/get/tempo":
            return (self.tempo,)
        if addr == "/live/song/get/signature_numerator":
            return (self.sig_num,)
        if addr == "/live/song/get/signature_denominator":
            return (self.sig_den,)
        if addr == "/live/song/get/num_tracks":
            return (len(self.track_names),)
        if addr == "/live/song/get/num_scenes":
            return (len(self.scene_names),)
        if addr == "/live/song/get/track_names":
            return tuple(self.track_names)
        if addr == "/live/song/get/scenes/name":
            return tuple(self.scene_names)
        if addr.startswith("/live/track/get/") and args:
            ti = int(args[0])
            field = addr.removeprefix("/live/track/get/")
            val = self.per_track.get(ti, {}).get(field)
            if val is None:
                # Default values so the snapshot doesn't have to guess.
                default_for = {
                    "mute": 0, "solo": 0, "arm": 0, "color_index": 0,
                }
                val = default_for.get(field, 0)
            return (ti, val)
        if addr == "/live/scene/get/name" and args:
            si = int(args[0])
            return (si, self.scene_names[si] if si < len(self.scene_names) else None)
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")


@pytest.mark.asyncio
async def test_snapshot_basic_session() -> None:
    osc = FakeOSC(
        tempo=125.0, sig_num=4, sig_den=4,
        track_names=["Drums", "Bass"],
        scene_names=["Intro"],
        per_track={0: {"mute": 1}, 1: {"solo": 1}},
    )
    snap = await snapshot_song_state(osc)
    assert snap.tempo == 125.0
    assert snap.time_signature == "4/4"
    assert snap.num_tracks == 2
    assert snap.num_scenes == 1
    assert len(snap.tracks) == 2
    assert snap.tracks[0].name == "Drums"
    assert snap.tracks[0].mute is True
    assert snap.tracks[1].solo is True
    assert len(snap.scenes) == 1
    assert snap.scenes[0].name == "Intro"


@pytest.mark.asyncio
async def test_snapshot_handles_per_field_read_failure_gracefully() -> None:
    """Tempo read fails → tempo is None; rest of snapshot still populates."""
    osc = FakeOSC(
        track_names=["A"],
        raise_on_addr={"/live/song/get/tempo"},
    )
    snap = await snapshot_song_state(osc)
    assert snap.tempo is None
    assert snap.num_tracks == 1


@pytest.mark.asyncio
async def test_snapshot_handles_missing_scene_name_endpoint() -> None:
    """If the bulk scene names endpoint fails, fall back to per-scene reads."""
    osc = FakeOSC(
        scene_names=["A", "B", "C"],
        raise_on_addr={"/live/song/get/scenes/name"},
    )
    snap = await snapshot_song_state(osc)
    assert snap.num_scenes == 3
    assert [s.name for s in snap.scenes] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_snapshot_empty_session() -> None:
    """An empty Live set should produce a clean empty snapshot."""
    osc = FakeOSC(track_names=[], scene_names=[])
    snap = await snapshot_song_state(osc)
    assert snap.num_tracks == 0
    assert snap.num_scenes == 0
    assert snap.tracks == ()
    assert snap.scenes == ()


# ---------------------------------------------------------------------------
# diff_state — pure-function tests
# ---------------------------------------------------------------------------


def _t(index: int, name: str, **overrides: Any) -> TrackSnapshot:
    """Helper: build a TrackSnapshot with defaults."""
    return TrackSnapshot(
        index=index, name=name,
        mute=overrides.get("mute", False),
        solo=overrides.get("solo", False),
        arm=overrides.get("arm", False),
        color_index=overrides.get("color_index", 0),
    )


def _s(index: int, name: str | None) -> SceneSnapshot:
    return SceneSnapshot(index=index, name=name)


def _snap(tempo: float = 120.0, tracks: tuple = (), scenes: tuple = (),
          time_signature: str = "4/4") -> SongStateSnapshot:
    return SongStateSnapshot(
        tempo=tempo, time_signature=time_signature,
        num_tracks=len(tracks), num_scenes=len(scenes),
        tracks=tracks, scenes=scenes,
    )


def test_diff_identical_snapshots_reports_no_change() -> None:
    a = _snap(tracks=(_t(0, "A"), _t(1, "B")))
    b = _snap(tracks=(_t(0, "A"), _t(1, "B")))
    d = diff_state(a, b)
    assert d["changed"] is False
    assert d["song"] == {}
    assert d["tracks"]["added"] == []
    assert d["tracks"]["removed"] == []
    assert d["tracks"]["modified"] == []


def test_diff_tempo_change_surfaces_in_song_block() -> None:
    a = _snap(tempo=120.0)
    b = _snap(tempo=140.0)
    d = diff_state(a, b)
    assert d["changed"] is True
    assert d["song"]["tempo"] == (120.0, 140.0)


def test_diff_time_signature_change() -> None:
    a = _snap(time_signature="4/4")
    b = _snap(time_signature="3/4")
    d = diff_state(a, b)
    assert d["song"]["time_signature"] == ("4/4", "3/4")


def test_diff_track_added() -> None:
    a = _snap(tracks=(_t(0, "A"),))
    b = _snap(tracks=(_t(0, "A"), _t(1, "B")))
    d = diff_state(a, b)
    assert d["tracks"]["added"] == [_t(1, "B").to_dict()]
    assert d["tracks"]["removed"] == []
    assert d["tracks"]["modified"] == []


def test_diff_track_removed() -> None:
    a = _snap(tracks=(_t(0, "A"), _t(1, "B")))
    b = _snap(tracks=(_t(0, "A"),))
    d = diff_state(a, b)
    assert d["tracks"]["removed"] == [{"index": 1, "name": "B"}]
    assert d["tracks"]["added"] == []


def test_diff_track_renamed_is_modified_not_added_removed() -> None:
    a = _snap(tracks=(_t(0, "OldName"),))
    b = _snap(tracks=(_t(0, "NewName"),))
    d = diff_state(a, b)
    assert d["tracks"]["modified"] == [
        {"index": 0, "fields": {"name": ("OldName", "NewName")}},
    ]
    assert d["tracks"]["added"] == []
    assert d["tracks"]["removed"] == []


def test_diff_track_mute_solo_arm_each_surface_separately() -> None:
    a = _snap(tracks=(_t(0, "A"),))
    b = _snap(tracks=(_t(0, "A", mute=True, solo=True, arm=True),))
    d = diff_state(a, b)
    mods = d["tracks"]["modified"]
    assert len(mods) == 1
    assert mods[0]["fields"] == {
        "mute": (False, True),
        "solo": (False, True),
        "arm": (False, True),
    }


def test_diff_scene_added() -> None:
    a = _snap(scenes=(_s(0, "Intro"),))
    b = _snap(scenes=(_s(0, "Intro"), _s(1, "Verse")))
    d = diff_state(a, b)
    assert d["scenes"]["added"] == [_s(1, "Verse").to_dict()]


def test_diff_scene_renamed() -> None:
    a = _snap(scenes=(_s(0, "Intro"),))
    b = _snap(scenes=(_s(0, "Opener"),))
    d = diff_state(a, b)
    assert d["scenes"]["renamed"] == [{"index": 0, "from": "Intro", "to": "Opener"}]


def test_diff_complex_multi_change() -> None:
    """One snapshot with several simultaneous changes — verify each lands
    in the right bucket."""
    a = _snap(
        tempo=120.0,
        tracks=(_t(0, "Drums"), _t(1, "Bass", mute=True), _t(2, "Gtr")),
        scenes=(_s(0, "Intro"),),
    )
    b = _snap(
        tempo=140.0,
        tracks=(_t(0, "Drums"), _t(1, "Bass"), _t(3, "New")),  # track 1 unmuted, track 2 removed, track 3 added
        scenes=(_s(0, "Opening"), _s(1, "Verse")),  # rename + add
    )
    d = diff_state(a, b)
    assert d["changed"] is True
    assert d["song"]["tempo"] == (120.0, 140.0)
    # num_tracks unchanged (3 = 3) so it's NOT in the song diff — same
    # count but different members surface via tracks.added/removed.
    assert "num_tracks" not in d["song"]
    assert d["song"]["num_scenes"] == (1, 2)
    added_indices = {t["index"] for t in d["tracks"]["added"]}
    removed_indices = {t["index"] for t in d["tracks"]["removed"]}
    modified_indices = {t["index"] for t in d["tracks"]["modified"]}
    assert added_indices == {3}
    assert removed_indices == {2}
    assert modified_indices == {1}  # mute toggle
    # Scene 0 renamed, scene 1 added.
    assert d["scenes"]["added"] == [_s(1, "Verse").to_dict()]
    assert d["scenes"]["renamed"] == [{"index": 0, "from": "Intro", "to": "Opening"}]
