"""Live session state diffing.

Snapshot a small set of "cares about" properties from a Live session,
then diff two snapshots to produce a compact delta. The intent is to
let tools that mutate Live state answer the question "what did this
call actually change?" without forcing the caller to re-query the
whole session.

This module is **opt-in infrastructure**. No tool calls it
automatically. Callers wrap their mutating logic in::

    before = await snapshot_song_state()
    result = await some_mutating_call()
    after = await snapshot_song_state()
    delta = diff_state(before, after)

and surface ``delta`` alongside ``result``.

Two design constraints shape what we snapshot:

1. **Cheap to gather** — one full snapshot must be sub-second on
   reasonable sessions. We read shallow track + scene properties via
   OSC and don't enumerate clips or devices (those are O(tracks ×
   things-per-track) and would dominate). Callers that need device-
   or clip-level diffs should layer on top.
2. **Mutation-friendly** — the snapshot captures what tools actually
   change: tempo, time signature, track count + names + mute/solo/arm,
   scene count + names. Things tools don't change (e.g. song time
   while playing) are skipped to keep the diff stable.

The diff format is intentionally simple — a flat dict of changed-field
descriptions, suitable for surfacing to an LLM client. See
:func:`diff_state` for the shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackSnapshot:
    """Snapshot of one track's mutable state. ``index`` is captured so a
    diff can report stable identity even if names change."""

    index: int
    name: str | None
    mute: bool
    solo: bool
    arm: bool
    color_index: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "name": self.name,
            "mute": self.mute, "solo": self.solo, "arm": self.arm,
            "color_index": self.color_index,
        }


@dataclass(frozen=True)
class SceneSnapshot:
    index: int
    name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "name": self.name}


@dataclass(frozen=True)
class SongStateSnapshot:
    """Snapshot of song-level + per-track + per-scene state.

    Frozen so multiple snapshots can be passed around as values without
    aliasing surprises.
    """

    tempo: float | None
    time_signature: str | None
    num_tracks: int
    num_scenes: int
    tracks: tuple[TrackSnapshot, ...] = field(default_factory=tuple)
    scenes: tuple[SceneSnapshot, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tempo": self.tempo,
            "time_signature": self.time_signature,
            "num_tracks": self.num_tracks,
            "num_scenes": self.num_scenes,
            "tracks": [t.to_dict() for t in self.tracks],
            "scenes": [s.to_dict() for s in self.scenes],
        }


async def _safe_value(client: Any, addr: str, *args: Any, cast: type | None = None) -> Any:
    """Read one OSC value, returning None on any failure. Used so a
    single bad property doesn't break the whole snapshot."""
    try:
        reply = await client.request(addr, *args)
    except Exception:
        return None
    if not reply:
        return None
    # Track-level replies are (track_id, value); song-level are (value,).
    val = reply[1] if (len(args) >= 1 and len(reply) > 1) else reply[0]
    if cast is None or val is None:
        return val
    try:
        return cast(val)
    except (TypeError, ValueError):
        return None


async def snapshot_song_state(client: Any = None) -> SongStateSnapshot:
    """Capture the current song state. Cheap — one shallow read per field.

    Pass an OSC client explicitly for testing; otherwise the function
    fetches the process-wide singleton. Errors on individual reads are
    swallowed and surface as ``None`` in the resulting snapshot — a
    diff against a None-on-both-sides field reports no change.
    """
    if client is None:
        from .osc_client import get_client
        client = await get_client()

    # Song-level fields.
    tempo = await _safe_value(client, "/live/song/get/tempo", cast=float)
    sig_num = await _safe_value(client, "/live/song/get/signature_numerator")
    sig_den = await _safe_value(client, "/live/song/get/signature_denominator")
    time_signature = (
        f"{int(sig_num)}/{int(sig_den)}"
        if sig_num is not None and sig_den is not None else None
    )
    num_tracks_raw = await _safe_value(client, "/live/song/get/num_tracks", cast=int)
    num_scenes_raw = await _safe_value(client, "/live/song/get/num_scenes", cast=int)
    num_tracks = int(num_tracks_raw) if num_tracks_raw is not None else 0
    num_scenes = int(num_scenes_raw) if num_scenes_raw is not None else 0

    # Per-track names — one bulk read.
    try:
        names_reply = await client.request("/live/song/get/track_names")
        track_names: list[str | None] = [str(n) for n in names_reply]
    except Exception:
        track_names = [None] * num_tracks

    # Per-track state. We do an OSC read per (track, field) which scales
    # O(tracks * 4). On a 30-track session that's ~120 round-trips —
    # acceptable for a snapshot but the implementation is naive on
    # purpose so it's easy to read.
    tracks: list[TrackSnapshot] = []
    for ti in range(num_tracks):
        tracks.append(TrackSnapshot(
            index=ti,
            name=track_names[ti] if ti < len(track_names) else None,
            mute=bool(await _safe_value(client, "/live/track/get/mute", ti) or False),
            solo=bool(await _safe_value(client, "/live/track/get/solo", ti) or False),
            arm=bool(await _safe_value(client, "/live/track/get/arm", ti) or False),
            color_index=_cast_int_or_none(
                await _safe_value(client, "/live/track/get/color_index", ti)
            ),
        ))

    # Per-scene names — one bulk read if AbletonOSC supports it; else
    # per-scene fallback.
    scenes: list[SceneSnapshot] = []
    scene_names: list[str | None]
    try:
        names_reply = await client.request("/live/song/get/scenes/name")
        scene_names = [str(n) for n in names_reply]
    except Exception:
        scene_names = []
        for si in range(num_scenes):
            try:
                reply = await client.request("/live/scene/get/name", si)
                scene_names.append(reply[1] if len(reply) > 1 else None)
            except Exception:
                scene_names.append(None)
    for si in range(num_scenes):
        scenes.append(SceneSnapshot(
            index=si,
            name=scene_names[si] if si < len(scene_names) else None,
        ))

    return SongStateSnapshot(
        tempo=tempo,
        time_signature=time_signature,
        num_tracks=num_tracks,
        num_scenes=num_scenes,
        tracks=tuple(tracks),
        scenes=tuple(scenes),
    )


