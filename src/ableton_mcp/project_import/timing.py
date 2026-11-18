"""Explicit MIDI timebases; never assume the first tempo applies to a whole file."""

from __future__ import annotations

import bisect
import io
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mido

from .assets import ImportError


def load_midi(path: Path) -> tuple[mido.MidiFile, list[str]]:
    """Tolerate invalid key annotations without changing performance events.

    Some Suno exports encode 19 sharps, outside SMF's -7..7 range. Walk
    actual SMF event boundaries (never search/replace arbitrary bytes), and
    represent only that invalid annotation as opaque sequencer metadata.
    The original download stays byte-identical in the collected MIDI folder.
    """
    data = bytearray(path.read_bytes())
    if data[:4] != b"MThd" or len(data) < 14:
        raise ImportError(f"Invalid MIDI header: {path}")
    cursor = 8 + int.from_bytes(data[4:8], "big")
    repairs = []
    while cursor < len(data):
        if data[cursor:cursor + 4] != b"MTrk" or cursor + 8 > len(data):
            raise ImportError(f"Invalid MIDI track chunk: {path}")
        end = cursor + 8 + int.from_bytes(data[cursor + 4:cursor + 8], "big")
        cursor += 8
        if end > len(data):
            raise ImportError(f"Truncated MIDI track: {path}")
        status = None

        def variable() -> int:
            nonlocal cursor
            value = 0
            for _ in range(4):
                if cursor >= end:
                    break
                byte = data[cursor]
                cursor += 1
                value = (value << 7) | (byte & 127)
                if byte < 128:
                    return value
            raise ImportError(f"Invalid MIDI variable integer: {path}")

        while cursor < end:
            variable()  # delta time
            if cursor >= end:
                raise ImportError(f"Truncated MIDI event: {path}")
            first = data[cursor]
            if first >= 128:
                cursor += 1
                if first != 255:
                    status = first
            elif status is None:
                raise ImportError(f"MIDI running status without a status byte: {path}")
            event = first if first >= 128 else status
            if event == 255:
                kind_at = cursor
                if cursor >= end:
                    raise ImportError(f"Truncated MIDI meta event: {path}")
                kind = data[cursor]
                cursor += 1
                length = variable()
                if kind == 0x59 and length == 2 and cursor + 2 <= end:
                    sharps = int.from_bytes(data[cursor:cursor + 1], "big", signed=True)
                    mode = data[cursor + 1]
                    if not -7 <= sharps <= 7 or mode not in (0, 1):
                        data[kind_at] = 0x7f
                        repairs.append(f"Ignored invalid key annotation ({sharps} sharps, mode {mode})")
                cursor += length
            elif event in (240, 247):
                length = variable()
                cursor += length
            elif 128 <= event <= 239:
                cursor += 1 if event & 0xf0 in (0xc0, 0xd0) else 2
            else:
                raise ImportError(f"Unsupported MIDI status {event}: {path}")
            if cursor > end:
                raise ImportError(f"MIDI event exceeds its track: {path}")
    return mido.MidiFile(file=io.BytesIO(data)), repairs


@dataclass
class TempoMap:
    """Piecewise constant tempo, specified as (quarter-note beat, BPM)."""

    points: list[tuple[float, float]]

    def __post_init__(self) -> None:
        import math
        if not self.points or self.points[0][0] != 0:
            raise ImportError("Tempo map must start at beat zero")
        if any(not math.isfinite(b) or not math.isfinite(v) or b < 0 or not 20 <= v <= 999
               for b, v in self.points):
            raise ImportError("Invalid tempo map")
        if any(a[0] >= b[0] for a, b in zip(self.points, self.points[1:])):
            raise ImportError("Tempo map beats must increase")
        self.beats = [b for b, _ in self.points]
        self.seconds = [0.0]
        for (a, bpm), (b, _) in zip(self.points, self.points[1:]):
            self.seconds.append(self.seconds[-1] + (b - a) * 60 / bpm)

    def beat_to_seconds(self, beat: float) -> float:
        i = max(0, bisect.bisect_right(self.beats, beat) - 1)
        return self.seconds[i] + (beat - self.beats[i]) * 60 / self.points[i][1]

    def seconds_to_beat(self, seconds: float) -> float:
        i = max(0, bisect.bisect_right(self.seconds, seconds) - 1)
        return self.beats[i] + (seconds - self.seconds[i]) * self.points[i][1] / 60


