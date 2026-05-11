"""Clip-level ops not exposed by AbletonOSC: consolidate / crop / reverse / session→arrangement copy.

Verified LOM signatures in Live 11.3.43 (do NOT guess these — see
``docs/LIVE_API_GOTCHAS.md`` for the full reference):

- ``Track.duplicate_clip_to_arrangement(clip, time)`` — first arg is the
  ``Clip`` object (``slot.clip``), NOT the ``ClipSlot`` and NOT the integer
  slot index. Wrong shapes raise ``TypeError`` silently. Returns the new
  arrangement ``Clip``.
- ``Track.create_midi_clip(start, end)`` — does NOT exist in Live 11.3. Do
  not use it as a fallback.
- ``Track.arrangement_clips`` — property, iterable of ``Clip`` objects.
- ``ClipSlot.duplicate_clip_to_arrangement`` — does NOT exist; only on Track.
- ``Clip.consolidate()`` and ``Clip.crop()`` — exist.
- ``Clip.reverse()`` — does NOT exist; UI-only command. Bridge falls back
  to a structured ``{supported: false}`` result with a workaround hint.
- ``Clip.add_new_notes(((p, t, d, v, m), ...))`` — bulk note-add API.
  Newer Lives have this; ``set_notes(...)`` is the older fallback.
"""

from __future__ import absolute_import


EXPORTS = (
    "consolidate",
    "crop",
    "reverse",
    "duplicate_to_arrangement",
    "arrangement_clip_info",
    "get_arrangement_pitch_state",
    "set_arrangement_warp",
    "set_arrangement_warp_mode",
    "set_arrangement_pitch",
    "get_arrangement_notes",
    "set_arrangement_notes",
    "create_arrangement_audio_clip",
    "list_arrangement_clips",
    # Session-view counterparts (slot-addressed instead of arrangement-index).
    # Used by song_flow.transpose_song(include_session=True) to walk session
    # clip slots in addition to arrangement-view clips.
    "get_session_pitch_state",
    "set_session_warp",
    "set_session_warp_mode",
    "set_session_pitch",
    "get_session_notes",
    "set_session_notes",
    "list_session_clip_slots",
    "_dir_track",
    "_probe_audio_clip_creation",
)


def arrangement_clip_info(c_instance, track_index=None, clip_index=0, **_):
    """Return file_path + start_time + length for an arrangement clip on a track.

    Used by the resampling bounce path: after Live records into an audio track,
    the arrangement clip's ``file_path`` points at the wav Live just wrote
    (typically ``<project>/Samples/Recorded/.../Audio_xxxx.wav``). We read it
    here and copy the file to the user's destination.
    """
    track = _track(int(track_index))
    clips = list(track.arrangement_clips)
    ci = int(clip_index)
    if ci < 0 or ci >= len(clips):
        raise ValueError(
            "clip_index %d out of range; track has %d arrangement clips"
            % (ci, len(clips))
        )
    clip = clips[ci]
    return {
        "track_index": int(track_index),
        "clip_index": ci,
        "file_path": getattr(clip, "file_path", None),
        "name": getattr(clip, "name", None),
        "start_time": float(getattr(clip, "start_time", 0.0)),
        "end_time": float(getattr(clip, "end_time", 0.0)),
        "length": float(getattr(clip, "length", 0.0)),
        "is_audio_clip": bool(getattr(clip, "is_audio_clip", False)),
    }


def _arrangement_clip(track_index, clip_index):
    """Resolve `(track_index, clip_index)` to a Clip on the arrangement timeline.

    Mirrors `_clip` (which targets the session clip slot) for the arrangement
    case. The new song-flow handlers below all use this.
    """
    track = _track(int(track_index))
    clips = list(track.arrangement_clips)
    ci = int(clip_index)
    if ci < 0 or ci >= len(clips):
        raise ValueError(
            "clip_index %d out of range; track has %d arrangement clips"
            % (ci, len(clips))
        )
    return clips[ci]


def get_arrangement_pitch_state(c_instance, track_index=None, clip_index=0, **_):
    """Snapshot warp + pitch + clip-type state for an arrangement clip.

    Used by the song-flow transpose path before mutating warp/pitch so the
    original state can be restored after the bounce. Audio-only fields return
    None on a MIDI clip (LOM does not expose warp/pitch on MIDI clips).
    """
    clip = _arrangement_clip(track_index, clip_index)
    is_midi = bool(getattr(clip, "is_midi_clip", False))
    return {
        "track_index": int(track_index),
        "clip_index": int(clip_index),
        "is_midi_clip": is_midi,
        "warping": None if is_midi else bool(getattr(clip, "warping", False)),
        "warp_mode": None if is_midi else int(getattr(clip, "warp_mode", 0) or 0),
        "pitch_coarse": None if is_midi else int(getattr(clip, "pitch_coarse", 0) or 0),
        "pitch_fine": None if is_midi else int(getattr(clip, "pitch_fine", 0) or 0),
    }