def _cast_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def diff_state(
    before: SongStateSnapshot, after: SongStateSnapshot,
) -> dict[str, Any]:
    """Return a flat delta describing what changed between two snapshots.

    Shape::

        {
            "changed": bool,                # True iff any field differs
            "song": { "tempo": (before, after), ... },  # song-level field diffs
            "tracks": {
                "added": [TrackSnapshot.to_dict(), ...],
                "removed": [{"index": int, "name": str}, ...],
                "modified": [{"index": int, "fields": {"mute": (False, True), ...}}, ...]
            },
            "scenes": { "added": [...], "removed": [...], "renamed": [...] }
        }

    Empty sub-collections are still included so callers don't need to
    check for missing keys.
    """
    song_diff: dict[str, tuple[Any, Any]] = {}
    if before.tempo != after.tempo:
        song_diff["tempo"] = (before.tempo, after.tempo)
    if before.time_signature != after.time_signature:
        song_diff["time_signature"] = (before.time_signature, after.time_signature)
    if before.num_tracks != after.num_tracks:
        song_diff["num_tracks"] = (before.num_tracks, after.num_tracks)
    if before.num_scenes != after.num_scenes:
        song_diff["num_scenes"] = (before.num_scenes, after.num_scenes)

    tracks_diff = _diff_tracks(before.tracks, after.tracks)
    scenes_diff = _diff_scenes(before.scenes, after.scenes)

    changed = bool(
        song_diff
        or tracks_diff["added"] or tracks_diff["removed"] or tracks_diff["modified"]
        or scenes_diff["added"] or scenes_diff["removed"] or scenes_diff["renamed"]
    )

    return {
        "changed": changed,
        "song": song_diff,
        "tracks": tracks_diff,
        "scenes": scenes_diff,
    }


def _diff_tracks(
    before: tuple[TrackSnapshot, ...], after: tuple[TrackSnapshot, ...],
) -> dict[str, list[Any]]:
    """Diff two track lists by index, surfacing added/removed/modified.

    Track identity is by index (the LOM doesn't expose stable IDs across
    a session). Renaming a track is reported as a `modified` row with
    the `name` field changed. Deleting + adding tracks at indices
    naturally surface as `removed` + `added`.
    """
    before_by_idx = {t.index: t for t in before}
    after_by_idx = {t.index: t for t in after}

    added = [
        after_by_idx[i].to_dict()
        for i in sorted(set(after_by_idx) - set(before_by_idx))
    ]
    removed = [
        {"index": i, "name": before_by_idx[i].name}
        for i in sorted(set(before_by_idx) - set(after_by_idx))
    ]
    modified = []
    for i in sorted(set(before_by_idx) & set(after_by_idx)):
        b = before_by_idx[i]
        a = after_by_idx[i]
        field_changes: dict[str, tuple[Any, Any]] = {}
        for fname in ("name", "mute", "solo", "arm", "color_index"):
            bv = getattr(b, fname)
            av = getattr(a, fname)
            if bv != av:
                field_changes[fname] = (bv, av)
        if field_changes:
            modified.append({"index": i, "fields": field_changes})
    return {"added": added, "removed": removed, "modified": modified}


def _diff_scenes(
    before: tuple[SceneSnapshot, ...], after: tuple[SceneSnapshot, ...],
) -> dict[str, list[Any]]:
    """Diff two scene lists by index. Renames go into ``renamed``."""
    before_by_idx = {s.index: s for s in before}
    after_by_idx = {s.index: s for s in after}
    added = [
        after_by_idx[i].to_dict()
        for i in sorted(set(after_by_idx) - set(before_by_idx))
    ]
    removed = [
        {"index": i, "name": before_by_idx[i].name}
        for i in sorted(set(before_by_idx) - set(after_by_idx))
    ]
    renamed = []
    for i in sorted(set(before_by_idx) & set(after_by_idx)):
        if before_by_idx[i].name != after_by_idx[i].name:
            renamed.append({
                "index": i,
                "from": before_by_idx[i].name,
                "to": after_by_idx[i].name,
            })
    return {"added": added, "removed": removed, "renamed": renamed}