def read_midi(path: Path, destination: TempoMap, offset_sec: float = 0) -> dict[str, Any]:
    midi, annotations = load_midi(path)
    if midi.type == 2 or midi.ticks_per_beat <= 0:
        raise ImportError("SMF2 and SMPTE MIDI require an explicit sequence/timebase conversion")
    tick = 0
    changes = {0.0: 120.0}
    explicit_changes = {}
    signatures: list[dict[str, Any]] = []
    merged = list(mido.merge_tracks(midi.tracks))
    for message in merged:
        tick += message.time
        beat = tick / midi.ticks_per_beat
        if message.type == "set_tempo":
            if beat in explicit_changes and explicit_changes[beat] != message.tempo:
                raise ImportError(f"Conflicting MIDI tempo events at beat {beat}")
            explicit_changes[beat] = message.tempo
            changes[beat] = mido.tempo2bpm(message.tempo)
        elif message.type == "time_signature":
            signatures.append({"beat": beat, "numerator": message.numerator,
                               "denominator": message.denominator})
    source_map = TempoMap(sorted(changes.items()))

    def time(absolute_tick: int) -> float:
        sec = source_map.beat_to_seconds(absolute_tick / midi.ticks_per_beat) + offset_sec
        if sec < 0:
            raise ImportError("MIDI offset would discard events before project start")
        return destination.seconds_to_beat(sec)

    parts: list[dict[str, Any]] = []
    control_parts: list[dict[str, Any]] = []
    issues: list[str] = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        name = f"Part {track_index + 1}"
        channels: dict[int, dict[str, Any]] = {}
        active: dict[tuple[int, int], deque] = defaultdict(deque)
        for msg in track:
            tick += msg.time
            if msg.type == "track_name":
                name = msg.name
            if msg.is_meta:
                continue
            if not hasattr(msg, "channel"):
                issues.append(f"Unsupported MIDI event: {msg.type}")
                continue
            channel = msg.channel
            part = channels.setdefault(channel, {
                "name": name, "channel": channel, "source_track": track_index,
                "program": 0, "notes": [], "controllers": [], "pitch_bends": [],
                "program_explicit": False,
            })
            if msg.type == "note_on" and msg.velocity:
                active[(channel, msg.note)].append((tick, msg.velocity))
            elif msg.type in {"note_on", "note_off"}:
                if active[(channel, msg.note)]:
                    start, velocity = active[(channel, msg.note)].popleft()
                    if tick > start:
                        part["notes"].append({
                            "pitch": msg.note, "start": time(start),
                            "duration": time(tick) - time(start), "velocity": velocity,
                            "off_velocity": msg.velocity if msg.type == "note_off" else 0,
                        })
                else:
                    issues.append(f"Unmatched note-off in track {track_index}")
            elif msg.type == "control_change":
                part["controllers"].append({"time": time(tick), "control": msg.control,
                                            "value": msg.value})
            elif msg.type == "pitchwheel":
                part["pitch_bends"].append({"time": time(tick), "value": msg.pitch + 8192})
            elif msg.type == "program_change":
                if part["notes"] or any(active.values()):
                    issues.append(f"Program change inside track {track_index}; original retained")
                part["program"] = msg.program
                part["program_explicit"] = True
            else:
                issues.append(f"Unsupported MIDI event: {msg.type}")
        if any(active.values()):
            issues.append(f"Unclosed notes in track {track_index}; original retained")
        for part in channels.values():
            part["name"] = name
            if part["notes"]:
                parts.append(part)
            else:
                control_parts.append(part)
    for controls in control_parts:
        recipients = [p for p in parts if p["channel"] == controls["channel"]]
        if not recipients and (controls["controllers"] or controls["pitch_bends"]):
            issues.append(f"Controller-only channel {controls['channel'] + 1} has no note part")
        for recipient in recipients:
            recipient["controllers"].extend(controls["controllers"])
            recipient["pitch_bends"].extend(controls["pitch_bends"])
            recipient["controllers"].sort(key=lambda event: event["time"])
            recipient["pitch_bends"].sort(key=lambda event: event["time"])
            if controls["program_explicit"] and not recipient["program_explicit"]:
                recipient["program"] = controls["program"]
    return {"parts": parts, "tempo_map": source_map.points, "time_signatures": signatures,
            "annotations": annotations,
            "ticks_per_beat": midi.ticks_per_beat, "explicit_tempo": any(
                m.type == "set_tempo" for m in merged), "issues": sorted(set(issues))}