def set_arrangement_warp(c_instance, track_index=None, clip_index=0, value=True, **_):
    """Toggle warping on an audio arrangement clip."""
    clip = _arrangement_clip(track_index, clip_index)
    if bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("warp is audio-only; clip is MIDI")
    clip.warping = bool(value)
    return {"track_index": int(track_index), "clip_index": int(clip_index),
            "warping": bool(clip.warping)}


def set_arrangement_warp_mode(c_instance, track_index=None, clip_index=0, mode=5, **_):
    """Set warp mode on an audio arrangement clip.

    Mode integers (Live 11): 0=Beats, 1=Tones, 2=Texture, 3=Re-Pitch,
    4=Complex, 5=Complex Pro. The transpose flow uses 5 (Complex Pro) for
    pitch-preserving high-quality transposition.
    """
    clip = _arrangement_clip(track_index, clip_index)
    if bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("warp_mode is audio-only; clip is MIDI")
    clip.warp_mode = int(mode)
    return {"track_index": int(track_index), "clip_index": int(clip_index),
            "warp_mode": int(clip.warp_mode)}


def set_arrangement_pitch(c_instance, track_index=None, clip_index=0,
                          coarse=0, fine=0, **_):
    """Set pitch_coarse (semitones) and pitch_fine (cents) on an audio clip.

    LOM enforces pitch_coarse in [-48, 48] and pitch_fine in [-50, 50]; values
    outside that range raise. Caller is expected to clamp.
    """
    clip = _arrangement_clip(track_index, clip_index)
    if bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("pitch is audio-only; clip is MIDI")
    clip.pitch_coarse = int(coarse)
    clip.pitch_fine = int(fine)
    return {"track_index": int(track_index), "clip_index": int(clip_index),
            "pitch_coarse": int(clip.pitch_coarse),
            "pitch_fine": int(clip.pitch_fine)}


def get_arrangement_notes(c_instance, track_index=None, clip_index=0, **_):
    """Read all notes from a MIDI arrangement clip.

    Returns a list of {pitch, start, duration, velocity, mute} dicts. Uses
    the same fallback chain (`get_notes_extended` → `get_notes`) as the
    session-clip note read path in `duplicate_to_arrangement`.
    """
    clip = _arrangement_clip(track_index, clip_index)
    if not bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("notes are MIDI-only; clip is audio")
    length = float(getattr(clip, "length", 0.0))
    rows = []
    read_err = None
    try:
        for n in clip.get_notes_extended(0, 128, 0.0, length):
            rows.append({
                "pitch": int(n.pitch),
                "start": float(n.start_time),
                "duration": float(n.duration),
                "velocity": int(n.velocity),
                "mute": bool(n.mute),
            })
        return {"track_index": int(track_index), "clip_index": int(clip_index),
                "length": length, "notes": rows, "via": "get_notes_extended"}
    except (AttributeError, TypeError) as exc:
        read_err = exc
    try:
        for (p, t, d, v, m) in clip.get_notes(0.0, 0, length, 128):
            rows.append({
                "pitch": int(p), "start": float(t), "duration": float(d),
                "velocity": int(v), "mute": bool(m),
            })
        return {"track_index": int(track_index), "clip_index": int(clip_index),
                "length": length, "notes": rows, "via": "get_notes"}
    except Exception as exc:
        raise RuntimeError(
            "could not read notes: %r (extended err: %r)" % (exc, read_err)
        )


