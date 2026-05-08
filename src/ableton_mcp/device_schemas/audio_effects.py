"""Schemas for Ableton Live 11 built-in audio effects.

Class names match the LOM-exposed ``Device.class_name`` strings. UI
labels match the Live 11 reference manual. Where a device has hundreds
of internal parameters (e.g. EQ Eight's eight bands), only the most
sweep-relevant params for *one* band/section are enumerated and the rest
are summarised in ``notes``.
"""

from __future__ import annotations

from .base import DeviceSchema, Parameter


# --------------------------------------------------------------------------- #
# EQ Eight                                                                    #
# --------------------------------------------------------------------------- #
EQ_EIGHT = DeviceSchema(
    class_name="Eq8",
    display_name="EQ Eight",
    device_type="audio_effect",
    description="Eight-band parametric equalizer with shelf, bell, notch, lowpass, highpass modes.",
    categories=["eq", "filter"],
    parameters=[
        Parameter("Global Gain", "continuous", 0.0, -15.0, 15.0, "dB", "mix", False,
                  "Output gain trim."),
        Parameter("Scale", "continuous", 1.0, 0.0, 1.0, None, "global", False,
                  "Master scale of all band gains."),
        Parameter("1 Filter On A", "enum", 1, 0, 1, None, "band", False,
                  "Band 1 enable."),
        Parameter("1 Filter Type A", "enum", 0, 0, 7, None, "band", True,
                  "Band 1 type: HP48, HP12, LowShelf, Bell, Notch, HighShelf, LP12, LP48."),
        Parameter("1 Frequency A", "continuous", 80.0, 10.0, 22000.0, "Hz", "band", True,
                  "Band 1 frequency."),
        Parameter("1 Gain A", "continuous", 0.0, -15.0, 15.0, "dB", "band", True,
                  "Band 1 gain (bell / shelf bands)."),
        Parameter("1 Resonance A", "continuous", 0.71, 0.1, 18.0, None, "band", True,
                  "Band 1 Q / resonance."),
        Parameter("4 Frequency A", "continuous", 1000.0, 10.0, 22000.0, "Hz", "band", True,
                  "Band 4 frequency."),
        Parameter("4 Gain A", "continuous", 0.0, -15.0, 15.0, "dB", "band", True,
                  "Band 4 gain."),
        Parameter("4 Resonance A", "continuous", 0.71, 0.1, 18.0, None, "band", True,
                  "Band 4 Q."),
    ],
    notes=(
        "Schema partial: only Bands 1 and 4 enumerated; Bands 2/3/5/6/7/8 "
        "follow the same '<n> Filter Type/Frequency/Gain/Resonance A' "
        "pattern. The 'A' suffix is for the A channel (M/S mode also has B)."
    ),
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#eq-eight",
)


# --------------------------------------------------------------------------- #
# EQ Three                                                                    #
# --------------------------------------------------------------------------- #
EQ_THREE = DeviceSchema(
    class_name="FilterEQ3",
    display_name="EQ Three",
    device_type="audio_effect",
    description="DJ-style 3-band equalizer with kill switches.",
    categories=["eq", "filter"],
    parameters=[
        Parameter("GainLo", "continuous", 0.0, -70.0, 6.0, "dB", "band", True,
                  "Low band gain (-inf at minimum kills the band)."),
        Parameter("GainMid", "continuous", 0.0, -70.0, 6.0, "dB", "band", True,
                  "Mid band gain."),
        Parameter("GainHi", "continuous", 0.0, -70.0, 6.0, "dB", "band", True,
                  "High band gain."),
        Parameter("FreqLo", "continuous", 250.0, 50.0, 1000.0, "Hz", "band", True,
                  "Low/mid crossover."),
        Parameter("FreqHi", "continuous", 2500.0, 1000.0, 18000.0, "Hz", "band", True,
                  "Mid/high crossover."),
        Parameter("LowOn", "enum", 1, 0, 1, None, "band", False,
                  "Low band on/off (kill switch)."),
        Parameter("MidOn", "enum", 1, 0, 1, None, "band", False,
                  "Mid band on/off."),
        Parameter("HighOn", "enum", 1, 0, 1, None, "band", False,
                  "High band on/off."),
    ],
    notes="Gain bottom is -inf dB (full kill); Live exposes this as a very large negative.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#eq-three",
)


