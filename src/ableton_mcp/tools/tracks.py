"""Track creation, mixer state, sends, color, fold/group state, monitoring, meters."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def track_list() -> list[dict[str, Any]]:
        """Summarise every track: index, name, mute, solo, arm, grouped/foldable/visible flags, IO capability."""
        client = await get_client()
        n = int((await client.request("/live/song/get/num_tracks"))[0])
        names = await client.request("/live/song/get/track_names")
        out: list[dict[str, Any]] = []
        for i in range(n):
            mute = (await client.request("/live/track/get/mute", i))[1]
            solo = (await client.request("/live/track/get/solo", i))[1]
            arm = (await client.request("/live/track/get/arm", i))[1]
            can_arm = (await client.request("/live/track/get/can_be_armed", i))[1]
            has_midi_in = (await client.request("/live/track/get/has_midi_input", i))[1]
            has_audio_in = (await client.request("/live/track/get/has_audio_input", i))[1]
            has_midi_out = (await client.request("/live/track/get/has_midi_output", i))[1]
            has_audio_out = (await client.request("/live/track/get/has_audio_output", i))[1]
            is_grouped = (await client.request("/live/track/get/is_grouped", i))[1]
            is_foldable = (await client.request("/live/track/get/is_foldable", i))[1]
            is_visible = (await client.request("/live/track/get/is_visible", i))[1]
            color_index = (await client.request("/live/track/get/color_index", i))[1]
            out.append(
                {
                    "index": i,
                    "name": names[i] if i < len(names) else f"Track {i+1}",
                    "mute": bool(mute),
                    "solo": bool(solo),
                    "armed": bool(arm),
                    "can_be_armed": bool(can_arm),
                    "has_midi_input": bool(has_midi_in),
                    "has_audio_input": bool(has_audio_in),
                    "has_midi_output": bool(has_midi_out),
                    "has_audio_output": bool(has_audio_out),
                    "is_grouped": bool(is_grouped),
                    "is_foldable": bool(is_foldable),
                    "is_visible": bool(is_visible),
                    "color_index": int(color_index),
                }
            )
        return out

    @mcp.tool()
    async def track_get(track_index: int) -> dict[str, Any]:
        """Detailed snapshot of a single track including mixer state, monitoring, fired/playing slots."""
        client = await get_client()
        async def g(addr: str) -> Any:
            args = await client.request(addr, int(track_index))
            return args[1] if len(args) > 1 else None
        return {
            "index": track_index,
            "name": (await client.request("/live/song/get/track_names"))[track_index],
            "volume": float(await g("/live/track/get/volume")),
            "panning": float(await g("/live/track/get/panning")),
            "mute": bool(await g("/live/track/get/mute")),
            "solo": bool(await g("/live/track/get/solo")),
            "arm": bool(await g("/live/track/get/arm")),
            "color_index": int(await g("/live/track/get/color_index")),
            "color": int(await g("/live/track/get/color")),
            "monitoring_state": int(await g("/live/track/get/current_monitoring_state")),
            "fold_state": int(await g("/live/track/get/fold_state") or 0),
            "is_grouped": bool(await g("/live/track/get/is_grouped")),
            "is_foldable": bool(await g("/live/track/get/is_foldable")),
            "is_visible": bool(await g("/live/track/get/is_visible")),
            "fired_slot_index": int(await g("/live/track/get/fired_slot_index")),
            "playing_slot_index": int(await g("/live/track/get/playing_slot_index")),
            "input_routing_type": await g("/live/track/get/input_routing_type"),
            "input_routing_channel": await g("/live/track/get/input_routing_channel"),
            "output_routing_type": await g("/live/track/get/output_routing_type"),
            "output_routing_channel": await g("/live/track/get/output_routing_channel"),
        }

    @mcp.tool()
    async def track_get_meters(track_index: int) -> dict[str, float]:
        """Return current output meters (left, right, mono level) for a track. Sampled instantaneously."""
        client = await get_client()
        left = (await client.request("/live/track/get/output_meter_left", int(track_index)))[1]
        right = (await client.request("/live/track/get/output_meter_right", int(track_index)))[1]
        level = (await client.request("/live/track/get/output_meter_level", int(track_index)))[1]
        return {"track": track_index, "left": float(left), "right": float(right), "level": float(level)}

    # --- Creation / lifecycle ---

    @mcp.tool()
    async def track_create_midi(at_index: int = -1, name: str | None = None) -> dict[str, Any]:
        """Create a MIDI track. at_index=-1 appends at the end."""
        client = await get_client()
        client.send("/live/song/create_midi_track", int(at_index))
        n = int((await client.request("/live/song/get/num_tracks"))[0])
        new_index = n - 1 if at_index < 0 else at_index
        if name:
            client.send("/live/track/set/name", new_index, name)
        return {"index": new_index, "name": name}

    @mcp.tool()
    async def track_create_audio(at_index: int = -1, name: str | None = None) -> dict[str, Any]:
        """Create an audio track."""
        client = await get_client()
        client.send("/live/song/create_audio_track", int(at_index))
        n = int((await client.request("/live/song/get/num_tracks"))[0])
        new_index = n - 1 if at_index < 0 else at_index
        if name:
            client.send("/live/track/set/name", new_index, name)
        return {"index": new_index, "name": name}

    @mcp.tool()
    async def track_create_return(name: str | None = None) -> dict[str, Any]:
        """Create a return track."""
        (await get_client()).send("/live/song/create_return_track")
        return {"status": "return_created", "name": name}

    @mcp.tool()
    async def track_delete(track_index: int) -> dict[str, int]:
        """Delete a regular track by index."""
        (await get_client()).send("/live/song/delete_track", int(track_index))
        return {"deleted_index": track_index}

    @mcp.tool()
    async def track_delete_return(return_track_index: int) -> dict[str, int]:
        """Delete a return track by its return-track index."""
        (await get_client()).send("/live/song/delete_return_track", int(return_track_index))
        return {"deleted_return_index": return_track_index}

    @mcp.tool()
    async def track_duplicate(track_index: int) -> dict[str, int]:
        """Duplicate a track by index."""
        (await get_client()).send("/live/song/duplicate_track", int(track_index))
        return {"duplicated_from": track_index}

    # --- Mixer state ---

    @mcp.tool()
    async def track_set_name(track_index: int, name: str) -> dict[str, Any]:
        (await get_client()).send("/live/track/set/name", int(track_index), str(name))
        return {"index": track_index, "name": name}

    @mcp.tool()
    async def track_set_volume(track_index: int, volume: float) -> dict[str, Any]:
        """Set track volume (0.0..1.0; ~0.85 ≈ 0 dB)."""
        (await get_client()).send("/live/track/set/volume", int(track_index), float(volume))
        return {"index": track_index, "volume": volume}

    @mcp.tool()
    async def track_set_pan(track_index: int, pan: float) -> dict[str, Any]:
        """Set track panning (-1.0 left .. 1.0 right)."""
        (await get_client()).send("/live/track/set/panning", int(track_index), float(pan))
        return {"index": track_index, "pan": pan}

    @mcp.tool()
    async def track_set_mute(track_index: int, muted: bool) -> dict[str, Any]:
        (await get_client()).send("/live/track/set/mute", int(track_index), 1 if muted else 0)
        return {"index": track_index, "mute": muted}

    @mcp.tool()
    async def track_set_solo(track_index: int, solo: bool) -> dict[str, Any]:
        (await get_client()).send("/live/track/set/solo", int(track_index), 1 if solo else 0)
        return {"index": track_index, "solo": solo}

    @mcp.tool()
    async def track_set_arm(track_index: int, armed: bool) -> dict[str, Any]:
        (await get_client()).send("/live/track/set/arm", int(track_index), 1 if armed else 0)
        return {"index": track_index, "arm": armed}

    @mcp.tool()
    async def track_set_send(track_index: int, send_index: int, value: float) -> dict[str, Any]:
        """Set the level of a send (0.0..1.0) on a track."""
        (await get_client()).send(
            "/live/track/set/send", int(track_index), int(send_index), float(value)
        )
        return {"track": track_index, "send": send_index, "value": value}

    @mcp.tool()
    async def track_set_color_index(track_index: int, color_index: int) -> dict[str, Any]:
        """Set track color from Live's palette (0..69)."""
        (await get_client()).send(
            "/live/track/set/color_index", int(track_index), int(color_index)
        )
        return {"index": track_index, "color_index": color_index}

    @mcp.tool()
    async def track_set_color(track_index: int, color: int) -> dict[str, Any]:
        """Set track color as a 24-bit RGB int (e.g. 0xFF8800)."""
        (await get_client()).send("/live/track/set/color", int(track_index), int(color))
        return {"index": track_index, "color": color}

    @mcp.tool()
    async def track_set_fold(track_index: int, folded: bool) -> dict[str, Any]:
        """Fold/unfold a group track."""
        (await get_client()).send(
            "/live/track/set/fold_state", int(track_index), 1 if folded else 0
        )
        return {"index": track_index, "folded": folded}

    @mcp.tool()
    async def track_set_monitoring(track_index: int, state: int) -> dict[str, Any]:
        """Set monitoring state on an audio/midi track. 0=In, 1=Auto, 2=Off."""
        (await get_client()).send(
            "/live/track/set/current_monitoring_state", int(track_index), int(state)
        )
        return {"index": track_index, "monitoring": ["In", "Auto", "Off"][state] if state in (0, 1, 2) else state}

    @mcp.tool()
    async def track_stop_all_clips(track_index: int) -> dict[str, int]:
        """Stop all clips on a track."""
        (await get_client()).send("/live/track/stop_all_clips", int(track_index))
        return {"track": track_index}

    @mcp.tool()
    async def track_get_send(track_index: int, send_index: int) -> dict[str, Any]:
        """Get the current value of a send."""
        client = await get_client()
        args = await client.request("/live/track/get/send", int(track_index), int(send_index))
        return {"track": track_index, "send": send_index, "value": float(args[2])}
