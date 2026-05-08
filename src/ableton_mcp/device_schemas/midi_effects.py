"""Schemas for Ableton Live 11 built-in MIDI effects."""

from __future__ import annotations

from .base import DeviceSchema, Parameter


# --------------------------------------------------------------------------- #
# Arpeggiator                                                                 #
# --------------------------------------------------------------------------- #
ARPEGGIATOR = DeviceSchema(
    class_name="MidiArpeggiator",
    display_name="Arpeggiator",
    device_type="midi_effect",
    description="Polyphonic MIDI arpeggiator with patterns, gate, retrigger, groove.",
    categories=["pattern", "rhythm", "pitch"],
    parameters=[
        Parameter("Style", "enum", 0, 0, 17, None, "pattern", True,
                  "Up / Down / UpDown / DownUp / Chord / Random / Random Other / Other / etc."),
        Parameter("Rate", "enum", 5, 0, 21, None, "rhythm", True,
                  "Step rate (1/4, 1/4T, 1/8, 1/8T, ..., 1/64)."),
        Parameter("Sync", "enum", 1, 0, 1, None, "rhythm", False,
                  "Tempo sync."),
        Parameter("Steps", "quantized", 8, 1, 16, None, "pattern", True,
                  "Pattern length in steps."),
        Parameter("Distance", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", True,
                  "Inter-note distance for the chosen style."),
        Parameter("Octave", "quantized", 1, 1, 8, None, "pitch", True,
                  "Octave range of the arp."),
        Parameter("Repeats", "enum", 0, 0, 9, None, "rhythm", False,
                  "Repeats per cycle: Inf / 1..8."),
        Parameter("Gate", "continuous", 50.0, 1.0, 200.0, "%", "rhythm", True,
                  "Note gate length."),
        Parameter("Retrigger", "enum", 0, 0, 2, None, "pattern", False,
                  "Off / Note / Beat retrigger."),
        Parameter("Velocity Decay", "continuous", 0.0, -1.0, 1.0, None, "pattern", True,
                  "Velocity slope across the pattern."),
        Parameter("Groove", "continuous", 0.0, 0.0, 1.0, None, "rhythm", False,
                  "Groove amount."),
        Parameter("Hold", "enum", 0, 0, 1, None, "pattern", False,
                  "Hold notes on/off."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#arpeggiator",
)


# --------------------------------------------------------------------------- #
# Chord                                                                       #
# --------------------------------------------------------------------------- #
CHORD = DeviceSchema(
    class_name="MidiChord",
    display_name="Chord",
    device_type="midi_effect",
    description="Add up to six pitch-shifted copies of every incoming note.",
    categories=["pitch"],
    parameters=[
        Parameter("Shift1 Pitch", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", True,
                  "Shift voice 1 pitch (relative)."),
        Parameter("Shift1 Velocity", "continuous", 100.0, 0.0, 100.0, "%", "pitch", False,
                  "Voice 1 velocity %."),
        Parameter("Shift2 Pitch", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", True,
                  "Voice 2 pitch."),
        Parameter("Shift3 Pitch", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", True,
                  "Voice 3 pitch."),
        Parameter("Shift4 Pitch", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", False,
                  "Voice 4 pitch."),
        Parameter("Shift5 Pitch", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", False,
                  "Voice 5 pitch."),
        Parameter("Shift6 Pitch", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", False,
                  "Voice 6 pitch."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#chord",
)


# --------------------------------------------------------------------------- #
# Note Length                                                                 #
# --------------------------------------------------------------------------- #
NOTE_LENGTH = DeviceSchema(
    class_name="MidiNoteLength",
    display_name="Note Length",
    device_type="midi_effect",
    description="Force every note to a fixed length (sync or free).",
    categories=["rhythm"],
    parameters=[
        Parameter("Sync On", "enum", 0, 0, 1, None, "rhythm", False,
                  "Sync to tempo."),
        Parameter("Length", "continuous", 100.0, 0.0, 1000.0, "ms", "rhythm", True,
                  "Note length when free (ms)."),
        Parameter("Synced Length", "enum", 5, 0, 21, None, "rhythm", True,
                  "Note length when synced (1/16, 1/8, ..., 4 bars)."),
        Parameter("Gate", "continuous", 50.0, 1.0, 100.0, "%", "rhythm", True,
                  "Gate as % of length."),
        Parameter("On/Off Trigger", "enum", 0, 0, 1, None, "global", False,
                  "Trigger on note-on or note-off."),
        Parameter("Decay Time", "continuous", 0.0, 0.0, 10.0, "sec", "rhythm", False,
                  "Decay if held."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#note-length",
)


# --------------------------------------------------------------------------- #
# Pitch                                                                       #
# --------------------------------------------------------------------------- #
PITCH = DeviceSchema(
    class_name="MidiPitcher",
    display_name="Pitch",
    device_type="midi_effect",
    description="Transpose incoming MIDI notes; clamp to a high/low range.",
    categories=["pitch"],
    parameters=[
        Parameter("Pitch", "continuous", 0.0, -128.0, 128.0, "semitones", "pitch", True,
                  "Transposition."),
        Parameter("Range", "continuous", 127.0, 0.0, 127.0, None, "pitch", False,
                  "Range upper limit."),
        Parameter("Lowest", "continuous", 0.0, 0.0, 127.0, None, "pitch", False,
                  "Lowest note allowed."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#pitch",
)


# --------------------------------------------------------------------------- #
# Random                                                                      #
# --------------------------------------------------------------------------- #
RANDOM = DeviceSchema(
    class_name="MidiRandom",
    display_name="Random",
    device_type="midi_effect",
    description="Add random pitch deviation to incoming notes.",
    categories=["pitch", "random"],
    parameters=[
        Parameter("Chance", "continuous", 0.5, 0.0, 1.0, None, "random", True,
                  "Probability of randomization per note."),
        Parameter("Choices", "quantized", 1, 1, 24, None, "random", True,
                  "How many possible random pitches to choose from."),
        Parameter("Scale", "continuous", 12.0, 0.0, 36.0, "semitones", "pitch", True,
                  "Scale of randomization (semitones between choices)."),
        Parameter("Sign", "enum", 0, 0, 2, None, "pitch", False,
                  "Up / Down / Bi-directional."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#random",
)


# --------------------------------------------------------------------------- #
# Scale                                                                       #
# --------------------------------------------------------------------------- #
SCALE = DeviceSchema(
    class_name="MidiScale",
    display_name="Scale",
    device_type="midi_effect",
    description="Map incoming MIDI notes to a target scale.",
    categories=["pitch"],
    parameters=[
        Parameter("Base", "quantized", 0, 0, 11, None, "pitch", True,
                  "Root note (C, C#, ..., B)."),
        Parameter("Transpose", "continuous", 0.0, -36.0, 36.0, "semitones", "pitch", True,
                  "Transposition after scale mapping."),
        Parameter("Range", "continuous", 127.0, 0.0, 127.0, None, "pitch", False,
                  "Filter range."),
        Parameter("Lowest", "continuous", 0.0, 0.0, 127.0, None, "pitch", False,
                  "Lowest accepted note."),
    ],
    notes="The 12-cell scale-grid itself is configured via the Live UI and is not a normal automatable parameter.",
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#scale",
)


# --------------------------------------------------------------------------- #
# Velocity                                                                    #
# --------------------------------------------------------------------------- #
VELOCITY = DeviceSchema(
    class_name="MidiVelocity",
    display_name="Velocity",
    device_type="midi_effect",
    description="Reshape, compress, expand, randomize note velocities.",
    categories=["dynamics"],
    parameters=[
        Parameter("Drive", "continuous", 0.0, -64.0, 64.0, None, "dynamics", True,
                  "Velocity offset."),
        Parameter("Compand", "continuous", 0.0, -64.0, 64.0, None, "dynamics", True,
                  "Velocity compression / expansion."),
        Parameter("Random", "continuous", 0.0, 0.0, 64.0, None, "dynamics", True,
                  "Velocity randomization."),
        Parameter("Out Hi", "continuous", 127.0, 0.0, 127.0, None, "dynamics", True,
                  "Output high cap."),
        Parameter("Out Low", "continuous", 0.0, 0.0, 127.0, None, "dynamics", True,
                  "Output low cap."),
        Parameter("Range", "continuous", 127.0, 1.0, 127.0, None, "dynamics", False,
                  "Input range."),
        Parameter("Lowest", "continuous", 0.0, 0.0, 127.0, None, "dynamics", False,
                  "Lowest input velocity."),
        Parameter("Mode", "enum", 0, 0, 2, None, "dynamics", False,
                  "Mode: Fixed / Linear / Random."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-midi-effect-reference/#velocity",
)


MIDI_EFFECT_SCHEMAS: list = [
    ARPEGGIATOR,
    CHORD,
    NOTE_LENGTH,
    PITCH,
    RANDOM,
    SCALE,
    VELOCITY,
]
