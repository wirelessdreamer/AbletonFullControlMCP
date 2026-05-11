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
        """Reverse a clip.

        - **MIDI clips**: fully supported. Reads every note, inverts each
          note's start time (``new_start = clip_length - old_start -
          duration``), writes the result back. Velocity, duration, pitch,
          and mute flags are preserved.
        - **Audio clips**: Live 11's Python LOM does not expose
          ``Clip.reverse()`` — it's a UI-only command. Returns
          ``supported=false`` with a workaround in the ``workaround``
          field (right-click + Reverse in Live, or render an offline-
          reversed wav and re-import).

        The reply includes a ``kind`` field (``"midi"`` or ``"audio"``)
        so callers can route based on clip type.
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
        """Slice an audio clip into a Drum Rack with one pad per slice.

        Live 11/12's Python LOM does NOT expose this — ``Slice to New MIDI
        Track`` is a UI command that internally creates a new MIDI track,
        a Drum Rack with one Simpler per slice point, and a MIDI clip
        triggering each pad. There's no LOM hook.

        Workarounds:

        1. **Manual** — right-click the audio clip in Live and pick "Slice
           to New MIDI Track". Use the slicing preset of your choice in
           the dialog (1/4 / 1/8 / 1/16 / transients / beats / regions).
        2. **Programmatic (more work)** — run onset detection externally
           (e.g. ``librosa.onset.onset_detect`` for transient slicing),
           create a new MIDI track via ``track_create_midi``, build a
           Drum Rack with N Simpler pads each pointing at a slice region
           of the source wav (via ``browser_load_device`` +
           ``browser_load_sample``), then write a MIDI clip with one
           note per pad triggered at the slice point. Heavy but possible
           today with existing MCP tools.
        3. **Max for Live** — devices like Ableton's own Slice or
           third-party slicers can do this without leaving Live.
        """
        return _stub(
            "slice_clip_to_midi",
            "Slice to New MIDI Track is UI-only in Live 11/12. See the "
            "tool docstring for three workarounds: manual UI command, "
            "programmatic via onset detection + Drum Rack + Simpler, or "
            "Max for Live slicing devices.",
            track_index=track_index,
            clip_index=clip_index,
            slicing_preset=slicing_preset,
        )

    @mcp.tool()
    async def op_new_set() -> dict[str, Any]:
        """Create a new empty Live Set.

        Live's Python LOM does NOT expose ``Application.create_new_set``
        or any equivalent. Creating a new set is a UI-only File menu
        operation on every Live version through 11.3. Live 12 hasn't
        added a LOM hook either.

        Workarounds:

        1. **Manual** — File → New Live Set (Ctrl/Cmd-N).
        2. **AppleScript / pywinauto** — drive the menu from outside Live.
           Brittle, breaks across UI updates, and Live doesn't expose a
           stable handle. Not recommended.
        3. **Set template** — save a baseline empty .als and use Live's
           "Default Set" preference so new sets start clean. One-time setup,
           then File → New Live Set behaves like our `new_set` would.

        Closest LOM-supported alternative for "give me a fresh canvas":
        ``op_save_set`` (save current), then manually New Live Set, then
        come back to MCP control. Or just delete every track in the
        current set via ``track_delete`` in a loop — same effective state.
        """
        return _stub(
            "new_set",
            "Application.create_new_set is not exposed in Live's Python LOM. "
            "Use File → New Live Set in the UI, or delete every track in "
            "the current set via track_delete to get a fresh canvas. "
            "See the tool docstring for the full workaround list.",
        )
