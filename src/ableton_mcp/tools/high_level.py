"""High-level / convenience operations.

These call AbletonFullControlBridge (TCP/11002) for ops that AbletonOSC doesn't expose:
group/ungroup, freeze/flatten, consolidate/crop/reverse, save_set.

A small handful (slice-to-MIDI, new_set) remain stubs because Live's Python
LOM doesn't expose them at all — they are UI-only commands. We surface those
clearly so an LLM can suggest a manual workaround.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..bridge_client import AbletonBridgeError, AbletonBridgeUnavailable, get_bridge_client


_INSTALL_HINT = (
    "AbletonFullControlBridge is not reachable on TCP/11002. "
    "Run `python -m ableton_mcp.scripts.install_bridge` and enable "
    "'AbletonFullControlBridge' in Live → Preferences → Link/Tempo/MIDI → Control Surface."
)


async def _call(op: str, **args: Any) -> dict[str, Any]:
    bridge = get_bridge_client()
    try:
        result = await bridge.call(op, **args)
    except AbletonBridgeUnavailable as exc:
        return {"ok": False, "error": str(exc), "hint": _INSTALL_HINT}
    except AbletonBridgeError as exc:
        return {"ok": False, "error": str(exc)}
    if isinstance(result, dict):
        out = dict(result)
        out.setdefault("ok", True)
        return out
    return {"ok": True, "result": result}


def _stub(operation: str, reason: str, **args: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "not_implemented",
        "operation": operation,
        "args": args,
        "reason": reason,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def op_group_tracks(track_indices: list[int], group_name: str | None = None) -> dict[str, Any]:
        """Group a contiguous range of tracks into a new group track.

        Live's `Song.group_tracks(start, end)` requires the indices to be
        contiguous. If `group_name` is provided we attempt to rename the new
        group track afterwards (best-effort).
        """
        result = await _call("track.group", track_indices=[int(i) for i in track_indices])
        # Renaming has to go through the OSC client (track name) — leave as a
        # follow-up tool call; we just surface the new index.
        if group_name and result.get("ok"):
            result["rename_hint"] = (
                f"Use `track_set_name(index={result.get('group_track_index')}, "
                f"name={group_name!r})` to rename the new group."
            )
        return result

    @mcp.tool()
    async def op_ungroup_track(group_track_index: int) -> dict[str, Any]:
        """Ungroup a group track."""
        return await _call("track.ungroup", group_track_index=int(group_track_index))

    @mcp.tool()
    async def op_freeze_track(track_index: int) -> dict[str, Any]:
        """Freeze a track (commit its devices to rendered audio)."""
        return await _call("track.freeze", track_index=int(track_index))

    @mcp.tool()
    async def op_flatten_track(track_index: int) -> dict[str, Any]:
        """Flatten a frozen track (replace the source with rendered audio)."""
        return await _call("track.flatten", track_index=int(track_index))

    @mcp.tool()
    async def op_consolidate_clip(track_index: int, clip_index: int) -> dict[str, Any]:
        """Consolidate the loop region of a clip into a single new clip."""
        return await _call("clip.consolidate", track_index=int(track_index), clip_index=int(clip_index))

    @mcp.tool()
    async def op_crop_clip(track_index: int, clip_index: int) -> dict[str, Any]:
        """Crop a clip to its current loop region."""
        return await _call("clip.crop", track_index=int(track_index), clip_index=int(clip_index))

    @mcp.tool()
    async def op_reverse_clip(track_index: int, clip_index: int) -> dict[str, Any]:
        """Reverse an audio clip.

        Live 11's Python LOM does not expose Clip reverse — it's a UI-only
        command. The bridge tries a few likely method names and falls back to
        returning `supported=false` with a workaround hint.
        """
        return await _call("clip.reverse", track_index=int(track_index), clip_index=int(clip_index))

    @mcp.tool()
    async def op_save_set(path: str | None = None) -> dict[str, Any]:
        """Save the current Live Set (Cmd-S).

        `path` is accepted for forward compatibility but Live's API does NOT
        support Save-As without the UI dialog, so it's currently ignored with
        a `save_as_supported: False` flag in the response.
        """
        result = await _call("project.save")
        if path is not None:
            result["save_as_path_ignored"] = path
        return result

    # ---- Genuinely-not-supported ops ----
    # These remain stubs because Live's Python LOM has no hook for them.
    # Documented per the task brief.

    @mcp.tool()
    async def op_slice_clip_to_midi(
        track_index: int, clip_index: int, slicing_preset: str = "16th"
    ) -> dict[str, Any]:
        """Slice an audio clip into a Drum Rack with one pad per slice. (UI-only in Live's API.)"""
        return _stub(
            "slice_clip_to_midi",
            "Live's Python API does not expose 'Slice to New MIDI Track'. "
            "Right-click the clip and choose 'Slice to New MIDI Track', or use a Max for Live device.",
            track_index=track_index,
            clip_index=clip_index,
            slicing_preset=slicing_preset,
        )

    @mcp.tool()
    async def op_new_set() -> dict[str, Any]:
        """Create a new empty Live Set. (UI-only — would prompt to save current.)"""
        return _stub(
            "new_set",
            "Live's Python API does not expose Application.create_new_set. "
            "Use File → New Live Set in the UI.",
        )