def set_arrangement_notes(c_instance, track_index=None, clip_index=0,
                          notes=None, **_):
    """Replace all notes on a MIDI arrangement clip.

    `notes` is a list of {pitch, start, duration, velocity, mute} dicts (the
    same shape `get_arrangement_notes` returns). The clip's existing notes
    are removed first; then the new notes are written via `add_new_notes`
    (Live 11+) with `set_notes` as fallback. Returns the count written.
    """
    clip = _arrangement_clip(track_index, clip_index)
    if not bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("notes are MIDI-only; clip is audio")
    notes = notes or []
    length = float(getattr(clip, "length", 0.0))

    rows = tuple(
        (int(n["pitch"]), float(n["start"]), float(n["duration"]),
         int(n["velocity"]), bool(n.get("mute", False)))
        for n in notes
    )

    # Wipe existing notes. `remove_notes_extended` covers all pitches/time.
    if hasattr(clip, "remove_notes_extended"):
        try:
            clip.remove_notes_extended(0, 128, 0.0, max(length, 0.0))
        except Exception:
            pass
    elif hasattr(clip, "remove_notes"):
        try:
            clip.remove_notes(0.0, 0, max(length, 0.0), 128)
        except Exception:
            pass

    write_err = None
    written = False
    if hasattr(clip, "add_new_notes"):
        try:
            clip.add_new_notes(rows)
            written = True
        except Exception as exc:
            write_err = "add_new_notes: %r" % (exc,)
    if not written and hasattr(clip, "set_notes"):
        try:
            clip.set_notes(rows)
            written = True
        except Exception as exc:
            write_err = (write_err or "") + " | set_notes: %r" % (exc,)
    if not written:
        raise RuntimeError("could not write notes: %s" % write_err)

    return {"track_index": int(track_index), "clip_index": int(clip_index),
            "notes_written": len(rows)}


# ---------------------------------------------------------------------------
# Session-view clip handlers — mirror the arrangement ones for session clips.
#
# Why these exist: song_flow.transpose_song() walks every clip in the set and
# applies a semitone shift. It originally only handled arrangement-view clips,
# but plenty of producers drive playback from Session view. These handlers
# let the transpose path snapshot + mutate + restore session clips with the
# same per-clip pattern. See song_flow/transpose.py for the caller.
#
# Addressing: session clips live at ``Track.clip_slots[slot_index].clip``.
# Empty slots have ``clip=None``; we return ``is_empty=True`` so the Python
# side knows to skip without raising.
# ---------------------------------------------------------------------------


def _session_clip_slot(track_index, slot_index):
    """Resolve ``(track_index, slot_index)`` to a ClipSlot. Raises on out-of-range."""
    track = _track(int(track_index))
    slots = list(track.clip_slots)
    si = int(slot_index)
    if si < 0 or si >= len(slots):
        raise ValueError(
            "slot_index %d out of range; track has %d clip slots"
            % (si, len(slots))
        )
    return slots[si]


def _session_clip(track_index, slot_index):
    """Resolve to the Clip inside the slot. Returns (clip, is_empty)."""
    slot = _session_clip_slot(track_index, slot_index)
    clip = getattr(slot, "clip", None)
    if clip is None:
        return None, True
    return clip, False


def list_session_clip_slots(c_instance, track_index=None, **_):
    """Enumerate session clip slots on a track.

    Returns one entry per slot (including empty ones). Empty slots have
    ``is_empty=True`` and minimal data; populated slots include name,
    length, color, and whether the clip is MIDI vs audio. Mirrors
    ``list_arrangement_clips`` but slot-addressed.
    """
    track = _track(int(track_index))
    slots = list(track.clip_slots)
    out = []
    for si, slot in enumerate(slots):
        clip = getattr(slot, "clip", None)
        if clip is None:
            out.append({"slot_index": si, "is_empty": True})
            continue
        out.append({
            "slot_index": si,
            "is_empty": False,
            "name": getattr(clip, "name", None),
            "length": float(getattr(clip, "length", 0.0)),
            "is_midi_clip": bool(getattr(clip, "is_midi_clip", False)),
            "is_audio_clip": bool(getattr(clip, "is_audio_clip", False)),
            "color": int(getattr(clip, "color", 0) or 0),
        })
    return {"track_index": int(track_index), "slots": out}


def get_session_pitch_state(c_instance, track_index=None, slot_index=0, **_):
    """Snapshot warp + pitch + clip-type state for a session clip.

    Returns ``is_empty=True`` when the slot has no clip. Audio-only fields
    return None on MIDI clips (LOM does not expose warp/pitch there).
    """
    clip, is_empty = _session_clip(track_index, slot_index)
    if is_empty:
        return {
            "track_index": int(track_index), "slot_index": int(slot_index),
            "is_empty": True,
        }
    is_midi = bool(getattr(clip, "is_midi_clip", False))
    return {
        "track_index": int(track_index),
        "slot_index": int(slot_index),
        "is_empty": False,
        "is_midi_clip": is_midi,
        "warping": None if is_midi else bool(getattr(clip, "warping", False)),
        "warp_mode": None if is_midi else int(getattr(clip, "warp_mode", 0) or 0),
        "pitch_coarse": None if is_midi else int(getattr(clip, "pitch_coarse", 0) or 0),
        "pitch_fine": None if is_midi else int(getattr(clip, "pitch_fine", 0) or 0),
    }


