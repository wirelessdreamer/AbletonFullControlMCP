"""Browser handlers — walk Live's browser tree, search, and load items.

Live's `Application.browser` exposes top-level categories (`instruments`,
`audio_effects`, `midi_effects`, `drums`, `sounds`, `samples`, `plugins`,
`user_library`, `current_project`, `packs`). Each is a `BrowserItem` whose
`iter_children` yields nested `BrowserItem` objects. Loadable items have
`is_loadable == True`. Loading goes through `browser.load_item(item)` which
applies to whatever track is currently selected.

To address an item without round-tripping the full tree we use a
forward-slash-delimited "path" of human-readable names, e.g.

    "instruments/Operator/Synth Lead/Bright Mood"

Paths are resolved by walking `iter_children` at each step. Names are matched
case-insensitively and are taken from `BrowserItem.name`.

Live's API note: `BrowserItem.uri` exists in Live 11+ for hotswap purposes;
we expose it but don't rely on it for loading because some categories don't
support URI lookup.
"""

from __future__ import absolute_import


EXPORTS = (
    "tree",
    "list_at_path",
    "search",
    "load_device",
    "load_drum_kit",
    "load_sample",
)


_TOP_LEVEL_ATTRS = (
    "instruments",
    "audio_effects",
    "midi_effects",
    "drums",
    "sounds",
    "samples",
    "plugins",
    "user_library",
    "current_project",
    "packs",
)


# ----- helpers -----


def _get_browser():
    import Live  # type: ignore
    return Live.Application.get_application().browser


def _get_song():
    import Live  # type: ignore
    return Live.Application.get_application().get_document()


def _safe_iter_children(item):
    try:
        return list(item.iter_children)
    except Exception:
        # Some BrowserItems are leaf-only and raise if you ask for children.
        return []


def _item_summary(item, include_children=False):
    out = {
        "name": getattr(item, "name", None),
        "is_loadable": bool(getattr(item, "is_loadable", False)),
        "is_folder": bool(getattr(item, "is_folder", False)) if hasattr(item, "is_folder") else None,
    }
    try:
        out["uri"] = getattr(item, "uri", None)
    except Exception:
        out["uri"] = None
    if include_children:
        out["children"] = [_item_summary(c) for c in _safe_iter_children(item)]
    return out


def _resolve_top(category):
    browser = _get_browser()
    if category not in _TOP_LEVEL_ATTRS:
        raise ValueError("unknown browser category: %s" % category)
    try:
        return getattr(browser, category)
    except AttributeError:
        # Live 11 may not expose every category; surface a clear error.
        raise ValueError("browser category not available in this Live version: %s" % category)


def _walk_path(path):
    """Resolve "category/sub/sub/leaf" → BrowserItem."""
    if not path:
        raise ValueError("path must be non-empty")
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ValueError("path must contain at least one segment")
    current = _resolve_top(parts[0])
    for seg in parts[1:]:
        seg_lc = seg.lower()
        match = None
        for child in _safe_iter_children(current):
            if (getattr(child, "name", "") or "").lower() == seg_lc:
                match = child
                break
        if match is None:
            raise ValueError("path segment not found: %r under %r"
                             % (seg, getattr(current, "name", "?")))
        current = match
    return current


def _select_track(track_index):
    song = _get_song()
    tracks = list(song.tracks)
    if track_index < 0 or track_index >= len(tracks):
        raise ValueError("track_index %d out of range (0..%d)"
                         % (track_index, len(tracks) - 1))
    track = tracks[track_index]
    song.view.selected_track = track
    return track


def _select_clip(track_index, clip_index):
    track = _select_track(track_index)
    slots = list(track.clip_slots)
    if clip_index < 0 or clip_index >= len(slots):
        raise ValueError("clip_index %d out of range" % clip_index)
    slot = slots[clip_index]
    if not slot.has_clip:
        raise ValueError("no clip at track %d slot %d" % (track_index, clip_index))
    return slot.clip


# ----- handlers -----


def tree(c_instance, depth=2, **_):
    """Return the top-level browser tree, optionally with `depth` levels of children."""
    depth = max(0, int(depth))
    browser = _get_browser()
    out = {}
    for cat in _TOP_LEVEL_ATTRS:
        try:
            top = getattr(browser, cat)
        except AttributeError:
            continue
        out[cat] = _walk_with_depth(top, depth)
    return {"categories": out}


