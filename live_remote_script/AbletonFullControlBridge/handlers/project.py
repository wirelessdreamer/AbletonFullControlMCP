"""Project-level ops: save (and a few introspection helpers)."""

from __future__ import absolute_import

import os
import sys


EXPORTS = (
    "capabilities",
    "open_diagnostics",
    "reload_bridge",
    "open_set",
    "import_checkpoint",
    "import_begin",
    "import_restore",
    "import_arm_switch",
    "import_accept_switch",
    "import_cancel_open",
    "save",
    "info",
    "get_file_path",
    "list_freezing_dir",
    "import_snapshot",
    "import_configure",
    "import_seek",
    "import_stop",
)


def capabilities(c_instance, **_):
    """Capabilities reflect the running API, never assumed method names."""
    import Live
    app = Live.Application.get_application()
    song = app.get_document()
    return {"native_save": callable(getattr(song, "save", None)),
            "native_file_path": any(hasattr(song, name) for name in ("get_file_path", "file_path")),
            "import_artifact_checkpoint": True, "open_set": os.name == "nt" or sys.platform == "darwin",
            "open_dialog_count": int(app.open_dialog_count),
            "screen_control": False, "handler_version": "1.7.0"}


def open_diagnostics(c_instance, **_):
    """Read native file-opening capabilities and configured browser folders."""
    import Live
    app = Live.Application.get_application()
    browser = app.browser
    folders = []
    for folder in getattr(browser, "user_folders", []):
        folders.append({"name": str(folder.name), "uri": str(getattr(folder, "uri", "")),
                        "children": [{"name": str(c.name), "uri": str(getattr(c, "uri", "")),
                                      "loadable": bool(c.is_loadable)} for c in folder.iter_children][:50]})
    return {"folders": folders, "surfaces": [type(s).__module__ + "." + type(s).__name__ for s in app.control_surfaces],
            "dialog_message": str(app.current_dialog_message), "dialog_buttons": int(app.current_dialog_button_count),
            "browser_members": [n for n in dir(browser) if not n.startswith("_")],
            "application_members": [n for n in dir(app) if not n.startswith("_")],
            "view_members": [n for n in dir(app.view) if not n.startswith("_")]}


def reload_bridge(c_instance, **_):
    """Upgrade the Python bridge class while retaining its listening socket.

    Explicitly refresh the package's factory alias as well as the live instance;
    handler-only reload cannot fix missing Remote Script lifecycle callbacks.
    """
    import Live
    import importlib
    from .. import bridge_server
    if _IMPORT_TRANSACTION is not None or _IMPORT_PREVIEW is not None:
        raise RuntimeError("Finish import verification before reloading the bridge")
    app = Live.Application.get_application()
    candidates = [s for s in app.control_surfaces if type(s).__module__ == bridge_server.__name__
                  and type(s).__name__ == "BridgeServer"]
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one active AbletonFullControlBridge")
    surface = candidates[0]
    if surface._c_instance != c_instance:
        raise RuntimeError("Bridge ownership differs")
    module = importlib.reload(bridge_server)
    surface.__class__ = module.BridgeServer
    package = sys.modules[module.__package__]
    package.BridgeServer = module.BridgeServer
    surface._handlers = surface._build_handler_table()
    return {"reloaded": True, "bridge_version": module.BRIDGE_VERSION,
            "listener_preserved": surface._listener is not None,
            "refresh_state": callable(surface.refresh_state)}


def save(c_instance, expected_set_path=None, **_):
    """Use native save only when available; do not synthesize keyboard input."""
    _require_import_set(c_instance, expected_set_path)
    import Live
    song = Live.Application.get_application().get_document()
    save_fn = getattr(song, "save", None)
    if not callable(save_fn):
        return {"saved": False, "supported": False, "save_as_supported": False,
                "reason": "This Live API has no native save. Import projects are written as portable .als artifacts and verified by import_checkpoint/reopen."}
    save_fn()
    return {"saved": True, "supported": True, "save_as_supported": False}


