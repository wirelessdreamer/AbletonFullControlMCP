"""song_transpose: per-clip in-place transpose, then bounce once.

For every audio arrangement clip we snapshot ``(warping, warp_mode,
pitch_coarse, pitch_fine)``, set warping=True + warp_mode=Complex Pro, and
add the semitone delta to ``pitch_coarse`` (so a clip the user pre-tuned
rides along instead of being reset). For every MIDI arrangement clip we
snapshot every note and rewrite them shifted by ``delta`` semitones. Then
we bounce the song. Then — in a ``finally`` block — we restore every
snapshot in reverse, wrapping each restore call in its own try/except so
one failure doesn't leave the rest unrestored.

Live's OSC layer is read-only for arrangement clips; the bridge handlers
in ``live_remote_script/AbletonFullControlBridge/handlers/clips.py``
(``clip.get_arrangement_pitch_state`` etc.) carry the writes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..bounce.resampling import bounce_song_via_resampling
from ..bridge_client import get_bridge_client
from ..osc_client import get_client
from .analyze import analyze_song
from .key import normalize_key, semitone_delta

log = logging.getLogger(__name__)

# Ableton's pitch_coarse is bounded [-48, 48] (4-octave range either way).
# Restored snapshots are obviously already in range, but the post-delta
# value can land outside if the user pre-tuned a clip aggressively. Clamp
# rather than raise — the rest of the transpose still produces a usable
# bounce, the affected clip just doesn't get the full shift.
PITCH_COARSE_MIN = -48
PITCH_COARSE_MAX = 48

# 5 = Complex Pro in Live 11. The plan's "high quality, not chipmunk"
# requirement maps directly to this mode.
WARP_MODE_COMPLEX_PRO = 5


@dataclass
class _AudioSnapshot:
    track_index: int
    clip_index: int
    warping: bool
    warp_mode: int
    pitch_coarse: int
    pitch_fine: int
    # "arrangement" or "session". Determines which bridge handlers we call
    # to mutate + restore. session uses slot_index instead of clip_index in
    # the bridge call but we keep the field name `clip_index` here so the
    # downstream dataflow stays uniform.
    scope: str = "arrangement"


@dataclass
class _MidiSnapshot:
    track_index: int
    clip_index: int
    notes: list[dict[str, Any]]
    scope: str = "arrangement"


@dataclass
class _State:
    audio: list[_AudioSnapshot] = field(default_factory=list)
    midi: list[_MidiSnapshot] = field(default_factory=list)
    clamped: list[dict[str, Any]] = field(default_factory=list)


async def _arrangement_clip_count(track_index: int) -> int:
    """Count arrangement clips on a track via the bridge (direct LOM).

    AbletonOSC's ``/live/track/get/arrangement_clips/*`` replies are empty
    for clips that landed after AbletonOSC's listeners were attached
    (typical of user-dragged-after-startup or programmatic clips). The
    bridge handler ``clip.list_arrangement_clips`` reads
    ``track.arrangement_clips`` directly via LOM and is always current.

    See the "Discovered blocker" section of
    ``~/.claude/plans/next-feature-i-want-expressive-pnueli.md`` for the
    full investigation.
    """
    bridge = get_bridge_client()
    info = await bridge.call(
        "clip.list_arrangement_clips", track_index=int(track_index)
    )
    return len(info.get("clips") or [])


async def _num_tracks() -> int:
    client = await get_client()
    return int((await client.request("/live/song/get/num_tracks"))[0])


async def _list_session_clip_slot_indices(track_index: int) -> list[int]:
    """Return slot indices that have a clip (skipping empty slots).

    Uses the bridge's ``clip.list_session_clip_slots`` handler. Returns an
    empty list if the bridge call fails or the handler isn't available
    (caller can decide whether to surface that as an error or proceed
    without session-view handling).
    """
    bridge = get_bridge_client()
    try:
        reply = await bridge.call(
            "clip.list_session_clip_slots", track_index=int(track_index)
        )
    except Exception as exc:
        log.warning(
            "list_session_clip_slots failed for track %d: %r; "
            "session clips on this track will not be transposed",
            track_index, exc,
        )
        return []
    slots = reply.get("slots") if isinstance(reply, dict) else None
    if not isinstance(slots, list):
        return []
    return [int(s["slot_index"]) for s in slots if not s.get("is_empty")]


async def _shift_one_clip(
    state: _State,
    delta: int,
    track_index: int,
    clip_index: int,
    scope: str,
) -> None:
    """Snapshot + mutate a single clip in either arrangement or session scope.

    ``scope`` selects which bridge handlers to call. The snapshot is
    appended to ``state`` so the caller's ``_restore`` can replay it.
    """
    bridge = get_bridge_client()
    if scope == "session":
        getp_op = "clip.get_session_pitch_state"
        getn_op = "clip.get_session_notes"
        setn_op = "clip.set_session_notes"
        setw_op = "clip.set_session_warp"
        setm_op = "clip.set_session_warp_mode"
        setp_op = "clip.set_session_pitch"
        kw = {"track_index": track_index, "slot_index": clip_index}
    else:
        getp_op = "clip.get_arrangement_pitch_state"
        getn_op = "clip.get_arrangement_notes"
        setn_op = "clip.set_arrangement_notes"
        setw_op = "clip.set_arrangement_warp"
        setm_op = "clip.set_arrangement_warp_mode"
        setp_op = "clip.set_arrangement_pitch"
        kw = {"track_index": track_index, "clip_index": clip_index}

    snap_dict = await bridge.call(getp_op, **kw)
    if snap_dict.get("is_empty"):
        return  # session slot was empty between list and snapshot — skip

    if snap_dict.get("is_midi_clip"):
        notes_reply = await bridge.call(getn_op, **kw)
        original_notes = list(notes_reply.get("notes") or [])
        state.midi.append(_MidiSnapshot(track_index, clip_index, original_notes, scope))
        shifted = [
            {
                **n,
                "pitch": max(0, min(127, int(n["pitch"]) + delta)),
            }
            for n in original_notes
        ]
        await bridge.call(setn_op, notes=shifted, **kw)
    else:
        snap = _AudioSnapshot(
            track_index=track_index,
            clip_index=clip_index,
            warping=bool(snap_dict.get("warping") or False),
            warp_mode=int(snap_dict.get("warp_mode") or 0),
            pitch_coarse=int(snap_dict.get("pitch_coarse") or 0),
            pitch_fine=int(snap_dict.get("pitch_fine") or 0),
            scope=scope,
        )
        state.audio.append(snap)
        target_coarse = snap.pitch_coarse + delta
        clamped = max(PITCH_COARSE_MIN, min(PITCH_COARSE_MAX, target_coarse))
        if clamped != target_coarse:
            state.clamped.append({
                "track_index": track_index, "clip_index": clip_index,
                "scope": scope,
                "wanted": target_coarse, "applied": clamped,
            })
        # Order: warping must be on before warp_mode/pitch take effect,
        # per docs/LIVE_API_GOTCHAS.md.
        await bridge.call(setw_op, value=True, **kw)
        await bridge.call(setm_op, mode=WARP_MODE_COMPLEX_PRO, **kw)
        await bridge.call(setp_op, coarse=clamped, fine=snap.pitch_fine, **kw)


async def _shift_arrangement_clips(delta: int, *, include_session: bool = True) -> _State:
    """Walk every clip (arrangement + optionally session) and apply the
    shift. Returns the snapshot state needed to restore.

    The function name kept its original "arrangement" prefix for backwards
    compatibility with the test surface, but it now covers both scopes
    when ``include_session=True`` (the default since session clips are
    the common case for Session-view-driven sets).
    """
    state = _State()
    n_tracks = await _num_tracks()

    for ti in range(n_tracks):
        # Arrangement clips first.
        n_clips = await _arrangement_clip_count(ti)
        for ci in range(n_clips):
            await _shift_one_clip(state, delta, ti, ci, "arrangement")
        # Then session clip slots, if enabled. The bridge handler can fail
        # on pre-1.2.0 bridges; we treat that as "no session clips" rather
        # than aborting the whole transpose.
        if include_session:
            slot_indices = await _list_session_clip_slot_indices(ti)
            for si in slot_indices:
                await _shift_one_clip(state, delta, ti, si, "session")
    return state


async def _restore(state: _State) -> list[dict[str, Any]]:
    """Replay snapshots to undo the in-place mutations. Each call is
    isolated so a partial failure still attempts the rest. Returns the
    list of restoration errors (empty when everything restored cleanly).

    Each snapshot's ``scope`` field decides whether we call the arrangement
    or session bridge handlers + how the second clip-identifier kwarg is
    named (``clip_index`` vs ``slot_index``).
    """
    bridge = get_bridge_client()
    errors: list[dict[str, Any]] = []

    def _kw(snap: Any) -> dict[str, Any]:
        if snap.scope == "session":
            return {"track_index": snap.track_index, "slot_index": snap.clip_index}
        return {"track_index": snap.track_index, "clip_index": snap.clip_index}

    def _ops(scope: str) -> tuple[str, str, str, str]:
        if scope == "session":
            return ("clip.set_session_pitch", "clip.set_session_warp_mode",
                    "clip.set_session_warp", "clip.set_session_notes")
        return ("clip.set_arrangement_pitch", "clip.set_arrangement_warp_mode",
                "clip.set_arrangement_warp", "clip.set_arrangement_notes")

    for snap in reversed(state.audio):
        set_pitch_op, set_mode_op, set_warp_op, _ = _ops(snap.scope)
        kw = _kw(snap)
        try:
            await bridge.call(set_pitch_op, coarse=snap.pitch_coarse,
                              fine=snap.pitch_fine, **kw)
            await bridge.call(set_mode_op, mode=snap.warp_mode, **kw)
            await bridge.call(set_warp_op, value=snap.warping, **kw)
        except Exception as exc:
            errors.append({
                "track_index": snap.track_index, "clip_index": snap.clip_index,
                "scope": snap.scope, "kind": "audio", "error": repr(exc),
            })

    for snap in reversed(state.midi):
        _, _, _, set_notes_op = _ops(snap.scope)
        kw = _kw(snap)
        try:
            await bridge.call(set_notes_op, notes=snap.notes, **kw)
        except Exception as exc:
            errors.append({
                "track_index": snap.track_index, "clip_index": snap.clip_index,
                "scope": snap.scope, "kind": "midi", "error": repr(exc),
            })

    return errors


async def transpose_song(
    target_key: str,
    source_key: str | None = None,
    direction: str = "auto",
    output_path: str | Path | None = None,
    bounce_tail_sec: float = 1.0,
    include_session: bool = True,
) -> dict[str, Any]:
    """Transpose the active set to ``target_key`` and bounce the result to a wav.

    Walks every audio + MIDI clip in both arrangement view and (by default)
    session view, snapshots their pitch/warp/notes state, mutates each by
    the computed semitone delta, runs a single bounce, then restores every
    snapshot in a ``finally`` block. The source session is bit-identical
    to its pre-call state after a successful (or failed) run, modulo
    ``restore_errors`` for individual clips that fail to restore.

    If ``source_key`` is omitted, runs ``analyze_song`` to detect the key
    via librosa chroma. The detected key is always surfaced in the result
    so the caller can re-call with an explicit override if the auto-detect
    was wrong.

    ``include_session=True`` (default) walks session clip slots in addition
    to arrangement-view clips. Required when the session is driven from
    Session view. Set False for arrangement-only sessions to skip the
    extra bridge calls (~5 ms/track for the slot listing).

    The Live session is mutated in-place during the bounce window and
    restored afterwards. A partial-failure event is reported in
    ``restore_errors`` rather than raising, so the caller can decide
    whether to manually undo or keep the current state.
    """
    target_key = normalize_key(target_key)

    detected_key: str | None = None
    used_key: str
    length_sec: float | None = None

    # If the caller provided source_key we can short-circuit a noop without
    # any I/O. If they didn't, we must analyze first (and that does I/O).
    if source_key is not None:
        used_key = normalize_key(source_key)
        delta_preview = semitone_delta(used_key, target_key, direction=direction)  # type: ignore[arg-type]
        if delta_preview == 0:
            return {
                "status": "noop",
                "reason": "source_key == target_key (after normalization)",
                "source_key_used": used_key,
                "detected_key": None,
                "target_key": target_key,
                "semitone_delta": 0,
            }
        # Need length to drive the bounce duration.
        client = await get_client()
        tempo = float((await client.request("/live/song/get/tempo"))[0])
        length_beats = float((await client.request("/live/song/get/song_length"))[0])
        length_sec = length_beats * 60.0 / tempo if tempo > 0 else 0.0
    else:
        analyze = await analyze_song()
        if analyze.get("status") != "ok":
            return {
                "status": "error",
                "stage": "analyze",
                "error": analyze.get("error") or "song_analyze failed",
                "detail": analyze,
            }
        detected_key = analyze["detected_key"]
        used_key = detected_key
        length_sec = float(analyze["length_sec"])

    delta = semitone_delta(used_key, target_key, direction=direction)  # type: ignore[arg-type]

    if delta == 0:
        return {
            "status": "noop",
            "reason": "source_key == target_key (after normalization)",
            "source_key_used": used_key,
            "detected_key": detected_key,
            "target_key": target_key,
            "semitone_delta": 0,
        }

    if length_sec is None or length_sec <= 0:
        return {
            "status": "error",
            "stage": "length",
            "error": f"invalid song length {length_sec}",
        }

    if output_path is None:
        output_path = (
            Path("data/song_flow")
            / time.strftime("%Y%m%d-%H%M%S")
            / f"transposed_{target_key.replace('#','sharp')}.wav"
        )
    out_path = Path(output_path)

    state = _State()
    bounce_result: dict[str, Any] | None = None
    transpose_error: str | None = None
    try:
        state = await _shift_arrangement_clips(delta, include_session=include_session)
        bounce_result = await bounce_song_via_resampling(
            str(out_path), duration_sec=length_sec + bounce_tail_sec,
        )
    except Exception as exc:
        log.exception("transpose_song mid-flight failure")
        transpose_error = repr(exc)
    finally:
        restore_errors = await _restore(state)

    if transpose_error is not None:
        return {
            "status": "error",
            "stage": "transpose_or_bounce",
            "error": transpose_error,
            "audio_clips_transposed": len(state.audio),
            "midi_clips_transposed": len(state.midi),
            "restore_errors": restore_errors,
        }

    if not (bounce_result and bounce_result.get("copied")):
        return {
            "status": "error",
            "stage": "bounce",
            "error": "bounce did not produce an output wav",
            "bounce_result": bounce_result,
            "restore_errors": restore_errors,
        }

    return {
        "status": "ok",
        "output_path": str(out_path.resolve()),
        "source_key_used": used_key,
        "detected_key": detected_key,
        "target_key": target_key,
        "semitone_delta": delta,
        "audio_clips_transposed": len(state.audio),
        "midi_clips_transposed": len(state.midi),
        "clamped_clips": state.clamped,
        "restore_errors": restore_errors,
        "duration_sec": length_sec,
    }
