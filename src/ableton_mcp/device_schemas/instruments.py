"""Schemas for Ableton Live 11 built-in instruments.

The class_names match the strings AbletonOSC returns from
``/live/track/get/devices/class_name`` (and that the Live LOM exposes via
``Device.class_name``). UI labels match the Live 11 reference manual.

A handful of complex instruments (Operator, Wavetable, Sampler, Drum
Rack, Tension, Collision) have *partial* schemas — the most relevant
sweep-worthy params are captured, but per-element / matrix params (e.g.
Operator's full operator A/B/C/D matrix, Wavetable's per-mod-source
routing) are summarised rather than enumerated. ``notes`` calls this out
on each schema so the caller doesn't assume completeness.
"""

from __future__ import annotations

from .base import DeviceSchema, Parameter


# --------------------------------------------------------------------------- #
# Operator                                                                    #
# --------------------------------------------------------------------------- #
OPERATOR = DeviceSchema(
    class_name="Operator",
    display_name="Operator",
    device_type="instrument",
    description=(
        "4-operator FM / additive / wavetable hybrid synth. Each operator "
        "(A/B/C/D) has its own oscillator + envelope; the global Algorithm "
        "selects the FM routing topology."
    ),
    categories=["oscillator", "envelope", "filter", "lfo", "global", "pitch"],
    parameters=[
        Parameter("Algorithm", "enum", 0, 0, 10, None, "global", True,
                  "FM routing topology. 11 algorithms; 0 is parallel, higher indices stack operators."),
        Parameter("Time", "continuous", 0.5, 0.0, 1.0, None, "envelope", True,
                  "Global time scale for all envelopes."),
        Parameter("Tone", "continuous", 0.5, 0.0, 1.0, None, "global", True,
                  "Global tone / brightness shaping."),
        Parameter("Volume", "continuous", -12.0, -36.0, 0.0, "dB", "mix", False,
                  "Master output volume."),
        Parameter("Transpose", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Global pitch transposition."),
        Parameter("Detune", "continuous", 0.0, -50.0, 50.0, "cents", "pitch", True,
                  "Global detune in cents."),
        # Per-operator (A is osc 1; the rest follow the same pattern in Live)
        Parameter("A Coarse", "quantized", 1, 0, 32, None, "oscillator", True,
                  "Operator A frequency ratio coarse multiplier."),
        Parameter("A Fine", "continuous", 0.0, -500.0, 500.0, "cents", "oscillator", False,
                  "Operator A fine detune."),
        Parameter("A Level", "continuous", 0.5, 0.0, 1.0, None, "oscillator", True,
                  "Operator A output level (carrier when at end of chain, else modulator depth)."),
        Parameter("A Wave", "enum", 0, 0, 22, None, "oscillator", True,
                  "Operator A waveform: Sine, Sine 4..., Saw, Square, Triangle, Noise, user wavetable, etc."),
        Parameter("A Attack", "continuous", 0.0, 0.0, 1.0, "sec", "envelope", True,
                  "Operator A envelope attack time."),
        Parameter("A Decay", "continuous", 0.6, 0.0, 1.0, "sec", "envelope", True,
                  "Operator A envelope decay time."),
        Parameter("A Sustain", "continuous", 1.0, 0.0, 1.0, None, "envelope", True,
                  "Operator A envelope sustain level."),
        Parameter("A Release", "continuous", 0.4, 0.0, 1.0, "sec", "envelope", True,
                  "Operator A envelope release time."),
        Parameter("B Coarse", "quantized", 1, 0, 32, None, "oscillator", True,
                  "Operator B frequency ratio coarse multiplier."),
        Parameter("B Level", "continuous", 0.0, 0.0, 1.0, None, "oscillator", True,
                  "Operator B output level."),
        Parameter("C Coarse", "quantized", 1, 0, 32, None, "oscillator", False,
                  "Operator C frequency ratio coarse multiplier."),
        Parameter("C Level", "continuous", 0.0, 0.0, 1.0, None, "oscillator", False,
                  "Operator C output level."),
        Parameter("D Coarse", "quantized", 1, 0, 32, None, "oscillator", False,
                  "Operator D frequency ratio coarse multiplier."),
        Parameter("D Level", "continuous", 0.0, 0.0, 1.0, None, "oscillator", False,
                  "Operator D output level."),
        # Filter
        Parameter("Filter Type", "enum", 0, 0, 12, None, "filter", True,
                  "Filter topology: LP12, LP24, BP, HP, Notch, Morph, etc."),
        Parameter("Filter Freq", "continuous", 8000.0, 20.0, 19000.0, "Hz", "filter", True,
                  "Filter cutoff frequency."),
        Parameter("Filter Res", "continuous", 0.0, 0.0, 1.25, None, "filter", True,
                  "Filter resonance / Q."),
        Parameter("Filter Drive", "continuous", 0.0, 0.0, 1.0, None, "filter", False,
                  "Filter overdrive amount."),
        # LFO
        Parameter("LFO On", "enum", 0, 0, 1, None, "lfo", False,
                  "LFO enable."),
        Parameter("LFO Rate", "continuous", 5.0, 0.0, 50.0, "Hz", "lfo", True,
                  "LFO rate (Hz when free, beats when synced)."),
        Parameter("LFO Amount", "continuous", 0.0, 0.0, 1.0, None, "lfo", True,
                  "LFO modulation depth."),
        Parameter("LFO Type", "enum", 0, 0, 5, None, "lfo", False,
                  "LFO waveform: Sine, Square, Triangle, Saw Up, Saw Down, S&H, Noise."),
        # Pitch / global modulation
        Parameter("Pitch Env Amount", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Pitch envelope depth."),
    ],
    notes=(
        "Schema partial: full per-operator envelope (Initial/Peak/End "
        "levels, time slopes), per-op wave-folder, glide, and full LFO "
        "destination routing matrix are not enumerated. Verified core "
        "params: Algorithm, Time, Tone, A/B/C/D Coarse+Level+Wave, "
        "A envelope, Filter Type/Freq/Res, LFO Rate/Amount."
    ),
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#operator",
)


# --------------------------------------------------------------------------- #
# Wavetable                                                                   #
# --------------------------------------------------------------------------- #
WAVETABLE = DeviceSchema(
    class_name="InstrumentVector",
    display_name="Wavetable",
    device_type="instrument",
    description=(
        "Two wavetable oscillators with sub osc, modulation matrix, dual "
        "filters, and three envelopes / two LFOs / MPE-style modulators."
    ),
    categories=["oscillator", "envelope", "filter", "lfo", "modulation", "mix"],
    parameters=[
        # Osc 1
        Parameter("Oscillator 1 On", "enum", 1, 0, 1, None, "oscillator", False,
                  "Toggle Oscillator 1."),
        Parameter("Oscillator 1 Effect Mode", "enum", 0, 0, 4, None, "oscillator", True,
                  "Wavetable effect: None, FM, Classic, Modern, Sub."),
        Parameter("Oscillator 1 Wavetables", "enum", 0, 0, 99, None, "oscillator", True,
                  "Wavetable selection (which preset table)."),
        Parameter("Oscillator 1 Wavetable Position", "continuous", 0.0, 0.0, 1.0, None, "oscillator", True,
                  "Position within the wavetable (the 'morph' control)."),
        Parameter("Oscillator 1 Effect 1", "continuous", 0.5, 0.0, 1.0, None, "oscillator", True,
                  "Effect amount knob 1 (meaning depends on Effect Mode)."),
        Parameter("Oscillator 1 Effect 2", "continuous", 0.5, 0.0, 1.0, None, "oscillator", True,
                  "Effect amount knob 2."),
        Parameter("Oscillator 1 Pitch", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Osc 1 transpose."),
        Parameter("Oscillator 1 Detune", "continuous", 0.0, -50.0, 50.0, "cents", "pitch", False,
                  "Osc 1 fine detune."),
        Parameter("Oscillator 1 Gain", "continuous", 0.0, -36.0, 12.0, "dB", "mix", False,
                  "Osc 1 mix gain."),
        # Osc 2
        Parameter("Oscillator 2 On", "enum", 0, 0, 1, None, "oscillator", True,
                  "Toggle Oscillator 2."),
        Parameter("Oscillator 2 Wavetable Position", "continuous", 0.0, 0.0, 1.0, None, "oscillator", True,
                  "Position within Oscillator 2's wavetable."),
        # Sub
        Parameter("Sub Oscillator On", "enum", 0, 0, 1, None, "oscillator", False,
                  "Toggle sub oscillator."),
        Parameter("Sub Oscillator Gain", "continuous", -12.0, -36.0, 0.0, "dB", "mix", True,
                  "Sub oscillator mix level."),
        # Filter 1
        Parameter("Filter 1 On", "enum", 1, 0, 1, None, "filter", False,
                  "Toggle Filter 1."),
        Parameter("Filter 1 Type", "enum", 0, 0, 4, None, "filter", True,
                  "Filter 1 type: Clean, OSR, MS2, SMP, PRD."),
        Parameter("Filter 1 Mode", "enum", 0, 0, 5, None, "filter", True,
                  "Filter 1 mode: Lowpass, Highpass, Bandpass, Notch, Morph, etc."),
        Parameter("Filter 1 Frequency", "continuous", 8000.0, 10.0, 22000.0, "Hz", "filter", True,
                  "Filter 1 cutoff."),
        Parameter("Filter 1 Resonance", "continuous", 0.4, 0.0, 1.25, None, "filter", True,
                  "Filter 1 resonance."),
        Parameter("Filter 1 Drive", "continuous", 0.0, 0.0, 24.0, "dB", "filter", False,
                  "Filter 1 drive."),
        # Envelope 1 (amp)
        Parameter("Envelope 1 Attack Time", "continuous", 0.001, 0.0, 60.0, "sec", "envelope", True,
                  "Amp envelope attack."),
        Parameter("Envelope 1 Decay Time", "continuous", 0.6, 0.0, 60.0, "sec", "envelope", True,
                  "Amp envelope decay."),
        Parameter("Envelope 1 Sustain Level", "continuous", 1.0, 0.0, 1.0, None, "envelope", True,
                  "Amp envelope sustain."),
        Parameter("Envelope 1 Release Time", "continuous", 0.5, 0.0, 60.0, "sec", "envelope", True,
                  "Amp envelope release."),
        # LFO 1
        Parameter("LFO 1 Rate", "continuous", 1.0, 0.0, 8.0, "Hz", "lfo", True,
                  "LFO 1 rate."),
        Parameter("LFO 1 Amount", "continuous", 0.0, 0.0, 1.0, None, "lfo", True,
                  "LFO 1 depth."),
        Parameter("LFO 1 Shape", "enum", 0, 0, 7, None, "lfo", False,
                  "LFO 1 waveform."),
        # Globals
        Parameter("Volume", "continuous", -6.0, -36.0, 12.0, "dB", "mix", False,
                  "Master volume."),
        Parameter("Voices", "quantized", 8, 1, 16, None, "global", False,
                  "Maximum simultaneous voices."),
    ],
    notes=(
        "Schema partial: only Filter 1 / Envelope 1 / LFO 1 enumerated; "
        "Filter 2, Envelopes 2-3, LFO 2, MPE / mod-matrix sources, and "
        "the per-osc Unison parameters are summarised. Class name is "
        "InstrumentVector (Live's internal name for Wavetable)."
    ),
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#wavetable",
)


# --------------------------------------------------------------------------- #
# Analog                                                                      #
# --------------------------------------------------------------------------- #
ANALOG = DeviceSchema(
    class_name="AnalogDevice",
    display_name="Analog",
    device_type="instrument",
    description=(
        "Two-oscillator virtual-analog subtractive synth modelled after "
        "vintage polysynths. Two filters, two LFOs, two amp envelopes."
    ),
    categories=["oscillator", "envelope", "filter", "lfo", "mix"],
    parameters=[
        Parameter("OSC1 Shape", "enum", 0, 0, 3, None, "oscillator", True,
                  "Oscillator 1 waveform: Sine, Saw, Rectangle, Noise."),
        Parameter("OSC1 Octave", "quantized", 0, -3, 3, None, "pitch", True,
                  "Oscillator 1 octave."),
        Parameter("OSC1 Semi", "continuous", 0.0, -12.0, 12.0, "semitones", "pitch", True,
                  "Oscillator 1 semitone offset."),
        Parameter("OSC1 Detune", "continuous", 0.0, -50.0, 50.0, "cents", "pitch", False,
                  "Oscillator 1 detune."),
        Parameter("OSC1 PW", "continuous", 0.5, 0.0, 1.0, None, "oscillator", True,
                  "Oscillator 1 pulse width."),
        Parameter("OSC2 Shape", "enum", 1, 0, 3, None, "oscillator", True,
                  "Oscillator 2 waveform."),
        Parameter("OSC2 Octave", "quantized", 0, -3, 3, None, "pitch", False,
                  "Oscillator 2 octave."),
        Parameter("OSC2 Detune", "continuous", 0.0, -50.0, 50.0, "cents", "pitch", True,
                  "Oscillator 2 detune."),
        Parameter("Filter1 Type", "enum", 0, 0, 3, None, "filter", True,
                  "Filter 1 mode: Lowpass, Highpass, Bandpass, Notch."),
        Parameter("Filter1 Slope", "enum", 0, 0, 1, None, "filter", False,
                  "Filter 1 slope: 12 dB or 24 dB."),
        Parameter("Filter1 Freq", "continuous", 8000.0, 20.0, 18000.0, "Hz", "filter", True,
                  "Filter 1 cutoff."),
        Parameter("Filter1 Res", "continuous", 0.0, 0.0, 1.0, None, "filter", True,
                  "Filter 1 resonance."),
        Parameter("Filter1 Env", "continuous", 0.0, -1.0, 1.0, None, "filter", True,
                  "Filter envelope amount."),
        Parameter("Amp1 Attack", "continuous", 0.01, 0.0, 10.0, "sec", "envelope", True,
                  "Amp 1 attack."),
        Parameter("Amp1 Decay", "continuous", 0.5, 0.0, 10.0, "sec", "envelope", True,
                  "Amp 1 decay."),
        Parameter("Amp1 Sustain", "continuous", 0.7, 0.0, 1.0, None, "envelope", True,
                  "Amp 1 sustain."),
        Parameter("Amp1 Release", "continuous", 0.3, 0.0, 10.0, "sec", "envelope", True,
                  "Amp 1 release."),
        Parameter("LFO1 Rate", "continuous", 5.0, 0.0, 50.0, "Hz", "lfo", True,
                  "LFO 1 rate."),
        Parameter("LFO1 Shape", "enum", 0, 0, 5, None, "lfo", False,
                  "LFO 1 waveform."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
    ],
    notes="Schema partial: filter routing (serial/parallel), filter 2, amp 2, LFO 2, vibrato, and MPE params not enumerated.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#analog",
)


# --------------------------------------------------------------------------- #
# Drift                                                                       #
# --------------------------------------------------------------------------- #
DRIFT = DeviceSchema(
    class_name="Drift",
    display_name="Drift",
    device_type="instrument",
    description=(
        "Compact MPE-ready monosynth with two oscillators, a multimode "
        "filter, two envelopes and two LFOs. Designed for fast sound-design "
        "and live tweakability."
    ),
    categories=["oscillator", "envelope", "filter", "lfo", "mix"],
    parameters=[
        Parameter("Osc 1 Shape", "enum", 0, 0, 4, None, "oscillator", True,
                  "Oscillator 1 wave: Sine, Triangle, Saw, Square, Pulse."),
        Parameter("Osc 1 Shape Mod", "continuous", 0.0, -1.0, 1.0, None, "oscillator", True,
                  "Wave shape modulation depth (PWM, etc.)."),
        Parameter("Osc 1 Pitch", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Osc 1 transpose."),
        Parameter("Osc 2 Shape", "enum", 0, 0, 4, None, "oscillator", True,
                  "Oscillator 2 wave."),
        Parameter("Osc 2 Pitch", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Osc 2 transpose."),
        Parameter("Osc Mix", "continuous", 0.0, -1.0, 1.0, None, "mix", True,
                  "Crossfade between Osc 1 and Osc 2 (-1 = all 1, +1 = all 2)."),
        Parameter("Noise Level", "continuous", 0.0, 0.0, 1.0, None, "mix", True,
                  "Noise generator level."),
        Parameter("Filter Type", "enum", 0, 0, 3, None, "filter", True,
                  "Filter mode: LP, HP, BP, Notch."),
        Parameter("Filter Frequency", "continuous", 8000.0, 20.0, 18000.0, "Hz", "filter", True,
                  "Filter cutoff."),
        Parameter("Filter Resonance", "continuous", 0.3, 0.0, 1.0, None, "filter", True,
                  "Filter resonance."),
        Parameter("Filter Tracking", "continuous", 0.0, -1.0, 1.0, None, "filter", False,
                  "Keyboard tracking of cutoff."),
        Parameter("Env 1 Attack", "continuous", 0.001, 0.0, 60.0, "sec", "envelope", True,
                  "Amp envelope attack."),
        Parameter("Env 1 Decay", "continuous", 0.5, 0.0, 60.0, "sec", "envelope", True,
                  "Amp envelope decay."),
        Parameter("Env 1 Sustain", "continuous", 0.8, 0.0, 1.0, None, "envelope", True,
                  "Amp envelope sustain."),
        Parameter("Env 1 Release", "continuous", 0.3, 0.0, 60.0, "sec", "envelope", True,
                  "Amp envelope release."),
        Parameter("LFO 1 Rate", "continuous", 1.0, 0.0, 30.0, "Hz", "lfo", True,
                  "LFO 1 rate."),
        Parameter("LFO 1 Shape", "enum", 0, 0, 5, None, "lfo", False,
                  "LFO 1 waveform."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
    ],
    notes="Schema partial; envelope 2 and full mod-matrix routing summarised.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#drift",
)


# --------------------------------------------------------------------------- #
# Simpler                                                                     #
# --------------------------------------------------------------------------- #
SIMPLER = DeviceSchema(
    class_name="OriginalSimpler",
    display_name="Simpler",
    device_type="instrument",
    description=(
        "Single-sample instrument with three play modes (Classic, "
        "One-Shot, Slicing), a multimode filter and an amp envelope."
    ),
    categories=["sample", "envelope", "filter", "lfo", "mix"],
    parameters=[
        Parameter("Playback Mode", "enum", 0, 0, 2, None, "sample", True,
                  "Classic / One-Shot / Slicing."),
        Parameter("Sample Start", "continuous", 0.0, 0.0, 1.0, None, "sample", True,
                  "Where in the sample playback begins (0..1 fraction)."),
        Parameter("Sample End", "continuous", 1.0, 0.0, 1.0, None, "sample", True,
                  "Where in the sample playback ends."),
        Parameter("Loop On", "enum", 0, 0, 1, None, "sample", False,
                  "Sample loop enable (Classic mode)."),
        Parameter("Warp", "enum", 0, 0, 1, None, "sample", False,
                  "Enable Live's warp engine for sample playback."),
        Parameter("Transpose", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Pitch in semitones."),
        Parameter("Detune", "continuous", 0.0, -50.0, 50.0, "cents", "pitch", False,
                  "Pitch in cents."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
        Parameter("Filter Frequency", "continuous", 22000.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Filter cutoff."),
        Parameter("Filter Resonance", "continuous", 0.0, 0.0, 1.25, None, "filter", True,
                  "Filter resonance."),
        Parameter("Filter Type", "enum", 0, 0, 3, None, "filter", True,
                  "Filter type: LP, HP, BP, Notch."),
        Parameter("Volume Envelope Attack", "continuous", 0.001, 0.0, 60.0, "sec", "envelope", True,
                  "Amp attack."),
        Parameter("Volume Envelope Decay", "continuous", 0.5, 0.0, 60.0, "sec", "envelope", True,
                  "Amp decay."),
        Parameter("Volume Envelope Sustain", "continuous", 1.0, 0.0, 1.0, None, "envelope", True,
                  "Amp sustain."),
        Parameter("Volume Envelope Release", "continuous", 0.05, 0.0, 60.0, "sec", "envelope", True,
                  "Amp release."),
        Parameter("Filter Envelope Amount", "continuous", 0.0, -1.0, 1.0, None, "filter", True,
                  "Filter envelope modulation depth."),
        Parameter("LFO Rate", "continuous", 1.0, 0.0, 30.0, "Hz", "lfo", True,
                  "LFO rate."),
        Parameter("LFO Amount", "continuous", 0.0, 0.0, 1.0, None, "lfo", False,
                  "LFO depth."),
        Parameter("Glide", "continuous", 0.0, 0.0, 10.0, "sec", "global", False,
                  "Glide / portamento time."),
    ],
    notes="Param names track the AbletonOSC-exposed ones for OriginalSimpler.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#simpler",
)


# --------------------------------------------------------------------------- #
# Sampler                                                                     #
# --------------------------------------------------------------------------- #
SAMPLER = DeviceSchema(
    class_name="MultiSampler",
    display_name="Sampler",
    device_type="instrument",
    description=(
        "Multi-sample instrument with key/velocity/round-robin zones, "
        "filter, three envelopes, three LFOs and modulation matrix."
    ),
    categories=["sample", "envelope", "filter", "lfo", "modulation", "mix"],
    parameters=[
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
        Parameter("Pitch / Transpose", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Global transpose."),
        Parameter("Pitch / Detune", "continuous", 0.0, -50.0, 50.0, "cents", "pitch", False,
                  "Global detune."),
        Parameter("Filter On", "enum", 1, 0, 1, None, "filter", False,
                  "Filter enable."),
        Parameter("Filter Type", "enum", 0, 0, 6, None, "filter", True,
                  "Filter type."),
        Parameter("Filter Freq", "continuous", 8000.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Filter cutoff."),
        Parameter("Filter Res", "continuous", 0.0, 0.0, 1.25, None, "filter", True,
                  "Filter resonance."),
        Parameter("Filter Env Amount", "continuous", 0.0, -1.0, 1.0, None, "filter", True,
                  "Filter envelope depth."),
        Parameter("Volume Envelope Attack", "continuous", 0.001, 0.0, 60.0, "sec", "envelope", True,
                  "Amp attack."),
        Parameter("Volume Envelope Decay", "continuous", 0.5, 0.0, 60.0, "sec", "envelope", True,
                  "Amp decay."),
        Parameter("Volume Envelope Sustain", "continuous", 1.0, 0.0, 1.0, None, "envelope", True,
                  "Amp sustain."),
        Parameter("Volume Envelope Release", "continuous", 0.05, 0.0, 60.0, "sec", "envelope", True,
                  "Amp release."),
        Parameter("LFO 1 Rate", "continuous", 1.0, 0.0, 30.0, "Hz", "lfo", True,
                  "LFO 1 rate."),
        Parameter("LFO 1 Amount", "continuous", 0.0, 0.0, 1.0, None, "lfo", False,
                  "LFO 1 depth."),
    ],
    notes=(
        "Schema partial: per-zone params, mod matrix, pitch envelope, "
        "and LFO 2/3 not enumerated. Class name MultiSampler is the LOM "
        "internal name for Sampler."
    ),
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#sampler",
)


# --------------------------------------------------------------------------- #
# Drum Rack                                                                   #
# --------------------------------------------------------------------------- #
DRUM_RACK = DeviceSchema(
    class_name="DrumGroupDevice",
    display_name="Drum Rack",
    device_type="drum",
    description=(
        "128-slot drum rack: each pad holds a chain (typically a Simpler) "
        "and is mapped to a MIDI note. Macros control multiple chain params."
    ),
    categories=["rack", "mix", "macros"],
    parameters=[
        Parameter("Macro 1", "continuous", 0.0, 0.0, 127.0, None, "macros", True,
                  "Macro 1 (mapped to whatever the rack maps it to)."),
        Parameter("Macro 2", "continuous", 0.0, 0.0, 127.0, None, "macros", True,
                  "Macro 2."),
        Parameter("Macro 3", "continuous", 0.0, 0.0, 127.0, None, "macros", True,
                  "Macro 3."),
        Parameter("Macro 4", "continuous", 0.0, 0.0, 127.0, None, "macros", True,
                  "Macro 4."),
        Parameter("Macro 5", "continuous", 0.0, 0.0, 127.0, None, "macros", False,
                  "Macro 5."),
        Parameter("Macro 6", "continuous", 0.0, 0.0, 127.0, None, "macros", False,
                  "Macro 6."),
        Parameter("Macro 7", "continuous", 0.0, 0.0, 127.0, None, "macros", False,
                  "Macro 7."),
        Parameter("Macro 8", "continuous", 0.0, 0.0, 127.0, None, "macros", False,
                  "Macro 8."),
    ],
    notes=(
        "Drum Rack params at the rack level are just the 8 macros. "
        "Per-pad params live on the chain devices (typically Simpler) — "
        "look those up via device_list on the rack's nested chains."
    ),
    manual_url="https://www.ableton.com/en/manual/instrument-effect-and-midi-racks/#drum-racks",
)


# --------------------------------------------------------------------------- #
# Impulse                                                                     #
# --------------------------------------------------------------------------- #
IMPULSE = DeviceSchema(
    class_name="Impulse",
    display_name="Impulse",
    device_type="instrument",
    description=(
        "8-slot drum sampler. Each slot has start, transpose, stretch, "
        "filter, saturator, pan, volume, and a global decay."
    ),
    categories=["sample", "filter", "fx", "mix"],
    parameters=[
        Parameter("Slot 1 Start", "continuous", 0.0, 0.0, 1.0, None, "sample", True,
                  "Slot 1 sample start."),
        Parameter("Slot 1 Transp", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Slot 1 transpose."),
        Parameter("Slot 1 Stretch", "continuous", 0.0, -100.0, 100.0, "%", "sample", True,
                  "Slot 1 time-stretch amount."),
        Parameter("Slot 1 Saturator", "enum", 0, 0, 1, None, "fx", False,
                  "Slot 1 saturator on/off."),
        Parameter("Slot 1 Sat Drive", "continuous", 0.0, 0.0, 24.0, "dB", "fx", False,
                  "Slot 1 saturator drive."),
        Parameter("Slot 1 Filter On", "enum", 0, 0, 1, None, "filter", False,
                  "Slot 1 filter enable."),
        Parameter("Slot 1 Filter Type", "enum", 0, 0, 3, None, "filter", False,
                  "Slot 1 filter type: LP, HP, BP, Notch."),
        Parameter("Slot 1 Filter Freq", "continuous", 8000.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Slot 1 filter cutoff."),
        Parameter("Slot 1 Filter Res", "continuous", 0.0, 0.0, 1.0, None, "filter", True,
                  "Slot 1 filter resonance."),
        Parameter("Slot 1 Decay", "continuous", 0.6, 0.0, 1.0, "sec", "envelope", True,
                  "Slot 1 amp decay."),
        Parameter("Slot 1 Pan", "continuous", 0.0, -1.0, 1.0, None, "mix", False,
                  "Slot 1 pan."),
        Parameter("Slot 1 Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Slot 1 volume."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master output."),
    ],
    notes=(
        "Schema partial: only Slot 1 params enumerated; Slots 2-8 follow "
        "the same pattern (e.g. 'Slot 2 Start', etc.)."
    ),
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#impulse",
)


# --------------------------------------------------------------------------- #
# Electric                                                                    #
# --------------------------------------------------------------------------- #
ELECTRIC = DeviceSchema(
    class_name="Electric",
    display_name="Electric",
    device_type="instrument",
    description="Physical-modelled electric piano (Rhodes / Wurlitzer style).",
    categories=["physical_model", "filter", "fx", "mix"],
    parameters=[
        Parameter("Mallet Stiffness", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "How hard the mallet feels — affects attack timbre."),
        Parameter("Mallet Force Min", "continuous", 0.2, 0.0, 1.0, None, "physical_model", False,
                  "Min mallet velocity."),
        Parameter("Mallet Force Max", "continuous", 0.8, 0.0, 1.0, None, "physical_model", False,
                  "Max mallet velocity."),
        Parameter("Fork Tine Decay", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Tine decay length."),
        Parameter("Fork Tine Level", "continuous", 0.7, 0.0, 1.0, None, "physical_model", True,
                  "Tine bar level."),
        Parameter("Fork Tone Decay", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Tone bar decay length."),
        Parameter("Fork Tone Level", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Tone bar level."),
        Parameter("Pickup Symmetry", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "How asymmetric the pickup is (affects harmonic content)."),
        Parameter("Pickup Distance", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Pickup gap distance."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
    ],
    notes="Schema partial; damper/release params and global stretch tuning summarised.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#electric",
)


# --------------------------------------------------------------------------- #
# Tension                                                                     #
# --------------------------------------------------------------------------- #
TENSION = DeviceSchema(
    class_name="Tension",
    display_name="Tension",
    device_type="instrument",
    description="Physical-modelled string instrument with excitator + string + body.",
    categories=["physical_model", "filter", "fx", "mix"],
    parameters=[
        Parameter("Excitator Type", "enum", 0, 0, 4, None, "physical_model", True,
                  "Bow / Hammer / Hammer (Bouncing) / Plectrum / Plectrum (Soft)."),
        Parameter("Force", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Excitator force."),
        Parameter("Velocity", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Excitator velocity."),
        Parameter("Friction", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Excitator friction (bow only)."),
        Parameter("Position", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Excitation position along the string."),
        Parameter("String Decay", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "String decay length."),
        Parameter("String Damping", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "String damping factor."),
        Parameter("Body Type", "enum", 0, 0, 6, None, "physical_model", True,
                  "Resonating body (Violin/Viola/Cello/Bass/Guitar/Piano/None)."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
    ],
    notes="Schema partial; full damper, termination and body radiation params not enumerated.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#tension",
)


# --------------------------------------------------------------------------- #
# Bass                                                                        #
# --------------------------------------------------------------------------- #
BASS = DeviceSchema(
    class_name="Bass",
    display_name="Bass",
    device_type="instrument",
    description=(
        "Compact monosynth with a single oscillator (Saw/Square/Sub blend), "
        "Sub-Harmonic and Overdrive sections. New in Live 11.1."
    ),
    categories=["oscillator", "envelope", "filter", "fx", "mix"],
    parameters=[
        Parameter("Osc Shape", "continuous", 0.0, -1.0, 1.0, None, "oscillator", True,
                  "Crossfade Saw <-> Square."),
        Parameter("Osc Sub", "continuous", 0.0, 0.0, 1.0, None, "oscillator", True,
                  "Sub oscillator level."),
        Parameter("Octave", "quantized", 0, -2, 2, None, "pitch", True,
                  "Octave offset."),
        Parameter("Glide", "continuous", 0.0, 0.0, 1.0, "sec", "global", False,
                  "Glide / portamento time."),
        Parameter("Filter Frequency", "continuous", 5000.0, 20.0, 18000.0, "Hz", "filter", True,
                  "Filter cutoff."),
        Parameter("Filter Resonance", "continuous", 0.3, 0.0, 1.0, None, "filter", True,
                  "Filter resonance."),
        Parameter("Filter Env Amount", "continuous", 0.5, -1.0, 1.0, None, "filter", True,
                  "Filter envelope depth."),
        Parameter("Filter Env Decay", "continuous", 0.4, 0.0, 1.0, "sec", "envelope", True,
                  "Filter envelope decay."),
        Parameter("Amp Attack", "continuous", 0.001, 0.0, 1.0, "sec", "envelope", True,
                  "Amp attack."),
        Parameter("Amp Decay", "continuous", 0.4, 0.0, 1.0, "sec", "envelope", True,
                  "Amp decay."),
        Parameter("Amp Sustain", "continuous", 0.8, 0.0, 1.0, None, "envelope", True,
                  "Amp sustain."),
        Parameter("Amp Release", "continuous", 0.2, 0.0, 1.0, "sec", "envelope", True,
                  "Amp release."),
        Parameter("Drive", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Built-in overdrive amount."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
    ],
    notes="Schema partial; modulation matrix and MPE params not enumerated.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#bass",
)


# --------------------------------------------------------------------------- #
# Collision                                                                   #
# --------------------------------------------------------------------------- #
COLLISION = DeviceSchema(
    class_name="Collision",
    display_name="Collision",
    device_type="instrument",
    description="Physical-modelled mallet percussion (mallet → resonator).",
    categories=["physical_model", "fx", "mix"],
    parameters=[
        Parameter("Mallet Stiffness", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Mallet stiffness."),
        Parameter("Mallet Noise Amount", "continuous", 0.2, 0.0, 1.0, None, "physical_model", True,
                  "Noise mixed into the strike."),
        Parameter("Mallet Color", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Mallet noise tone."),
        Parameter("Resonator 1 Type", "enum", 0, 0, 6, None, "physical_model", True,
                  "Resonator 1 type: Beam, Marimba, String, Membrane, Plate, Pipe, Tube."),
        Parameter("Resonator 1 Brightness", "continuous", 0.5, 0.0, 1.0, None, "physical_model", True,
                  "Resonator 1 brightness."),
        Parameter("Resonator 1 Decay", "continuous", 0.5, 0.0, 1.0, "sec", "physical_model", True,
                  "Resonator 1 decay."),
        Parameter("Resonator 1 Tune", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Resonator 1 tune."),
        Parameter("Volume", "continuous", -6.0, -36.0, 6.0, "dB", "mix", False,
                  "Master volume."),
    ],
    notes="Schema partial; resonator 2, mallet position, listening position and per-resonator inharmonicity not enumerated.",
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#collision",
)


# --------------------------------------------------------------------------- #
# External Instrument                                                         #
# --------------------------------------------------------------------------- #
EXTERNAL_INSTRUMENT = DeviceSchema(
    class_name="ExternalInstrumentDevice",
    display_name="External Instrument",
    device_type="instrument",
    description=(
        "Routing-only device: sends MIDI to an external hardware synth or "
        "VST/AU and brings audio back."
    ),
    categories=["routing", "mix"],
    parameters=[
        Parameter("Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Audio return gain."),
        Parameter("Hardware Latency", "continuous", 0.0, 0.0, 100.0, "ms", "global", False,
                  "Hardware roundtrip latency compensation."),
    ],
    notes=(
        "External Instrument has very few automatable params; routing "
        "(MIDI To / Audio From / channel) is set via the GUI and not "
        "exposed as DeviceParameters."
    ),
    manual_url="https://www.ableton.com/en/manual/live-instrument-reference/#external-instrument",
)


INSTRUMENT_SCHEMAS: list = [
    OPERATOR,
    WAVETABLE,
    ANALOG,
    DRIFT,
    SIMPLER,
    SAMPLER,
    DRUM_RACK,
    IMPULSE,
    ELECTRIC,
    TENSION,
    BASS,
    COLLISION,
    EXTERNAL_INSTRUMENT,
]