def set_session_warp(c_instance, track_index=None, slot_index=0, value=True, **_):
    """Toggle warping on an audio session clip. Raises if the slot is
    empty or the clip is MIDI."""
    clip, is_empty = _session_clip(track_index, slot_index)
    if is_empty:
        raise RuntimeError(
            "slot %d on track %d is empty" % (int(slot_index), int(track_index))
        )
    if bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("warp is audio-only; clip is MIDI")
    clip.warping = bool(value)
    return {"track_index": int(track_index), "slot_index": int(slot_index),
            "warping": bool(clip.warping)}


def set_session_warp_mode(c_instance, track_index=None, slot_index=0, mode=5, **_):
    """Set warp mode on an audio session clip. Mode integers same as the
    arrangement variant (0=Beats, 1=Tones, ..., 5=Complex Pro)."""
    clip, is_empty = _session_clip(track_index, slot_index)
    if is_empty:
        raise RuntimeError(
            "slot %d on track %d is empty" % (int(slot_index), int(track_index))
        )
    if bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("warp_mode is audio-only; clip is MIDI")
    clip.warp_mode = int(mode)
    return {"track_index": int(track_index), "slot_index": int(slot_index),
            "warp_mode": int(clip.warp_mode)}


def set_session_pitch(c_instance, track_index=None, slot_index=0,
                     coarse=0, fine=0, **_):
    """Set pitch_coarse (semitones) and pitch_fine (cents) on an audio
    session clip. Same [-48, 48] / [-50, 50] LOM limits as arrangement."""
    clip, is_empty = _session_clip(track_index, slot_index)
    if is_empty:
        raise RuntimeError(
            "slot %d on track %d is empty" % (int(slot_index), int(track_index))
        )
    if bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("pitch is audio-only; clip is MIDI")
    clip.pitch_coarse = int(coarse)
    clip.pitch_fine = int(fine)
    return {"track_index": int(track_index), "slot_index": int(slot_index),
            "pitch_coarse": int(clip.pitch_coarse),
            "pitch_fine": int(clip.pitch_fine)}


def get_session_notes(c_instance, track_index=None, slot_index=0, **_):
    """Read all notes from a MIDI session clip. Same return shape +
    ``get_notes_extended`` → ``get_notes`` fallback as the arrangement
    variant. Raises on empty slot or audio clip."""
    clip, is_empty = _session_clip(track_index, slot_index)
    if is_empty:
        raise RuntimeError(
            "slot %d on track %d is empty" % (int(slot_index), int(track_index))
        )
    if not bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("notes are MIDI-only; clip is audio")
    length = float(getattr(clip, "length", 0.0))
    rows = []
    read_err = None
    try:
        for n in clip.get_notes_extended(0, 128, 0.0, length):
            rows.append({
                "pitch": int(n.pitch),
                "start": float(n.start_time),
                "duration": float(n.duration),
                "velocity": int(n.velocity),
                "mute": bool(n.mute),
            })
        return {"track_index": int(track_index), "slot_index": int(slot_index),
                "length": length, "notes": rows, "via": "get_notes_extended"}
    except (AttributeError, TypeError) as exc:
        read_err = exc
    try:
        for (p, t, d, v, m) in clip.get_notes(0.0, 0, length, 128):
            rows.append({
                "pitch": int(p), "start": float(t), "duration": float(d),
                "velocity": int(v), "mute": bool(m),
            })
        return {"track_index": int(track_index), "slot_index": int(slot_index),
                "length": length, "notes": rows, "via": "get_notes"}
    except Exception as exc:
        raise RuntimeError(
            "could not read notes: %r (extended err: %r)" % (exc, read_err)
        )


