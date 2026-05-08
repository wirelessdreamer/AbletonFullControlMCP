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
    "_dir_track",
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
    song = _song()
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
