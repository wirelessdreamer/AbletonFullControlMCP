"""Curated preset library.

Each :class:`Preset` is a hand-tuned starting point for a specific synth /
device class. ``device_class`` matches one of:

- ``synth_stub`` — the in-process numpy synth (always available).
- A synth_bench synth name (``subtractive``, ``fm_2op``, ``fm_4op``,
  ``wavetable``, ``additive``, ``granular``) — only meaningful when
  Agent 4's :mod:`ableton_mcp.synth_bench` is shipped.
- A real Live device class_name (e.g. ``"Operator"``, ``"Wavetable"``,
  ``"Analog"``) — applied via OSC by :mod:`.applier`.

The :data:`LIBRARY` list is the canonical source. :mod:`.storage` seeds
sqlite from it on first call.

Curation principles
-------------------
- Names describe the *sound*, not the patch number.
- Tags are useful for search: instrument family, character, articulation.
- Params target the synth's documented ranges (see ``synth_stub`` for the
  reference schema).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Preset:
    """A named param dictionary plus searchable metadata.

    ``source`` is ``'curated'`` for hand-authored entries and
    ``'discovered'`` for those produced by :mod:`.clusterer`.
    """

    name: str
    device_class: str
    params: dict[str, float]
    tags: list[str] = field(default_factory=list)
    description: str = ""
    source: str = "curated"  # 'curated' | 'discovered'

    def to_dict(self) -> dict:
        """JSON-friendly view."""
        return {
            "name": self.name,
            "device_class": self.device_class,
            "params": dict(self.params),
            "tags": list(self.tags),
            "description": self.description,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Curated preset library.
# ---------------------------------------------------------------------------
# synth_stub schema (see sound/synth_stub.py):
#   freq         80..1200 Hz
#   attack       0.001..0.5 s
#   decay        0.01..0.6 s
#   sustain      0..1
#   release      0.01..0.5 s
#   cutoff       200..8000 Hz
#   resonance    0.5..6.0
#   noise_amount 0..0.5

_STUB_PRESETS: list[Preset] = [
    Preset(
        name="Warm Saw Pad",
        device_class="synth_stub",
        params={
            "freq": 220.0,
            "attack": 0.35,
            "decay": 0.25,
            "sustain": 0.85,
            "release": 0.45,
            "cutoff": 1400.0,
            "resonance": 1.2,
            "noise_amount": 0.02,
        },
        tags=["pad", "warm", "slow attack", "lush", "ambient", "low cutoff"],
        description="Slow-attacking warm pad sitting in the lower mids — good for chord beds.",
    ),
    Preset(
        name="Plucky Square Lead",
        device_class="synth_stub",
        params={
            "freq": 440.0,
            "attack": 0.005,
            "decay": 0.18,
            "sustain": 0.25,
            "release": 0.12,
            "cutoff": 3200.0,
            "resonance": 2.0,
            "noise_amount": 0.0,
        },
        tags=["lead", "pluck", "fast attack", "bright", "melodic"],
        description="Snappy mid lead with a quick decay — sits in front of a mix.",
    ),
    Preset(
        name="Sub Bass",
        device_class="synth_stub",
        params={
            "freq": 110.0,
            "attack": 0.005,
            "decay": 0.4,
            "sustain": 0.9,
            "release": 0.18,
            "cutoff": 600.0,
            "resonance": 0.7,
            "noise_amount": 0.0,
        },
        tags=["bass", "sub", "low", "deep", "low cutoff", "fundamentals"],
        description="Pure low-end fundamental for kick reinforcement and 808-style drops.",
    ),
    Preset(
        name="Fat Saw Bass",
        device_class="synth_stub",
        params={
            "freq": 165.0,
            "attack": 0.002,
            "decay": 0.3,
            "sustain": 0.7,
            "release": 0.15,
            "cutoff": 1800.0,
            "resonance": 2.5,
            "noise_amount": 0.05,
        },
        tags=["bass", "fat", "growl", "mid bass", "resonant"],
        description="Resonant mid-bass with bite — works for synthwave and house.",
    ),
    Preset(
        name="Aggressive Lead",
        device_class="synth_stub",
        params={
            "freq": 660.0,
            "attack": 0.003,
            "decay": 0.1,
            "sustain": 0.8,
            "release": 0.08,
            "cutoff": 6500.0,
            "resonance": 4.5,
            "noise_amount": 0.08,
        },
        tags=["lead", "aggressive", "bright", "resonant", "screaming", "fast attack"],
        description="High-cutoff resonant lead — cuts through dense arrangements.",
    ),
    Preset(
        name="Ambient Drone",
        device_class="synth_stub",
        params={
            "freq": 130.0,
            "attack": 0.5,
            "decay": 0.6,
            "sustain": 1.0,
            "release": 0.5,
            "cutoff": 900.0,
            "resonance": 1.0,
            "noise_amount": 0.04,
        },
        tags=["drone", "ambient", "slow attack", "evolving", "dark", "long release"],
        description="Sustained drone for ambient beds and film score textures.",
    ),
    Preset(
        name="Bright Bell",
        device_class="synth_stub",
        params={
            "freq": 880.0,
            "attack": 0.002,
            "decay": 0.5,
            "sustain": 0.0,
            "release": 0.4,
            "cutoff": 7500.0,
            "resonance": 1.5,
            "noise_amount": 0.0,
        },
        tags=["bell", "bright", "percussive", "melodic", "high cutoff", "decay"],
        description="Decaying bell-like tone with a bright top — good for melodies.",
    ),
    Preset(
        name="Soft Sine Pad",
        device_class="synth_stub",
        params={
            "freq": 330.0,
            "attack": 0.4,
            "decay": 0.3,
            "sustain": 0.9,
            "release": 0.45,
            "cutoff": 2200.0,
            "resonance": 0.8,
            "noise_amount": 0.0,
        },
        tags=["pad", "soft", "smooth", "warm", "slow attack", "clean"],
        description="Pure-tone pad without grit — lush behind vocals.",
    ),
    Preset(
        name="Noisy Wash",
        device_class="synth_stub",
        params={
            "freq": 220.0,
            "attack": 0.35,
            "decay": 0.4,
            "sustain": 0.7,
            "release": 0.45,
            "cutoff": 4500.0,
            "resonance": 1.0,
            "noise_amount": 0.45,
        },
        tags=["pad", "noise", "wash", "atmospheric", "textural", "noisy"],
        description="Air-and-noise textural pad — great for transitions and risers.",
    ),
    Preset(
        name="Acid Squelch",
        device_class="synth_stub",
        params={
            "freq": 220.0,
            "attack": 0.002,
            "decay": 0.18,
            "sustain": 0.4,
            "release": 0.1,
            "cutoff": 2400.0,
            "resonance": 5.5,
            "noise_amount": 0.02,
        },
        tags=["bass", "acid", "resonant", "squelch", "303", "fast attack"],
        description="High-resonance 303-style squelch — modulate cutoff for movement.",
    ),
    Preset(
        name="Pluck Stab",
        device_class="synth_stub",
        params={
            "freq": 523.0,
            "attack": 0.001,
            "decay": 0.08,
            "sustain": 0.0,
            "release": 0.05,
            "cutoff": 4000.0,
            "resonance": 1.5,
            "noise_amount": 0.0,
        },
        tags=["pluck", "stab", "percussive", "fast attack", "short", "rhythmic"],
        description="Short percussive stab — sequencer/arp food.",
    ),
    Preset(
        name="Dark Cinematic Bass",
        device_class="synth_stub",
        params={
            "freq": 90.0,
            "attack": 0.05,
            "decay": 0.5,
            "sustain": 0.85,
            "release": 0.35,
            "cutoff": 450.0,
            "resonance": 0.9,
            "noise_amount": 0.05,
        },
        tags=["bass", "cinematic", "dark", "low cutoff", "ominous"],
        description="Sustained dark bass for trailers and tension scenes.",
    ),
    Preset(
        name="Hollow Pad",
        device_class="synth_stub",
        params={
            "freq": 261.0,
            "attack": 0.25,
            "decay": 0.4,
            "sustain": 0.6,
            "release": 0.4,
            "cutoff": 1100.0,
            "resonance": 3.5,
            "noise_amount": 0.02,
        },
        tags=["pad", "hollow", "resonant", "cold", "phasey"],
        description="Resonant peak gives a vocal-formant hollow character.",
    ),
    Preset(
        name="Bright Saw Stab",
        device_class="synth_stub",
        params={
            "freq": 392.0,
            "attack": 0.002,
            "decay": 0.12,
            "sustain": 0.2,
            "release": 0.1,
            "cutoff": 5500.0,
            "resonance": 1.2,
            "noise_amount": 0.0,
        },
        tags=["stab", "bright", "saw", "rhythmic", "cutting"],
        description="Bright cutting saw stab — dance/EDM chord layer.",
    ),
    Preset(
        name="Sub Pulse",
        device_class="synth_stub",
        params={
            "freq": 82.0,
            "attack": 0.005,
            "decay": 0.5,
            "sustain": 0.5,
            "release": 0.25,
            "cutoff": 380.0,
            "resonance": 0.8,
            "noise_amount": 0.0,
        },
        tags=["bass", "sub", "deep", "pulse", "low"],
        description="Pulsing low-end pad-bass — slow tempo.",
    ),
    Preset(
        name="Resonant Sweep",
        device_class="synth_stub",
        params={
            "freq": 220.0,
            "attack": 0.3,
            "decay": 0.5,
            "sustain": 0.7,
            "release": 0.4,
            "cutoff": 3000.0,
            "resonance": 5.0,
            "noise_amount": 0.1,
        },
        tags=["sweep", "resonant", "movement", "fx", "dramatic"],
        description="Resonant filter sweep — automate cutoff for risers.",
    ),
    Preset(
        name="Vintage Lead",
        device_class="synth_stub",
        params={
            "freq": 523.0,
            "attack": 0.008,
            "decay": 0.2,
            "sustain": 0.6,
            "release": 0.18,
            "cutoff": 2800.0,
            "resonance": 2.8,
            "noise_amount": 0.04,
        },
        tags=["lead", "vintage", "warm", "analog", "mid"],
        description="Warm vintage-flavoured mid lead with a touch of grit.",
    ),
    Preset(
        name="Whisper Pad",
        device_class="synth_stub",
        params={
            "freq": 440.0,
            "attack": 0.45,
            "decay": 0.5,
            "sustain": 0.6,
            "release": 0.5,
            "cutoff": 3500.0,
            "resonance": 0.9,
            "noise_amount": 0.35,
        },
        tags=["pad", "whisper", "noise", "ethereal", "ambient", "slow attack"],
        description="Breathy noisy pad — very textural, sits high.",
    ),
    Preset(
        name="Reese Bass",
        device_class="synth_stub",
        params={
            "freq": 130.0,
            "attack": 0.005,
            "decay": 0.5,
            "sustain": 0.95,
            "release": 0.2,
            "cutoff": 1100.0,
            "resonance": 3.0,
            "noise_amount": 0.06,
        },
        tags=["bass", "reese", "growl", "dnb", "modulated"],
        description="Sustained mid-bass for DnB and dubstep — modulate cutoff for the wobble.",
    ),
    Preset(
        name="Glassy Lead",
        device_class="synth_stub",
        params={
            "freq": 740.0,
            "attack": 0.005,
            "decay": 0.15,
            "sustain": 0.7,
            "release": 0.15,
            "cutoff": 7800.0,
            "resonance": 1.0,
            "noise_amount": 0.0,
        },
        tags=["lead", "glassy", "bright", "high cutoff", "clean"],
        description="Clean high lead with brilliant top end.",
    ),
    Preset(
        name="Muted Bass",
        device_class="synth_stub",
        params={
            "freq": 145.0,
            "attack": 0.01,
            "decay": 0.3,
            "sustain": 0.6,
            "release": 0.12,
            "cutoff": 700.0,
            "resonance": 0.8,
            "noise_amount": 0.0,
        },
        tags=["bass", "muted", "soft", "rounded", "low cutoff"],
        description="Rounded bass with no top — sits politely under leads.",
    ),
    Preset(
        name="Long Evolving Pad",
        device_class="synth_stub",
        params={
            "freq": 196.0,
            "attack": 0.5,
            "decay": 0.6,
            "sustain": 0.95,
            "release": 0.5,
            "cutoff": 1700.0,
            "resonance": 1.6,
            "noise_amount": 0.08,
        },
        tags=["pad", "evolving", "long", "ambient", "slow attack", "long release"],
        description="Very slow attack and release — feed through reverb for cinematic beds.",
    ),
    Preset(
        name="Punchy Bass",
        device_class="synth_stub",
        params={
            "freq": 110.0,
            "attack": 0.001,
            "decay": 0.15,
            "sustain": 0.45,
            "release": 0.1,
            "cutoff": 1400.0,
            "resonance": 1.8,
            "noise_amount": 0.03,
        },
        tags=["bass", "punchy", "fast attack", "tight", "rhythmic"],
        description="Fast-attack tight bass — funk and electronic basslines.",
    ),
    Preset(
        name="Tape Hiss Texture",
        device_class="synth_stub",
        params={
            "freq": 220.0,
            "attack": 0.4,
            "decay": 0.5,
            "sustain": 0.5,
            "release": 0.5,
            "cutoff": 4200.0,
            "resonance": 0.9,
            "noise_amount": 0.5,
        },
        tags=["fx", "noise", "texture", "vintage", "atmospheric"],
        description="Mostly-noise textural element — layer behind anything for grain.",
    ),
    Preset(
        name="Round Sine Bass",
        device_class="synth_stub",
        params={
            "freq": 98.0,
            "attack": 0.003,
            "decay": 0.4,
            "sustain": 0.85,
            "release": 0.2,
            "cutoff": 500.0,
            "resonance": 0.6,
            "noise_amount": 0.0,
        },
        tags=["bass", "sine", "round", "clean", "deep", "low cutoff"],
        description="Pure sine bass — perfect for sub layering.",
    ),
    Preset(
        name="Detuned Pad",
        device_class="synth_stub",
        params={
            "freq": 246.9,
            "attack": 0.3,
            "decay": 0.4,
            "sustain": 0.85,
            "release": 0.4,
            "cutoff": 1900.0,
            "resonance": 1.4,
            "noise_amount": 0.06,
        },
        tags=["pad", "detuned", "warm", "wide", "slow attack"],
        description="Slightly detuned-feeling pad — width via the noise mix.",
    ),
    Preset(
        name="Chip Lead",
        device_class="synth_stub",
        params={
            "freq": 587.3,
            "attack": 0.001,
            "decay": 0.05,
            "sustain": 0.9,
            "release": 0.05,
            "cutoff": 4500.0,
            "resonance": 1.0,
            "noise_amount": 0.0,
        },
        tags=["lead", "chiptune", "bright", "8-bit", "rhythmic"],
        description="8-bit-flavoured lead — square-ish, no envelope curve.",
    ),
    Preset(
        name="Ethereal Bell Pad",
        device_class="synth_stub",
        params={
            "freq": 660.0,
            "attack": 0.2,
            "decay": 0.6,
            "sustain": 0.4,
            "release": 0.45,
            "cutoff": 5200.0,
            "resonance": 1.2,
            "noise_amount": 0.0,
        },
        tags=["pad", "bell", "ethereal", "bright", "ambient"],
        description="Bell pad hybrid — slow-attack but with metallic decay.",
    ),
    Preset(
        name="Filtered Noise FX",
        device_class="synth_stub",
        params={
            "freq": 220.0,
            "attack": 0.05,
            "decay": 0.5,
            "sustain": 0.0,
            "release": 0.3,
            "cutoff": 2800.0,
            "resonance": 4.0,
            "noise_amount": 0.5,
        },
        tags=["fx", "noise", "filtered", "transition", "riser"],
        description="Resonant filtered noise — riser/transition material.",
    ),
    Preset(
        name="Wide Stack Lead",
        device_class="synth_stub",
        params={
            "freq": 466.2,
            "attack": 0.005,
            "decay": 0.25,
            "sustain": 0.7,
            "release": 0.2,
            "cutoff": 4800.0,
            "resonance": 1.4,
            "noise_amount": 0.04,
        },
        tags=["lead", "stack", "wide", "bright", "supersaw"],
        description="Bright wide-feel lead — chorus/reverb friendly.",
    ),
    Preset(
        name="Slow Filter Pad",
        device_class="synth_stub",
        params={
            "freq": 174.6,
            "attack": 0.5,
            "decay": 0.5,
            "sustain": 0.9,
            "release": 0.5,
            "cutoff": 800.0,
            "resonance": 2.5,
            "noise_amount": 0.05,
        },
        tags=["pad", "slow", "filter", "evolving", "low cutoff", "slow attack"],
        description="Low-cutoff resonant pad — automate cutoff for slow opens.",
    ),
    Preset(
        name="Snappy Bass Pluck",
        device_class="synth_stub",
        params={
            "freq": 130.8,
            "attack": 0.001,
            "decay": 0.1,
            "sustain": 0.0,
            "release": 0.08,
            "cutoff": 1600.0,
            "resonance": 2.0,
            "noise_amount": 0.02,
        },
        tags=["bass", "pluck", "snappy", "fast attack", "short", "rhythmic"],
        description="Short percussive bass pluck — sequencer/16th-note basslines.",
    ),
]


# ---------------------------------------------------------------------------
# synth_bench presets — only meaningful when Agent 4 has shipped synth_bench.
# These are duplicates of the synth_stub-friendly schema, retagged for the
# specific synth family. If synth_bench is unavailable they're still
# valid library entries (just not renderable until it lands).
# ---------------------------------------------------------------------------

_SYNTH_BENCH_PRESETS: list[Preset] = [
    Preset(
        name="Subtractive Warm Pad",
        device_class="subtractive",
        params={
            "freq": 220.0,
            "attack": 0.35,
            "decay": 0.25,
            "sustain": 0.85,
            "release": 0.45,
            "cutoff": 1400.0,
            "resonance": 1.2,
            "noise_amount": 0.02,
        },
        tags=["pad", "warm", "subtractive", "slow attack", "lush"],
        description="Subtractive synth warm pad — saw + slow attack + low cutoff.",
    ),
    Preset(
        name="Subtractive Sub Bass",
        device_class="subtractive",
        params={
            "freq": 110.0,
            "attack": 0.005,
            "decay": 0.4,
            "sustain": 0.9,
            "release": 0.18,
            "cutoff": 600.0,
            "resonance": 0.7,
            "noise_amount": 0.0,
        },
        tags=["bass", "sub", "subtractive", "deep", "low"],
        description="Subtractive deep sub bass.",
    ),
    Preset(
        name="FM Glassy Bell",
        device_class="fm_2op",
        params={
            "carrier_freq": 440.0,
            "ratio": 3.0,
            "mod_index": 4.0,
            "attack": 0.002,
            "decay": 0.5,
            "sustain": 0.0,
            "release": 0.4,
        },
        tags=["bell", "fm", "glassy", "bright", "metallic"],
        description="Classic 2-op FM bell — high mod index for metallic overtones.",
    ),
    Preset(
        name="FM Electric Piano",
        device_class="fm_2op",
        params={
            "carrier_freq": 261.0,
            "ratio": 1.0,
            "mod_index": 1.5,
            "attack": 0.005,
            "decay": 0.4,
            "sustain": 0.4,
            "release": 0.25,
        },
        tags=["fm", "ep", "electric piano", "warm", "vintage", "tine"],
        description="Tine-style FM electric piano — moderate mod index.",
    ),
    Preset(
        name="FM Plucky Bass",
        device_class="fm_2op",
        params={
            "carrier_freq": 110.0,
            "ratio": 2.0,
            "mod_index": 2.5,
            "attack": 0.001,
            "decay": 0.18,
            "sustain": 0.3,
            "release": 0.12,
        },
        tags=["fm", "bass", "pluck", "fast attack", "punchy"],
        description="Punchy FM bass with bite.",
    ),
    Preset(
        name="FM 4-op Brass Stack",
        device_class="fm_4op",
        params={
            "carrier_freq": 220.0,
            "ratio_a": 1.0,
            "ratio_b": 2.0,
            "mod_index_a": 2.0,
            "mod_index_b": 1.5,
            "attack": 0.02,
            "decay": 0.3,
            "sustain": 0.7,
            "release": 0.3,
        },
        tags=["fm", "brass", "stack", "bright", "punchy"],
        description="4-op FM brass stab — multiple operators give the brass blat.",
    ),
    Preset(
        name="Wavetable Sweep Pad",
        device_class="wavetable",
        params={
            "freq": 220.0,
            "wt_position": 0.3,
            "wt_morph_rate": 0.15,
            "attack": 0.4,
            "decay": 0.5,
            "sustain": 0.85,
            "release": 0.5,
            "cutoff": 2200.0,
            "resonance": 1.1,
        },
        tags=["wavetable", "pad", "sweep", "evolving", "movement"],
        description="Slowly morphing wavetable pad.",
    ),
    Preset(
        name="Wavetable Plucky Lead",
        device_class="wavetable",
        params={
            "freq": 440.0,
            "wt_position": 0.65,
            "wt_morph_rate": 0.0,
            "attack": 0.005,
            "decay": 0.18,
            "sustain": 0.5,
            "release": 0.15,
            "cutoff": 4500.0,
            "resonance": 1.5,
        },
        tags=["wavetable", "lead", "pluck", "bright", "fast attack"],
        description="Static wavetable position — bright plucky lead.",
    ),
    Preset(
        name="Additive Choir Pad",
        device_class="additive",
        params={
            "freq": 261.0,
            "harmonics": 32.0,
            "harmonic_rolloff": 1.5,
            "even_odd_balance": 0.5,
            "attack": 0.4,
            "decay": 0.5,
            "sustain": 0.9,
            "release": 0.5,
        },
        tags=["additive", "choir", "pad", "slow attack", "vocal", "lush"],
        description="Many-harmonic additive choir-like pad.",
    ),
    Preset(
        name="Additive Organ",
        device_class="additive",
        params={
            "freq": 220.0,
            "harmonics": 16.0,
            "harmonic_rolloff": 0.7,
            "even_odd_balance": 0.4,
            "attack": 0.005,
            "decay": 0.05,
            "sustain": 1.0,
            "release": 0.05,
        },
        tags=["additive", "organ", "tonewheel", "sustained", "vintage"],
        description="Drawbar organ flavour from the additive engine.",
    ),
    Preset(
        name="Granular Cloud Texture",
        device_class="granular",
        params={
            "grain_size": 0.05,
            "grain_density": 30.0,
            "grain_pitch_jitter": 0.1,
            "attack": 0.3,
            "decay": 0.4,
            "sustain": 0.8,
            "release": 0.5,
        },
        tags=["granular", "cloud", "texture", "ambient", "evolving"],
        description="Dense grain cloud — generative ambient texture.",
    ),
    Preset(
        name="Granular Pluck",
        device_class="granular",
        params={
            "grain_size": 0.02,
            "grain_density": 80.0,
            "grain_pitch_jitter": 0.02,
            "attack": 0.001,
            "decay": 0.12,
            "sustain": 0.0,
            "release": 0.08,
        },
        tags=["granular", "pluck", "percussive", "fast attack", "short"],
        description="Short dense grain burst — percussive granular.",
    ),
]


LIBRARY: list[Preset] = _STUB_PRESETS + _SYNTH_BENCH_PRESETS
"""All curated presets, in declaration order. Stable identifier: ``preset.name``."""


def by_name(name: str) -> Preset | None:
    """Look up a curated preset by exact name (case-insensitive)."""
    lowered = name.strip().lower()
    for p in LIBRARY:
        if p.name.lower() == lowered:
            return p
    return None
