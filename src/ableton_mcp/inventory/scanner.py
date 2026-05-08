"""Walk Live's browser via the bridge, return a flat list of loadable items.

This is the cheap (read-only, non-Live-mutating) half of the inventory
pipeline: it ONLY queries `browser.list_at_path` on the AbletonFullControlBridge.
It does not load anything, so it is safe to call against a user's session
at any time and is also what `dry_run=True` uses inside the bulk operation.

The output is a flat ``list[BrowserItem]`` rather than a tree. Each item
keeps its ``tree_path`` (the list of parent names) so callers can render
or filter as they wish.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from ..bridge_client import AbletonBridgeClient, get_bridge_client


log = logging.getLogger(__name__)


# Top-level browser categories that the bridge exposes. These mirror
# `_TOP_LEVEL_ATTRS` in `live_remote_script/AbletonFullControlBridge/handlers/browser.py`.
BROWSER_CATEGORIES: tuple[str, ...] = (
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


# Categories that participate in "load on a track and dump params" workflow.
# `samples`, `sounds`, `current_project`, `packs`, `user_library` are leaf-y
# or organisational and don't get probed by default — but they ARE returned
# by `scan_browser` so callers can list them.
PROBEABLE_CATEGORIES: frozenset[str] = frozenset(
    {"instruments", "audio_effects", "midi_effects", "drums", "plugins"}
)


@dataclass
class BrowserItem:
    """A single loadable (or folder) browser entry."""

    name: str
    uri: Optional[str]
    category: str
    is_loadable: bool
    tree_path: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        """Slash-delimited path the bridge accepts (e.g. ``instruments/Operator``)."""
        return "/".join(self.tree_path)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = self.path
        return d


async def scan_browser(
    category: Optional[str] = None,
    *,
    client: Optional[AbletonBridgeClient] = None,
    max_depth: int = 8,
    sleep: float = 0.0,
) -> list[BrowserItem]:
    """Walk Live's browser and return a flat list of every loadable item.

    Args:
        category: One of :data:`BROWSER_CATEGORIES` to scope the walk;
            ``None`` walks every category we know about.
        client: Override for tests; defaults to the process-wide singleton.
        max_depth: Safety guard against pathological circular trees in
            third-party packs.
        sleep: Optional pause (seconds) between bridge calls so a huge
            library doesn't hammer the Remote Script.

    Returns:
        A flat ``list[BrowserItem]``. Folders ARE included (with
        ``is_loadable=False``) so the caller can show structure if they
        want, but they're easily filtered out.
    """
    bridge = client or get_bridge_client()
    targets: Iterable[str]
    if category is None:
        targets = BROWSER_CATEGORIES
    else:
        if category not in BROWSER_CATEGORIES:
            raise ValueError(
                f"unknown category {category!r}; expected one of {BROWSER_CATEGORIES}"
            )
        targets = (category,)

    out: list[BrowserItem] = []
    for cat in targets:
        try:
            await _walk_category(bridge, cat, [cat], out, max_depth, sleep)
        except Exception as exc:  # noqa: BLE001 — third-party packs raise weird things
            log.warning("scan_browser: walk of %s failed: %s", cat, exc)
    return out


async def _walk_category(
    bridge: AbletonBridgeClient,
    category: str,
    path: list[str],
    out: list[BrowserItem],
    depth_left: int,
    sleep: float,
) -> None:
    if depth_left <= 0:
        return
    path_str = "/".join(path)
    try:
        listing = await bridge.call("browser.list_at_path", path=path_str)
    except Exception as exc:  # noqa: BLE001
        log.debug("list_at_path %s failed: %s", path_str, exc)
        return
    children = (listing or {}).get("children") or []
    for child in children:
        name = child.get("name") or "?"
        is_loadable = bool(child.get("is_loadable"))
        uri = child.get("uri")
        child_path = path + [name]
        item = BrowserItem(
            name=name,
            uri=uri,
            category=category,
            is_loadable=is_loadable,
            tree_path=list(child_path),
        )
        out.append(item)
        # Recurse into folders. We treat anything non-loadable as a
        # potential folder; the bridge returns an empty children list for
        # leaves so the recursion bottoms out cheaply.
        if not is_loadable:
            if sleep:
                await asyncio.sleep(sleep)
            await _walk_category(bridge, category, child_path, out, depth_left - 1, sleep)