# --------------------------------------------------------------------------- #
# Compressor                                                                  #
# --------------------------------------------------------------------------- #
COMPRESSOR = DeviceSchema(
    class_name="Compressor2",
    display_name="Compressor",
    device_type="audio_effect",
    description="Feed-forward / feedback compressor with peak/RMS detection and sidechain.",
    categories=["dynamics"],
    parameters=[
        Parameter("Threshold", "continuous", -20.0, -70.0, 0.0, "dB", "dynamics", True,
                  "Threshold above which compression engages."),
        Parameter("Ratio", "continuous", 4.0, 1.0, 100.0, ":1", "dynamics", True,
                  "Compression ratio."),
        Parameter("Attack", "continuous", 5.0, 0.01, 200.0, "ms", "dynamics", True,
                  "Attack time."),
        Parameter("Release", "continuous", 50.0, 1.0, 1000.0, "ms", "dynamics", True,
                  "Release time."),
        Parameter("Knee", "continuous", 6.0, 0.0, 18.0, "dB", "dynamics", False,
                  "Knee softness."),
        Parameter("Output Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", True,
                  "Make-up gain."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Parallel-compression mix."),
        Parameter("Model", "enum", 0, 0, 2, None, "dynamics", False,
                  "Detection model: Peak / RMS / Expand."),
        Parameter("Sidechain On", "enum", 0, 0, 1, None, "sidechain", False,
                  "External sidechain enable."),
        Parameter("EQ On", "enum", 0, 0, 1, None, "sidechain", False,
                  "Sidechain EQ enable."),
    ],
    notes="Class name Compressor2 is the modern Live compressor (Live 9+).",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#compressor",
)


# --------------------------------------------------------------------------- #
# Glue Compressor                                                             #
# --------------------------------------------------------------------------- #
GLUE_COMPRESSOR = DeviceSchema(
    class_name="GlueCompressor",
    display_name="Glue Compressor",
    device_type="audio_effect",
    description="SSL-style bus compressor modelled on the 4000 series.",
    categories=["dynamics"],
    parameters=[
        Parameter("Threshold", "continuous", -10.0, -36.0, 0.0, "dB", "dynamics", True,
                  "Threshold."),
        Parameter("Ratio", "enum", 1, 0, 5, None, "dynamics", True,
                  "Ratio: 1.5, 2, 4, 10, 20, ∞ (6 fixed positions)."),
        Parameter("Attack", "enum", 2, 0, 5, "ms", "dynamics", True,
                  "Attack: 0.01 / 0.3 / 1 / 3 / 10 / 30 ms."),
        Parameter("Release", "enum", 1, 0, 6, None, "dynamics", True,
                  "Release: 0.1 / 0.2 / 0.4 / 0.6 / 0.8 / 1.2 / Auto (s)."),
        Parameter("Makeup", "continuous", 0.0, -20.0, 20.0, "dB", "mix", True,
                  "Make-up gain."),
        Parameter("Range", "continuous", 0.0, -60.0, 0.0, "dB", "dynamics", False,
                  "Max gain reduction range."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Parallel mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#glue-compressor",
)


# --------------------------------------------------------------------------- #
# Multiband Dynamics                                                          #
# --------------------------------------------------------------------------- #
MULTIBAND_DYNAMICS = DeviceSchema(
    class_name="MultibandDynamics",
    display_name="Multiband Dynamics",
    device_type="audio_effect",
    description="Three-band upward + downward compressor / expander.",
    categories=["dynamics", "multiband"],
    parameters=[
        Parameter("LF Above Threshold", "continuous", 0.0, -70.0, 0.0, "dB", "dynamics", True,
                  "Low band above-threshold compression threshold."),
        Parameter("LF Below Threshold", "continuous", -40.0, -70.0, 0.0, "dB", "dynamics", True,
                  "Low band below-threshold expansion threshold."),
        Parameter("LF Above Ratio", "continuous", 1.0, 0.5, 100.0, ":1", "dynamics", True,
                  "Low band above-threshold ratio."),
        Parameter("MF Above Threshold", "continuous", 0.0, -70.0, 0.0, "dB", "dynamics", True,
                  "Mid band threshold."),
        Parameter("HF Above Threshold", "continuous", 0.0, -70.0, 0.0, "dB", "dynamics", True,
                  "High band threshold."),
        Parameter("Time", "continuous", 0.5, 0.0, 1.0, None, "dynamics", False,
                  "Global time scale."),
        Parameter("Output", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output trim."),
        Parameter("Crossover Frequency Low", "continuous", 250.0, 20.0, 5000.0, "Hz", "multiband", True,
                  "Low/mid split."),
        Parameter("Crossover Frequency High", "continuous", 2500.0, 200.0, 18000.0, "Hz", "multiband", True,
                  "Mid/high split."),
    ],
    notes="Schema partial: full per-band attack/release/below-ratio not enumerated.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#multiband-dynamics",
)


# --------------------------------------------------------------------------- #
# Limiter                                                                     #
# --------------------------------------------------------------------------- #
LIMITER = DeviceSchema(
    class_name="Limiter",
    display_name="Limiter",
    device_type="audio_effect",
    description="Brickwall limiter for the master bus.",
    categories=["dynamics", "mastering"],
    parameters=[
        Parameter("Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", True,
                  "Input gain."),
        Parameter("Ceiling", "continuous", -0.3, -70.0, 0.0, "dB", "dynamics", True,
                  "Output ceiling."),
        Parameter("Release", "continuous", 300.0, 1.0, 1000.0, "ms", "dynamics", True,
                  "Release time (Auto if very high)."),
        Parameter("Lookahead", "enum", 1, 0, 2, "ms", "dynamics", False,
                  "Lookahead: 1.5 / 3 / 6 ms."),
        Parameter("Stereo/L/R Link", "enum", 0, 0, 1, None, "global", False,
                  "Stereo link mode."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#limiter",
)


# --------------------------------------------------------------------------- #
# Gate                                                                        #
# --------------------------------------------------------------------------- #
GATE = DeviceSchema(
    class_name="Gate",
    display_name="Gate",
    device_type="audio_effect",
    description="Noise gate with sidechain.",
    categories=["dynamics"],
    parameters=[
        Parameter("Threshold", "continuous", -20.0, -70.0, 0.0, "dB", "dynamics", True,
                  "Below this level the gate closes."),
        Parameter("Return", "continuous", 3.0, 0.0, 36.0, "dB", "dynamics", True,
                  "Hysteresis amount above threshold for re-opening."),
        Parameter("Floor", "continuous", -70.0, -70.0, 0.0, "dB", "dynamics", True,
                  "Closed-gate level (how much signal still passes; -inf at min)."),
        Parameter("Attack", "continuous", 1.0, 0.0, 100.0, "ms", "dynamics", True,
                  "Open time."),
        Parameter("Hold", "continuous", 5.0, 0.0, 1000.0, "ms", "dynamics", False,
                  "Hold time."),
        Parameter("Release", "continuous", 50.0, 1.0, 1000.0, "ms", "dynamics", True,
                  "Close time."),
        Parameter("Sidechain On", "enum", 0, 0, 1, None, "sidechain", False,
                  "External sidechain enable."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#gate",
)


# --------------------------------------------------------------------------- #
# Reverb                                                                      #
# --------------------------------------------------------------------------- #
REVERB = DeviceSchema(
    class_name="Reverb",
    display_name="Reverb",
    device_type="audio_effect",
    description="Algorithmic reverb with early reflections + diffusion network.",
    categories=["fx", "reverb"],
    parameters=[
        Parameter("Predelay", "continuous", 5.0, 0.5, 250.0, "ms", "fx", True,
                  "Predelay before early reflections."),
        Parameter("Decay Time", "continuous", 1.5, 0.2, 60.0, "sec", "fx", True,
                  "RT60 decay length."),
        Parameter("Room Size", "continuous", 100.0, 0.22, 500.0, "m²", "fx", True,
                  "Diffusion network room size."),
        Parameter("Diffuse Level", "continuous", 0.0, -36.0, 6.0, "dB", "fx", True,
                  "Tail level."),
        Parameter("Reflect Level", "continuous", 0.0, -36.0, 6.0, "dB", "fx", True,
                  "Early reflections level."),
        Parameter("Quality", "enum", 1, 0, 2, None, "global", False,
                  "Quality: Eco / Mid / High."),
        Parameter("Stereo Image", "continuous", 100.0, 0.0, 120.0, "deg", "fx", False,
                  "Tail stereo width."),
        Parameter("Freeze On", "enum", 0, 0, 1, None, "fx", False,
                  "Freeze the tail (infinite reverb)."),
        Parameter("In Filter Freq", "continuous", 600.0, 50.0, 18000.0, "Hz", "filter", True,
                  "Input bandpass center."),
        Parameter("In Filter Width", "continuous", 6.0, 0.5, 9.0, None, "filter", False,
                  "Input filter width."),
        Parameter("HiShelf Freq", "continuous", 6000.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Tail high-shelf cutoff."),
        Parameter("HiShelf Gain", "continuous", -6.0, -24.0, 12.0, "dB", "filter", True,
                  "Tail high-shelf gain."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Wet/dry balance."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#reverb",
)


# --------------------------------------------------------------------------- #
# Hybrid Reverb                                                               #
# --------------------------------------------------------------------------- #
HYBRID_REVERB = DeviceSchema(
    class_name="HybridReverb",
    display_name="Hybrid Reverb",
    device_type="audio_effect",
    description="Convolution + algorithmic reverb in one device (Live 11+).",
    categories=["fx", "reverb"],
    parameters=[
        Parameter("Algorithm Type", "enum", 0, 0, 3, None, "fx", True,
                  "Algorithm tail type: Hall / Plate / Room / Ambience."),
        Parameter("Predelay", "continuous", 0.0, 0.0, 250.0, "ms", "fx", True,
                  "Convolution + algorithm predelay."),
        Parameter("Decay Time", "continuous", 1.5, 0.1, 60.0, "sec", "fx", True,
                  "Algorithm tail length."),
        Parameter("Size", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Algorithm room size."),
        Parameter("Damping", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "High-frequency damping."),
        Parameter("Convolution Type", "enum", 0, 0, 5, None, "fx", True,
                  "IR family (Halls / Chambers / Rooms / Plates / Ambiences / User)."),
        Parameter("Conv/Algorithm Blend", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Mix between convolution and algorithm."),
        Parameter("Dry/Wet", "continuous", 0.3, 0.0, 1.0, None, "mix", True,
                  "Wet/dry."),
    ],
    notes="Schema partial; per-IR file selection isn't a single param.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#hybrid-reverb",
)


# --------------------------------------------------------------------------- #
# Convolution Reverb (Max for Live)                                           #
# --------------------------------------------------------------------------- #
CONVOLUTION_REVERB = DeviceSchema(
    class_name="ConvolutionReverb",
    display_name="Convolution Reverb",
    device_type="audio_effect",
    description="Max-for-Live convolution reverb (Suite). Loads user IRs.",
    categories=["fx", "reverb"],
    parameters=[
        Parameter("Predelay", "continuous", 0.0, 0.0, 200.0, "ms", "fx", True,
                  "Predelay."),
        Parameter("Size Smoothing", "continuous", 0.0, 0.0, 1.0, None, "fx", False,
                  "Size smoothing."),
        Parameter("IR Time Stretch", "continuous", 1.0, 0.5, 2.0, None, "fx", True,
                  "Stretch the impulse response."),
        Parameter("Damping", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Tail high-frequency damping."),
        Parameter("Dry/Wet", "continuous", 0.3, 0.0, 1.0, None, "mix", True,
                  "Wet/dry."),
    ],
    notes="Max for Live device; param surface depends on M4L patch.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#convolution-reverb",
)


# --------------------------------------------------------------------------- #
# Delay                                                                       #
# --------------------------------------------------------------------------- #
DELAY = DeviceSchema(
    class_name="Delay",
    display_name="Delay",
    device_type="audio_effect",
    description="Stereo delay with sync/free per side, ping-pong, freeze.",
    categories=["fx", "delay"],
    parameters=[
        Parameter("L Sync", "enum", 1, 0, 1, None, "fx", False,
                  "Left side tempo-sync mode."),
        Parameter("L 16th", "quantized", 4, 1, 32, None, "fx", True,
                  "Left side delay time in 16th notes (when synced)."),
        Parameter("L Time", "continuous", 250.0, 1.0, 5000.0, "ms", "fx", True,
                  "Left side delay time (when free)."),
        Parameter("L Offset", "continuous", 0.0, -50.0, 50.0, "%", "fx", False,
                  "Left side offset."),
        Parameter("R 16th", "quantized", 4, 1, 32, None, "fx", True,
                  "Right side delay time in 16th notes."),
        Parameter("Feedback", "continuous", 0.5, 0.0, 1.1, None, "fx", True,
                  "Feedback (>1 self-oscillates)."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Wet/dry."),
        Parameter("Mode", "enum", 0, 0, 2, None, "fx", True,
                  "Mode: Repitch / Fade / Jump."),
        Parameter("Filter On", "enum", 1, 0, 1, None, "filter", False,
                  "Feedback bandpass on."),
        Parameter("Filter Freq", "continuous", 800.0, 20.0, 18000.0, "Hz", "filter", True,
                  "Feedback filter freq."),
        Parameter("Filter Width", "continuous", 4.0, 0.5, 9.0, None, "filter", False,
                  "Feedback filter width."),
        Parameter("Modulation Frequency", "continuous", 0.7, 0.0, 10.0, "Hz", "modulation", False,
                  "Modulation LFO rate."),
        Parameter("Modulation Filter", "continuous", 0.0, 0.0, 1.0, None, "modulation", False,
                  "Modulation depth on filter."),
        Parameter("Modulation Time", "continuous", 0.0, 0.0, 1.0, None, "modulation", False,
                  "Modulation depth on time."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#delay",
)


# --------------------------------------------------------------------------- #
# Echo                                                                        #
# --------------------------------------------------------------------------- #
ECHO = DeviceSchema(
    class_name="Echo",
    display_name="Echo",
    device_type="audio_effect",
    description="Character delay with reverb tail, modulation, ducking, character section.",
    categories=["fx", "delay", "modulation"],
    parameters=[
        Parameter("L Sync", "enum", 1, 0, 1, None, "fx", False,
                  "Left side sync mode."),
        Parameter("L 16th", "quantized", 4, 1, 32, None, "fx", True,
                  "Left side delay (16ths)."),
        Parameter("L Time", "continuous", 250.0, 1.0, 5000.0, "ms", "fx", True,
                  "Left side delay (ms)."),
        Parameter("Feedback", "continuous", 0.4, 0.0, 1.1, None, "fx", True,
                  "Feedback."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Wet/dry."),
        Parameter("Output Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output gain."),
        Parameter("Filter Freq Hi", "continuous", 8000.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Hi cut."),
        Parameter("Filter Freq Lo", "continuous", 100.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Lo cut."),
        Parameter("Mod Wave", "enum", 0, 0, 4, None, "modulation", False,
                  "Modulation waveform."),
        Parameter("Mod Rate", "continuous", 0.5, 0.0, 30.0, "Hz", "modulation", True,
                  "Modulation rate."),
        Parameter("Mod Filter", "continuous", 0.0, 0.0, 1.0, None, "modulation", True,
                  "Modulation amount on filter."),
        Parameter("Mod Delay", "continuous", 0.0, 0.0, 1.0, None, "modulation", True,
                  "Modulation amount on time."),
        Parameter("Reverb Amount", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Reverb tail mixed into the echoes."),
        Parameter("Reverb Decay", "continuous", 0.5, 0.0, 1.0, "sec", "fx", True,
                  "Reverb decay length."),
        Parameter("Character Noise", "continuous", 0.0, 0.0, 1.0, None, "fx", False,
                  "Adds tape-like noise."),
        Parameter("Character Wobble", "continuous", 0.0, 0.0, 1.0, None, "fx", False,
                  "Adds tape wobble."),
        Parameter("Gate On", "enum", 0, 0, 1, None, "dynamics", False,
                  "Ducker enable."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#echo",
)


# --------------------------------------------------------------------------- #
# Auto Filter                                                                 #
# --------------------------------------------------------------------------- #
AUTO_FILTER = DeviceSchema(
    class_name="AutoFilter",
    display_name="Auto Filter",
    device_type="audio_effect",
    description="Multimode filter with envelope follower and LFO modulation.",
    categories=["filter", "lfo", "envelope"],
    parameters=[
        Parameter("Filter Type", "enum", 0, 0, 4, None, "filter", True,
                  "Filter type: LP / HP / BP / Notch / Morph."),
        Parameter("Filter Circuit - LP/HP", "enum", 0, 0, 3, None, "filter", True,
                  "Filter circuit: Clean / OSR / MS2 / SMP / PRD."),
        Parameter("Slope", "enum", 0, 0, 1, None, "filter", False,
                  "12 dB or 24 dB."),
        Parameter("Frequency", "continuous", 1000.0, 20.0, 19000.0, "Hz", "filter", True,
                  "Cutoff."),
        Parameter("Resonance", "continuous", 0.0, 0.0, 1.25, None, "filter", True,
                  "Resonance."),
        Parameter("Drive", "continuous", 0.0, 0.0, 24.0, "dB", "filter", False,
                  "Drive."),
        Parameter("LFO Amount", "continuous", 0.0, 0.0, 1.0, None, "lfo", True,
                  "LFO modulation depth on cutoff."),
        Parameter("LFO Frequency", "continuous", 1.0, 0.0, 30.0, "Hz", "lfo", True,
                  "LFO rate."),
        Parameter("LFO Waveform", "enum", 0, 0, 5, None, "lfo", False,
                  "LFO shape."),
        Parameter("Envelope Modulation", "continuous", 0.0, -24.0, 24.0, None, "envelope", True,
                  "Envelope-follower depth on cutoff."),
        Parameter("Envelope Attack", "continuous", 1.0, 0.1, 1000.0, "ms", "envelope", True,
                  "Envelope follower attack."),
        Parameter("Envelope Release", "continuous", 100.0, 1.0, 5000.0, "ms", "envelope", True,
                  "Envelope follower release."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#auto-filter",
)


# --------------------------------------------------------------------------- #
# Saturator                                                                   #
# --------------------------------------------------------------------------- #
SATURATOR = DeviceSchema(
    class_name="Saturator",
    display_name="Saturator",
    device_type="audio_effect",
    description="Waveshaping saturator with multiple curves and a built-in HP/LP/EQ.",
    categories=["fx", "distortion"],
    parameters=[
        Parameter("Drive", "continuous", 0.0, -36.0, 36.0, "dB", "fx", True,
                  "Input drive."),
        Parameter("Type", "enum", 0, 0, 5, None, "fx", True,
                  "Curve: Analog Clip / Soft Sine / Medium Curve / Hard Curve / Sinoid Fold / Digital Clip."),
        Parameter("Output", "continuous", 0.0, -36.0, 36.0, "dB", "mix", True,
                  "Output trim."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Mix."),
        Parameter("Color On", "enum", 0, 0, 1, None, "filter", False,
                  "Pre-emphasis EQ on."),
        Parameter("Base Frequency", "continuous", 200.0, 20.0, 22000.0, "Hz", "filter", False,
                  "Pre-emphasis frequency."),
        Parameter("Width", "continuous", 1.0, 0.1, 6.0, None, "filter", False,
                  "Pre-emphasis Q."),
        Parameter("Soft Clip On", "enum", 1, 0, 1, None, "fx", False,
                  "Soft-clip output stage."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#saturator",
)


# --------------------------------------------------------------------------- #
# Drum Buss                                                                   #
# --------------------------------------------------------------------------- #
DRUM_BUSS = DeviceSchema(
    class_name="DrumBuss",
    display_name="Drum Buss",
    device_type="audio_effect",
    description="One-stop drum-bus processor: drive, transients, sub generator, compressor.",
    categories=["fx", "dynamics", "distortion"],
    parameters=[
        Parameter("Drive", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Saturation amount."),
        Parameter("Crunch", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "High-frequency saturation."),
        Parameter("Damp", "continuous", 22000.0, 100.0, 22000.0, "Hz", "filter", True,
                  "Low-pass filter cutoff."),
        Parameter("Boom Amount", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Sub-harmonic generator level."),
        Parameter("Boom Frequency", "continuous", 70.0, 30.0, 150.0, "Hz", "fx", True,
                  "Sub generator frequency."),
        Parameter("Boom Decay", "continuous", 0.5, 0.0, 1.0, "sec", "fx", True,
                  "Sub envelope decay."),
        Parameter("Transients", "continuous", 0.0, -1.0, 1.0, None, "dynamics", True,
                  "Transient shaper amount."),
        Parameter("Compressor", "continuous", 0.0, 0.0, 1.0, None, "dynamics", True,
                  "Built-in compressor amount."),
        Parameter("Output", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#drum-buss",
)


# --------------------------------------------------------------------------- #
# Chorus-Ensemble                                                             #
# --------------------------------------------------------------------------- #
CHORUS_ENSEMBLE = DeviceSchema(
    class_name="ChorusEnsemble",
    display_name="Chorus-Ensemble",
    device_type="audio_effect",
    description="Modern chorus / ensemble with three modes (Live 10.1+).",
    categories=["fx", "modulation"],
    parameters=[
        Parameter("Mode", "enum", 0, 0, 2, None, "fx", True,
                  "Chorus / Ensemble / Vibrato."),
        Parameter("Amount", "continuous", 1.5, 0.0, 4.0, None, "modulation", True,
                  "Modulation depth."),
        Parameter("Rate", "continuous", 0.6, 0.01, 20.0, "Hz", "modulation", True,
                  "LFO rate."),
        Parameter("Feedback", "continuous", 0.0, -1.0, 1.0, None, "fx", True,
                  "Chorus feedback (Chorus mode)."),
        Parameter("Width", "continuous", 100.0, 0.0, 100.0, "%", "fx", False,
                  "Stereo width."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Mix."),
        Parameter("Output Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#chorus-ensemble",
)


# --------------------------------------------------------------------------- #
# Phaser-Flanger                                                              #
# --------------------------------------------------------------------------- #
PHASER_FLANGER = DeviceSchema(
    class_name="PhaserFlanger",
    display_name="Phaser-Flanger",
    device_type="audio_effect",
    description="Combined phaser + flanger + doubler (Live 10.1+).",
    categories=["fx", "modulation"],
    parameters=[
        Parameter("Mode", "enum", 0, 0, 2, None, "fx", True,
                  "Phaser / Flanger / Doubler."),
        Parameter("Center", "continuous", 1000.0, 20.0, 18000.0, "Hz", "fx", True,
                  "Center frequency."),
        Parameter("Amount", "continuous", 0.5, 0.0, 1.0, None, "modulation", True,
                  "Modulation depth."),
        Parameter("Rate", "continuous", 1.0, 0.0, 20.0, "Hz", "modulation", True,
                  "LFO rate."),
        Parameter("Feedback", "continuous", 0.5, -1.0, 1.0, None, "fx", True,
                  "Feedback."),
        Parameter("Notches", "quantized", 4, 2, 12, None, "fx", True,
                  "Phaser notches (Phaser mode)."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#phaser-flanger",
)


# --------------------------------------------------------------------------- #
# Auto Pan                                                                    #
# --------------------------------------------------------------------------- #
AUTO_PAN = DeviceSchema(
    class_name="AutoPan",
    display_name="Auto Pan",
    device_type="audio_effect",
    description="LFO-driven pan / amplitude modulation (also makes good tremolos).",
    categories=["fx", "modulation"],
    parameters=[
        Parameter("Frequency", "continuous", 1.0, 0.01, 30.0, "Hz", "modulation", True,
                  "LFO rate."),
        Parameter("Amount", "continuous", 1.0, 0.0, 1.0, None, "modulation", True,
                  "Pan depth."),
        Parameter("Phase", "continuous", 180.0, 0.0, 360.0, "deg", "modulation", True,
                  "L/R phase offset (180 = pan, 0 = tremolo)."),
        Parameter("Shape", "continuous", 0.0, -1.0, 1.0, None, "modulation", False,
                  "LFO shape morph."),
        Parameter("Waveform", "enum", 0, 0, 4, None, "modulation", False,
                  "LFO wave: Sine / Triangle / Saw down / Saw up / Random / S&H."),
        Parameter("Sync", "enum", 0, 0, 1, None, "modulation", False,
                  "Tempo-sync rate."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#auto-pan",
)


# --------------------------------------------------------------------------- #
# Tremolo (Suite)                                                             #
# --------------------------------------------------------------------------- #
TREMOLO = DeviceSchema(
    class_name="Tremolo",
    display_name="Tremolo",
    device_type="audio_effect",
    description="Vintage-flavoured tremolo with sync/free, optional spread (Suite).",
    categories=["fx", "modulation"],
    parameters=[
        Parameter("Rate", "continuous", 6.0, 0.01, 30.0, "Hz", "modulation", True,
                  "Tremolo rate."),
        Parameter("Amount", "continuous", 0.5, 0.0, 1.0, None, "modulation", True,
                  "Tremolo depth."),
        Parameter("Shape", "continuous", 0.0, -1.0, 1.0, None, "modulation", True,
                  "LFO shape."),
        Parameter("Spread", "continuous", 0.0, 0.0, 1.0, None, "modulation", False,
                  "L/R phase spread."),
        Parameter("Output Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#tremolo",
)


# --------------------------------------------------------------------------- #
# Vocoder                                                                     #
# --------------------------------------------------------------------------- #
VOCODER = DeviceSchema(
    class_name="Vocoder",
    display_name="Vocoder",
    device_type="audio_effect",
    description="Carrier/modulator vocoder with built-in modulator pitch tracker.",
    categories=["fx", "vocoder"],
    parameters=[
        Parameter("Carrier", "enum", 0, 0, 3, None, "vocoder", True,
                  "Carrier source: Modulator / Noise / Pulse / External."),
        Parameter("Bands", "quantized", 12, 4, 40, None, "vocoder", True,
                  "Number of bands."),
        Parameter("Range Low", "continuous", 80.0, 20.0, 8000.0, "Hz", "vocoder", True,
                  "Lower band edge."),
        Parameter("Range High", "continuous", 8000.0, 200.0, 22000.0, "Hz", "vocoder", True,
                  "Upper band edge."),
        Parameter("Attack", "continuous", 1.0, 0.1, 1000.0, "ms", "vocoder", True,
                  "Envelope follower attack."),
        Parameter("Release", "continuous", 50.0, 1.0, 1000.0, "ms", "vocoder", True,
                  "Envelope follower release."),
        Parameter("Formant Shift", "continuous", 0.0, -12.0, 12.0, "semitones", "vocoder", True,
                  "Formant pitch shift."),
        Parameter("Voiced/Unvoiced", "continuous", 0.0, 0.0, 1.0, None, "vocoder", False,
                  "Sibilance handling."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#vocoder",
)


# --------------------------------------------------------------------------- #
# Frequency Shifter                                                           #
# --------------------------------------------------------------------------- #
FREQUENCY_SHIFTER = DeviceSchema(
    class_name="FrequencyShifter",
    display_name="Frequency Shifter",
    device_type="audio_effect",
    description="Linear frequency shifter / ring modulator.",
    categories=["fx", "modulation"],
    parameters=[
        Parameter("Mode", "enum", 0, 0, 1, None, "fx", True,
                  "Frequency Shifter / Ring Modulator."),
        Parameter("Coarse", "continuous", 0.0, -5000.0, 5000.0, "Hz", "fx", True,
                  "Coarse shift."),
        Parameter("Fine", "continuous", 0.0, -100.0, 100.0, "Hz", "fx", True,
                  "Fine shift."),
        Parameter("Drive", "continuous", 0.0, -36.0, 36.0, "dB", "fx", False,
                  "Drive."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", True,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#frequency-shifter",
)


# --------------------------------------------------------------------------- #
# Pedal                                                                       #
# --------------------------------------------------------------------------- #
PEDAL = DeviceSchema(
    class_name="Pedal",
    display_name="Pedal",
    device_type="audio_effect",
    description="Three-mode guitar-pedal distortion (Overdrive / Distortion / Fuzz).",
    categories=["fx", "distortion"],
    parameters=[
        Parameter("Type", "enum", 0, 0, 2, None, "fx", True,
                  "Overdrive / Distortion / Fuzz."),
        Parameter("Gain", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Pedal gain."),
        Parameter("Bass", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Bass tone."),
        Parameter("Mid", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Mid tone."),
        Parameter("Mid Frequency", "continuous", 1000.0, 200.0, 5000.0, "Hz", "filter", False,
                  "Mid centre frequency."),
        Parameter("Treble", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Treble tone."),
        Parameter("Sub", "enum", 0, 0, 1, None, "fx", False,
                  "Sub-octave on/off."),
        Parameter("Output Gain", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output trim."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#pedal",
)


# --------------------------------------------------------------------------- #
# Amp                                                                         #
# --------------------------------------------------------------------------- #
AMP = DeviceSchema(
    class_name="Amp",
    display_name="Amp",
    device_type="audio_effect",
    description="Seven-model guitar amp emulator (Suite).",
    categories=["fx", "distortion"],
    parameters=[
        Parameter("Amp Type", "enum", 0, 0, 6, None, "fx", True,
                  "Amp model: Clean / Boost / Blues / Rock / Lead / Heavy / Bass."),
        Parameter("Gain", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Preamp gain."),
        Parameter("Bass", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Bass."),
        Parameter("Middle", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Middle."),
        Parameter("Treble", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Treble."),
        Parameter("Presence", "continuous", 0.5, 0.0, 1.0, None, "filter", True,
                  "Presence."),
        Parameter("Volume", "continuous", 0.5, 0.0, 1.0, None, "mix", False,
                  "Master volume."),
        Parameter("Dual Mono", "enum", 0, 0, 1, None, "global", False,
                  "Stereo dual-mono mode."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#amp",
)


# --------------------------------------------------------------------------- #
# Cabinet                                                                     #
# --------------------------------------------------------------------------- #
CABINET = DeviceSchema(
    class_name="Cabinet",
    display_name="Cabinet",
    device_type="audio_effect",
    description="Speaker cabinet IR + dual mic emulation, designed to follow Amp.",
    categories=["fx", "ir"],
    parameters=[
        Parameter("Cabinet Type", "enum", 0, 0, 4, None, "fx", True,
                  "Cab: 1x12, 2x12, 4x10, 4x12, etc."),
        Parameter("Microphone Type", "enum", 0, 0, 1, None, "fx", True,
                  "Dynamic / Condenser."),
        Parameter("Microphone Position", "enum", 0, 0, 1, None, "fx", True,
                  "Near / Far."),
        Parameter("Dual Mono", "enum", 0, 0, 1, None, "global", False,
                  "Stereo dual mode."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#cabinet",
)


# --------------------------------------------------------------------------- #
# Vinyl Distortion                                                            #
# --------------------------------------------------------------------------- #
VINYL_DISTORTION = DeviceSchema(
    class_name="VinylDistortion",
    display_name="Vinyl Distortion",
    device_type="audio_effect",
    description="Models tracing distortion + crackle from vinyl playback.",
    categories=["fx", "distortion"],
    parameters=[
        Parameter("Tracing Drive", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Tracing distortion drive."),
        Parameter("Tracing Frequency", "continuous", 5000.0, 50.0, 18000.0, "Hz", "fx", True,
                  "Tracing distortion frequency."),
        Parameter("Tracing Width", "continuous", 1.0, 0.5, 9.0, None, "fx", False,
                  "Tracing width."),
        Parameter("Pinch Drive", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Pinch distortion drive."),
        Parameter("Pinch Frequency", "continuous", 5000.0, 50.0, 18000.0, "Hz", "fx", True,
                  "Pinch frequency."),
        Parameter("Crackle Volume", "continuous", -36.0, -70.0, 0.0, "dB", "fx", True,
                  "Crackle level."),
        Parameter("Crackle Density", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Crackle density."),
        Parameter("Stereo/Mono", "enum", 1, 0, 1, None, "global", False,
                  "Stereo or mono crackle."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#vinyl-distortion",
)


# --------------------------------------------------------------------------- #
# Erosion                                                                     #
# --------------------------------------------------------------------------- #
EROSION = DeviceSchema(
    class_name="Erosion",
    display_name="Erosion",
    device_type="audio_effect",
    description="Adds bandpassed noise / sine / wide-noise modulation for grit.",
    categories=["fx", "distortion"],
    parameters=[
        Parameter("Mode", "enum", 0, 0, 2, None, "fx", True,
                  "Noise / Wide Noise / Sine."),
        Parameter("Frequency", "continuous", 5000.0, 20.0, 22000.0, "Hz", "fx", True,
                  "Modulator centre."),
        Parameter("Width", "continuous", 4.0, 0.5, 9.0, None, "fx", False,
                  "Modulator width."),
        Parameter("Amount", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Modulator amount."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#erosion",
)


# --------------------------------------------------------------------------- #
# Beat Repeat                                                                 #
# --------------------------------------------------------------------------- #
BEAT_REPEAT = DeviceSchema(
    class_name="BeatRepeat",
    display_name="Beat Repeat",
    device_type="audio_effect",
    description="Stutter / beat-repeat with grid, gate, pitch and filter.",
    categories=["fx", "rhythmic"],
    parameters=[
        Parameter("Interval", "enum", 4, 0, 12, None, "fx", True,
                  "Repeat interval (1/16, 1/8, ..., 4 bars)."),
        Parameter("Grid", "enum", 4, 0, 12, None, "fx", True,
                  "Grid resolution."),
        Parameter("Variation", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Grid variation."),
        Parameter("Chance", "continuous", 1.0, 0.0, 1.0, None, "fx", True,
                  "Probability of repeating."),
        Parameter("Gate", "continuous", 0.5, 0.0, 1.0, None, "fx", True,
                  "Repeat gate length."),
        Parameter("Pitch", "continuous", 0.0, -24.0, 0.0, "semitones", "pitch", True,
                  "Repeat pitch shift."),
        Parameter("Pitch Decay", "continuous", 0.0, 0.0, 1.0, None, "pitch", False,
                  "Pitch decay."),
        Parameter("Volume", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Output."),
        Parameter("Filter Frequency", "continuous", 5000.0, 20.0, 22000.0, "Hz", "filter", True,
                  "Repeat bandpass center."),
        Parameter("Filter Width", "continuous", 4.0, 0.5, 9.0, None, "filter", False,
                  "Repeat bandpass width."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#beat-repeat",
)


# --------------------------------------------------------------------------- #
# Grain Delay                                                                 #
# --------------------------------------------------------------------------- #
GRAIN_DELAY = DeviceSchema(
    class_name="GrainDelay",
    display_name="Grain Delay",
    device_type="audio_effect",
    description="Granular delay — pitch-shifts grains independently of the delay time.",
    categories=["fx", "delay", "granular"],
    parameters=[
        Parameter("Time 16th", "quantized", 4, 1, 32, None, "fx", True,
                  "Delay time in 16ths."),
        Parameter("Frequency", "continuous", 50.0, 1.0, 150.0, "Hz", "fx", True,
                  "Grain frequency."),
        Parameter("Pitch", "continuous", 0.0, -12.0, 12.0, "semitones", "pitch", True,
                  "Grain pitch shift."),
        Parameter("Random Pitch", "continuous", 0.0, 0.0, 12.0, "semitones", "pitch", True,
                  "Pitch randomization."),
        Parameter("Spray", "continuous", 0.0, 0.0, 500.0, "ms", "fx", True,
                  "Time randomization."),
        Parameter("Feedback", "continuous", 0.3, 0.0, 1.0, None, "fx", True,
                  "Feedback."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#grain-delay",
)


# --------------------------------------------------------------------------- #
# Roar (Live 12 backport / advanced distortion in 11.3+)                      #
# --------------------------------------------------------------------------- #
ROAR = DeviceSchema(
    class_name="Roar",
    display_name="Roar",
    device_type="audio_effect",
    description="Multistage saturator with feedback, modulation and parallel routings.",
    categories=["fx", "distortion"],
    parameters=[
        Parameter("Routing", "enum", 0, 0, 4, None, "fx", True,
                  "Routing topology: Single / Serial / Parallel / MidSide / Multiband."),
        Parameter("Stage 1 Type", "enum", 0, 0, 12, None, "fx", True,
                  "Stage 1 saturation curve."),
        Parameter("Stage 1 Drive", "continuous", 0.0, -36.0, 36.0, "dB", "fx", True,
                  "Stage 1 input drive."),
        Parameter("Stage 1 Tone", "continuous", 0.0, -1.0, 1.0, None, "fx", True,
                  "Stage 1 pre-filter tone tilt."),
        Parameter("Stage 1 Output", "continuous", 0.0, -36.0, 36.0, "dB", "fx", False,
                  "Stage 1 output trim."),
        Parameter("Feedback Amount", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Feedback amount."),
        Parameter("Feedback Tone", "continuous", 0.5, 0.0, 1.0, None, "fx", False,
                  "Feedback path tone."),
        Parameter("Compressor Amount", "continuous", 0.0, 0.0, 1.0, None, "dynamics", True,
                  "Built-in compressor amount."),
        Parameter("Output", "continuous", 0.0, -36.0, 36.0, "dB", "mix", False,
                  "Master output."),
        Parameter("Dry/Wet", "continuous", 1.0, 0.0, 1.0, None, "mix", False,
                  "Mix."),
    ],
    notes="Schema partial; Roar has 3 stages and a mod matrix — only Stage 1 enumerated.",
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#roar",
)


# --------------------------------------------------------------------------- #
# Spectral Resonator                                                          #
# --------------------------------------------------------------------------- #
SPECTRAL_RESONATOR = DeviceSchema(
    class_name="SpectralResonator",
    display_name="Spectral Resonator",
    device_type="audio_effect",
    description="FFT-based resonator / pitch processor (Live 11+).",
    categories=["fx", "spectral"],
    parameters=[
        Parameter("Frequency", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Resonance fundamental offset."),
        Parameter("Decay", "continuous", 0.5, 0.0, 1.0, "sec", "fx", True,
                  "Decay time."),
        Parameter("Mode", "enum", 0, 0, 4, None, "fx", True,
                  "Resonance mode (sequential / chord / etc.)."),
        Parameter("Stretch", "continuous", 0.0, -1.0, 1.0, None, "fx", True,
                  "Spectrum stretching factor."),
        Parameter("Shift", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Spectrum shift."),
        Parameter("MIDI", "enum", 0, 0, 1, None, "fx", False,
                  "MIDI sidechain pitch tracking."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#spectral-resonator",
)


# --------------------------------------------------------------------------- #
# Spectral Time                                                               #
# --------------------------------------------------------------------------- #
SPECTRAL_TIME = DeviceSchema(
    class_name="SpectralTime",
    display_name="Spectral Time",
    device_type="audio_effect",
    description="FFT-based freezer + time-stretch with pitch shifter.",
    categories=["fx", "spectral", "delay"],
    parameters=[
        Parameter("Freezer On", "enum", 0, 0, 1, None, "fx", True,
                  "Spectrum-freeze enable."),
        Parameter("Freezer Spray", "continuous", 0.0, 0.0, 1.0, None, "fx", True,
                  "Frame spray."),
        Parameter("Freezer Tilt", "continuous", 0.0, -1.0, 1.0, None, "fx", True,
                  "Frequency tilt."),
        Parameter("Delay Time", "continuous", 250.0, 1.0, 5000.0, "ms", "fx", True,
                  "Delay time."),
        Parameter("Delay Feedback", "continuous", 0.3, 0.0, 1.0, None, "fx", True,
                  "Delay feedback."),
        Parameter("Pitch Shift", "continuous", 0.0, -48.0, 48.0, "semitones", "pitch", True,
                  "Pitch shifter amount."),
        Parameter("Dry/Wet", "continuous", 0.5, 0.0, 1.0, None, "mix", True,
                  "Mix."),
    ],
    manual_url="https://www.ableton.com/en/manual/live-audio-effect-reference/#spectral-time",
)


AUDIO_EFFECT_SCHEMAS: list = [
    EQ_EIGHT,
    EQ_THREE,
    COMPRESSOR,
    GLUE_COMPRESSOR,
    MULTIBAND_DYNAMICS,
    LIMITER,
    GATE,
    REVERB,
    HYBRID_REVERB,
    CONVOLUTION_REVERB,
    DELAY,
    ECHO,
    AUTO_FILTER,
    SATURATOR,
    DRUM_BUSS,
    CHORUS_ENSEMBLE,
    PHASER_FLANGER,
    AUTO_PAN,
    TREMOLO,
    VOCODER,
    FREQUENCY_SHIFTER,
    PEDAL,
    AMP,
    CABINET,
    VINYL_DISTORTION,
    EROSION,
    BEAT_REPEAT,
    GRAIN_DELAY,
    ROAR,
    SPECTRAL_RESONATOR,
    SPECTRAL_TIME,
]
