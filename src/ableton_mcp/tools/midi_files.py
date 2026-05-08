"""MIDI file manipulation on disk + bridge into a Live clip via OSC."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pretty_midi
from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def _midi_to_clip_notes(pm: pretty_midi.PrettyMIDI, instrument_index: int = 0) -> list[dict[str, Any]]:
    """Convert a pretty_midi instrument's notes into AbletonOSC's beat-relative format."""
    if instrument_index >= len(pm.instruments):
        return []
    inst = pm.instruments[instrument_index]
    # Convert seconds to beats using the file's tempo map.
    tempos_change_times, tempos = pm.get_tempo_changes()
    bpm = float(tempos[0]) if len(tempos) else 120.0
    beats_per_sec = bpm / 60.0
    out: list[dict[str, Any]] = []
    for n in inst.notes:
        out.append(
            {
                "pitch": int(n.pitch),
                "start": float(n.start * beats_per_sec),
                "duration": float((n.end - n.start) * beats_per_sec),
                "velocity": int(n.velocity),
                "mute": False,
            }
        )
    return out


def _notes_to_midi(notes: list[dict[str, Any]], bpm: float, program: int = 0) -> pretty_midi.PrettyMIDI:
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    inst = pretty_midi.Instrument(program=program)
    sec_per_beat = 60.0 / bpm
    for n in notes:
        start_s = float(n["start"]) * sec_per_beat
        dur_s = float(n["duration"]) * sec_per_beat
        inst.notes.append(
            pretty_midi.Note(
                velocity=int(n.get("velocity", 100)),
                pitch=int(n["pitch"]),
                start=start_s,
                end=start_s + dur_s,
            )
        )
    pm.instruments.append(inst)
    return pm


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def midi_file_summary(path: str) -> dict[str, Any]:
        """Inspect a .mid file: tempo, instruments, note count per instrument, total length."""
        pm = pretty_midi.PrettyMIDI(str(Path(path)))
        tempos_change_times, tempos = pm.get_tempo_changes()
        return {
            "path": str(Path(path).resolve()),
            "duration_sec": pm.get_end_time(),
            "estimated_tempo": float(tempos[0]) if len(tempos) else None,
            "tempo_changes": int(len(tempos)),
            "instruments": [
                {
                    "index": i,
                    "name": inst.name or "(unnamed)",
                    "program": inst.program,
                    "is_drum": inst.is_drum,
                    "num_notes": len(inst.notes),
                    "pitch_range": [
                        min((n.pitch for n in inst.notes), default=None),
                        max((n.pitch for n in inst.notes), default=None),
                    ],
                }
                for i, inst in enumerate(pm.instruments)
            ],
        }

    @mcp.tool()
    async def midi_file_load_into_clip(
        path: str,
        track_index: int,
        clip_index: int,
        instrument_index: int = 0,
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Load a .mid file (single instrument) into an existing or new Session clip."""
        pm = pretty_midi.PrettyMIDI(str(Path(path)))
        notes = _midi_to_clip_notes(pm, instrument_index=instrument_index)
        if not notes:
            return {"error": "no notes found in selected instrument", "instruments_in_file": len(pm.instruments)}

        client = await get_client()
        # Compute clip length in beats (round up to next bar of 4 for sanity).
        max_end = max(n["start"] + n["duration"] for n in notes)
        length_beats = max(4.0, ((int(max_end) // 4) + 1) * 4)

        # Create or clear the clip.
        if replace_existing:
            client.send("/live/clip_slot/delete_clip", int(track_index), int(clip_index))
        client.send(
            "/live/clip_slot/create_clip",
            int(track_index),
            int(clip_index),
            float(length_beats),
        )

        flat: list[Any] = [int(track_index), int(clip_index)]
        for n in notes:
            flat.extend([
                int(n["pitch"]),
                float(n["start"]),
                float(n["duration"]),
                int(n["velocity"]),
                1 if n["mute"] else 0,
            ])
        client.send("/live/clip/add/notes", *flat)
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes_added": len(notes),
            "clip_length_beats": length_beats,
        }

    @mcp.tool()
    async def midi_file_export_from_clip(
        track_index: int, clip_index: int, output_path: str, bpm: float | None = None
    ) -> dict[str, Any]:
        """Export the notes of a Session clip to a .mid file. Uses live tempo if bpm is None."""
        client = await get_client()
        if bpm is None:
            bpm = float((await client.request("/live/song/get/tempo"))[0])
        args = await client.request(
            "/live/clip/get/notes", int(track_index), int(clip_index)
        )
        notes_flat = args[2:]
        notes: list[dict[str, Any]] = []
        for i in range(0, len(notes_flat) - 4, 5):
            notes.append(
                {
                    "pitch": int(notes_flat[i]),
                    "start": float(notes_flat[i + 1]),
                    "duration": float(notes_flat[i + 2]),
                    "velocity": int(notes_flat[i + 3]),
                    "mute": bool(notes_flat[i + 4]),
                }
            )
        pm = _notes_to_midi(notes, bpm=bpm)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pm.write(str(out))
        return {"path": str(out.resolve()), "notes_written": len(notes), "bpm": bpm}

    @mcp.tool()
    async def midi_file_quantize(
        path: str, output_path: str, grid_beats: float = 0.25, strength: float = 1.0
    ) -> dict[str, Any]:
        """Quantize a .mid file to a beat grid; strength=1.0 is full snap."""
        pm = pretty_midi.PrettyMIDI(str(Path(path)))
        tempos_change_times, tempos = pm.get_tempo_changes()
        bpm = float(tempos[0]) if len(tempos) else 120.0
        sec_per_beat = 60.0 / bpm
        grid_sec = grid_beats * sec_per_beat
        n_total = 0
        for inst in pm.instruments:
            for note in inst.notes:
                target = round(note.start / grid_sec) * grid_sec
                note.end = note.end + (target - note.start) * strength
                note.start = note.start + (target - note.start) * strength
                n_total += 1
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pm.write(str(out))
        return {"path": str(out.resolve()), "notes_quantized": n_total, "grid_beats": grid_beats}

    @mcp.tool()
    async def midi_file_transpose(
        path: str, output_path: str, semitones: int
    ) -> dict[str, Any]:
        """Transpose every non-drum note in a .mid file by N semitones (clamped to 0..127)."""
        pm = pretty_midi.PrettyMIDI(str(Path(path)))
        n = 0
        for inst in pm.instruments:
            if inst.is_drum:
                continue
            for note in inst.notes:
                note.pitch = max(0, min(127, note.pitch + semitones))
                n += 1
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pm.write(str(out))
        return {"path": str(out.resolve()), "notes_transposed": n, "semitones": semitones}

    @mcp.tool()
    async def midi_file_humanize(
        path: str,
        output_path: str,
        timing_jitter_beats: float = 0.02,
        velocity_jitter: int = 8,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Apply small random timing & velocity perturbations to a .mid file."""
        import random

        rng = random.Random(seed)
        pm = pretty_midi.PrettyMIDI(str(Path(path)))
        tempos_change_times, tempos = pm.get_tempo_changes()
        bpm = float(tempos[0]) if len(tempos) else 120.0
        sec_per_beat = 60.0 / bpm
        n = 0
        for inst in pm.instruments:
            for note in inst.notes:
                jitter = rng.uniform(-timing_jitter_beats, timing_jitter_beats) * sec_per_beat
                note.start = max(0.0, note.start + jitter)
                note.end = max(note.start + 1e-3, note.end + jitter)
                note.velocity = max(1, min(127, note.velocity + rng.randint(-velocity_jitter, velocity_jitter)))
                n += 1
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pm.write(str(out))
        return {"path": str(out.resolve()), "notes_humanized": n}
