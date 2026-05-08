"""Schemas for Ableton Live 11 utilities and rack containers."""

from __future__ import annotations

from .base import DeviceSchema, Parameter


# --------------------------------------------------------------------------- #
# Utility                                                                     #
# --------------------------------------------------------------------------- #
UTILITY = DeviceSchema(
    class_name="StereoGain",
    display_name="Utility",
    device_type="utility",
    description="Trim, gain, pan, width, mid/side, mute, mono, DC filter, phase invert.",
    categories=["mix", "stereo"],
    parameters=[
        Parameter("Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", True,
                  "Gain trim."),
        Parameter("Mute", "enum", 0, 0, 1, None, "mix", False,
                  "Mute."),
        Parameter("Channel Mode", "enum", 0, 0, 5, None, "stereo", True,
                  "Stereo / Left / Right / Swap / Mid / Side."),
        Parameter("Phase L", "enum", 0, 0, 1, None, "stereo", False,
                  "Invert left channel."),
        Parameter("Phase R", "enum", 0, 0, 1, None, "stereo", False,
                  "Invert right channel."),
        Parameter("Stereo Width", "continuous", 100.0, 0.0, 400.0, "%", "stereo", True,
                  "Stereo width (0 = mono, 100 = unchanged)."),
        Parameter("Balance", "continuous", 0.0, -1.0, 1.0, None, "stereo", True,
                  "Stereo balance."),
        Parameter("Mono", "enum", 0, 0, 1, None, "stereo", False,
                  "Sum to mono."),
        Parameter("Bass Mono", "enum", 0, 0, 1, None, "stereo", False,
                  "Sum bass to mono."),
        Parameter("Bass Mono Frequency", "continuous", 100.0, 20.0, 22000.0, "Hz", "stereo", False,
                  "Bass-mono crossover."),
        Parameter("DC Filter", "enum", 1, 0, 1, None, "filter", False,
                  "Remove DC offset."),
    ],
    notes="Class name 'StereoGain' is the LOM internal for Utility.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#utility",
)


# --------------------------------------------------------------------------- #
# Tuner                                                                       #
# --------------------------------------------------------------------------- #
TUNER = DeviceSchema(
    class_name="Tuner",
    display_name="Tuner",
    device_type="utility",
    description="Pitch/cents tuner display. Read-only; very few automatable params.",
    categories=["analysis"],
    parameters=[
        Parameter("Reference", "continuous", 440.0, 410.0, 480.0, "Hz", "analysis", False,
                  "A4 reference frequency."),
        Parameter("Mute", "enum", 0, 0, 1, None, "mix", False,
                  "Mute audio output while tuning."),
    ],
    notes="Tuner is a measurement device — most surface area is read-only.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#tuner",
)


# --------------------------------------------------------------------------- #
# Spectrum                                                                    #
# --------------------------------------------------------------------------- #
SPECTRUM = DeviceSchema(
    class_name="Spectrum",
    display_name="Spectrum",
    device_type="utility",
    description="Real-time FFT spectrum analyser. Display-only.",
    categories=["analysis"],
    parameters=[
        Parameter("Block", "enum", 6, 0, 7, None, "analysis", False,
                  "FFT block size."),
        Parameter("Channel", "enum", 0, 0, 3, None, "analysis", False,
                  "Channel: L+R / L / R / L&R."),
        Parameter("Range", "continuous", 60.0, 6.0, 100.0, "dB", "analysis", False,
                  "Display range in dB."),
    ],
    notes="Spectrum is purely display; no audio processing happens.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#spectrum",
)


# --------------------------------------------------------------------------- #
# Audio Effect Rack                                                           #
# --------------------------------------------------------------------------- #
AUDIO_EFFECT_RACK = DeviceSchema(
    class_name="AudioEffectGroupDevice",
    display_name="Audio Effect Rack",
    device_type="rack",
    description="Container for audio effects with 8 macros and chain selector.",
    categories=["rack", "macros"],
    parameters=[
        Parameter("Macro 1", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 1."),
        Parameter("Macro 2", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 2."),
        Parameter("Macro 3", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 3."),
        Parameter("Macro 4", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 4."),
        Parameter("Macro 5", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 5."),
        Parameter("Macro 6", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 6."),
        Parameter("Macro 7", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 7."),
        Parameter("Macro 8", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 8."),
        Parameter("Chain Selector", "continuous", 0.0, 0.0, 127.0, None, "rack", True,
                  "Chain selector for chain crossfading."),
    ],
    notes="Per-chain device parameters live on the chain devices, not the rack.",
    manual_url="https://www.ableton.com/en/manual/instrument-effect-and-midi-racks/",
)


# --------------------------------------------------------------------------- #
# Instrument Rack                                                             #
# --------------------------------------------------------------------------- #
INSTRUMENT_RACK = DeviceSchema(
    class_name="InstrumentGroupDevice",
    display_name="Instrument Rack",
    device_type="rack",
    description="Container for instruments + audio effect chains with 8 macros.",
    categories=["rack", "macros"],
    parameters=[
        Parameter("Macro 1", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 1."),
        Parameter("Macro 2", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 2."),
        Parameter("Macro 3", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 3."),
        Parameter("Macro 4", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 4."),
        Parameter("Macro 5", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 5."),
        Parameter("Macro 6", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 6."),
        Parameter("Macro 7", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 7."),
        Parameter("Macro 8", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 8."),
        Parameter("Chain Selector", "continuous", 0.0, 0.0, 127.0, None, "rack", True,
                  "Chain selector for chain crossfading and key/velocity zones."),
    ],
    manual_url="https://www.ableton.com/en/manual/instrument-effect-and-midi-racks/",
)


# --------------------------------------------------------------------------- #
# MIDI Effect Rack                                                            #
# --------------------------------------------------------------------------- #
MIDI_EFFECT_RACK = DeviceSchema(
    class_name="MidiEffectGroupDevice",
    display_name="MIDI Effect Rack",
    device_type="rack",
    description="Container for MIDI effects with 8 macros.",
    categories=["rack", "macros"],
    parameters=[
        Parameter("Macro 1", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 1."),
        Parameter("Macro 2", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 2."),
        Parameter("Macro 3", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 3."),
        Parameter("Macro 4", "continuous", 0.0, 0.0, 127.0, None, "macros", True, "Macro 4."),
        Parameter("Macro 5", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 5."),
        Parameter("Macro 6", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 6."),
        Parameter("Macro 7", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 7."),
        Parameter("Macro 8", "continuous", 0.0, 0.0, 127.0, None, "macros", False, "Macro 8."),
        Parameter("Chain Selector", "continuous", 0.0, 0.0, 127.0, None, "rack", True,
                  "Chain selector."),
    ],
    manual_url="https://www.ableton.com/en/manual/instrument-effect-and-midi-racks/",
)


UTILITY_SCHEMAS: list = [
    UTILITY,
    TUNER,
    SPECTRUM,
    AUDIO_EFFECT_RACK,
    INSTRUMENT_RACK,
    MIDI_EFFECT_RACK,
]
