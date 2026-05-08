"""Curated semantic vocabulary mapping natural-language descriptors to feature predicates.

A :class:`Descriptor` is the bridge between an audio engineer's word
("brighter", "punchier", "warmer") and the 32-dim feature vector produced
by :func:`ableton_mcp.sound.features.extract_features`.

Each descriptor pins itself to one or more :class:`FeatureAnchor` predicates
on named feature dimensions. ``percentile_above`` / ``percentile_below`` use
the empirical reference distribution stored in
:mod:`ableton_mcp.semantics.reference_distributions`; ``high`` / ``low`` use
explicit thresholds for cases where audio engineering intuition pins a
concrete absolute number (e.g. "centroid > 4 kHz is unambiguously bright").

The :data:`VOCABULARY` dict ships ~100 descriptors covering the ten
categories listed in the module ``__init__``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# All feature names a Descriptor may anchor to. These match
# ``ableton_mcp.sound.features.FEATURE_VECTOR_NAMES`` exactly so the describer
# and the transforms layer can share the same vocabulary of feature dimensions.
FEATURE_NAMES: tuple[str, ...] = (
    "spectral_centroid",
    "spectral_bandwidth",
    "spectral_rolloff",
    "zcr",
    "rms",
    "spectral_flatness",
    # MFCC mean coefficients (we mostly anchor to 0 for energy and 1-2 for slope)
    "mfcc_mean_0",
    "mfcc_mean_1",
    "mfcc_mean_2",
    "mfcc_mean_3",
    "mfcc_mean_4",
    # MFCC std (variability over time → enveloppe character)
    "mfcc_std_0",
    "mfcc_std_1",
    "mfcc_std_2",
)

PredicateKind = Literal["high", "low", "percentile_above", "percentile_below"]
Category = Literal[
    "brightness",
    "warmth",
    "dynamics",
    "space",
    "character",
    "envelope",
    "harmonic",
    "punch",
    "air",
    "body",
]


@dataclass(frozen=True)
class FeatureAnchor:
    """One predicate on one feature dimension.

    Examples:

    - ``FeatureAnchor("spectral_centroid", "percentile_above", 0.7)``
      → "this feature is in the top 30% of the reference distribution"
    - ``FeatureAnchor("zcr", "high", 0.15)``
      → "zero-crossing rate is above the absolute threshold 0.15"

    ``weight`` controls how much this anchor contributes to a descriptor's
    confidence in the describer.
    """

    feature: str
    predicate: PredicateKind
    threshold: float
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.feature not in FEATURE_NAMES:
            raise ValueError(
                f"FeatureAnchor.feature {self.feature!r} not in FEATURE_NAMES"
            )
        if self.predicate not in ("high", "low", "percentile_above", "percentile_below"):
            raise ValueError(f"unknown predicate {self.predicate!r}")
        if self.predicate.startswith("percentile") and not 0.0 <= self.threshold <= 1.0:
            raise ValueError(
                f"percentile threshold must be 0..1, got {self.threshold!r}"
            )


@dataclass(frozen=True)
class Descriptor:
    """One natural-language sound descriptor pinned to feature predicates."""

    label: str
    aliases: list[str] = field(default_factory=list)
    category: Category = "character"
    feature_anchors: list[FeatureAnchor] = field(default_factory=list)
    opposite: str | None = None
    intensity_scale: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        if not self.feature_anchors:
            raise ValueError(f"descriptor {self.label!r} has zero anchors")
        if not 0.0 <= self.intensity_scale <= 1.0:
            raise ValueError(
                f"descriptor {self.label!r} intensity_scale must be 0..1"
            )


# Convenience builders so the bulk vocabulary table below stays readable.
def _high(feature: str, threshold: float, weight: float = 1.0) -> FeatureAnchor:
    return FeatureAnchor(feature, "high", threshold, weight)


def _low(feature: str, threshold: float, weight: float = 1.0) -> FeatureAnchor:
    return FeatureAnchor(feature, "low", threshold, weight)


def _pa(feature: str, percentile: float, weight: float = 1.0) -> FeatureAnchor:
    return FeatureAnchor(feature, "percentile_above", percentile, weight)


def _pb(feature: str, percentile: float, weight: float = 1.0) -> FeatureAnchor:
    return FeatureAnchor(feature, "percentile_below", percentile, weight)


# --------------------------------------------------------------------------
# The vocabulary itself.  ~100 descriptors across all 10 categories.
# Anchors lean on percentile predicates (robust across reference distributions)
# with a few absolute "high"/"low" thresholds where the audio-engineering
# intuition pins to a concrete number.
# --------------------------------------------------------------------------

_DESCRIPTORS: list[Descriptor] = [
    # =========================== BRIGHTNESS ============================
    Descriptor(
        label="bright",
        aliases=["brilliant", "shimmering", "sparkling", "crisp"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.55, weight=1.0),
            _pa("spectral_rolloff", 0.5, weight=0.7),
        ],
        opposite="dark",
        description="Plenty of high-frequency energy; vocal-like presence above 2 kHz.",
    ),
    Descriptor(
        label="brilliant",
        aliases=["dazzling", "glittery"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.8),
            _pa("spectral_rolloff", 0.75),
        ],
        opposite="dull",
        description="Extremely high spectral centroid — top of the brightness range.",
    ),
    Descriptor(
        label="shimmering",
        aliases=["glistening", "twinkly"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.7),
            _pa("spectral_flatness", 0.5, weight=0.5),
        ],
        opposite="muffled",
        description="Bright with broadband shimmer in the highs.",
    ),
    Descriptor(
        label="sparkling",
        aliases=["sparkly"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.7),
            _pa("spectral_rolloff", 0.65),
            _pa("zcr", 0.5, weight=0.4),
        ],
        opposite="dull",
        description="Bright + airy; very present highs.",
    ),
    Descriptor(
        label="crisp",
        aliases=["clear", "articulate"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.6),
            _pa("zcr", 0.55, weight=0.5),
        ],
        opposite="muffled",
        description="Defined transients with high-frequency clarity.",
    ),
    Descriptor(
        label="dark",
        aliases=["dull", "muffled", "closed", "veiled"],
        category="brightness",
        feature_anchors=[
            _pb("spectral_centroid", 0.35, weight=1.0),
            _pb("spectral_rolloff", 0.4, weight=0.7),
        ],
        opposite="bright",
        description="Low-pass-filtered character; little energy above the midrange.",
    ),
    Descriptor(
        label="dull",
        aliases=["lifeless", "flat-sounding"],
        category="brightness",
        feature_anchors=[
            _pb("spectral_centroid", 0.3),
            _pb("spectral_rolloff", 0.35),
        ],
        opposite="brilliant",
        description="Lacking high-frequency content and articulation.",
    ),
    Descriptor(
        label="muffled",
        aliases=["smothered", "blanketed"],
        category="brightness",
        feature_anchors=[
            _pb("spectral_centroid", 0.25),
            _pb("spectral_rolloff", 0.3),
        ],
        opposite="crisp",
        description="As if a blanket has been thrown over the sound.",
    ),
    Descriptor(
        label="closed",
        aliases=["covered"],
        category="brightness",
        feature_anchors=[
            _pb("spectral_centroid", 0.3),
            _pb("zcr", 0.3, weight=0.4),
        ],
        opposite="open",
        description="Sealed-off top end; mid-heavy.",
    ),

    # ============================= WARMTH ==============================
    Descriptor(
        label="warm",
        aliases=["cozy", "round", "smooth"],
        category="warmth",
        feature_anchors=[
            _pa("rms", 0.4, weight=0.5),
            _pb("spectral_centroid", 0.55, weight=0.7),
            _pb("zcr", 0.5, weight=0.5),
        ],
        opposite="cold",
        description="Pleasant low-mid presence, gentle highs, full bottom.",
    ),
    Descriptor(
        label="lush",
        aliases=["luscious", "sumptuous"],
        category="warmth",
        feature_anchors=[
            _pa("spectral_bandwidth", 0.6),
            _pa("rms", 0.45, weight=0.4),
        ],
        opposite="thin",
        description="Wide harmonic spread, generous low-mids.",
    ),
    Descriptor(
        label="cozy",
        aliases=["snug"],
        category="warmth",
        feature_anchors=[
            _pb("spectral_centroid", 0.5),
            _pa("rms", 0.4),
        ],
        opposite="cold",
        description="Mid-heavy, rounded, intimate.",
    ),
    Descriptor(
        label="round",
        aliases=["rounded", "soft-edged"],
        category="warmth",
        feature_anchors=[
            _pb("spectral_centroid", 0.5),
            _pb("spectral_flatness", 0.4, weight=0.5),
        ],
        opposite="edgy",
        description="No sharp peaks — smooth spectral envelope.",
    ),
    Descriptor(
        label="smooth",
        aliases=["silky", "buttery"],
        category="warmth",
        feature_anchors=[
            _pb("spectral_flatness", 0.35),
            _pb("zcr", 0.4),
        ],
        opposite="harsh",
        description="Tonal, non-noisy, continuous.",
    ),
    Descriptor(
        label="silky",
        aliases=["velvety"],
        category="warmth",
        feature_anchors=[
            _pb("spectral_flatness", 0.3),
            _pb("zcr", 0.35),
            _pa("spectral_bandwidth", 0.5, weight=0.4),
        ],
        opposite="harsh",
        description="Smoothness + a touch of harmonic spread.",
    ),
    Descriptor(
        label="cold",
        aliases=["clinical", "sterile", "icy"],
        category="warmth",
        feature_anchors=[
            _pa("spectral_centroid", 0.55, weight=0.7),
            _pb("rms", 0.35, weight=0.4),
            _pa("zcr", 0.55, weight=0.5),
        ],
        opposite="warm",
        description="Bright, thin, low-energy in the body region.",
    ),
    Descriptor(
        label="clinical",
        aliases=["surgical"],
        category="warmth",
        feature_anchors=[
            _pa("spectral_centroid", 0.55),
            _pb("spectral_flatness", 0.3, weight=0.5),
        ],
        opposite="lush",
        description="Hyper-precise, low-distortion, exposed.",
    ),
    Descriptor(
        label="sterile",
        aliases=["antiseptic"],
        category="warmth",
        feature_anchors=[
            _pa("spectral_centroid", 0.55),
            _pb("rms", 0.35),
        ],
        opposite="warm",
        description="Lacking body or character; over-cleaned.",
    ),
    Descriptor(
        label="icy",
        aliases=["frosty"],
        category="warmth",
        feature_anchors=[
            _pa("spectral_centroid", 0.65),
            _pb("rms", 0.3),
        ],
        opposite="warm",
        description="Very bright with no warm body.",
    ),
    Descriptor(
        label="clean",
        aliases=["pure", "polished"],
        category="warmth",
        feature_anchors=[
            _pb("spectral_flatness", 0.3),
            _pb("zcr", 0.4),
        ],
        opposite="dirty",
        description="Low noise floor and minimal harmonic chaos.",
    ),

    # ============================ DYNAMICS =============================
    Descriptor(
        label="punchy",
        aliases=["punchful", "impactful"],
        category="dynamics",
        feature_anchors=[
            _pa("rms", 0.55),
            _pa("mfcc_std_0", 0.55, weight=0.6),
        ],
        opposite="soft",
        description="Strong transient + loud sustain; impactful.",
    ),
    Descriptor(
        label="aggressive",
        aliases=["fierce", "biting"],
        category="dynamics",
        feature_anchors=[
            _pa("rms", 0.55),
            _pa("zcr", 0.55, weight=0.6),
            _pa("spectral_flatness", 0.5, weight=0.4),
        ],
        opposite="laid-back",
        description="Loud, edgy, in-your-face.",
    ),
    Descriptor(
        label="tight",
        aliases=["controlled", "focused"],
        category="dynamics",
        feature_anchors=[
            _pb("mfcc_std_0", 0.5, weight=0.6),
            _pb("spectral_bandwidth", 0.5, weight=0.4),
        ],
        opposite="loose",
        description="Constrained envelope; nothing wandering.",
    ),
    Descriptor(
        label="snappy",
        aliases=["snappish"],
        category="dynamics",
        feature_anchors=[
            _pa("mfcc_std_0", 0.6),
            _pa("zcr", 0.5, weight=0.5),
        ],
        opposite="sluggish",
        description="Fast attack with quick decay.",
    ),
    Descriptor(
        label="soft",
        aliases=["gentle", "mellow"],
        category="dynamics",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("zcr", 0.4),
        ],
        opposite="aggressive",
        description="Low energy, gentle attack.",
    ),
    Descriptor(
        label="laid-back",
        aliases=["relaxed", "lazy-feel"],
        category="dynamics",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("mfcc_std_0", 0.45),
        ],
        opposite="aggressive",
        description="Easy-going, low-impact dynamic feel.",
    ),
    Descriptor(
        label="loose",
        aliases=["wandering", "open-feel"],
        category="dynamics",
        feature_anchors=[
            _pa("mfcc_std_0", 0.55),
            _pa("spectral_bandwidth", 0.55, weight=0.5),
        ],
        opposite="tight",
        description="Variability over time; not constrained.",
    ),
    Descriptor(
        label="sluggish",
        aliases=["slow-feel", "lethargic"],
        category="dynamics",
        feature_anchors=[
            _pb("mfcc_std_0", 0.4),
            _pb("zcr", 0.35),
        ],
        opposite="snappy",
        description="Slow attack and lingering decay.",
    ),
    Descriptor(
        label="dynamic",
        aliases=["expressive", "lively"],
        category="dynamics",
        feature_anchors=[
            _pa("mfcc_std_0", 0.6),
            _pa("mfcc_std_1", 0.55, weight=0.5),
        ],
        opposite="static",
        description="High variation in level/timbre over time.",
    ),
    Descriptor(
        label="static",
        aliases=["flat-dynamic", "steady"],
        category="dynamics",
        feature_anchors=[
            _pb("mfcc_std_0", 0.35),
            _pb("mfcc_std_1", 0.35),
        ],
        opposite="dynamic",
        description="Little variation over time; steady-state.",
    ),

    # ============================== SPACE ==============================
    Descriptor(
        label="wide",
        aliases=["broad"],
        category="space",
        feature_anchors=[
            _pa("spectral_bandwidth", 0.6),
            _pa("mfcc_std_2", 0.5, weight=0.4),
        ],
        opposite="narrow",
        description="Spread-out spectrum; broad timbre.",
    ),
    Descriptor(
        label="spacious",
        aliases=["roomy", "vast"],
        category="space",
        feature_anchors=[
            _pa("spectral_bandwidth", 0.55),
            _pa("mfcc_std_1", 0.5, weight=0.4),
            _pa("spectral_rolloff", 0.55, weight=0.4),
        ],
        opposite="dry",
        description="Sense of room and reflection.",
    ),
    Descriptor(
        label="lush-space",
        aliases=["cavernous", "reverberant"],
        category="space",
        feature_anchors=[
            _pa("spectral_bandwidth", 0.6),
            _pa("mfcc_std_2", 0.55),
        ],
        opposite="dry",
        description="Lots of reverb tail and stereo information.",
    ),
    Descriptor(
        label="narrow",
        aliases=["mono-feeling", "constricted"],
        category="space",
        feature_anchors=[
            _pb("spectral_bandwidth", 0.4),
            _pb("mfcc_std_2", 0.4, weight=0.4),
        ],
        opposite="wide",
        description="Concentrated spectrum; tight focus.",
    ),
    Descriptor(
        label="dry",
        aliases=["bone-dry", "unprocessed"],
        category="space",
        feature_anchors=[
            _pb("mfcc_std_1", 0.35),
            _pb("mfcc_std_2", 0.35),
        ],
        opposite="spacious",
        description="No reverb tail; intimate, close-miked feel.",
    ),
    Descriptor(
        label="tight-space",
        aliases=["small-room"],
        category="space",
        feature_anchors=[
            _pb("spectral_bandwidth", 0.4),
            _pb("mfcc_std_1", 0.4),
        ],
        opposite="spacious",
        description="Small enclosure; little ambience.",
    ),

    # =========================== CHARACTER =============================
    Descriptor(
        label="vintage",
        aliases=["retro", "old-school"],
        category="character",
        feature_anchors=[
            _pb("spectral_rolloff", 0.45),
            _pa("spectral_flatness", 0.4, weight=0.5),
        ],
        opposite="modern",
        description="Rolled-off highs with a touch of harmonic chaos.",
    ),
    Descriptor(
        label="analog",
        aliases=["analogue", "tape-like"],
        category="character",
        feature_anchors=[
            _pa("spectral_flatness", 0.45),
            _pb("spectral_rolloff", 0.5, weight=0.5),
        ],
        opposite="digital",
        description="Slightly chaotic harmonic content; hot-tubed.",
    ),
    Descriptor(
        label="dirty",
        aliases=["grimy", "filthy", "raw"],
        category="character",
        feature_anchors=[
            _pa("spectral_flatness", 0.55),
            _pa("zcr", 0.55),
        ],
        opposite="clean",
        description="Saturated, noisy, distorted feel.",
    ),
    Descriptor(
        label="grainy",
        aliases=["textured", "grungy"],
        category="character",
        feature_anchors=[
            _pa("spectral_flatness", 0.55),
            _pa("mfcc_std_2", 0.55, weight=0.4),
        ],
        opposite="pristine",
        description="Audible noise/grit at the macro envelope level.",
    ),
    Descriptor(
        label="modern",
        aliases=["contemporary"],
        category="character",
        feature_anchors=[
            _pa("spectral_rolloff", 0.6),
            _pb("spectral_flatness", 0.4, weight=0.5),
        ],
        opposite="vintage",
        description="Crisp top end, low noise floor.",
    ),
    Descriptor(
        label="pristine",
        aliases=["audiophile", "polished"],
        category="character",
        feature_anchors=[
            _pa("spectral_rolloff", 0.6),
            _pb("spectral_flatness", 0.3),
        ],
        opposite="grainy",
        description="High fidelity, very low noise.",
    ),
    Descriptor(
        label="digital",
        aliases=["dsp-like"],
        category="character",
        feature_anchors=[
            _pb("spectral_flatness", 0.3),
            _pa("spectral_rolloff", 0.55),
        ],
        opposite="analog",
        description="Sharp, exact, no analogue noise floor.",
    ),
    Descriptor(
        label="organic",
        aliases=["natural"],
        category="character",
        feature_anchors=[
            _pa("mfcc_std_1", 0.55),
            _pa("mfcc_std_2", 0.5, weight=0.6),
        ],
        opposite="static",
        description="Time-varying timbre — sounds alive.",
    ),
    Descriptor(
        label="lo-fi",
        aliases=["lofi", "crunchy"],
        category="character",
        feature_anchors=[
            _pb("spectral_rolloff", 0.4),
            _pa("spectral_flatness", 0.5),
        ],
        opposite="hifi",
        description="Restricted bandwidth + harmonic chaos.",
    ),
    Descriptor(
        label="hifi",
        aliases=["hi-fi", "highend"],
        category="character",
        feature_anchors=[
            _pa("spectral_rolloff", 0.6),
            _pb("spectral_flatness", 0.3),
        ],
        opposite="lo-fi",
        description="Full bandwidth, low noise.",
    ),

    # ============================ ENVELOPE =============================
    Descriptor(
        label="percussive",
        aliases=["plucky", "transient", "staccato"],
        category="envelope",
        feature_anchors=[
            _pa("mfcc_std_0", 0.6),
            _pa("zcr", 0.5, weight=0.5),
        ],
        opposite="sustained",
        description="Sharp attack, fast decay.",
    ),
    Descriptor(
        label="plucky",
        aliases=["pluck"],
        category="envelope",
        feature_anchors=[
            _pa("mfcc_std_0", 0.55),
            _pa("zcr", 0.45, weight=0.4),
        ],
        opposite="pad-like",
        description="Plucked-string envelope: short attack, decay, no sustain.",
    ),
    Descriptor(
        label="staccato",
        aliases=["short", "clipped"],
        category="envelope",
        feature_anchors=[
            _pa("mfcc_std_0", 0.6),
            _pb("rms", 0.5, weight=0.3),
        ],
        opposite="legato",
        description="Detached, separated notes; quick releases.",
    ),
    Descriptor(
        label="snappy-env",
        aliases=["fast-attack"],
        category="envelope",
        feature_anchors=[
            _pa("mfcc_std_0", 0.6),
            _pa("zcr", 0.45),
        ],
        opposite="slow-attack",
        description="Very fast attack envelope.",
    ),
    Descriptor(
        label="pad-like",
        aliases=["pad", "ambient"],
        category="envelope",
        feature_anchors=[
            _pb("mfcc_std_0", 0.4),
            _pa("rms", 0.4, weight=0.4),
        ],
        opposite="percussive",
        description="Slow attack, long sustain — string/pad envelope.",
    ),
    Descriptor(
        label="sustained",
        aliases=["held"],
        category="envelope",
        feature_anchors=[
            _pb("mfcc_std_0", 0.4),
            _pa("rms", 0.4),
        ],
        opposite="percussive",
        description="Continuous level over time.",
    ),
    Descriptor(
        label="legato",
        aliases=["smooth-line"],
        category="envelope",
        feature_anchors=[
            _pb("mfcc_std_0", 0.4),
            _pb("zcr", 0.4),
        ],
        opposite="staccato",
        description="Connected, gliding notes; no detached releases.",
    ),
    Descriptor(
        label="drone",
        aliases=["dronelike", "static-tone"],
        category="envelope",
        feature_anchors=[
            _pb("mfcc_std_0", 0.3),
            _pb("mfcc_std_1", 0.3),
        ],
        opposite="percussive",
        description="Unchanging tone over a long period.",
    ),
    Descriptor(
        label="slow-attack",
        aliases=["soft-attack"],
        category="envelope",
        feature_anchors=[
            _pb("mfcc_std_0", 0.35),
            _pb("zcr", 0.4, weight=0.4),
        ],
        opposite="snappy-env",
        description="Long attack ramp; gradual swell.",
    ),
    Descriptor(
        label="evolving",
        aliases=["morphing"],
        category="envelope",
        feature_anchors=[
            _pa("mfcc_std_1", 0.6),
            _pa("mfcc_std_2", 0.55),
        ],
        opposite="static",
        description="Timbre changes meaningfully over time.",
    ),

    # ============================ HARMONIC =============================
    Descriptor(
        label="rich",
        aliases=["complex-harm", "harmonically-dense"],
        category="harmonic",
        feature_anchors=[
            _pa("spectral_bandwidth", 0.6),
            _pb("spectral_flatness", 0.4),
        ],
        opposite="pure",
        description="Many harmonics, but tonal rather than noisy.",
    ),
    Descriptor(
        label="complex",
        aliases=["intricate"],
        category="harmonic",
        feature_anchors=[
            _pa("spectral_bandwidth", 0.55),
            _pa("mfcc_std_1", 0.5, weight=0.4),
        ],
        opposite="simple",
        description="Multi-component spectrum.",
    ),
    Descriptor(
        label="buzzy",
        aliases=["raspy", "buzz"],
        category="harmonic",
        feature_anchors=[
            _pa("zcr", 0.6),
            _pa("spectral_flatness", 0.45),
        ],
        opposite="pure",
        description="Lots of high-frequency partials with noisy texture.",
    ),
    Descriptor(
        label="inharmonic",
        aliases=["bell-like-noise", "metallic-noise"],
        category="harmonic",
        feature_anchors=[
            _pa("spectral_flatness", 0.55),
            _pa("spectral_bandwidth", 0.5, weight=0.5),
        ],
        opposite="harmonic",
        description="Partials not on the integer harmonic series.",
    ),
    Descriptor(
        label="metallic",
        aliases=["bell", "ringing"],
        category="harmonic",
        feature_anchors=[
            _pa("spectral_flatness", 0.55),
            _pa("spectral_centroid", 0.55, weight=0.5),
        ],
        opposite="round",
        description="Bell-/cymbal-like inharmonic content.",
    ),
    Descriptor(
        label="pure",
        aliases=["sine-like", "tonal"],
        category="harmonic",
        feature_anchors=[
            _pb("spectral_bandwidth", 0.35),
            _pb("spectral_flatness", 0.25),
        ],
        opposite="rich",
        description="Near-sinusoidal; few harmonics.",
    ),
    Descriptor(
        label="simple",
        aliases=["plain"],
        category="harmonic",
        feature_anchors=[
            _pb("spectral_bandwidth", 0.4),
            _pb("mfcc_std_1", 0.4, weight=0.5),
        ],
        opposite="complex",
        description="Few partials, low time variation.",
    ),
    Descriptor(
        label="harmonic",
        aliases=["consonant"],
        category="harmonic",
        feature_anchors=[
            _pb("spectral_flatness", 0.35),
            _pa("rms", 0.35, weight=0.4),
        ],
        opposite="inharmonic",
        description="Partials sit on integer multiples of a fundamental.",
    ),
    Descriptor(
        label="resonant",
        aliases=["resonating", "peaky"],
        category="harmonic",
        feature_anchors=[
            _pb("spectral_bandwidth", 0.45),
            _pa("rms", 0.45, weight=0.5),
        ],
        opposite="dampened",
        description="Sharp peak in the spectrum — like a filter ringing.",
    ),
    Descriptor(
        label="dampened",
        aliases=["damped", "muted"],
        category="harmonic",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("spectral_rolloff", 0.4),
        ],
        opposite="resonant",
        description="Quick spectral roll-off; partials damped.",
    ),

    # ============================== BODY ===============================
    Descriptor(
        label="thick",
        aliases=["fat", "full", "beefy"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.55),
            _pa("spectral_bandwidth", 0.5, weight=0.4),
        ],
        opposite="thin",
        description="Dense low-mid energy.",
    ),
    Descriptor(
        label="full",
        aliases=["full-bodied", "fleshed-out"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.5),
            _pa("spectral_bandwidth", 0.5, weight=0.4),
        ],
        opposite="hollow",
        description="Whole-spectrum presence.",
    ),
    Descriptor(
        label="fat",
        aliases=["chunky"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.6),
            _pb("spectral_centroid", 0.5, weight=0.5),
        ],
        opposite="thin",
        description="Big low-end body, mid-heavy.",
    ),
    Descriptor(
        label="heavy",
        aliases=["weighty", "massive"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.6),
            _pb("spectral_centroid", 0.45, weight=0.5),
        ],
        opposite="light",
        description="Strong low-frequency content; gravity.",
    ),
    Descriptor(
        label="thin",
        aliases=["weak", "anemic"],
        category="body",
        feature_anchors=[
            _pb("rms", 0.35),
            _pa("spectral_centroid", 0.55, weight=0.5),
        ],
        opposite="thick",
        description="Lacking low-mid energy; high-only.",
    ),
    Descriptor(
        label="hollow",
        aliases=["empty", "shelled"],
        category="body",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("spectral_bandwidth", 0.4),
        ],
        opposite="full",
        description="Notch in the midrange.",
    ),
    Descriptor(
        label="lean",
        aliases=["spare"],
        category="body",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("spectral_bandwidth", 0.45),
        ],
        opposite="lush",
        description="Stripped-down body.",
    ),
    Descriptor(
        label="light",
        aliases=["airy-body", "feathery"],
        category="body",
        feature_anchors=[
            _pb("rms", 0.4),
            _pa("spectral_centroid", 0.55, weight=0.5),
        ],
        opposite="heavy",
        description="High-pass-filtered feel; little weight.",
    ),
    Descriptor(
        label="boomy",
        aliases=["boom", "bass-heavy"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.6),
            _pb("spectral_centroid", 0.35),
        ],
        opposite="thin",
        description="Excess low-frequency build-up.",
    ),
    Descriptor(
        label="bassy",
        aliases=["sub-heavy"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.55),
            _pb("spectral_centroid", 0.4),
        ],
        opposite="thin",
        description="Strong sub/low energy.",
    ),

    # =============================== PUNCH =============================
    Descriptor(
        label="snappy-punch",
        aliases=["snap", "crack"],
        category="punch",
        feature_anchors=[
            _pa("mfcc_std_0", 0.6),
            _pa("rms", 0.5),
        ],
        opposite="blunt",
        description="Crisp attack with audible transient.",
    ),
    Descriptor(
        label="transient",
        aliases=["click", "attacky"],
        category="punch",
        feature_anchors=[
            _pa("mfcc_std_0", 0.65),
            _pa("zcr", 0.5, weight=0.4),
        ],
        opposite="smooth-attack",
        description="Pronounced initial transient burst.",
    ),
    Descriptor(
        label="impactful",
        aliases=["thumping", "smacking"],
        category="punch",
        feature_anchors=[
            _pa("rms", 0.6),
            _pa("mfcc_std_0", 0.55),
        ],
        opposite="soft",
        description="Heavy hit with strong transient.",
    ),
    Descriptor(
        label="blunt",
        aliases=["dull-attack"],
        category="punch",
        feature_anchors=[
            _pb("mfcc_std_0", 0.4),
            _pb("zcr", 0.4),
        ],
        opposite="snappy-punch",
        description="No sharp attack; rounded onset.",
    ),
    Descriptor(
        label="smooth-attack",
        aliases=["soft-onset"],
        category="punch",
        feature_anchors=[
            _pb("mfcc_std_0", 0.4),
            _pb("rms", 0.5, weight=0.4),
        ],
        opposite="transient",
        description="Gradual onset, no spike.",
    ),
    Descriptor(
        label="thwacking",
        aliases=["thwack"],
        category="punch",
        feature_anchors=[
            _pa("rms", 0.6),
            _pa("mfcc_std_0", 0.55),
            _pa("zcr", 0.5, weight=0.4),
        ],
        opposite="blunt",
        description="Powerful slap — loud, fast, percussive.",
    ),

    # =============================== AIR ===============================
    Descriptor(
        label="airy",
        aliases=["open", "breathy"],
        category="air",
        feature_anchors=[
            _pa("spectral_rolloff", 0.65),
            _pa("zcr", 0.5, weight=0.5),
        ],
        opposite="boxy",
        description="Lots of energy above 8 kHz; sense of air.",
    ),
    Descriptor(
        label="open",
        aliases=["breathing"],
        category="air",
        feature_anchors=[
            _pa("spectral_rolloff", 0.6),
            _pa("spectral_bandwidth", 0.55, weight=0.5),
        ],
        opposite="closed",
        description="Wide-open top end; breathing room.",
    ),
    Descriptor(
        label="breathy",
        aliases=["whisper", "breath"],
        category="air",
        feature_anchors=[
            _pa("zcr", 0.6),
            _pa("spectral_flatness", 0.45, weight=0.5),
        ],
        opposite="closed",
        description="Audible noise component above the tone.",
    ),
    Descriptor(
        label="boxy",
        aliases=["mid-heavy", "tunnel-like"],
        category="air",
        feature_anchors=[
            _pb("spectral_rolloff", 0.4),
            _pb("spectral_bandwidth", 0.4, weight=0.5),
        ],
        opposite="airy",
        description="Concentrated midrange, no top air.",
    ),
    Descriptor(
        label="stuffy",
        aliases=["stifled"],
        category="air",
        feature_anchors=[
            _pb("spectral_rolloff", 0.35),
            _pb("zcr", 0.4),
        ],
        opposite="airy",
        description="Closed-off top; no breathing room.",
    ),
    Descriptor(
        label="ethereal",
        aliases=["floating"],
        category="air",
        feature_anchors=[
            _pa("spectral_rolloff", 0.6),
            _pa("mfcc_std_1", 0.5, weight=0.4),
        ],
        opposite="grounded",
        description="Light, high-frequency, evolving.",
    ),
    Descriptor(
        label="grounded",
        aliases=["earthy"],
        category="air",
        feature_anchors=[
            _pb("spectral_rolloff", 0.45),
            _pa("rms", 0.45, weight=0.4),
        ],
        opposite="ethereal",
        description="Anchored low-end, little top air.",
    ),
    Descriptor(
        label="hissy",
        aliases=["sibilant", "whitelike"],
        category="air",
        feature_anchors=[
            _pa("zcr", 0.65),
            _pa("spectral_flatness", 0.55),
        ],
        opposite="smooth",
        description="High-band noise, sibilant-style.",
    ),

    # ===== a few extras to round to ~100 =====
    Descriptor(
        label="harsh",
        aliases=["abrasive", "edgy"],
        category="character",
        feature_anchors=[
            _pa("zcr", 0.6),
            _pa("spectral_centroid", 0.55, weight=0.6),
            _pa("spectral_flatness", 0.45, weight=0.4),
        ],
        opposite="smooth",
        description="Dissonant high-frequency content; uncomfortable.",
    ),
    Descriptor(
        label="edgy",
        aliases=["bitey"],
        category="character",
        feature_anchors=[
            _pa("zcr", 0.55),
            _pa("spectral_centroid", 0.55),
        ],
        opposite="round",
        description="Sharp top-end emphasis.",
    ),
    Descriptor(
        label="gritty",
        aliases=["grit"],
        category="character",
        feature_anchors=[
            _pa("spectral_flatness", 0.55),
            _pa("zcr", 0.5),
        ],
        opposite="clean",
        description="Saturated/crunchy texture.",
    ),
    Descriptor(
        label="silken",
        aliases=["soft-top"],
        category="warmth",
        feature_anchors=[
            _pb("zcr", 0.35),
            _pb("spectral_flatness", 0.3),
        ],
        opposite="harsh",
        description="Smooth top end; no harshness.",
    ),
    Descriptor(
        label="muddy",
        aliases=["mushy"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.55),
            _pb("spectral_centroid", 0.35),
            _pa("spectral_flatness", 0.45, weight=0.5),
        ],
        opposite="clear",
        description="Low-mid build-up that obscures detail.",
    ),
    Descriptor(
        label="clear",
        aliases=["defined"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.55),
            _pb("spectral_flatness", 0.35),
        ],
        opposite="muddy",
        description="Distinct, well-defined components.",
    ),
    Descriptor(
        label="pristine-air",
        aliases=["clean-air"],
        category="air",
        feature_anchors=[
            _pa("spectral_rolloff", 0.6),
            _pb("spectral_flatness", 0.3),
        ],
        opposite="hissy",
        description="High-frequency air without noise.",
    ),
    Descriptor(
        label="noisy",
        aliases=["whitey", "static-like"],
        category="harmonic",
        feature_anchors=[
            _pa("spectral_flatness", 0.6),
            _pa("zcr", 0.55),
        ],
        opposite="tonal",
        description="High noise content vs. tone.",
    ),
    Descriptor(
        label="tonal",
        aliases=["pitched"],
        category="harmonic",
        feature_anchors=[
            _pb("spectral_flatness", 0.3),
            _pb("zcr", 0.4),
        ],
        opposite="noisy",
        description="Clear pitch; minimal noise.",
    ),
    Descriptor(
        label="present",
        aliases=["forward"],
        category="dynamics",
        feature_anchors=[
            _pa("rms", 0.5),
            _pa("spectral_centroid", 0.5, weight=0.5),
        ],
        opposite="recessed",
        description="Sits up front in the mix.",
    ),
    Descriptor(
        label="recessed",
        aliases=["backward", "distant"],
        category="dynamics",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("spectral_centroid", 0.4, weight=0.4),
        ],
        opposite="present",
        description="Sits behind other elements.",
    ),
    Descriptor(
        label="punchy-tight",
        aliases=["compact"],
        category="punch",
        feature_anchors=[
            _pa("mfcc_std_0", 0.55),
            _pb("mfcc_std_2", 0.45, weight=0.4),
        ],
        opposite="loose",
        description="Punch with controlled tail.",
    ),
    Descriptor(
        label="glassy",
        aliases=["icy-clear"],
        category="brightness",
        feature_anchors=[
            _pa("spectral_centroid", 0.7),
            _pb("spectral_flatness", 0.35),
        ],
        opposite="muffled",
        description="Bright + tonal + clean.",
    ),
    Descriptor(
        label="velvet",
        aliases=["plush"],
        category="warmth",
        feature_anchors=[
            _pb("spectral_centroid", 0.45),
            _pb("zcr", 0.35),
            _pa("rms", 0.4, weight=0.4),
        ],
        opposite="harsh",
        description="Plush mids with rolled-off highs.",
    ),
    Descriptor(
        label="punchy-fat",
        aliases=["fat-punch"],
        category="punch",
        feature_anchors=[
            _pa("rms", 0.6),
            _pa("mfcc_std_0", 0.55),
            _pb("spectral_centroid", 0.5, weight=0.4),
        ],
        opposite="thin",
        description="Powerful low-mid hit.",
    ),
    Descriptor(
        label="space-lush",
        aliases=["deep-reverb"],
        category="space",
        feature_anchors=[
            _pa("mfcc_std_1", 0.55),
            _pa("mfcc_std_2", 0.55),
        ],
        opposite="dry",
        description="Long evolving tail; ambient sense.",
    ),
    Descriptor(
        label="vibey",
        aliases=["mood"],
        category="character",
        feature_anchors=[
            _pa("mfcc_std_1", 0.55),
            _pa("spectral_flatness", 0.4, weight=0.5),
        ],
        opposite="sterile",
        description="Atmospheric, slightly imperfect.",
    ),
    Descriptor(
        label="big",
        aliases=["enormous", "huge"],
        category="body",
        feature_anchors=[
            _pa("rms", 0.6),
            _pa("spectral_bandwidth", 0.55),
        ],
        opposite="small",
        description="Wide and loud.",
    ),
    Descriptor(
        label="small",
        aliases=["tiny", "petite"],
        category="body",
        feature_anchors=[
            _pb("rms", 0.4),
            _pb("spectral_bandwidth", 0.4),
        ],
        opposite="big",
        description="Narrow and quiet.",
    ),
]


VOCABULARY: dict[str, Descriptor] = {d.label: d for d in _DESCRIPTORS}


def descriptors_in_category(category: str) -> list[Descriptor]:
    """Return all descriptors in the named category, in declaration order."""
    return [d for d in _DESCRIPTORS if d.category == category]


def lookup(name: str) -> Descriptor | None:
    """Look up a descriptor by label or any alias (case-insensitive)."""
    if not name:
        return None
    n = name.strip().lower()
    direct = VOCABULARY.get(n)
    if direct is not None:
        return direct
    for d in _DESCRIPTORS:
        if any(n == a.strip().lower() for a in d.aliases):
            return d
    return None