def _walk_with_depth(item, depth):
    node = _item_summary(item)
    if depth > 0:
        node["children"] = [_walk_with_depth(c, depth - 1) for c in _safe_iter_children(item)]
    return node


def list_at_path(c_instance, path="", **_):
    """List immediate children at `path`. Empty path lists the top categories."""
    if not path:
        return {"path": "", "children": [{"name": c, "is_loadable": False} for c in _TOP_LEVEL_ATTRS]}
    item = _walk_path(path)
    return {
        "path": path,
        "self": _item_summary(item),
        "children": [_item_summary(c) for c in _safe_iter_children(item)],
    }


def search(c_instance, query="", category=None, limit=200, **_):
    """Return browser items whose `name` contains `query` (case-insensitive)."""
    query_lc = (query or "").lower()
    limit = int(limit)
    results = []

    def visit(item, path):
        if len(results) >= limit:
            return
        name = getattr(item, "name", "") or ""
        if query_lc and query_lc in name.lower() and getattr(item, "is_loadable", False):
            results.append({"name": name, "path": path, "is_loadable": True,
                            "uri": getattr(item, "uri", None)})
        for child in _safe_iter_children(item):
            child_path = (path + "/" + (child.name or "?")) if path else (child.name or "?")
            visit(child, child_path)
            if len(results) >= limit:
                return

    if category:
        if category not in _TOP_LEVEL_ATTRS:
            raise ValueError("unknown category: %s" % category)
        visit(_resolve_top(category), category)
    else:
        for cat in _TOP_LEVEL_ATTRS:
            try:
                visit(_resolve_top(cat), cat)
            except ValueError:
                continue
            if len(results) >= limit:
                break
    return {"query": query, "category": category, "count": len(results), "results": results}


def _load(item, track_index):
    if not getattr(item, "is_loadable", False):
        raise ValueError("item %r is not loadable" % getattr(item, "name", "?"))
    _select_track(int(track_index))
    _get_browser().load_item(item)
    return {"loaded": getattr(item, "name", None), "track_index": int(track_index)}


def load_device(c_instance, path=None, uri=None, track_index=0, **_):
    """Load an instrument or effect onto `track_index`. Provide either path or uri."""
    if path is None and uri is None:
        raise ValueError("must supply 'path' or 'uri'")
    if path is not None:
        item = _walk_path(path)
    else:
        item = _find_by_uri(uri)
    return _load(item, int(track_index))


def load_drum_kit(c_instance, path=None, uri=None, track_index=0, **_):
    """Load a drum rack onto a track. Same as load_device but documents intent."""
    return load_device(c_instance, path=path, uri=uri, track_index=track_index)


def load_sample(c_instance, path=None, uri=None, track_index=0, clip_index=None, **_):
    """Load a sample onto an audio track. If clip_index is given, also focus that slot."""
    if path is None and uri is None:
        raise ValueError("must supply 'path' or 'uri'")
    track = _select_track(int(track_index))
    if clip_index is not None:
        slots = list(track.clip_slots)
        if 0 <= int(clip_index) < len(slots):
            _get_song().view.highlighted_clip_slot = slots[int(clip_index)]
    if path is not None:
        item = _walk_path(path)
    else:
        item = _find_by_uri(uri)
    if not getattr(item, "is_loadable", False):
        raise ValueError("sample item is not loadable: %r" % getattr(item, "name", "?"))
    _get_browser().load_item(item)
    return {
        "loaded": getattr(item, "name", None),
        "track_index": int(track_index),
        "clip_index": clip_index,
    }


def _find_by_uri(uri):
    """Best-effort URI lookup. Walks the tree until it finds a matching uri."""
    target = str(uri)

    def walk(item):
        try:
            if getattr(item, "uri", None) == target:
                return item
        except Exception:
            pass
        for child in _safe_iter_children(item):
            hit = walk(child)
            if hit is not None:
                return hit
        return None

    for cat in _TOP_LEVEL_ATTRS:
        try:
            top = _resolve_top(cat)
        except ValueError:
            continue
        hit = walk(top)
        if hit is not None:
            return hit
    raise ValueError("no browser item with uri=%r" % uri)
