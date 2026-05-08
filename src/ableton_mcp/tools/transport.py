"""Transport, tempo, time-signature, loop, recording, scale, groove, quantization."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


# Quantization values accepted by Live (per LOM):
QUANTIZATION_VALUES = [
    "no_q", "8_bars", "4_bars", "2_bars", "1_bar",
    "1/2", "1/2t", "1/4", "1/4t", "1/8", "1/8t",
    "1/16", "1/16t", "1/32",
]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def live_ping() -> dict[str, Any]:
        """Verify AbletonOSC is reachable. Call this first if anything else fails."""
        client = await get_client()
        ok = await client.ping()
        if not ok:
            return {
                "ok": False,
                "hint": (
                    "AbletonOSC did not reply on UDP/11001. Confirm Live is open, "
                    "AbletonOSC is enabled in Preferences > Link/Tempo/MIDI > Control Surface, "
                    "and no other process is bound to port 11001."
                ),
            }
        version = await client.request("/live/application/get/version")
        return {"ok": True, "live_version": ".".join(str(v) for v in version)}

    @mcp.tool()
    async def live_get_state() -> dict[str, Any]:
        """Comprehensive transport snapshot."""
        client = await get_client()
        async def get(addr: str) -> Any:
            return (await client.request(addr))[0]
        return {
            "is_playing": bool(await get("/live/song/get/is_playing")),
            "tempo": await get("/live/song/get/tempo"),
            "time_signature": f"{await get('/live/song/get/signature_numerator')}/{await get('/live/song/get/signature_denominator')}",
            "song_time_beats": await get("/live/song/get/current_song_time"),
            "song_length_beats": await get("/live/song/get/song_length"),
            "loop": {
                "enabled": bool(await get("/live/song/get/loop")),
                "start": await get("/live/song/get/loop_start"),
                "length": await get("/live/song/get/loop_length"),
            },
            "metronome": bool(await get("/live/song/get/metronome")),
            "session_record": bool(await get("/live/song/get/session_record")),
            "arrangement_overdub": bool(await get("/live/song/get/arrangement_overdub")),
            "back_to_arranger": bool(await get("/live/song/get/back_to_arranger")),
            "punch_in": bool(await get("/live/song/get/punch_in")),
            "punch_out": bool(await get("/live/song/get/punch_out")),
            "record_mode": await get("/live/song/get/record_mode"),
            "midi_recording_quantization": await get("/live/song/get/midi_recording_quantization"),
            "clip_trigger_quantization": await get("/live/song/get/clip_trigger_quantization"),
            "groove_amount": await get("/live/song/get/groove_amount"),
            "root_note": await get("/live/song/get/root_note"),
            "scale_name": await get("/live/song/get/scale_name"),
            "num_tracks": await get("/live/song/get/num_tracks"),
            "num_scenes": await get("/live/song/get/num_scenes"),
            "can_undo": bool(await get("/live/song/get/can_undo")),
            "can_redo": bool(await get("/live/song/get/can_redo")),
        }

    @mcp.tool()
    async def live_play() -> dict[str, str]:
        """Start playback from current position."""
        (await get_client()).send("/live/song/start_playing")
        return {"status": "playing"}

    @mcp.tool()
    async def live_continue() -> dict[str, str]:
        """Resume playback from where it stopped."""
        (await get_client()).send("/live/song/continue_playing")
        return {"status": "continuing"}

    @mcp.tool()
    async def live_stop() -> dict[str, str]:
        """Stop transport."""
        (await get_client()).send("/live/song/stop_playing")
        return {"status": "stopped"}

    @mcp.tool()
    async def live_stop_all_clips() -> dict[str, str]:
        """Stop every playing clip."""
        (await get_client()).send("/live/song/stop_all_clips")
        return {"status": "all_clips_stopped"}

    @mcp.tool()
    async def live_set_tempo(bpm: float) -> dict[str, float]:
        """Set the global tempo in BPM."""
        (await get_client()).send("/live/song/set/tempo", float(bpm))
        return {"tempo": bpm}

    @mcp.tool()
    async def live_tap_tempo() -> dict[str, str]:
        """Tap the tempo (one tap; call repeatedly to actually change tempo)."""
        (await get_client()).send("/live/song/tap_tempo")
        return {"status": "tapped"}

    @mcp.tool()
    async def live_set_time_signature(numerator: int, denominator: int) -> dict[str, int]:
        """Set time signature (e.g. 7,8 for 7/8)."""
        client = await get_client()
        client.send("/live/song/set/signature_numerator", int(numerator))
        client.send("/live/song/set/signature_denominator", int(denominator))
        return {"numerator": numerator, "denominator": denominator}

    @mcp.tool()
    async def live_set_metronome(on: bool) -> dict[str, bool]:
        """Enable or disable the metronome."""
        (await get_client()).send("/live/song/set/metronome", 1 if on else 0)
        return {"metronome": on}

    @mcp.tool()
    async def live_set_loop(
        enabled: bool, start_beats: float | None = None, length_beats: float | None = None
    ) -> dict[str, Any]:
        """Configure the arrangement loop."""
        client = await get_client()
        client.send("/live/song/set/loop", 1 if enabled else 0)
        if start_beats is not None:
            client.send("/live/song/set/loop_start", float(start_beats))
        if length_beats is not None:
            client.send("/live/song/set/loop_length", float(length_beats))
        return {"loop": enabled, "start": start_beats, "length": length_beats}

    @mcp.tool()
    async def live_jump_to_beat(beats: float) -> dict[str, float]:
        """Jump arrangement playback to absolute time in beats."""
        (await get_client()).send("/live/song/set/current_song_time", float(beats))
        return {"song_time_beats": beats}

    @mcp.tool()
    async def live_jump_by(beats: float) -> dict[str, float]:
        """Nudge arrangement playback by a relative beat offset."""
        (await get_client()).send("/live/song/jump_by", float(beats))
        return {"jumped_by_beats": beats}

    @mcp.tool()
    async def live_undo() -> dict[str, str]:
        """Undo the last edit."""
        (await get_client()).send("/live/song/undo")
        return {"status": "undone"}

    @mcp.tool()
    async def live_redo() -> dict[str, str]:
        """Redo the last undone edit."""
        (await get_client()).send("/live/song/redo")
        return {"status": "redone"}

    @mcp.tool()
    async def live_show_message(message: str) -> dict[str, str]:
        """Display a status message in Ableton's status bar."""
        (await get_client()).send("/live/api/show_message", str(message))
        return {"shown": message}

    # --- Recording ---

    @mcp.tool()
    async def live_capture_midi() -> dict[str, str]:
        """Capture the MIDI buffer into a clip on every armed MIDI track."""
        (await get_client()).send("/live/song/capture_midi")
        return {"status": "captured"}

    @mcp.tool()
    async def live_session_record(on: bool) -> dict[str, bool]:
        """Toggle session record (the big record button)."""
        (await get_client()).send("/live/song/set/session_record", 1 if on else 0)
        return {"session_record": on}

    @mcp.tool()
    async def live_trigger_session_record() -> dict[str, str]:
        """Trigger session recording on all armed tracks."""
        (await get_client()).send("/live/song/trigger_session_record")
        return {"status": "triggered"}

    @mcp.tool()
    async def live_set_arrangement_overdub(on: bool) -> dict[str, bool]:
        """Toggle arrangement overdub."""
        (await get_client()).send("/live/song/set/arrangement_overdub", 1 if on else 0)
        return {"arrangement_overdub": on}

    @mcp.tool()
    async def live_set_back_to_arranger(on: bool) -> dict[str, bool]:
        """Toggle the 'Back to Arrangement' button."""
        (await get_client()).send("/live/song/set/back_to_arranger", 1 if on else 0)
        return {"back_to_arranger": on}

    @mcp.tool()
    async def live_set_punch(in_: bool | None = None, out: bool | None = None) -> dict[str, Any]:
        """Toggle punch-in and/or punch-out."""
        client = await get_client()
        if in_ is not None:
            client.send("/live/song/set/punch_in", 1 if in_ else 0)
        if out is not None:
            client.send("/live/song/set/punch_out", 1 if out else 0)
        return {"punch_in": in_, "punch_out": out}

    @mcp.tool()
    async def live_set_record_mode(mode: int) -> dict[str, int]:
        """Set the global record mode (Live's internal int — see Live API)."""
        (await get_client()).send("/live/song/set/record_mode", int(mode))
        return {"record_mode": mode}

    @mcp.tool()
    async def live_set_nudge(direction: str, on: bool) -> dict[str, Any]:
        """Toggle the nudge_up or nudge_down button (used to manually push tempo)."""
        addr = "/live/song/set/nudge_up" if direction == "up" else "/live/song/set/nudge_down"
        (await get_client()).send(addr, 1 if on else 0)
        return {"direction": direction, "on": on}

    # --- Quantization & groove ---

    @mcp.tool()
    async def live_set_clip_trigger_quantization(value: int) -> dict[str, int]:
        """Set the global clip trigger quantization (Live enum int 0..13; 0=None, 4=1/1, etc.)."""
        (await get_client()).send("/live/song/set/clip_trigger_quantization", int(value))
        return {"clip_trigger_quantization": value, "values_legend": QUANTIZATION_VALUES}

    @mcp.tool()
    async def live_set_midi_recording_quantization(value: int) -> dict[str, int]:
        """Set the global MIDI recording quantization (same enum as trigger quantization)."""
        (await get_client()).send("/live/song/set/midi_recording_quantization", int(value))
        return {"midi_recording_quantization": value, "values_legend": QUANTIZATION_VALUES}

    @mcp.tool()
    async def live_set_groove_amount(amount: float) -> dict[str, float]:
        """Set the global groove amount (0.0..1.0)."""
        (await get_client()).send("/live/song/set/groove_amount", float(amount))
        return {"groove_amount": amount}

    @mcp.tool()
    async def live_get_quantization_legend() -> dict[str, list[str]]:
        """Return the integer→label mapping for clip trigger / MIDI recording quantization."""
        return {"values": QUANTIZATION_VALUES}
