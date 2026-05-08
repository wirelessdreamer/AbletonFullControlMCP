"""Curated descriptor → parameter rules for Ableton Live 11 stock devices.

The :data:`DEVICE_RULES` dict is a hand-curated, dataset-free mapping
from a device's LOM ``class_name`` to a set of musician-facing
descriptors (``"bright"``, ``"warm"``, ``"aggressive"``, ...) and from
each descriptor to a list of :class:`ParamRule` records that say how a
specific parameter should be nudged to push the sound in that direction.

Why curated rather than learned? Because the user's other path
(``shape_apply`` in :mod:`ableton_mcp.tools.sound_shaping`) needs a
probe dataset built by sweeping the device, capturing audio and
extracting features — heavy. These rules are the "lightweight" fallback:
they apply instantly without any audio capture, at the cost of being
generic across presets.

Each :class:`ParamRule` says:

- ``param_name``  — the canonical schema parameter name (must match
  :mod:`ableton_mcp.device_schemas` exactly).
- ``direction``   — ``+1`` (push the param up) or ``-1`` (push it down).
- ``weight``      — 0..1, how strongly this rule contributes to the
  descriptor. Used for ranking when several rules touch the same param,
  and for proportional intensity scaling in the applier.
- ``note``        — one-line caveat for the LLM to surface to the user
  (e.g. "above 0.7 self-oscillates").

Coverage is honest: where I'm unsure how a knob behaves musically, the
rule's ``note`` says so. Rules cover, at minimum, these 13 descriptors
for each major device:

  bright, dark, warm, aggressive, soft, punchy, sustained, plucky,
  distorted, clean, wide, tight, dense

Some descriptors don't apply to every device (Reverb has no "plucky"
analog) — the rule list is simply omitted when nothing meaningful can
be done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class ParamRule:
    """One rule contributing to a descriptor on a device.

    The applier interprets ``direction`` × ``weight`` × ``intensity`` as a
    fraction of the parameter's range to move from its current value
    toward the corresponding endpoint. ``note`` is human-readable
    context (caveat / pitfall / why this knob).
    """

    param_name: str
    direction: int  # +1 or -1
    weight: float = 0.6
    note: str = ""

    def __post_init__(self) -> None:  # pragma: no cover — guard
        if self.direction not in (-1, +1):
            raise ValueError(f"direction must be -1 or +1, got {self.direction!r}")
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"weight must be in [0,1], got {self.weight!r}")


# The minimum set of descriptors every supported device should cover.
REQUIRED_DESCRIPTORS: tuple[str, ...] = (
    "bright",
    "dark",
    "warm",
    "aggressive",
    "soft",
    "punchy",
    "sustained",
    "plucky",
    "distorted",
    "clean",
    "wide",
    "tight",
    "dense",
)


# Common descriptor synonyms / aliases so the LLM can throw casual words.
DESCRIPTOR_ALIASES: dict[str, str] = {
    "brighter": "bright",
    "darker": "dark",
    "warmer": "warm",
    "harder": "aggressive",
    "harsher": "aggressive",
    "biting": "aggressive",
    "bite": "aggressive",
    "softer": "soft",
    "smoother": "soft",
    "mellow": "soft",
    "punch": "punchy",
    "punchier": "punchy",
    "tighter": "tight",
    "wider": "wide",
    "thicker": "dense",
    "denser": "dense",
    "fatter": "dense",
    "longer": "sustained",
    "sustain": "sustained",
    "shorter": "plucky",
    "snappy": "plucky",
    "pluck": "plucky",
    "dirty": "distorted",
    "gritty": "distorted",
    "saturated": "distorted",
    "cleaner": "clean",
    "pristine": "clean",
    "open": "bright",
    "muffled": "dark",
    "boomy": "warm",
    "thin": "tight",
}


def normalize_descriptor(name: str) -> str:
    """Lowercase + alias-resolve a descriptor name. Returns "" if blank."""
    if not name:
        return ""
    n = name.strip().lower()
    return DESCRIPTOR_ALIASES.get(n, n)


# ---------------------------------------------------------------------------
# Drift — compact monosynth (instruments)
# ---------------------------------------------------------------------------
DRIFT_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Filter Frequency", +1, 0.9,
                  "Open the filter cutoff — single biggest brightness move."),
        ParamRule("Filter Resonance", +1, 0.3,
                  "Touch of resonance accents harmonics around cutoff."),
        ParamRule("Osc 1 Shape", +1, 0.2,
                  "Higher shape index (saw/square) has more high content than sine."),
    ],
    "dark": [
        ParamRule("Filter Frequency", -1, 0.9, "Close the filter."),
        ParamRule("Filter Resonance", -1, 0.2, "Less resonance for a smoother roll-off."),
        ParamRule("Noise Level", -1, 0.4, "Less noise = less HF energy."),
    ],
    "warm": [
        ParamRule("Filter Frequency", -1, 0.5,
                  "Warmth is dark-but-not-dull; pull cutoff to mids."),
        ParamRule("Filter Resonance", -1, 0.2, "Soften the cutoff peak."),
        ParamRule("Env 1 Attack", +1, 0.3, "Slow attack reduces transient bite."),
    ],
    "aggressive": [
        ParamRule("Filter Resonance", +1, 0.7,
                  "Resonance is the main 'edge' driver."),
        ParamRule("Filter Frequency", +1, 0.4, "Open the cutoff to expose teeth."),
        ParamRule("Osc Mix", +1, 0.2,
                  "Bias toward Osc 2 — usually a more aggressive shape."),
    ],
    "soft": [
        ParamRule("Filter Frequency", -1, 0.4, "Roll off the highs."),
        ParamRule("Env 1 Attack", +1, 0.5, "Slower attack for less bite."),
        ParamRule("Filter Resonance", -1, 0.4, "No resonant peak."),
    ],
    "punchy": [
        ParamRule("Env 1 Attack", -1, 0.7, "Fastest possible attack."),
        ParamRule("Env 1 Decay", -1, 0.4,
                  "Short decay leaves the transient prominent."),
        ParamRule("Env 1 Sustain", -1, 0.3,
                  "Lower sustain emphasises the transient peak."),
    ],
    "sustained": [
        ParamRule("Env 1 Sustain", +1, 0.8, "Full sustain holds the body."),
        ParamRule("Env 1 Release", +1, 0.5, "Long release trails."),
        ParamRule("Env 1 Decay", +1, 0.3, "Slow decay maintains level."),
    ],
    "plucky": [
        ParamRule("Env 1 Decay", -1, 0.7, "Short decay = pluck."),
        ParamRule("Env 1 Sustain", -1, 0.7, "No sustain pad."),
        ParamRule("Env 1 Attack", -1, 0.4, "Fast attack for the pluck transient."),
    ],
    "distorted": [
        ParamRule("Filter Resonance", +1, 0.4,
                  "Drift has no built-in drive; resonance is the closest knob."),
        ParamRule("Noise Level", +1, 0.3,
                  "Noise approximates grit when no saturator is in chain."),
    ],
    "clean": [
        ParamRule("Filter Resonance", -1, 0.4, "No resonant peak."),
        ParamRule("Noise Level", -1, 0.5, "Pure tone."),
    ],
    "wide": [
        ParamRule("LFO 1 Rate", +1, 0.3,
                  "Faster LFO modulating pitch creates pseudo-stereo movement."),
    ],
    "tight": [
        ParamRule("Env 1 Release", -1, 0.6, "Short release prevents tail blur."),
        ParamRule("Env 1 Decay", -1, 0.3, "Short decay = tight."),
        ParamRule("Noise Level", -1, 0.3, "Less noise = clearer attack."),
    ],
    "dense": [
        ParamRule("Osc Mix", -1, 0.3,
                  "Balance the two oscillators (centre = both audible)."),
        ParamRule("Noise Level", +1, 0.3, "Adds spectral mass."),
    ],
}


# ---------------------------------------------------------------------------
# Operator — 4-op FM (instruments)
# ---------------------------------------------------------------------------
OPERATOR_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Filter Freq", +1, 0.7, "Open the global filter."),
        ParamRule("B Level", +1, 0.5,
                  "Operator B is typically a modulator — more level = more sidebands."),
        ParamRule("Tone", +1, 0.6, "Global tone control biases toward HF."),
    ],
    "dark": [
        ParamRule("Filter Freq", -1, 0.7, "Close the filter."),
        ParamRule("Tone", -1, 0.6, "Tone toward LF."),
        ParamRule("B Level", -1, 0.4, "Less FM modulator depth = fewer sidebands."),
    ],
    "warm": [
        ParamRule("Tone", -1, 0.4, "Tone toward warmth."),
        ParamRule("Filter Freq", -1, 0.4, "Soften the highs."),
        ParamRule("B Level", -1, 0.3,
                  "Less modulation = simpler, warmer tone."),
    ],
    "aggressive": [
        ParamRule("B Level", +1, 0.7,
                  "Aggressive FM voices push modulator levels high; expect harshness above ~0.7."),
        ParamRule("C Level", +1, 0.4, "Stack another modulator."),
        ParamRule("Filter Res", +1, 0.4, "Resonant peak adds bite."),
    ],
    "soft": [
        ParamRule("B Level", -1, 0.6, "Strip back the modulator."),
        ParamRule("A Attack", +1, 0.4, "Slower attack."),
        ParamRule("Filter Freq", -1, 0.3, "Roll off the highs."),
    ],
    "punchy": [
        ParamRule("A Attack", -1, 0.7, "Sharpen the carrier attack."),
        ParamRule("A Decay", -1, 0.4, "Short decay."),
        ParamRule("Pitch Env Amount", +1, 0.3,
                  "Small positive pitch envelope adds attack 'click'."),
    ],
    "sustained": [
        ParamRule("A Sustain", +1, 0.8, "Hold the body."),
        ParamRule("A Release", +1, 0.5, "Long tail."),
    ],
    "plucky": [
        ParamRule("A Decay", -1, 0.7, "Fast decay."),
        ParamRule("A Sustain", -1, 0.7, "No sustain plateau."),
        ParamRule("B Level", +1, 0.3,
                  "A bit more modulator yields the metallic/percussive 'thunk'."),
    ],
    "distorted": [
        ParamRule("Filter Drive", +1, 0.7, "Drive the global filter."),
        ParamRule("B Level", +1, 0.5,
                  "Excess FM is 'distortion' in FM terms; above 0.7 turns harsh."),
    ],
    "clean": [
        ParamRule("B Level", -1, 0.6, "Strip modulators back."),
        ParamRule("Filter Drive", -1, 0.5, "No drive."),
        ParamRule("Filter Res", -1, 0.3, "No resonant peak."),
    ],
    "wide": [
        ParamRule("Detune", +1, 0.5,
                  "Detune broadens the unison stack and stereo image."),
    ],
    "tight": [
        ParamRule("A Release", -1, 0.5, "Short release."),
        ParamRule("Time", -1, 0.3, "Global envelope time scale shorter."),
    ],
    "dense": [
        ParamRule("C Level", +1, 0.4, "More operators audible."),
        ParamRule("D Level", +1, 0.3, "Stack the last operator."),
    ],
}


# ---------------------------------------------------------------------------
# Wavetable (LOM class InstrumentVector)
# ---------------------------------------------------------------------------
WAVETABLE_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Filter 1 Frequency", +1, 0.8, "Open the filter."),
        ParamRule("Filter 1 Resonance", +1, 0.2, "Touch of resonance."),
        ParamRule("Oscillator 1 Wavetable Position", +1, 0.3,
                  "Later positions are typically brighter — depends on the table."),
    ],
    "dark": [
        ParamRule("Filter 1 Frequency", -1, 0.8, "Close the filter."),
        ParamRule("Oscillator 1 Wavetable Position", -1, 0.3,
                  "Earlier positions are usually softer."),
    ],
    "warm": [
        ParamRule("Filter 1 Frequency", -1, 0.5, "Roll off."),
        ParamRule("Sub Oscillator Gain", +1, 0.5,
                  "Sub fills the bottom — warmth comes from low-mid weight."),
    ],
    "aggressive": [
        ParamRule("Filter 1 Drive", +1, 0.7, "Drive the filter."),
        ParamRule("Filter 1 Resonance", +1, 0.5, "Resonant teeth."),
        ParamRule("Oscillator 1 Effect 1", +1, 0.4,
                  "Effect knob 1 typically increases harmonic energy (FM/PD/etc)."),
    ],
    "soft": [
        ParamRule("Filter 1 Frequency", -1, 0.5, "Roll off."),
        ParamRule("Filter 1 Drive", -1, 0.4, "No drive."),
        ParamRule("Envelope 1 Attack Time", +1, 0.5, "Slow attack."),
    ],
    "punchy": [
        ParamRule("Envelope 1 Attack Time", -1, 0.7, "Snap attack."),
        ParamRule("Envelope 1 Decay Time", -1, 0.4, "Short decay."),
        ParamRule("Envelope 1 Sustain Level", -1, 0.3, "Lower sustain."),
    ],
    "sustained": [
        ParamRule("Envelope 1 Sustain Level", +1, 0.8, "Full sustain."),
        ParamRule("Envelope 1 Release Time", +1, 0.5, "Long tail."),
    ],
    "plucky": [
        ParamRule("Envelope 1 Decay Time", -1, 0.7, "Fast decay."),
        ParamRule("Envelope 1 Sustain Level", -1, 0.7, "No sustain."),
    ],
    "distorted": [
        ParamRule("Filter 1 Drive", +1, 0.8, "Drive into the filter."),
        ParamRule("Filter 1 Resonance", +1, 0.3,
                  "Resonance + drive = nasty in a good way."),
    ],
    "clean": [
        ParamRule("Filter 1 Drive", -1, 0.7, "No drive."),
        ParamRule("Filter 1 Resonance", -1, 0.4, "No resonant peak."),
    ],
    "wide": [
        ParamRule("Oscillator 2 On", +1, 0.5,
                  "Engaging Osc 2 enables stereo detune by default."),
        ParamRule("LFO 1 Amount", +1, 0.3, "Movement creates space."),
    ],
    "tight": [
        ParamRule("Envelope 1 Release Time", -1, 0.6, "No tail."),
        ParamRule("Envelope 1 Decay Time", -1, 0.3, "Quick decay."),
    ],
    "dense": [
        ParamRule("Sub Oscillator On", +1, 0.5, "Engage the sub."),
        ParamRule("Oscillator 2 On", +1, 0.4, "Two oscillators audible."),
    ],
}


# ---------------------------------------------------------------------------
# Tension — physical-modelled string (instruments)
# ---------------------------------------------------------------------------
TENSION_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Position", +1, 0.5,
                  "Plucking nearer the bridge brightens the spectrum."),
        ParamRule("String Damping", -1, 0.5,
                  "Less damping leaves more upper partials."),
    ],
    "dark": [
        ParamRule("Position", -1, 0.5, "Plucking nearer the middle softens highs."),
        ParamRule("String Damping", +1, 0.6, "More damping rolls off highs."),
    ],
    "warm": [
        ParamRule("String Damping", +1, 0.4, "Slightly damped."),
        ParamRule("Position", -1, 0.4, "Plucking near the middle for fundamental weight."),
    ],
    "aggressive": [
        ParamRule("Force", +1, 0.7,
                  "More excitator force; bow Force at max produces pure noise."),
        ParamRule("Velocity", +1, 0.5, "Faster excitation = more harmonic content."),
        ParamRule("Position", +1, 0.3, "Closer to the bridge."),
    ],
    "soft": [
        ParamRule("Force", -1, 0.6, "Light excitation."),
        ParamRule("Velocity", -1, 0.4, "Slow attack."),
        ParamRule("Friction", -1, 0.3, "Low friction (bow only)."),
    ],
    "punchy": [
        ParamRule("Velocity", +1, 0.5,
                  "Fast strike for sharp attack — only meaningful with hammer/plectrum excitator."),
        ParamRule("Force", +1, 0.4, "Firm excitation."),
    ],
    "sustained": [
        ParamRule("String Decay", +1, 0.8,
                  "Long string decay — bow excitator keeps it indefinite."),
        ParamRule("String Damping", -1, 0.5, "No damping = ring out."),
    ],
    "plucky": [
        ParamRule("String Decay", -1, 0.6, "Short decay."),
        ParamRule("String Damping", +1, 0.5, "Damp the string."),
        ParamRule("Excitator Type", -1, 0.3,
                  "Plectrum excitator (lower index) is more pluck-shaped than bow."),
    ],
    "distorted": [
        ParamRule("Force", +1, 0.6,
                  "Excessive force enters the noisy regime; this isn't 'clean' distortion."),
    ],
    "clean": [
        ParamRule("Force", -1, 0.5, "Moderate force keeps the model in linear region."),
        ParamRule("Friction", -1, 0.3, "Low friction = clean tone."),
    ],
    "wide": [
        # Tension's body Type is mono — width comes from per-voice variation.
    ],
    "tight": [
        ParamRule("String Decay", -1, 0.5, "Short decay."),
        ParamRule("String Damping", +1, 0.4, "Damp the tail."),
    ],
    "dense": [
        ParamRule("String Damping", -1, 0.3,
                  "Less damping means more sustained partials = denser tail."),
    ],
}


# ---------------------------------------------------------------------------
# Analog — virtual analog (instruments)
# ---------------------------------------------------------------------------
ANALOG_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Filter1 Freq", +1, 0.9, "Open the filter."),
        ParamRule("Filter1 Res", +1, 0.3, "A bit of resonance."),
        ParamRule("Filter1 Env", +1, 0.3, "Filter envelope opens with notes."),
    ],
    "dark": [
        ParamRule("Filter1 Freq", -1, 0.9, "Close the filter."),
        ParamRule("Filter1 Res", -1, 0.2, "No resonant peak."),
    ],
    "warm": [
        ParamRule("Filter1 Freq", -1, 0.5, "Roll off the highs."),
        ParamRule("OSC1 Shape", -1, 0.3,
                  "Sine/saw lower-index waves are warmer than rectangle/noise."),
    ],
    "aggressive": [
        ParamRule("Filter1 Res", +1, 0.6, "Resonance for bite."),
        ParamRule("Filter1 Env", +1, 0.5, "Open envelope sweep."),
        ParamRule("OSC2 Detune", +1, 0.4, "Wide detune sounds aggressive."),
    ],
    "soft": [
        ParamRule("Filter1 Freq", -1, 0.4, "Lower cutoff."),
        ParamRule("Amp1 Attack", +1, 0.5, "Slow attack."),
        ParamRule("Filter1 Res", -1, 0.3, "No resonant peak."),
    ],
    "punchy": [
        ParamRule("Amp1 Attack", -1, 0.7, "Fast attack."),
        ParamRule("Amp1 Decay", -1, 0.4, "Quick decay."),
        ParamRule("Amp1 Sustain", -1, 0.3, "Lower sustain."),
    ],
    "sustained": [
        ParamRule("Amp1 Sustain", +1, 0.8, "Full sustain."),
        ParamRule("Amp1 Release", +1, 0.5, "Long release."),
    ],
    "plucky": [
        ParamRule("Amp1 Decay", -1, 0.7, "Short decay."),
        ParamRule("Amp1 Sustain", -1, 0.7, "No sustain."),
    ],
    "distorted": [
        ParamRule("Filter1 Res", +1, 0.5,
                  "No internal drive; resonance is the closest analog of grit."),
    ],
    "clean": [
        ParamRule("Filter1 Res", -1, 0.4, "Tame resonance."),
    ],
    "wide": [
        ParamRule("OSC2 Detune", +1, 0.6, "Detune the second oscillator."),
    ],
    "tight": [
        ParamRule("Amp1 Release", -1, 0.6, "Short release."),
        ParamRule("Amp1 Decay", -1, 0.3, "Tighter envelope."),
    ],
    "dense": [
        ParamRule("OSC1 PW", +1, 0.2,
                  "Pulse width affects spectral fill on the rectangle waveform."),
    ],
}


# ---------------------------------------------------------------------------
# Compressor (Compressor2)
# ---------------------------------------------------------------------------
COMPRESSOR_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        # Compression doesn't directly brighten; pulling threshold down can
        # raise the perceived high content via make-up gain, but it's a
        # stretch to claim that as "brighten" — leave empty.
    ],
    "dark": [
        # Same — compression isn't an EQ.
    ],
    "warm": [
        ParamRule("Attack", +1, 0.5,
                  "Slower attack lets transients through; warmer feel on full mixes."),
        ParamRule("Ratio", -1, 0.3,
                  "Lower ratio = gentler compression = more natural / warm."),
    ],
    "aggressive": [
        ParamRule("Ratio", +1, 0.7, "Heavy compression."),
        ParamRule("Threshold", -1, 0.6, "Push more signal into compression."),
        ParamRule("Attack", -1, 0.4,
                  "Fast attack flattens transients — sounds slammed."),
    ],
    "soft": [
        ParamRule("Ratio", -1, 0.6, "Light compression."),
        ParamRule("Threshold", +1, 0.4, "Less signal compressed."),
    ],
    "punchy": [
        ParamRule("Attack", +1, 0.6,
                  "Slow attack (10–30 ms) lets the transient through, body gets compressed."),
        ParamRule("Release", -1, 0.5,
                  "Fast release recovers between transients = more punch."),
        ParamRule("Ratio", +1, 0.4, "Decent ratio so the body actually pumps down."),
    ],
    "sustained": [
        ParamRule("Release", +1, 0.5, "Long release smooths the tail."),
        ParamRule("Ratio", +1, 0.4, "Heavier compression."),
    ],
    "plucky": [
        ParamRule("Attack", -1, 0.6, "Fast attack clamps the transient."),
        ParamRule("Release", -1, 0.4, "Fast release."),
    ],
    "distorted": [
        ParamRule("Ratio", +1, 0.4,
                  "Comp by itself doesn't distort; this is a 'thicker' move."),
    ],
    "clean": [
        ParamRule("Ratio", -1, 0.6, "Light compression."),
        ParamRule("Threshold", +1, 0.4, "Less reduction."),
    ],
    "wide": [
        # Compression is mono-summed inside this device; width is on Utility.
    ],
    "tight": [
        ParamRule("Release", -1, 0.6, "Short release."),
        ParamRule("Attack", -1, 0.4, "Fast attack."),
        ParamRule("Ratio", +1, 0.4, "Firm grip."),
    ],
    "dense": [
        ParamRule("Threshold", -1, 0.5, "Push more signal through compression."),
        ParamRule("Ratio", +1, 0.4, "Heavier compression = denser apparent loudness."),
        ParamRule("Output Gain", +1, 0.3, "Make-up gain fills the meter."),
    ],
}


# ---------------------------------------------------------------------------
# Reverb
# ---------------------------------------------------------------------------
REVERB_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("HiShelf Gain", +1, 0.6, "Lift the tail's high shelf."),
        ParamRule("HiShelf Freq", +1, 0.3, "Push the shelf corner higher."),
        ParamRule("In Filter Freq", +1, 0.4, "Open the input bandpass."),
    ],
    "dark": [
        ParamRule("HiShelf Gain", -1, 0.7, "Roll off the tail."),
        ParamRule("HiShelf Freq", -1, 0.4, "Lower shelf corner."),
        ParamRule("In Filter Freq", -1, 0.3, "Tighter input filter."),
    ],
    "warm": [
        ParamRule("HiShelf Gain", -1, 0.5, "Soften the highs in the tail."),
        ParamRule("Decay Time", +1, 0.3, "Slightly longer tail."),
    ],
    "aggressive": [
        ParamRule("Reflect Level", +1, 0.5,
                  "Loud early reflections make the reverb assertive."),
        ParamRule("Dry/Wet", +1, 0.4, "More wet."),
    ],
    "soft": [
        ParamRule("Reflect Level", -1, 0.4, "Quieter reflections."),
        ParamRule("HiShelf Gain", -1, 0.3, "Soften the tail."),
        ParamRule("Predelay", +1, 0.3,
                  "Predelay separates the dry from the wet, gentler perception."),
    ],
    "punchy": [
        ParamRule("Predelay", +1, 0.5, "Predelay protects the transient."),
        ParamRule("Decay Time", -1, 0.3, "Short tail."),
    ],
    "sustained": [
        ParamRule("Decay Time", +1, 0.8, "Long tail."),
        ParamRule("Diffuse Level", +1, 0.4, "Diffuse network louder."),
    ],
    "plucky": [
        ParamRule("Decay Time", -1, 0.6, "Short tail."),
        ParamRule("Dry/Wet", -1, 0.3, "Less wet."),
    ],
    "distorted": [
        # Reverb has no drive; skip rather than fake it.
    ],
    "clean": [
        ParamRule("Diffuse Level", -1, 0.3, "Less diffuse tail."),
    ],
    "wide": [
        ParamRule("Stereo Image", +1, 0.7, "Open the stereo width of the tail."),
        ParamRule("Room Size", +1, 0.3, "Bigger rooms feel wider."),
    ],
    "tight": [
        ParamRule("Decay Time", -1, 0.6, "Short tail."),
        ParamRule("Room Size", -1, 0.4, "Smaller room."),
        ParamRule("Predelay", -1, 0.3, "Tight predelay."),
    ],
    "dense": [
        ParamRule("Diffuse Level", +1, 0.6, "More diffuse network."),
        ParamRule("Decay Time", +1, 0.4, "Longer tail = denser."),
    ],
}


# ---------------------------------------------------------------------------
# Auto Filter
# ---------------------------------------------------------------------------
AUTO_FILTER_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Frequency", +1, 0.9, "Open the cutoff."),
        ParamRule("Resonance", +1, 0.2, "Touch of Q."),
    ],
    "dark": [
        ParamRule("Frequency", -1, 0.9, "Close the cutoff."),
    ],
    "warm": [
        ParamRule("Frequency", -1, 0.5, "Roll off the highs."),
        ParamRule("Drive", +1, 0.3, "A little drive can warm the filter."),
    ],
    "aggressive": [
        ParamRule("Resonance", +1, 0.7, "Resonant peak."),
        ParamRule("Drive", +1, 0.6, "Drive the filter into clipping."),
    ],
    "soft": [
        ParamRule("Resonance", -1, 0.4, "No peak."),
        ParamRule("Drive", -1, 0.4, "No drive."),
        ParamRule("Frequency", -1, 0.4, "Lower cutoff."),
    ],
    "punchy": [
        ParamRule("Envelope Modulation", +1, 0.5,
                  "Envelope follower opens cutoff with the transient — adds punch."),
        ParamRule("Envelope Attack", -1, 0.4, "Fast follower attack."),
    ],
    "sustained": [
        ParamRule("LFO Amount", +1, 0.4, "LFO movement keeps the filter alive."),
    ],
    "plucky": [
        ParamRule("Envelope Modulation", +1, 0.6,
                  "Big envelope sweep + fast release = pluck shape."),
        ParamRule("Envelope Release", -1, 0.5, "Quick close."),
    ],
    "distorted": [
        ParamRule("Drive", +1, 0.8, "Drive — Auto Filter has a dedicated drive stage."),
        ParamRule("Resonance", +1, 0.4, "Adds nastiness."),
    ],
    "clean": [
        ParamRule("Drive", -1, 0.6, "No drive."),
        ParamRule("Resonance", -1, 0.4, "Light Q."),
    ],
    "wide": [
        ParamRule("LFO Amount", +1, 0.5,
                  "Stereo LFO offsets the filter per channel — pseudo-width."),
    ],
    "tight": [
        ParamRule("Envelope Release", -1, 0.5, "Snappy follower release."),
        ParamRule("Resonance", -1, 0.3, "Less ringing."),
    ],
    "dense": [
        ParamRule("LFO Amount", +1, 0.3, "Movement adds spectral density over time."),
    ],
}


# ---------------------------------------------------------------------------
# Saturator
# ---------------------------------------------------------------------------
SATURATOR_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Drive", +1, 0.5,
                  "Saturation generates upper harmonics — perceptually brighter."),
    ],
    "dark": [
        ParamRule("Drive", -1, 0.5, "Less drive = less HF content."),
    ],
    "warm": [
        ParamRule("Drive", +1, 0.4, "Light drive on the soft curves is classic warmth."),
        ParamRule("Type", -1, 0.3,
                  "Lower-index curves (Analog Clip / Soft Sine) are warmer than digital clip."),
    ],
    "aggressive": [
        ParamRule("Drive", +1, 0.8, "Push the drive."),
        ParamRule("Type", +1, 0.5,
                  "Higher-index curves (Hard Curve / Sinoid Fold / Digital Clip) are nastier."),
    ],
    "soft": [
        ParamRule("Drive", -1, 0.6, "Pull back drive."),
    ],
    "punchy": [
        # Saturator doesn't shape transients per se, but soft clipping the
        # transient peaks can feel punchier.
        ParamRule("Drive", +1, 0.3, "Light drive cleans up transient peaks."),
    ],
    "sustained": [
        # Skip — saturator doesn't change sustain.
    ],
    "plucky": [
        # Skip — saturator doesn't change envelope shape.
    ],
    "distorted": [
        ParamRule("Drive", +1, 0.9, "The whole point of this device."),
        ParamRule("Type", +1, 0.5, "Harder curves."),
    ],
    "clean": [
        ParamRule("Drive", -1, 0.9, "No drive."),
        ParamRule("Type", -1, 0.3, "Soft curves."),
    ],
    "wide": [
        # Saturator is generally mono-correlated; skip.
    ],
    "tight": [
        ParamRule("Drive", -1, 0.3, "Less low-mid mush."),
    ],
    "dense": [
        ParamRule("Drive", +1, 0.5,
                  "Drive thickens the spectrum (more harmonics = denser)."),
    ],
}


# ---------------------------------------------------------------------------
# Amp (Suite guitar amp)
# ---------------------------------------------------------------------------
AMP_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Treble", +1, 0.7, "Lift treble."),
        ParamRule("Presence", +1, 0.6, "Open the presence band."),
    ],
    "dark": [
        ParamRule("Treble", -1, 0.7, "Cut treble."),
        ParamRule("Presence", -1, 0.5, "Pull presence down."),
    ],
    "warm": [
        ParamRule("Bass", +1, 0.5, "Lift bass."),
        ParamRule("Treble", -1, 0.4, "Roll off treble."),
        ParamRule("Amp Type", -1, 0.3,
                  "Clean / Boost / Blues amps are warmer than Heavy / Lead."),
    ],
    "aggressive": [
        ParamRule("Gain", +1, 0.8, "Push the preamp."),
        ParamRule("Amp Type", +1, 0.5,
                  "Higher-index amps (Lead / Heavy) are aggressive by design."),
        ParamRule("Presence", +1, 0.4, "More presence cuts through."),
    ],
    "soft": [
        ParamRule("Gain", -1, 0.7, "Lower preamp gain."),
        ParamRule("Amp Type", -1, 0.4, "Clean / Blues amps."),
    ],
    "punchy": [
        ParamRule("Middle", +1, 0.4, "Mids are where punch lives."),
        ParamRule("Bass", +1, 0.3, "A little body."),
    ],
    "sustained": [
        ParamRule("Gain", +1, 0.5,
                  "Higher gain = compressed amp = longer sustain (especially Lead amps)."),
    ],
    "plucky": [
        ParamRule("Gain", -1, 0.5, "Cleaner amps preserve attack."),
    ],
    "distorted": [
        ParamRule("Gain", +1, 0.9, "Pour on the gain."),
        ParamRule("Amp Type", +1, 0.5, "Pick a high-gain model."),
    ],
    "clean": [
        ParamRule("Gain", -1, 0.8, "Pull gain back."),
        ParamRule("Amp Type", -1, 0.6, "Clean amp model."),
    ],
    "wide": [
        ParamRule("Dual Mono", +1, 0.7,
                  "Dual Mono runs separate L/R amps — instant width."),
    ],
    "tight": [
        ParamRule("Bass", -1, 0.4, "Less low end = tighter."),
        ParamRule("Middle", +1, 0.3, "Mids define the note."),
    ],
    "dense": [
        ParamRule("Gain", +1, 0.4, "More gain = more harmonic content."),
        ParamRule("Middle", +1, 0.3, "Mid forward."),
    ],
}


# ---------------------------------------------------------------------------
# Cabinet
# ---------------------------------------------------------------------------
CABINET_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Microphone Type", +1, 0.5,
                  "Condenser mic is brighter than dynamic in this device."),
        ParamRule("Microphone Position", +1, 0.3,
                  "Far position picks up more room HF — usually 'open' rather than literally bright."),
    ],
    "dark": [
        ParamRule("Microphone Type", -1, 0.5, "Dynamic mic is warmer."),
        ParamRule("Cabinet Type", -1, 0.3,
                  "Smaller cabs (1x12 / 2x12) tend less bright on the high end."),
    ],
    "warm": [
        ParamRule("Microphone Type", -1, 0.5, "Dynamic mic."),
        ParamRule("Microphone Position", -1, 0.3, "Near, on-axis."),
    ],
    "aggressive": [
        ParamRule("Microphone Type", +1, 0.4,
                  "Condenser mic is brighter and more in-your-face — not 'aggressive' in a drive sense."),
        ParamRule("Cabinet Type", +1, 0.3,
                  "Larger cabs (4x10 / 4x12) are more assertive."),
    ],
    "soft": [
        ParamRule("Microphone Position", +1, 0.3, "Far position softens."),
        ParamRule("Microphone Type", -1, 0.3, "Dynamic mic softens the highs."),
    ],
    "punchy": [
        ParamRule("Microphone Position", -1, 0.5,
                  "Near mic is more direct/punchy."),
    ],
    "sustained": [
        # Skip — cabinet has no envelope.
    ],
    "plucky": [
        # Skip — cabinet has no envelope.
    ],
    "distorted": [
        # Cabinet is post-distortion — skip rather than fake it.
    ],
    "clean": [
        ParamRule("Microphone Type", -1, 0.3,
                  "Dynamic mic on a smaller cab keeps things clean."),
    ],
    "wide": [
        ParamRule("Dual Mono", +1, 0.7,
                  "Dual mono creates stereo cabinet image."),
    ],
    "tight": [
        ParamRule("Microphone Position", -1, 0.4, "Near mic, less room."),
    ],
    "dense": [
        ParamRule("Cabinet Type", +1, 0.3,
                  "Larger cabs (4x10 / 4x12) push more low-mid."),
    ],
}


# ---------------------------------------------------------------------------
# Echo (delay) — bonus device, included for completeness
# ---------------------------------------------------------------------------
ECHO_RULES: dict[str, list[ParamRule]] = {
    "bright": [
        ParamRule("Filter Freq Hi", +1, 0.6, "Open the high cut on echoes."),
    ],
    "dark": [
        ParamRule("Filter Freq Hi", -1, 0.7, "Close the high cut for darker echoes."),
    ],
    "warm": [
        ParamRule("Filter Freq Hi", -1, 0.5, "Roll off."),
        ParamRule("Character Wobble", +1, 0.3, "Tape wobble = warm character."),
    ],
    "aggressive": [
        ParamRule("Feedback", +1, 0.6,
                  "Heavy feedback (>0.8) self-oscillates — careful."),
        ParamRule("Dry/Wet", +1, 0.4, "Wetter."),
    ],
    "soft": [
        ParamRule("Dry/Wet", -1, 0.4, "Drier."),
        ParamRule("Filter Freq Hi", -1, 0.4, "Roll off."),
    ],
    "punchy": [
        ParamRule("Feedback", -1, 0.4, "Single-repeat for punch."),
    ],
    "sustained": [
        ParamRule("Feedback", +1, 0.6, "More repeats."),
        ParamRule("Reverb Amount", +1, 0.4, "Built-in reverb tail."),
    ],
    "plucky": [
        ParamRule("Feedback", -1, 0.6, "Single repeat."),
        ParamRule("Dry/Wet", -1, 0.3, "Drier."),
    ],
    "distorted": [
        ParamRule("Character Noise", +1, 0.4, "Tape noise."),
    ],
    "clean": [
        ParamRule("Character Noise", -1, 0.5, "No tape noise."),
        ParamRule("Character Wobble", -1, 0.4, "No wobble."),
    ],
    "wide": [
        ParamRule("Mod Filter", +1, 0.3,
                  "Modulating the filter sweeps L/R differently."),
    ],
    "tight": [
        ParamRule("Feedback", -1, 0.5, "Less smear."),
        ParamRule("Reverb Amount", -1, 0.4, "No tail."),
    ],
    "dense": [
        ParamRule("Feedback", +1, 0.5, "More repeats."),
        ParamRule("Reverb Amount", +1, 0.3, "Add tail."),
    ],
}


# ---------------------------------------------------------------------------
# DEVICE_RULES — the master dict.
#
# Keyed by LOM class_name (the value AbletonOSC returns from
# /live/track/get/devices/class_name and stored on device_schemas).
# ---------------------------------------------------------------------------
DEVICE_RULES: dict[str, dict[str, list[ParamRule]]] = {
    # Instruments
    "Drift": DRIFT_RULES,
    "Operator": OPERATOR_RULES,
    "InstrumentVector": WAVETABLE_RULES,  # Wavetable's LOM class
    "Tension": TENSION_RULES,
    "AnalogDevice": ANALOG_RULES,
    # Effects
    "Compressor2": COMPRESSOR_RULES,
    "Reverb": REVERB_RULES,
    "AutoFilter": AUTO_FILTER_RULES,
    "Saturator": SATURATOR_RULES,
    "Amp": AMP_RULES,
    "Cabinet": CABINET_RULES,
    "Echo": ECHO_RULES,
}


# ---------------------------------------------------------------------------
# Public lookup helpers
# ---------------------------------------------------------------------------


def get_rules(class_name: str) -> Optional[dict[str, list[ParamRule]]]:
    """Return the rule dict for a device class, or None if unsupported."""
    if not class_name:
        return None
    return DEVICE_RULES.get(class_name)


def get_descriptor_rules(class_name: str, descriptor: str) -> list[ParamRule]:
    """Return the list of ParamRules for (class, descriptor), or []."""
    rules = get_rules(class_name)
    if rules is None:
        return []
    return list(rules.get(normalize_descriptor(descriptor), []))


def supported_classes() -> list[str]:
    """Devices with at least one rule set defined."""
    return sorted(DEVICE_RULES.keys())


def supported_descriptors_for(class_name: str) -> list[str]:
    """All descriptor labels with at least one rule for this device."""
    rules = get_rules(class_name)
    if not rules:
        return []
    return sorted(label for label, lst in rules.items() if lst)


def coverage_table() -> list[dict[str, object]]:
    """Per-device descriptor coverage. Useful for diagnostics."""
    out: list[dict[str, object]] = []
    for cls in supported_classes():
        d = DEVICE_RULES[cls]
        covered = [label for label, lst in d.items() if lst]
        out.append(
            {
                "class_name": cls,
                "descriptors_with_rules": sorted(covered),
                "missing_required": sorted(set(REQUIRED_DESCRIPTORS) - set(covered)),
                "rule_count": sum(len(lst) for lst in d.values()),
            }
        )
    return out


def all_descriptors() -> list[str]:
    """Union of all descriptors across all devices."""
    out: set[str] = set()
    for d in DEVICE_RULES.values():
        out.update(label for label, lst in d.items() if lst)
    return sorted(out)


def iter_rules() -> Iterable[tuple[str, str, ParamRule]]:
    """Flat iteration over (class_name, descriptor, rule) tuples."""
    for cls, d in DEVICE_RULES.items():
        for desc, rules in d.items():
            for r in rules:
                yield cls, desc, r


__all__ = [
    "ParamRule",
    "DEVICE_RULES",
    "REQUIRED_DESCRIPTORS",
    "DESCRIPTOR_ALIASES",
    "normalize_descriptor",
    "get_rules",
    "get_descriptor_rules",
    "supported_classes",
    "supported_descriptors_for",
    "coverage_table",
    "all_descriptors",
    "iter_rules",
]