def set_session_notes(c_instance, track_index=None, slot_index=0,
                     notes=None, **_):
    """Replace all notes on a MIDI session clip. Same write path
    (``remove_notes_extended`` → ``add_new_notes`` → ``set_notes`` fallback)
    as the arrangement variant."""
    clip, is_empty = _session_clip(track_index, slot_index)
    if is_empty:
        raise RuntimeError(
            "slot %d on track %d is empty" % (int(slot_index), int(track_index))
        )
    if not bool(getattr(clip, "is_midi_clip", False)):
        raise RuntimeError("notes are MIDI-only; clip is audio")
    notes = notes or []
    length = float(getattr(clip, "length", 0.0))

    rows = tuple(
        (int(n["pitch"]), float(n["start"]), float(n["duration"]),
         int(n["velocity"]), bool(n.get("mute", False)))
        for n in notes
    )

    if hasattr(clip, "remove_notes_extended"):
        try:
            clip.remove_notes_extended(0, 128, 0.0, max(length, 0.0))
        except Exception:
            pass
    elif hasattr(clip, "remove_notes"):
        try:
            clip.remove_notes(0.0, 0, max(length, 0.0), 128)
        except Exception:
            pass

    write_err = None
    written = False
    if hasattr(clip, "add_new_notes"):
        try:
            clip.add_new_notes(rows)
            written = True
        except Exception as exc:
            write_err = "add_new_notes: %r" % (exc,)
    if not written and hasattr(clip, "set_notes"):
        try:
            clip.set_notes(rows)
            written = True
        except Exception as exc:
            write_err = (write_err or "") + " | set_notes: %r" % (exc,)
    if not written:
        raise RuntimeError("could not write notes: %s" % write_err)

    return {"track_index": int(track_index), "slot_index": int(slot_index),
            "notes_written": len(rows)}


def create_arrangement_audio_clip(c_instance, track_index=None, file_path=None,
                                   position=0.0, length=None, **_):
    """Create an arrangement audio clip on a track and (try to) load a wav.

    Live 11.3.43's documented surface is ``Track.create_audio_clip(name,
    position)`` which creates an empty audio clip — the LOM doesn't expose
    a single-call "drop wav into arrangement". This handler probes a few
    plausible signatures + a post-create ``file_path`` setter, returning
    rich diagnostics on failure so the caller can route (e.g. fall back
    to "drag manually").

    On success: returns ``{loaded: True, via: <method>, clip_file_path,
    track_index, file_path, position, length}``.

    On failure: returns ``{loaded: False, supported: False, attempt_errors,
    workaround}``. NEVER raises for a "couldn't load" case — that belongs
    in the response so the higher layer can decide.
    """
    import os
    track = _track(int(track_index))
    fp = str(file_path or "")
    pos = float(position)
    if not fp or not os.path.exists(fp):
        raise ValueError("file_path missing or does not exist: %r" % fp)

    if not getattr(track, "create_audio_clip", None):
        return {
            "track_index": int(track_index),
            "file_path": fp,
            "loaded": False, "supported": False,
            "workaround": "Track.create_audio_clip not available in this Live build; drag the wav onto the arrangement manually.",
        }

    # Plausible call shapes, ordered by hypothesis strength. The doc'd
    # signature takes (name, position) — but Live builds vary; we try a
    # few interpretations + an optional length hint.
    candidates = []
    if length is not None:
        end = pos + float(length)
        candidates.extend([
            ("create_audio_clip(file_path, position, end)",
             lambda: track.create_audio_clip(fp, pos, end)),
        ])
    candidates.extend([
        ("create_audio_clip(file_path, position)",
         lambda: track.create_audio_clip(fp, pos)),
        ("create_audio_clip(file_path)",
         lambda: track.create_audio_clip(fp)),
    ])
    # Speculative: build with name=basename then attempt to set file_path.
    candidates.extend([
        ("create_audio_clip(basename, position) + clip.file_path = fp",
         lambda: ("post_set", track.create_audio_clip(os.path.basename(fp), pos))),
    ])

    attempt_errors = []
    new_clip = None
    method_used = None
    for desc, fn in candidates:
        try:
            outcome = fn()
            if isinstance(outcome, tuple) and outcome and outcome[0] == "post_set":
                empty_clip = outcome[1]
                if empty_clip is None:
                    # Live silently returned None — record as a failure, not
                    # a success-with-no-clip.
                    attempt_errors.append("%s -> returned None" % desc)
                    continue
                # Try to populate the freshly-created empty clip.
                try:
                    empty_clip.file_path = fp
                    new_clip = empty_clip
                    method_used = desc
                except Exception as set_exc:
                    attempt_errors.append("%s -> set file_path: %s: %s" % (
                        desc, type(set_exc).__name__, set_exc))
                    # Clean up the empty clip — leaving an empty clip on
                    # the user's arrangement is worse than failing cleanly.
                    try:
                        if hasattr(track, "delete_clip"):
                            track.delete_clip(empty_clip)
                    except Exception:
                        pass
                    new_clip = None
                    continue
            else:
                if outcome is None:
                    # Live's create_audio_clip(weird_args) returns None
                    # silently rather than raising on some builds. Treat
                    # as a failed attempt so attempt_errors is informative.
                    attempt_errors.append("%s -> returned None" % desc)
                    continue
                new_clip = outcome
                method_used = desc
            break
        except Exception as exc:
            attempt_errors.append("%s -> %s: %s" % (desc, type(exc).__name__, exc))
            continue

    if new_clip is None:
        return {
            "track_index": int(track_index),
            "file_path": fp,
            "position": pos,
            "loaded": False, "supported": False,
            "attempt_errors": attempt_errors,
            "workaround": (
                "Live 11's LOM doesn't accept this on your build. "
                "Drag the wav onto the arrangement timeline manually."
            ),
        }

    actual = getattr(new_clip, "file_path", None)
    looks_loaded = bool(actual) and os.path.exists(actual)
    return {
        "track_index": int(track_index),
        "file_path": fp,
        "position": pos,
        "length": float(getattr(new_clip, "length", 0.0) or 0.0),
        "loaded": looks_loaded,
        "supported": True,
        "via": method_used,
        "clip_file_path": actual,
        "name": getattr(new_clip, "name", None),
        "is_audio_clip": bool(getattr(new_clip, "is_audio_clip", False)),
        "attempt_errors": attempt_errors,
    }


