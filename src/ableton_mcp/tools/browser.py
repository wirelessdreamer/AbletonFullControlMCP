"""Browser / preset loading.

Lives on the AbletonFullControlBridge JSON-over-TCP channel (port 11002) because
AbletonOSC does not expose Live's browser tree. See
`live_remote_script/AbletonFullControlBridge/README.md` for the wire protocol.

If the bridge isn't running we surface a clear error including the install
command.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bridge_client import (
    AbletonBridgeError,
    AbletonBridgeUnavailable,
    get_bridge_client,
)


_INSTALL_HINT = (
    "AbletonFullControlBridge is not reachable on TCP/11002. "
    "Run `python -m ableton_mcp.scripts.install_bridge` and enable "
    "'AbletonFullControlBridge' in Live → Preferences → Link/Tempo/MIDI → Control Surface."
)


async def _call(op: str, **args: Any) -> dict[str, Any]:
    bridge = get_bridge_client()
    try:
        return await bridge.call(op, **args)
    except AbletonBridgeUnavailable as exc:
        return {"ok": False, "error": str(exc), "hint": _INSTALL_HINT}
    except AbletonBridgeError as exc:
        return {"ok": False, "error": str(exc)}


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def browser_get_tree(depth: int = 2) -> dict[str, Any]:
        """Walk Live's browser. `depth` controls how many child levels are returned."""
        return await _call("browser.tree", depth=int(depth))

    @mcp.tool()
    async def browser_list_at_path(path: str) -> dict[str, Any]:
        """List immediate children at a slash-delimited browser path.

        Examples: `"instruments"`, `"instruments/Operator"`, `"samples/Drums"`.
        Top-level categories: instruments, audio_effects, midi_effects, drums,
        sounds, samples, plugins, user_library, current_project, packs.
        """
        return await _call("browser.list_at_path", path=str(path))

    @mcp.tool()
    async def browser_search(query: str, category: str | None = None) -> dict[str, Any]:
        """Substring-search Live's browser (case-insensitive). `category` narrows the scope.

        Returns up to 200 loadable items as `{name, path, is_loadable, uri}`.
        Use the returned `path` with `browser_load_device` / `browser_load_sample`.
        """
        return await _call("browser.search", query=str(query), category=category)

    @mcp.tool()
    async def browser_load_device(uri: str, track_index: int) -> dict[str, Any]:
        """Load a device (instrument or effect) onto a track.

        `uri` may be either a slash-delimited browser path (preferred — what
        `browser_search` returns as `path`) or a Live browser URI.
        """
        # Heuristic: paths contain '/'; URIs typically don't (or use ':' / 'live:').
        if "/" in uri and not uri.startswith(("live:", "query:")):
            return await _call("browser.load_device", path=uri, track_index=int(track_index))
        return await _call("browser.load_device", uri=uri, track_index=int(track_index))

    @mcp.tool()
    async def browser_load_drum_kit(uri: str, track_index: int) -> dict[str, Any]:
        """Load a drum rack onto a track. Accepts a path or URI as `browser_load_device`."""
        if "/" in uri and not uri.startswith(("live:", "query:")):
            return await _call("browser.load_drum_kit", path=uri, track_index=int(track_index))
        return await _call("browser.load_drum_kit", uri=uri, track_index=int(track_index))

    @mcp.tool()
    async def browser_load_sample(
        uri: str, track_index: int, clip_index: int | None = None
    ) -> dict[str, Any]:
        """Load a sample onto an audio track. Optionally focus a specific clip slot first."""
        kwargs: dict[str, Any] = {"track_index": int(track_index)}
        if clip_index is not None:
            kwargs["clip_index"] = int(clip_index)
        if "/" in uri and not uri.startswith(("live:", "query:")):
            kwargs["path"] = uri
        else:
            kwargs["uri"] = uri
        return await _call("browser.load_sample", **kwargs)
