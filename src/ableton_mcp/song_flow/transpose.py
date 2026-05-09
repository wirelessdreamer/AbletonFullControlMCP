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


@dataclass
class _MidiSnapshot:
    track_index: int
    clip_index: int
    notes: list[dict[str, Any]]


@dataclass
class _State:
    audio: list[_AudioSnapshot] = field(default_factory=list)
    midi: list[_MidiSnapshot] = field(default_factory=list)
    clamped: list[dict[str, Any]] = field(default_factory=list)


async def _arrangement_clip_count(track_index: int) -> int:
    """Count arrangement clips on a track via OSC.

    Reuses the same /live/track/get/arrangement_clips/length address that
    ``arrangement_clips_list`` uses, but only counts pairs — cheaper when
    we don't need names/lengths.
    """
    client = await get_client()
    reply = await client.request(
        "/live/track/get/arrangement_clips/length", int(track_index)
    )
    # Reply shape: (track_id, idx_0, len_0, idx_1, len_1, ...). Count pairs.
    return max(0, (len(reply) - 1) // 2)


async def _num_tracks() -> int:
    client = await get_client()
    return int((await client.request("/live/song/get/num_tracks"))[0])


async def _shift_arrangement_clips(delta: int) -> _State:
    """Walk every arrangement clip and apply the shift. Returns the
    snapshot state needed to restore.
    """
    bridge = get_bridge_client()
    state = _State()
    n_tracks = await _num_tracks()

    for ti in range(n_tracks):
        n_clips = await _arrangement_clip_count(ti)
        for ci in range(n_clips):
            snap_dict = await bridge.call(
                "clip.get_arrangement_pitch_state",
                track_index=ti, clip_index=ci,
            )
            if snap_dict.get("is_midi_clip"):
                notes_reply = await bridge.call(
                    "clip.get_arrangement_notes",
                    track_index=ti, clip_index=ci,
                )
                original_notes = list(notes_reply.get("notes") or [])
                state.midi.append(_MidiSnapshot(ti, ci, original_notes))
                shifted = [
                    {
                        **n,
                        "pitch": max(0, min(127, int(n["pitch"]) + delta)),
                    }
                    for n in original_notes
                ]
                await bridge.call(
                    "clip.set_arrangement_notes",
                    track_index=ti, clip_index=ci, notes=shifted,
                )
            else:
                snap = _AudioSnapshot(
                    track_index=ti,
                    clip_index=ci,
                    warping=bool(snap_dict.get("warping") or False),
                    warp_mode=int(snap_dict.get("warp_mode") or 0),
                    pitch_coarse=int(snap_dict.get("pitch_coarse") or 0),
                    pitch_fine=int(snap_dict.get("pitch_fine") or 0),
                )
                state.audio.append(snap)
                target_coarse = snap.pitch_coarse + delta
                clamped = max(PITCH_COARSE_MIN, min(PITCH_COARSE_MAX, target_coarse))
                if clamped != target_coarse:
                    state.clamped.append({
                        "track_index": ti, "clip_index": ci,
                        "wanted": target_coarse, "applied": clamped,
                    })
                # Order: warping must be on before warp_mode/pitch take
                # effect, per docs/LIVE_API_GOTCHAS.md.
                await bridge.call(
                    "clip.set_arrangement_warp",
                    track_index=ti, clip_index=ci, value=True,
                )
                await bridge.call(
                    "clip.set_arrangement_warp_mode",
                    track_index=ti, clip_index=ci, mode=WARP_MODE_COMPLEX_PRO,
                )
                await bridge.call(
                    "clip.set_arrangement_pitch",
                    track_index=ti, clip_index=ci,
                    coarse=clamped, fine=snap.pitch_fine,
                )
    return state


async def _restore(state: _State) -> list[dict[str, Any]]:
    """Replay snapshots to undo the in-place mutations. Each call is
    isolated so a partial failure still attempts the rest. Returns the
    list of restoration errors (empty when everything restored cleanly).
    """
    bridge = get_bridge_client()
    errors: list[dict[str, Any]] = []

    for snap in reversed(state.audio):
        try:
            await bridge.call(
                "clip.set_arrangement_pitch",
                track_index=snap.track_index, clip_index=snap.clip_index,
                coarse=snap.pitch_coarse, fine=snap.pitch_fine,
            )
            await bridge.call(
                "clip.set_arrangement_warp_mode",
                track_index=snap.track_index, clip_index=snap.clip_index,
                mode=snap.warp_mode,
            )
            await bridge.call(
                "clip.set_arrangement_warp",
                track_index=snap.track_index, clip_index=snap.clip_index,
                value=snap.warping,
            )
        except Exception as exc:
            errors.append({
                "track_index": snap.track_index, "clip_index": snap.clip_index,
                "kind": "audio", "error": repr(exc),
            })

    for snap in reversed(state.midi):
        try:
            await bridge.call(
                "clip.set_arrangement_notes",
                track_index=snap.track_index, clip_index=snap.clip_index,
                notes=snap.notes,
            )
        except Exception as exc:
            errors.append({
                "track_index": snap.track_index, "clip_index": snap.clip_index,
                "kind": "midi", "error": repr(exc),
            })

    return errors


async def transpose_song(
    target_key: str,
    source_key: str | None = None,
    direction: str = "auto",
    output_path: str | Path | None = None,
    bounce_tail_sec: float = 1.0,
) -> dict[str, Any]:
    """Transpose the active arrangement to ``target_key`` and bounce the
    result to a wav.

    If ``source_key`` is omitted, runs ``analyze_song`` to detect the key
    via librosa chroma. The detected key is always surfaced in the result
    so the caller can re-call with an explicit override if the auto-detect
    was wrong.

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
        state = await _shift_arrangement_clips(delta)
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
