"""Track-level ops not exposed by AbletonOSC: group/ungroup/freeze/flatten/delete_device."""

from __future__ import absolute_import


EXPORTS = (
    "group",
    "ungroup",
    "freeze",
    "unfreeze",
    "flatten",
    "is_frozen",
    "delete_device",
    "list_devices",
)


def _song():
    import Live  # type: ignore
    return Live.Application.get_application().get_document()


def _track(index):
    tracks = list(_song().tracks)
    if index < 0 or index >= len(tracks):
        raise ValueError("track_index %d out of range (0..%d)" % (index, len(tracks) - 1))
    return tracks[index]


def group(c_instance, track_indices=None, **_):
    """Group a contiguous range of tracks into a new group track.

    Live's `Song.group_tracks(start, end)` was added in Live 11. If unavailable
    we fall back to selecting the range and invoking the menu via the
    `application.view`-driven workaround which still requires the user; in that
    case we raise a clear error.
    """
    if not track_indices:
        raise ValueError("track_indices must be a non-empty list")
    indices = sorted(int(i) for i in track_indices)
    if indices[-1] - indices[0] + 1 != len(indices):
        raise ValueError("track_indices must be contiguous (got %r)" % indices)
    song = _song()
    start, end = indices[0], indices[-1]
    fn = getattr(song, "group_tracks", None)
    if fn is None:
        # Fallback: select range and emit the action via the application view.
        # Live 10's API didn't expose this — the user must run Cmd-G manually.
        raise RuntimeError(
            "Song.group_tracks not available in this Live build; "
            "select tracks %d..%d and press Cmd/Ctrl-G manually." % (start, end)
        )
    fn(start, end)
    # The new group track replaces the range; locate it (it's at `start`).
    new_idx = start
    return {"group_track_index": new_idx, "grouped": indices}


def ungroup(c_instance, group_track_index=None, **_):
    """Ungroup a group track. Uses Track.delete and re-parents children when possible.

    `Song.ungroup_track(index)` was added in Live 11. We prefer it; otherwise we
    surface a clear error explaining the limitation.
    """
    if group_track_index is None:
        raise ValueError("group_track_index is required")
    idx = int(group_track_index)
    track = _track(idx)
    if not getattr(track, "is_foldable", False):
        raise ValueError("track %d is not a group" % idx)
    song = _song()
    fn = getattr(song, "ungroup_track", None)
    if fn is not None:
        fn(idx)
        return {"ungrouped_index": idx}
    # Some Live versions put it on the track itself.
    fn2 = getattr(track, "ungroup", None)
    if fn2 is not None:
        fn2()
        return {"ungrouped_index": idx}
    raise RuntimeError(
        "ungroup_track not exposed in this Live build; right-click the group "
        "header and choose Ungroup manually."
    )


def freeze(c_instance, track_index=None, **_):
    """Freeze a track. Uses Track.freeze() (Live 11+)."""
    if track_index is None:
        raise ValueError("track_index is required")
    track = _track(int(track_index))
    fn = getattr(track, "freeze", None)
    if fn is None:
        raise RuntimeError("Track.freeze not available in this Live version")
    fn()
    return {"track_index": int(track_index), "frozen": True}


def flatten(c_instance, track_index=None, **_):
    """Flatten a frozen track. Track.flatten() exists on Live 11+."""
    if track_index is None:
        raise ValueError("track_index is required")
    track = _track(int(track_index))
    fn = getattr(track, "flatten", None)
    if fn is None:
        raise RuntimeError("Track.flatten not available in this Live version")
    fn()
    return {"track_index": int(track_index), "flattened": True}


def unfreeze(c_instance, track_index=None, **_):
    """Unfreeze a previously-frozen track (the reverse of `freeze`).

    Track.unfreeze() exists on Live 11+. Some Live versions only expose this
    as Track.is_frozen=False assignment; we prefer the explicit method.
    """
    if track_index is None:
        raise ValueError("track_index is required")
    track = _track(int(track_index))
    fn = getattr(track, "unfreeze", None)
    if fn is None:
        # Fallback for builds where unfreeze isn't on Track: try setting the
        # property directly (older Live behaviour). If neither path exists,
        # error out cleanly.
        try:
            track.is_frozen = False  # type: ignore[attr-defined]
            return {"track_index": int(track_index), "unfrozen": True, "via": "is_frozen"}
        except Exception as exc:
            raise RuntimeError(
                "Track.unfreeze not available in this Live version: %s" % exc
            )
    fn()
    return {"track_index": int(track_index), "unfrozen": True, "via": "unfreeze"}


def is_frozen(c_instance, track_index=None, **_):
    """Return the freezing state of a track.

    Maps Live's `Track.freezing_state` int directly:
        0 = normal (not frozen)
        1 = frozen
        2 = flattening (transient state during flatten())

    Falls back to inspecting `Track.is_frozen` (boolean) on builds that
    don't expose the integer state, mapping to 0 or 1.
    """
    if track_index is None:
        raise ValueError("track_index is required")
    track = _track(int(track_index))
    state = getattr(track, "freezing_state", None)
    if state is not None:
        return {"track_index": int(track_index), "freezing_state": int(state)}
    bool_state = getattr(track, "is_frozen", None)
    if bool_state is not None:
        return {
            "track_index": int(track_index),
            "freezing_state": 1 if bool(bool_state) else 0,
        }
    return {"track_index": int(track_index), "freezing_state": 0}


def list_devices(c_instance, track_index=None, **_):
    """Return devices on a track with names + class names."""
    if track_index is None:
        raise ValueError("track_index is required")
    track = _track(int(track_index))
    devices = list(track.devices)
    return {
        "track_index": int(track_index),
        "count": len(devices),
        "devices": [
            {
                "index": i,
                "name": getattr(d, "name", None),
                "class_name": getattr(d, "class_name", None),
                "type": getattr(d, "type", None),
            }
            for i, d in enumerate(devices)
        ],
    }


def delete_device(c_instance, track_index=None, device_index=None, **_):
    """Delete a device from a track by its index in the chain.

    Verified Live 11.3.43: ``Track.delete_device(int)`` exists and removes the
    device at that index. Use ``list_devices`` first to identify which slot
    holds the device you want gone.
    """
    if track_index is None or device_index is None:
        raise ValueError("track_index and device_index are required")
    track = _track(int(track_index))
    fn = getattr(track, "delete_device", None)
    if fn is None:
        raise RuntimeError("Track.delete_device not available in this Live version")
    devices = list(track.devices)
    di = int(device_index)
    if di < 0 or di >= len(devices):
        raise ValueError("device_index %d out of range (0..%d)" % (di, len(devices) - 1))
    name = getattr(devices[di], "name", None)
    fn(di)
    return {"track_index": int(track_index), "device_index": di, "deleted": name}