def open_set(c_instance, path, preserved_set_path=None, **_):
    """Open an .als through the OS file association; no screen/keyboard input.

    Refuse to replace a document with undoable edits. Verification owns an
    undo group and restores it before switching between imported projects.
    """
    import Live
    import subprocess
    app = Live.Application.get_application()
    song = app.get_document()
    path = os.path.abspath(path)
    if not path.lower().endswith(".als") or not os.path.isfile(path):
        raise ValueError("Provide an existing .als file")
    if app.open_dialog_count or song.is_playing or song.record_mode:
        raise RuntimeError("Close Live dialogs and stop playback/recording before opening another set")
    preserved = (preserved_set_path and _document_file_path()
                 and os.path.normcase(os.path.abspath(preserved_set_path))
                 == os.path.normcase(os.path.abspath(_document_file_path()))
                 and os.path.isfile(preserved_set_path))
    if preserved_set_path and not preserved:
        raise RuntimeError("The preserved set path does not identify the active saved document")
    if song.can_undo and not preserved:
        raise RuntimeError("The active set has undoable edits; preserve it before switching projects")
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("Opening Live sets is supported on Windows/macOS")
    return {"requested": True, "path": path, "method": "os_file_association", "screen_control": False,
            "preserved_set_path": preserved_set_path,
            "pending": "Any native Save Changes prompt is left untouched"}


