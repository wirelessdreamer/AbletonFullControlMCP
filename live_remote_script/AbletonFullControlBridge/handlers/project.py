"""Project-level ops: save (and a few introspection helpers)."""

from __future__ import absolute_import


EXPORTS = (
    "save",
    "info",
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