def list_arrangement_clips(c_instance, track_index=None, **_):
    """Direct-LOM equivalent of ``/live/track/get/arrangement_clips/*``.

    AbletonOSC's ``/live/track/get/arrangement_clips/length`` reply is empty
    for clips that arrived after AbletonOSC's listeners were attached at
    Live startup — its arrangement-clips state appears to be cached via
    listeners and doesn't refresh on user-drag-after-startup or
    programmatic clip creation. This handler enumerates the clips via
    direct LOM access (``track.arrangement_clips`` is always live), so the
    song-flow path (which needs to walk arrangement clips for transpose)
    is resilient to that upstream caching bug.

    Returns:
        ``{"track_index": int, "clips": [{clip_index, name, length,
            start_time, is_midi_clip, is_audio_clip}, ...]}``.
    """
    track = _track(int(track_index))
    out = []
    for i, clip in enumerate(track.arrangement_clips):
        out.append({
            "clip_index": i,
            "name": getattr(clip, "name", None),
            "length": float(getattr(clip, "length", 0.0)),
            "start_time": float(getattr(clip, "start_time", 0.0)),
            "is_midi_clip": bool(getattr(clip, "is_midi_clip", False)),
            "is_audio_clip": bool(getattr(clip, "is_audio_clip", False)),
        })
    return {"track_index": int(track_index), "clips": out}


def _probe_audio_clip_creation(c_instance, track_index=0, **_):
    """Diagnostic: dump every method on Track + Clip whose name plausibly
    relates to audio-clip creation or sample-loading.

    Read-only (creates no state). Useful for figuring out, on a real Live
    build, what signature variants ``create_arrangement_audio_clip``
    should add to its candidates list. Run via the bridge's `system.reload`
    or directly from a debug shell inside Live.
    """
    track = _track(int(track_index))

    def pub(obj):
        return sorted([m for m in dir(obj) if not m.startswith("_")])

    def filtered(obj):
        return [
            m for m in pub(obj)
            if any(k in m.lower() for k in (
                "audio", "clip", "sample", "arrangement", "load", "create", "file_path",
            ))
        ]

    sample_clip = None
    try:
        for c in track.arrangement_clips:
            if getattr(c, "is_audio_clip", False):
                sample_clip = c
                break
    except Exception:
        pass

    return {
        "track_methods": filtered(track),
        "track_clip_creation_specifics": [
            m for m in pub(track) if "create" in m.lower() and "clip" in m.lower()
        ],
        "sample_audio_clip_attrs": filtered(sample_clip) if sample_clip is not None else None,
        "sample_audio_clip_file_path": (
            getattr(sample_clip, "file_path", None) if sample_clip is not None else None
        ),
    }


def _song():
    import Live  # type: ignore
    return Live.Application.get_application().get_document()


def _track(track_index):
    song = _song()
    tracks = list(song.tracks)
    track_index = int(track_index)
    if track_index < 0 or track_index >= len(tracks):
        raise ValueError("track_index %d out of range" % track_index)
    return tracks[track_index]


def _clip(track_index, clip_index):
    track = _track(track_index)
    slots = list(track.clip_slots)
    if clip_index < 0 or clip_index >= len(slots):
        raise ValueError("clip_index %d out of range" % clip_index)
    slot = slots[clip_index]
    if not slot.has_clip:
        raise ValueError("no clip at track %d slot %d" % (track_index, clip_index))
    return slot.clip


