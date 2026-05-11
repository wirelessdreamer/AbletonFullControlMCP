"""Project-level ops: save (and a few introspection helpers)."""

from __future__ import absolute_import

import os


EXPORTS = (
    "save",
    "info",
    "get_file_path",
    "list_freezing_dir",
)


def save(c_instance, **_):
    """Save the current set. Equivalent to Cmd-S.

    `Application.get_document().save()` invokes Live's normal save path. If the
    set has never been saved Live will pop a Save As dialog on the main thread,
    which is the same behaviour as pressing Cmd-S in the UI.

    Save-As to a specific path is *not* exposed in Live's Python API — it
    requires a UI dialog. We document this by returning `save_as_supported:
    False` if the caller passes `path`.
    """
    import Live  # type: ignore
    app = Live.Application.get_application()
    doc = app.get_document()
    save_fn = getattr(doc, "save", None)
    if save_fn is None:
        raise RuntimeError("Application.get_document().save not available")
    save_fn()
    return {"saved": True, "save_as_supported": False}


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


def get_file_path(c_instance, **_):
    """Return the .als file path for the open project, or None if unsaved."""
    return {"file_path": _document_file_path()}


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
    doc_path = _document_file_path()
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