def import_checkpoint(c_instance, expected_set_path, **_):
    """Prove the generated artifact is intact and identify its active media.

    This is a checkpoint of an importer-owned artifact, not a native Live save
    and not a serializer for arbitrary user edits. Host-side verification also
    compares the observed clips, notes, devices and playback to the manifest.
    """
    global _IMPORT_VERIFIED
    _require_import_set(c_instance, expected_set_path)
    import Live
    import hashlib
    import json
    with open(expected_set_path, "rb") as stream:
        data = stream.read()
    sha = hashlib.sha256(data).hexdigest()
    with open(os.path.join(os.path.dirname(expected_set_path), "import_manifest.json"), "r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if sha != manifest.get("set_sha256"):
        raise RuntimeError("The generated .als was edited after import; checkpoint refused")
    # Live's embedded Python can omit expat. The host parses the gzip/XML
    # after comparing this hash; the bridge need not import an XML parser.
    _IMPORT_VERIFIED = (Live.Application.get_application().get_document(),
                        os.path.abspath(expected_set_path), sha)
    return {"checkpointed": True, "native_save": False, "set_sha256": sha,
            "file_path": os.path.abspath(expected_set_path), "method": "generated_artifact_readback"}


_IMPORT_TRANSACTION = None
_IMPORT_VERIFIED = None
_IMPORT_SWITCH = None


def _switch_fingerprint(c_instance):
    """Ignore transport/meter telemetry; retain document and device controls."""
    import Live
    import json
    snapshot = import_snapshot(c_instance)
    for key in ("master_left", "master_right", "playing", "position", "open_dialog_count"):
        snapshot.pop(key, None)
    for track, native in zip(snapshot["tracks"], Live.Application.get_application().get_document().tracks):
        track.pop("meter_left", None)
        track.pop("meter_right", None)
        track["device_parameters"] = [[(str(p.name), float(p.value)) for p in device.parameters]
                                       for device in native.devices]
    return json.dumps(snapshot, sort_keys=True)


def import_arm_switch(c_instance, expected_set_path, **_):
    global _IMPORT_SWITCH
    import Live
    import time
    _require_import_set(c_instance, expected_set_path)
    song = Live.Application.get_application().get_document()
    expected = os.path.abspath(expected_set_path)
    if (not _IMPORT_VERIFIED or _IMPORT_VERIFIED[:2] != (song, expected)
            or song.can_undo or _IMPORT_TRANSACTION is not None or _IMPORT_PREVIEW is not None):
        raise RuntimeError("Only an intact, checked and restored import can own a pending switch")
    _IMPORT_SWITCH = (song, expected, _IMPORT_VERIFIED[2], _switch_fingerprint(c_instance), time.monotonic())
    return {"armed": True}


def import_accept_switch(c_instance, expected_set_path, **_):
    """Close only a verified import's own transient audition changes via LOM."""
    global _IMPORT_SWITCH
    import Live
    import time
    import hashlib
    _require_import_set(c_instance, expected_set_path)
    app = Live.Application.get_application()
    song = app.get_document()
    lease = _IMPORT_SWITCH
    if (not lease or lease[:2] != (song, os.path.abspath(expected_set_path))
            or time.monotonic() - lease[4] > 120 or song.can_undo
            or _switch_fingerprint(c_instance) != lease[3]):
        raise RuntimeError("The pending switch is not owned by this restored verification")
    with open(expected_set_path, "rb") as stream:
        if hashlib.sha256(stream.read()).hexdigest() != lease[2]:
            raise RuntimeError("The checked import artifact changed")
    message = str(app.current_dialog_message).replace(u"\u201c", '"').replace(u"\u201d", '"')
    if (app.current_dialog_button_count != 3
            or message != 'Save changes to "%s" before closing?' % song.name):
        raise RuntimeError("The dialog is not the expected import Save Changes prompt")
    _IMPORT_SWITCH = None
    # Live's Save / Don't Save / Cancel dialog, checked above. The host verifies
    # the requested new set path and unchanged source hash after this one action.
    app.press_current_dialog_button(1)
    return {"requested": True, "method": "native_dialog_api", "screen_control": False}


def import_cancel_open(c_instance, expected_set_path, **_):
    """Cancel a pending Save Changes prompt on an identified import, preserving it."""
    import Live
    _require_import_set(c_instance, expected_set_path)
    app = Live.Application.get_application()
    message = str(app.current_dialog_message).replace(u"\u201c", '"').replace(u"\u201d", '"')
    if (app.current_dialog_button_count != 3
            or message != 'Save changes to "%s" before closing?' % app.get_document().name):
        raise RuntimeError("No matching import Save Changes prompt is active")
    app.press_current_dialog_button(2)
    return {"requested": True, "method": "native_dialog_api", "screen_control": False}


def import_begin(c_instance, expected_set_path, **_):
    global _IMPORT_TRANSACTION
    _require_import_set(c_instance, expected_set_path)
    import Live
    song = Live.Application.get_application().get_document()
    if _IMPORT_TRANSACTION is not None or song.can_undo:
        raise RuntimeError("Verification requires a freshly opened set with no undoable edits")
    _IMPORT_TRANSACTION = (song, expected_set_path)
    song.begin_undo_step()
    return {"started": True}


def import_restore(c_instance, expected_set_path, **_):
    global _IMPORT_TRANSACTION
    _require_import_set(c_instance, expected_set_path)
    import Live
    song = Live.Application.get_application().get_document()
    if _IMPORT_TRANSACTION is None or _IMPORT_TRANSACTION[0] != song:
        raise RuntimeError("No verification transaction belongs to this set")
    song.stop_playing()
    song.end_undo_step()
    _IMPORT_TRANSACTION = None
    if song.can_undo:
        song.undo()
    song.current_song_time = 0
    return {"restored": True, "can_undo": bool(song.can_undo)}


def info(c_instance, **_):
    """Return basic project metadata (length, tempo, name)."""
    import Live  # type: ignore
    app = Live.Application.get_application()
    doc = app.get_document()
    return {
        "live_version": "%d.%d.%d" % (
            app.get_major_version(),
            app.get_minor_version(),
            app.get_bugfix_version(),
        ),
        "tempo": float(getattr(doc, "tempo", 0.0)),
        "song_length": float(getattr(doc, "song_length", 0.0)),
        "is_playing": bool(getattr(doc, "is_playing", False)),
        "num_tracks": len(list(getattr(doc, "tracks", []))),
        "num_scenes": len(list(getattr(doc, "scenes", []))),
    }


def _document_file_path():
    """Return the absolute path of the .als file on disk, or None if unsaved.

    Live's LOM doesn't expose this through a single canonical property — we
    try the call/attribute variants that have shipped across Live 10/11/12.
    """
    import Live  # type: ignore
    doc = Live.Application.get_application().get_document()
    # Method-form first (newer Live):
    fn = getattr(doc, "get_file_path", None)
    if callable(fn):
        try:
            p = fn()
            return p if p else None
        except Exception:
            pass
    # Property-form fallback (older Live):
    p = getattr(doc, "file_path", None)
    if p:
        return p
    return None


def get_file_path(c_instance, expected_set_path=None, **_):
    """Return native or explicitly inferred import identity, with its evidence."""
    path = _document_file_path()
    if path:
        return {"file_path": path, "path_evidence": "native"}
    snapshot = import_snapshot(c_instance, expected_set_path=expected_set_path)
    return {"file_path": snapshot["file_path"], "path_evidence": snapshot["path_evidence"]}


def list_freezing_dir(c_instance, **_):
    """List wav files in the project's `Samples/Freezing/` directory.

    Returns ``{"freezing_dir": <abs path or None>, "files": [...]}`` where
    each file is ``{"path": str, "mtime": float, "size": int}``. Used by
    the freeze-mode bouncer to detect which file Live wrote during a
    just-completed Track.freeze() call.

    If the project hasn't been saved yet, ``freezing_dir`` is None and
    ``files`` is empty — freeze itself fails in that state, so the bouncer
    surfaces it as a precondition error.
    """
    doc_path = get_file_path(c_instance)["file_path"]
    if not doc_path:
        return {"freezing_dir": None, "files": []}
    project_dir = os.path.dirname(doc_path)
    freezing_dir = os.path.join(project_dir, "Samples", "Freezing")
    if not os.path.isdir(freezing_dir):
        return {"freezing_dir": freezing_dir, "files": []}
    files = []
    for name in sorted(os.listdir(freezing_dir)):
        full = os.path.join(freezing_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            stat = os.stat(full)
            files.append({
                "path": full,
                "mtime": float(stat.st_mtime),
                "size": int(stat.st_size),
            })
        except OSError:
            continue
    return {"freezing_dir": freezing_dir, "files": files}


_BOUND_IMPORT = None
_IMPORT_PREVIEW = None


def _require_import_set(c_instance, expected_set_path):
    if expected_set_path:
        snapshot = import_snapshot(c_instance, expected_set_path=expected_set_path)
        actual = snapshot.get("file_path")
        if not actual or os.path.normcase(os.path.abspath(actual)) != os.path.normcase(os.path.abspath(expected_set_path)):
            raise RuntimeError("The active Live set changed; import verification stopped")


def _parameter_display(parameter):
    fn = getattr(parameter, "str_for_value", None)
    return str(fn(parameter.value)) if callable(fn) else str(parameter)


def import_snapshot(c_instance, expected_set_path=None, **_):
    """Read actual Arrangement state rather than AbletonOSC's cached clip lists."""
    global _BOUND_IMPORT
    import Live
    app = Live.Application.get_application()
    song = app.get_document()
    if not expected_set_path and _BOUND_IMPORT and _BOUND_IMPORT[0] == song:
        expected_set_path = _BOUND_IMPORT[1]
    tracks = []
    for index, track in enumerate(song.tracks):
        clips = []
        for clip in track.arrangement_clips:
            is_audio = bool(clip.is_audio_clip)
            clips.append({
                "name": str(clip.name), "start_beats": float(clip.start_time),
                "end_beats": float(clip.end_time), "audio": is_audio,
                "file_path": str(clip.file_path) if is_audio else None,
                "file_exists": os.path.isfile(clip.file_path) if is_audio else None,
                "warping": bool(clip.warping) if is_audio else None,
                "looping": bool(clip.looping), "muted": bool(clip.muted),
                "start_marker": float(clip.start_marker), "end_marker": float(clip.end_marker),
                "pitch_coarse": int(clip.pitch_coarse) if is_audio else None,
                "pitch_fine": int(clip.pitch_fine) if is_audio else None,
                "gain_display": str(clip.gain_display_string) if is_audio else None,
            })
        tracks.append({
            "track_index": index, "name": str(track.name), "muted": bool(track.mute),
            "solo": bool(track.solo), "armed": bool(track.arm) if track.can_be_armed else False,
            "volume": float(track.mixer_device.volume.value),
            "volume_display": _parameter_display(track.mixer_device.volume),
            "pan": float(track.mixer_device.panning.value),
            "devices": [str(device.class_name) for device in track.devices],
            "output": str(getattr(track.output_routing_type, "display_name", track.output_routing_type)),
            "meter_left": float(track.output_meter_left) if track.has_audio_output else 0.0,
            "meter_right": float(track.output_meter_right) if track.has_audio_output else 0.0,
            "clips": clips,
        })
    file_path = _document_file_path()
    path_evidence = "native" if file_path else "unavailable"
    # Some Live 11 builds omit a document path API. An imported set can
    # identify itself by all track names and every collected media path.
    # This is explicit inferred identity, never a claimed native document path.
    if not file_path and expected_set_path:
        import json
        expected = os.path.abspath(expected_set_path)
        manifest_path = os.path.join(os.path.dirname(expected), "import_manifest.json")
        if os.path.isfile(expected) and os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            rows = manifest.get("tracks", [])
            matches = len(rows) == len(tracks) and bool(rows)
            audio_matches = 0
            for row, track in zip(rows, tracks):
                matches = matches and row["name"] == track["name"]
                if row["role"] != "midi":
                    clips = track["clips"]
                    wanted = os.path.normcase(os.path.abspath(os.path.join(
                        os.path.dirname(expected), row["path"])))
                    actual = os.path.normcase(os.path.abspath(clips[0]["file_path"])) if len(clips) == 1 and clips[0]["file_path"] else ""
                    matches = matches and wanted == actual
                    audio_matches += 1
            if matches and audio_matches:
                file_path = expected
                path_evidence = "all_track_names_and_collected_media_paths"
                _BOUND_IMPORT = (song, expected)
    return {
        "file_path": file_path, "path_evidence": path_evidence,
        "name": str(getattr(song, "name", "")),
        "can_undo": bool(song.can_undo), "open_dialog_count": int(app.open_dialog_count),
        "live_major": app.get_major_version(),
        "live_version": "%d.%d.%d" % (app.get_major_version(), app.get_minor_version(), app.get_bugfix_version()),
        "tempo": float(song.tempo),
        "meter": [int(song.signature_numerator), int(song.signature_denominator)],
        "playing": bool(song.is_playing), "position": float(song.current_song_time),
        "loop": bool(song.loop), "recording": bool(song.record_mode),
        "overdub": bool(song.overdub), "metronome": bool(song.metronome),
        "back_to_arranger": bool(song.back_to_arranger), "tracks": tracks,
        "master_left": float(song.master_track.output_meter_left),
        "master_right": float(song.master_track.output_meter_right),
        "master_volume": float(song.master_track.mixer_device.volume.value),
        "master_volume_display": _parameter_display(song.master_track.mixer_device.volume),
    }


def import_configure(c_instance, expected_set_path=None, **_):
    _require_import_set(c_instance, expected_set_path)
    import Live
    app = Live.Application.get_application()
    song = app.get_document()
    song.stop_playing()
    song.loop = False
    song.record_mode = False
    song.overdub = False
    song.metronome = False
    song.back_to_arranger = False
    for track in song.tracks:
        track.solo = False
        if track.can_be_armed:
            track.arm = False
    song.current_song_time = 0
    app.view.show_view("Arranger")
    return import_snapshot(c_instance)


def import_seek(c_instance, position=0.0, preview_track=None, expected_set_path=None, **_):
    """Seek/play; optional isolated MIDI preview is restored by import_stop."""
    global _IMPORT_PREVIEW
    _require_import_set(c_instance, expected_set_path)
    import Live
    song = Live.Application.get_application().get_document()
    if preview_track is not None:
        if _IMPORT_PREVIEW is not None and not any(t in song.tracks for t, _, _ in _IMPORT_PREVIEW):
            _IMPORT_PREVIEW = None
        if _IMPORT_PREVIEW is not None:
            raise RuntimeError("A preview is already running")
        index = int(preview_track)
        if index < 0 or index >= len(song.tracks):
            raise ValueError("Preview track out of range")
        _IMPORT_PREVIEW = [(t, bool(t.mute), bool(t.solo)) for t in song.tracks]
        for i, track in enumerate(song.tracks):
            track.mute = i != index
            track.solo = False
    song.current_song_time = max(0.0, float(position))
    song.start_playing()
    return {"playing": True}


def import_stop(c_instance, expected_set_path=None, **_):
    global _IMPORT_PREVIEW
    _require_import_set(c_instance, expected_set_path)
    import Live
    song = Live.Application.get_application().get_document()
    song.stop_playing()
    if _IMPORT_PREVIEW is not None:
        saved = _IMPORT_PREVIEW
        _IMPORT_PREVIEW = None
        for track, muted, solo in saved:
            if track in song.tracks:
                track.mute, track.solo = muted, solo
    song.current_song_time = 0
    return {"playing": False}