def consolidate(c_instance, track_index=None, clip_index=None, **_):
    """Consolidate a clip's loop region into a single new clip."""
    clip = _clip(int(track_index), int(clip_index))
    if getattr(clip, "consolidate", None) is None:
        raise RuntimeError("Clip.consolidate not available in this Live version")
    clip.consolidate()
    return {"track_index": int(track_index), "clip_index": int(clip_index), "consolidated": True}


def crop(c_instance, track_index=None, clip_index=None, **_):
    """Crop a clip to its loop region."""
    clip = _clip(int(track_index), int(clip_index))
    fn = getattr(clip, "crop", None)
    if fn is None:
        raise RuntimeError("Clip.crop not available in this Live version")
    fn()
    return {"track_index": int(track_index), "clip_index": int(clip_index), "cropped": True}


def reverse(c_instance, track_index=None, clip_index=None, **_):
    """Reverse an audio clip.

    Live 11's Python LOM does NOT expose a `Clip.reverse()` method — reverse is
    a UI-only command. We attempt a few likely API names; if none exist we
    return a structured `not_supported` result so the MCP server can surface a
    helpful workaround message rather than a hard exception.
    """
    clip = _clip(int(track_index), int(clip_index))
    for attr in ("reverse", "reverse_audio", "reverse_warp"):
        fn = getattr(clip, attr, None)
        if callable(fn):
            try:
                fn()
                return {"track_index": int(track_index), "clip_index": int(clip_index),
                        "reversed": True, "via": attr}
            except Exception:
                continue
    return {
        "track_index": int(track_index),
        "clip_index": int(clip_index),
        "reversed": False,
        "supported": False,
        "workaround": "Right-click the clip in Live and choose 'Reverse', or use a Max for Live device.",
    }


def _dir_track(c_instance, track_index=0, **_):
    """Debug introspection: dump methods/attrs + actual arrangement_clips state."""
    track = _track(track_index)
    slot = list(track.clip_slots)[0] if list(track.clip_slots) else None
    src = slot.clip if (slot and slot.has_clip) else None
    def public(obj):
        return sorted([m for m in dir(obj) if not m.startswith("_")])

    arr_clips_info = []
    try:
        for c in track.arrangement_clips:
            arr_clips_info.append({
                "name": getattr(c, "name", None),
                "start_time": getattr(c, "start_time", None),
                "end_time": getattr(c, "end_time", None),
                "length": getattr(c, "length", None),
                "position": getattr(c, "position", None),
                "is_arrangement_clip": getattr(c, "is_arrangement_clip", None),
                "is_midi_clip": getattr(c, "is_midi_clip", None),
            })
    except Exception as exc:
        arr_clips_info = [{"error": repr(exc)}]

    return {
        "track_name": getattr(track, "name", None),
        "track_methods_relevant": [m for m in public(track) if any(x in m.lower() for x in ("clip", "arrang", "duplicate", "create"))],
        "slot_methods_relevant": [m for m in public(slot) if any(x in m.lower() for x in ("clip", "arrang", "duplicate", "create"))] if slot else None,
        "src_clip_props": {
            "name": getattr(src, "name", None) if src else None,
            "is_arrangement_clip": getattr(src, "is_arrangement_clip", None) if src else None,
            "is_midi_clip": getattr(src, "is_midi_clip", None) if src else None,
            "length": getattr(src, "length", None) if src else None,
        } if src else None,
        "arrangement_clips_count": len(list(track.arrangement_clips)),
        "arrangement_clips": arr_clips_info,
    }


