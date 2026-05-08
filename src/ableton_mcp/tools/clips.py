"""Clip creation, transport, MIDI note manipulation, gain/pitch/launch/warp config."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import chunked, get_client

WARP_MODES = ["Beats", "Tones", "Texture", "Re-Pitch", "Complex", "Complex Pro"]
LAUNCH_MODES = ["Trigger", "Gate", "Toggle", "Repeat"]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def clip_list(track_index: int) -> list[dict[str, Any]]:
        """List clips on a track (Session view): slot index, name, length, color."""
        client = await get_client()
        names = await client.request("/live/track/get/clips/name", int(track_index))
        lengths = await client.request("/live/track/get/clips/length", int(track_index))
        colors = await client.request("/live/track/get/clips/color", int(track_index))

        def parse_pairs(args: tuple[Any, ...]) -> dict[int, Any]:
            if not args:
                return {}
            return {int(args[i]): args[i + 1] for i in range(1, len(args) - 1, 2)}

        names_m = parse_pairs(names)
        lengths_m = parse_pairs(lengths)
        colors_m = parse_pairs(colors)
        all_slots = sorted(set(names_m) | set(lengths_m) | set(colors_m))
        out: list[dict[str, Any]] = []
        for slot_idx in all_slots:
            out.append(
                {
                    "track_index": track_index,
                    "clip_index": slot_idx,
                    "name": names_m.get(slot_idx),
                    "length_beats": lengths_m.get(slot_idx),
                    "color": colors_m.get(slot_idx),
                }
            )
        return out

    @mcp.tool()
    async def clip_get(track_index: int, clip_index: int) -> dict[str, Any]:
        """Detailed snapshot of a single clip (covers most LOM clip properties)."""
        client = await get_client()
        async def g(addr: str) -> Any:
            args = await client.request(addr, int(track_index), int(clip_index))
            return args[2] if len(args) > 2 else None
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "name": await g("/live/clip/get/name"),
            "length": float(await g("/live/clip/get/length") or 0),
            "color": int(await g("/live/clip/get/color") or 0),
            "color_index": int(await g("/live/clip/get/color_index") or 0),
            "is_audio_clip": bool(await g("/live/clip/get/is_audio_clip")),
            "is_midi_clip": bool(await g("/live/clip/get/is_midi_clip")),
            "is_playing": bool(await g("/live/clip/get/is_playing")),
            "is_recording": bool(await g("/live/clip/get/is_recording")),
            "is_overdubbing": bool(await g("/live/clip/get/is_overdubbing")),
            "will_record_on_start": bool(await g("/live/clip/get/will_record_on_start")),
            "playing_position": float(await g("/live/clip/get/playing_position") or 0),
            "loop_start": float(await g("/live/clip/get/loop_start") or 0),
            "loop_end": float(await g("/live/clip/get/loop_end") or 0),
            "start_marker": float(await g("/live/clip/get/start_marker") or 0),
            "end_marker": float(await g("/live/clip/get/end_marker") or 0),
            "position": float(await g("/live/clip/get/position") or 0),
            "muted": bool(await g("/live/clip/get/muted")),
            "gain": float(await g("/live/clip/get/gain") or 0),
            "pitch_coarse": int(await g("/live/clip/get/pitch_coarse") or 0),
            "pitch_fine": float(await g("/live/clip/get/pitch_fine") or 0),
            "warping": bool(await g("/live/clip/get/warping")),
            "warp_mode": int(await g("/live/clip/get/warp_mode") or 0),
            "ram_mode": bool(await g("/live/clip/get/ram_mode")),
            "launch_mode": int(await g("/live/clip/get/launch_mode") or 0),
            "launch_quantization": int(await g("/live/clip/get/launch_quantization") or 0),
            "legato": bool(await g("/live/clip/get/legato")),
            "velocity_amount": float(await g("/live/clip/get/velocity_amount") or 0),
            "has_groove": bool(await g("/live/clip/get/has_groove")),
            "file_path": await g("/live/clip/get/file_path"),
        }

    # --- Lifecycle ---

    @mcp.tool()
    async def clip_create_midi(
        track_index: int, clip_index: int, length_beats: float = 4.0
    ) -> dict[str, Any]:
        """Create an empty MIDI clip in a Session slot."""
        (await get_client()).send(
            "/live/clip_slot/create_clip",
            int(track_index),
            int(clip_index),
            float(length_beats),
        )
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "length_beats": length_beats,
        }

    @mcp.tool()
    async def clip_delete(track_index: int, clip_index: int) -> dict[str, int]:
        """Delete a clip from a Session slot."""
        (await get_client()).send(
            "/live/clip_slot/delete_clip", int(track_index), int(clip_index)
        )
        return {"track_index": track_index, "clip_index": clip_index}

    @mcp.tool()
    async def clip_duplicate_loop(track_index: int, clip_index: int) -> dict[str, int]:
        """Duplicate the clip's loop region to extend its content."""
        (await get_client()).send(
            "/live/clip/duplicate_loop", int(track_index), int(clip_index)
        )
        return {"track_index": track_index, "clip_index": clip_index, "status": "loop_duplicated"}

    # --- Transport / launch ---

    @mcp.tool()
    async def clip_fire(track_index: int, clip_index: int) -> dict[str, int]:
        """Trigger a clip."""
        (await get_client()).send("/live/clip/fire", int(track_index), int(clip_index))
        return {"track_index": track_index, "clip_index": clip_index, "status": "fired"}

    @mcp.tool()
    async def clip_stop(track_index: int, clip_index: int) -> dict[str, int]:
        """Stop a clip."""
        (await get_client()).send("/live/clip/stop", int(track_index), int(clip_index))
        return {"track_index": track_index, "clip_index": clip_index, "status": "stopped"}

    # --- Naming, color, mute ---

    @mcp.tool()
    async def clip_set_name(track_index: int, clip_index: int, name: str) -> dict[str, Any]:
        (await get_client()).send(
            "/live/clip/set/name", int(track_index), int(clip_index), str(name)
        )
        return {"track_index": track_index, "clip_index": clip_index, "name": name}

    @mcp.tool()
    async def clip_set_color_index(track_index: int, clip_index: int, color_index: int) -> dict[str, Any]:
        """Set clip color by Live palette index (0..69)."""
        (await get_client()).send(
            "/live/clip/set/color_index", int(track_index), int(clip_index), int(color_index)
        )
        return {"track_index": track_index, "clip_index": clip_index, "color_index": color_index}

    @mcp.tool()
    async def clip_set_color(track_index: int, clip_index: int, color: int) -> dict[str, Any]:
        """Set clip color as a 24-bit RGB int."""
        (await get_client()).send(
            "/live/clip/set/color", int(track_index), int(clip_index), int(color)
        )
        return {"track_index": track_index, "clip_index": clip_index, "color": color}

    @mcp.tool()
    async def clip_set_muted(track_index: int, clip_index: int, muted: bool) -> dict[str, Any]:
        (await get_client()).send(
            "/live/clip/set/muted", int(track_index), int(clip_index), 1 if muted else 0
        )
        return {"track_index": track_index, "clip_index": clip_index, "muted": muted}

    # --- Loop / markers / position ---

    @mcp.tool()
    async def clip_set_loop(
        track_index: int, clip_index: int, loop_start: float, loop_end: float
    ) -> dict[str, Any]:
        client = await get_client()
        client.send("/live/clip/set/loop_start", int(track_index), int(clip_index), float(loop_start))
        client.send("/live/clip/set/loop_end", int(track_index), int(clip_index), float(loop_end))
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "loop_start": loop_start,
            "loop_end": loop_end,
        }

    @mcp.tool()
    async def clip_set_markers(
        track_index: int, clip_index: int, start_marker: float, end_marker: float
    ) -> dict[str, Any]:
        """Set the clip's start/end markers in beats."""
        client = await get_client()
        client.send(
            "/live/clip/set/start_marker", int(track_index), int(clip_index), float(start_marker)
        )
        client.send(
            "/live/clip/set/end_marker", int(track_index), int(clip_index), float(end_marker)
        )
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "start_marker": start_marker,
            "end_marker": end_marker,
        }

    @mcp.tool()
    async def clip_set_position(track_index: int, clip_index: int, position: float) -> dict[str, Any]:
        """Set the clip's position (in beats from clip start)."""
        (await get_client()).send(
            "/live/clip/set/position", int(track_index), int(clip_index), float(position)
        )
        return {"track_index": track_index, "clip_index": clip_index, "position": position}

    # --- Gain / pitch (audio clip) ---

    @mcp.tool()
    async def clip_set_gain(track_index: int, clip_index: int, gain: float) -> dict[str, Any]:
        """Set clip gain (audio clip; 0..1, 0.5 ≈ 0 dB)."""
        (await get_client()).send(
            "/live/clip/set/gain", int(track_index), int(clip_index), float(gain)
        )
        return {"track_index": track_index, "clip_index": clip_index, "gain": gain}

    @mcp.tool()
    async def clip_set_pitch(
        track_index: int, clip_index: int, semitones: int = 0, cents: float = 0.0
    ) -> dict[str, Any]:
        """Set clip transposition (audio clip): semitones (-48..48) and fine cents (-50..50)."""
        client = await get_client()
        client.send(
            "/live/clip/set/pitch_coarse", int(track_index), int(clip_index), int(semitones)
        )
        client.send(
            "/live/clip/set/pitch_fine", int(track_index), int(clip_index), float(cents)
        )
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "semitones": semitones,
            "cents": cents,
        }

    # --- Warp / launch config ---

    @mcp.tool()
    async def clip_set_warp(track_index: int, clip_index: int, warping: bool) -> dict[str, Any]:
        """Enable/disable warping on an audio clip."""
        (await get_client()).send(
            "/live/clip/set/warping", int(track_index), int(clip_index), 1 if warping else 0
        )
        return {"track_index": track_index, "clip_index": clip_index, "warp": warping}

    @mcp.tool()
    async def clip_set_warp_mode(track_index: int, clip_index: int, mode: int) -> dict[str, Any]:
        """Set warp mode: 0=Beats, 1=Tones, 2=Texture, 3=Re-Pitch, 4=Complex, 5=Complex Pro."""
        (await get_client()).send(
            "/live/clip/set/warp_mode", int(track_index), int(clip_index), int(mode)
        )
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "warp_mode": mode,
            "label": WARP_MODES[mode] if 0 <= mode < len(WARP_MODES) else None,
        }

    @mcp.tool()
    async def clip_set_ram_mode(track_index: int, clip_index: int, ram: bool) -> dict[str, Any]:
        """Toggle RAM mode for an audio clip."""
        (await get_client()).send(
            "/live/clip/set/ram_mode", int(track_index), int(clip_index), 1 if ram else 0
        )
        return {"track_index": track_index, "clip_index": clip_index, "ram_mode": ram}

    @mcp.tool()
    async def clip_set_launch_mode(track_index: int, clip_index: int, mode: int) -> dict[str, Any]:
        """Set launch mode: 0=Trigger, 1=Gate, 2=Toggle, 3=Repeat."""
        (await get_client()).send(
            "/live/clip/set/launch_mode", int(track_index), int(clip_index), int(mode)
        )
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "launch_mode": mode,
            "label": LAUNCH_MODES[mode] if 0 <= mode < len(LAUNCH_MODES) else None,
        }

    @mcp.tool()
    async def clip_set_launch_quantization(track_index: int, clip_index: int, value: int) -> dict[str, Any]:
        """Set per-clip launch quantization (Live enum int; 0=Global)."""
        (await get_client()).send(
            "/live/clip/set/launch_quantization", int(track_index), int(clip_index), int(value)
        )
        return {"track_index": track_index, "clip_index": clip_index, "launch_quantization": value}

    @mcp.tool()
    async def clip_set_legato(track_index: int, clip_index: int, legato: bool) -> dict[str, Any]:
        """Toggle legato launch behavior."""
        (await get_client()).send(
            "/live/clip/set/legato", int(track_index), int(clip_index), 1 if legato else 0
        )
        return {"track_index": track_index, "clip_index": clip_index, "legato": legato}

    @mcp.tool()
    async def clip_set_velocity_amount(track_index: int, clip_index: int, amount: float) -> dict[str, Any]:
        """Set the clip's velocity amount (0..1)."""
        (await get_client()).send(
            "/live/clip/set/velocity_amount", int(track_index), int(clip_index), float(amount)
        )
        return {"track_index": track_index, "clip_index": clip_index, "velocity_amount": amount}

    # --- MIDI notes ---

    @mcp.tool()
    async def clip_get_notes(
        track_index: int,
        clip_index: int,
        start_pitch: int | None = None,
        pitch_span: int | None = None,
        start_time: float | None = None,
        time_span: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return MIDI notes in a clip. Optional filters constrain pitch and time ranges."""
        client = await get_client()
        args: list[Any] = [int(track_index), int(clip_index)]
        if start_pitch is not None:
            args.extend([int(start_pitch), int(pitch_span or 128), float(start_time or 0), float(time_span or 1e6)])
        result = await client.request("/live/clip/get/notes", *args)
        notes_flat = result[2:]
        notes: list[dict[str, Any]] = []
        for chunk in chunked(notes_flat, 5):
            if len(chunk) < 5:
                break
            pitch, start, duration, velocity, mute = chunk
            notes.append({
                "pitch": int(pitch),
                "start": float(start),
                "duration": float(duration),
                "velocity": int(velocity),
                "mute": bool(mute),
            })
        return notes

    @mcp.tool()
    async def clip_add_notes(
        track_index: int,
        clip_index: int,
        notes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add MIDI notes to a clip.

        Each note: {pitch:int, start:float (beats), duration:float (beats),
        velocity:int 1..127, mute:bool (optional)}.
        """
        client = await get_client()
        flat: list[Any] = [int(track_index), int(clip_index)]
        for n in notes:
            flat.extend([
                int(n["pitch"]),
                float(n["start"]),
                float(n["duration"]),
                int(n.get("velocity", 100)),
                1 if n.get("mute", False) else 0,
            ])
        client.send("/live/clip/add/notes", *flat)
        return {"track_index": track_index, "clip_index": clip_index, "added": len(notes)}

    @mcp.tool()
    async def clip_remove_notes(
        track_index: int,
        clip_index: int,
        start_pitch: int | None = None,
        pitch_span: int | None = None,
        start_time: float | None = None,
        time_span: float | None = None,
    ) -> dict[str, Any]:
        """Remove notes from a clip. Defaults to removing all notes when no range given."""
        client = await get_client()
        args: list[Any] = []
        if start_pitch is not None:
            args.extend([int(start_pitch), int(pitch_span or 128), float(start_time or 0), float(time_span or 1e6)])
        client.send("/live/clip/remove/notes", int(track_index), int(clip_index), *args)
        return {"track_index": track_index, "clip_index": clip_index, "removed": "filtered" if args else "all"}
