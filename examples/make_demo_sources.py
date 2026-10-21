"""Create original local audio/MIDI fixtures for the README demos.

Usage: python examples/make_demo_sources.py data/readme_demo_sources
No external media, model, account, or network connection is needed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mido
import numpy as np
import soundfile as sf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    destination = parser.parse_args().destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    stems = destination / "prepared_stems"
    stems.mkdir()
    rate, bpm, bars = 44100, 120, 12
    duration = bars * 4 * 60 / bpm
    frames = round(duration * rate)
    audio = {name: np.zeros(frames) for name in ("Drums", "Bass", "Guitar", "Piano", "Vocals", "Other")}
    rng = np.random.default_rng(20261021)
    notes = []

    def add(name: str, beat: float, length: float, pitch: int, amplitude: float, kind="pluck"):
        start = round(beat * 60 / bpm * rate)
        count = min(round(length * 60 / bpm * rate), frames - start)
        if count <= 0:
            return
        t = np.arange(count) / rate
        freq = 440 * 2 ** ((pitch - 69) / 12)
        wave = np.sin(2 * np.pi * freq * t)
        if kind == "pluck":
            wave += 0.28 * np.sin(2 * np.pi * freq * 2 * t)
            envelope = np.exp(-t * 5) * np.minimum(t / 0.004, 1)
        else:
            wave += 0.14 * np.sin(2 * np.pi * freq * 3 * t)
            envelope = np.minimum(t / 0.06, 1) * np.minimum((count / rate - t) / 0.12, 1)
        audio[name][start:start + count] += amplitude * wave * envelope

    roots = [48, 45, 53, 43]
    for bar in range(bars):
        root = roots[bar % len(roots)]
        for beat in range(4):
            onset = bar * 4 + beat
            start = round(onset * rate / 2)
            t = np.arange(round(0.22 * rate)) / rate
            kick = np.sin(2 * np.pi * (48 * t + 65 * (1 - np.exp(-t * 24)) / 24)) * np.exp(-t * 18)
            audio["Drums"][start:start + len(t)] += 0.23 * kick
            if beat % 2:
                audio["Drums"][start:start + len(t)] += 0.07 * rng.standard_normal(len(t)) * np.exp(-t * 28)
            add("Bass", onset, 0.85, root - 12, 0.13)
            for half in (0, 0.5):
                hstart = round((onset + half) * rate / 2)
                hat = rng.standard_normal(round(0.05 * rate)) * np.exp(-np.arange(round(0.05 * rate)) / rate * 90)
                audio["Drums"][hstart:hstart + len(hat)] += 0.025 * hat
                add("Guitar", onset + half, 0.4, root + (7 if half else 12), 0.045)
        for delta in (0, 4 if bar % 4 in (0, 2) else 3, 7):
            add("Piano", bar * 4, 3.7, root + delta + 12, 0.055)
            notes.append((bar * 4, 3.7, root + delta + 12))
            add("Other", bar * 4, 3.9, root + delta, 0.015, "pad")
        if bar >= 2:
            add("Vocals", bar * 4 + 0.5, 2.5, root + 19, 0.045, "pad")

    mix = np.zeros((frames, 2), dtype=np.float32)
    for i, (name, mono) in enumerate(audio.items()):
        stereo = np.column_stack((mono * (0.9 + 0.015 * i), mono * (1 - 0.015 * i))).astype(np.float32)
        sf.write(stems / f"{name}.wav", stereo, rate, subtype="FLOAT")
        mix += stereo
    sf.write(destination / "Studio Sketch.wav", mix, rate, subtype="FLOAT")
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage("track_name", name="Chord sketch"),
                  mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)),
                  mido.MetaMessage("time_signature", numerator=4, denominator=4)])
    events = []
    for beat, length, pitch in notes:
        events.extend([(round(beat * 480), mido.Message("note_on", note=pitch, velocity=85)),
                       (round((beat + length) * 480), mido.Message("note_off", note=pitch, velocity=0))])
    previous = 0
    for tick, event in sorted(events, key=lambda item: item[0]):
        track.append(event.copy(time=tick - previous))
        previous = tick
    midi.save(stems / "Piano.mid")
    print(f"Created six original stems, companion MIDI and a {duration:g}s mix at {bpm} BPM: {destination}")


if __name__ == "__main__":
    main()