def duplicate_to_arrangement(c_instance, track_index=None, slot_index=None, time=0.0, **_):
    """Place a Session clip onto the Arrangement timeline at `time` (beats).

    Tries the native LOM duplicate method first if available, then falls back
    to a manual re-creation that works for MIDI clips on any Live 11+ build:
        track.create_midi_clip(start, end) + clip.set_notes(...).

    Audio clips fall through to a not_supported result because Live's LOM has
    no programmatic audio-clip-to-arrangement copy.
    """
    track = _track(track_index)
    slots = list(track.clip_slots)
    slot_index = int(slot_index)
    time = float(time)
    if slot_index < 0 or slot_index >= len(slots):
        raise ValueError("slot_index %d out of range" % slot_index)
    slot = slots[slot_index]
    if not slot.has_clip:
        raise ValueError("no clip at track %d slot %d" % (int(track_index), slot_index))
    src = slot.clip

    # Tier 1: native LOM helpers, if any exist in this Live build.
    # Live's API surface for "session clip → arrangement timeline" varies
    # across versions. We try every plausible call shape and record per-
    # attempt errors so debugging is straightforward.
    src_clip = slot.clip
    src_length = float(getattr(src_clip, "length", 0.0))
    natives = []
    if hasattr(slot, "duplicate_clip_to_arrangement"):
        natives.append(("ClipSlot.duplicate_clip_to_arrangement(time)",
                        lambda: slot.duplicate_clip_to_arrangement(time)))
    if hasattr(track, "duplicate_clip_to_arrangement"):
        natives.extend([
            ("Track.duplicate_clip_to_arrangement(slot, time)",
             lambda: track.duplicate_clip_to_arrangement(slot, time)),
            ("Track.duplicate_clip_to_arrangement(clip, time)",
             lambda: track.duplicate_clip_to_arrangement(src_clip, time)),
            ("Track.duplicate_clip_to_arrangement(slot_index, time)",
             lambda: track.duplicate_clip_to_arrangement(slot_index, time)),
            ("Track.duplicate_clip_to_arrangement(slot)",
             lambda: track.duplicate_clip_to_arrangement(slot)),
            ("Track.duplicate_clip_to_arrangement(slot_index)",
             lambda: track.duplicate_clip_to_arrangement(slot_index)),
            ("Track.duplicate_clip_to_arrangement(slot, time, time+length)",
             lambda: track.duplicate_clip_to_arrangement(slot, time, time + src_length)),
        ])
    attempt_errors = []
    for name, fn in natives:
        try:
            result = fn()
            return {
                "track_index": int(track_index),
                "slot_index": slot_index,
                "time": time,
                "method": name,
                "lom_returned": repr(result) if result is not None else None,
            }
        except Exception as exc:
            attempt_errors.append("%s -> %s: %s" % (name, type(exc).__name__, exc))
            continue
    last_err = " | ".join(attempt_errors) if attempt_errors else "no native methods exposed"

    # Tier 2: manual MIDI clip copy.
    if not getattr(src, "is_midi_clip", False):
        raise RuntimeError(
            "audio clip can't be programmatically copied to arrangement; "
            "native attempts failed (%s). Drag manually from Session to Arrangement."
            % last_err
        )

    length = float(src.length)
    if length <= 0:
        raise RuntimeError("source clip has zero length")

    # Read notes from source. Prefer the newer extended API.
    notes_tuple = None
    read_err = None
    try:
        ext_iter = src.get_notes_extended(0, 128, 0.0, length)
        rows = []
        for n in ext_iter:
            rows.append(
                (int(n.pitch), float(n.start_time), float(n.duration),
                 int(n.velocity), bool(n.mute))
            )
        notes_tuple = tuple(rows)
    except (AttributeError, TypeError) as exc:
        read_err = exc
    if notes_tuple is None:
        try:
            notes_data = src.get_notes(0.0, 0, length, 128)
            notes_tuple = tuple(
                (int(p), float(t), float(d), int(v), bool(m))
                for (p, t, d, v, m) in notes_data
            )
        except Exception as exc:
            raise RuntimeError("could not read source notes: %r (extended err: %r)"
                               % (exc, read_err))

    # Create the destination arrangement clip.
    create = getattr(track, "create_midi_clip", None)
    if create is None:
        raise RuntimeError("Track.create_midi_clip not available in this Live version")
    new_clip = create(time, time + length)

    # Write notes. Try the modern API first.
    write_err = None
    written = False
    if hasattr(new_clip, "add_new_notes"):
        try:
            # Live 11+: add_new_notes takes a tuple of MidiNoteSpec-shaped tuples.
            new_clip.add_new_notes(notes_tuple)
            written = True
        except Exception as exc:
            write_err = "add_new_notes: %r" % (exc,)
    if not written and hasattr(new_clip, "set_notes"):
        try:
            new_clip.set_notes(notes_tuple)
            written = True
        except Exception as exc:
            write_err = (write_err or "") + " | set_notes: %r" % (exc,)
    if not written:
        raise RuntimeError("could not write notes to new arrangement clip: %s" % write_err)

    try:
        new_clip.name = (src.name or "") + " (arr)"
    except Exception:
        pass
    try:
        new_clip.color = src.color
    except Exception:
        pass

    return {
        "track_index": int(track_index),
        "slot_index": slot_index,
        "time": time,
        "length": length,
        "notes_copied": len(notes_tuple),
        "method": "manual_midi_copy",
    }
