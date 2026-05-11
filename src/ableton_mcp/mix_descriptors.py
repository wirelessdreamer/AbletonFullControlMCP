"""Mix-vocabulary descriptors — Layer 3 of the mix-aware shaping stack.

A *descriptor* is a user-facing word ("cuts through", "muddy",
"buried", "boomy", ...) mapped to two things:

1. The frequency range it lives in.
2. The class of mix action that moves the sound toward (or away from)
   it.

This is the lookup table Layer 4 (``mix_propose``) consumes. It is
deliberately data-only — no DSP, no Live calls — so it's trivial to
extend and lint.

The two-layer separation matters: Layer 2 tells us *what's happening*
in the mix (per-track spectrum + masking score). Layer 3 tells us *what
the user means* by a word. Layer 4 puts them together.

Sign convention
---------------

``sign = +1`` means *the user wants more energy in this band* for the
focal track. ``sign = -1`` means *the user wants less*. The action
class disambiguates whether to act on the focal or the competitors:

- ``boost_focal``: raise focal's energy in the band.
- ``cut_focal``: drop focal's energy in the band.
- ``cut_competitors``: drop other tracks' energy in the band (most
  common — "cut through" is usually achieved by clearing space rather
  than pushing the focal harder).
- ``boost_competitors`` (rare; mainly here for sign-symmetry).
- ``high_pass_non_bass``: filter low-end off everything except the bass
  family of tracks (kick, bass).
- ``de_ess_focal``: dynamic high-frequency cut on the focal.
- ``high_shelf_focal`` / ``low_shelf_focal``: static shelves.
- ``compress_focal_transients``: shape attack/transient via compressor
  attack time.

These map to concrete device-parameter changes in Layer 4.

Aliases
-------

Many descriptors have natural-language synonyms ("lost in mix" =
"buried", "present" = "cuts through"). The ``aliases`` tuple captures
these. ``resolve_descriptor`` normalises whitespace and case before
matching, so "Cut Through" → "cuts_through" works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from .mix_analysis import BandSpec


@dataclass(frozen=True)
class MixDescriptor:
    """One descriptor — word + band + action class.

    Attributes:
        name: canonical snake_case identifier ("cuts_through").
        aliases: tuple of normalised synonyms ("present", "cuts through").
            Each is matched after normalising whitespace + case.
        band_low_hz / band_high_hz: frequency window the descriptor lives in.
        sign: +1 if the user wants MORE energy in the band for the focal,
            -1 if LESS. Combined with ``action_class`` to decide direction.
        action_class: one of the known classes (see module docstring).
        description: short human-readable explanation — surfaced to the
            LLM so it can choose between similar descriptors.
    """

    name: str
    aliases: tuple[str, ...]
    band_low_hz: float
    band_high_hz: float
    sign: int
    action_class: str
    description: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def _norm(s: str) -> str:
    """Normalise an input string for descriptor lookup. Lower-cases,
    strips whitespace, collapses internal whitespace + hyphens to
    underscores. ``"Cut Through"`` → ``"cut_through"``."""
    s = s.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return s


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_DESCRIPTOR_LIST: list[MixDescriptor] = [
    MixDescriptor(
        name="cuts_through",
        aliases=("present", "cut_through", "presence", "forward"),
        band_low_hz=2000.0, band_high_hz=5000.0,
        sign=+1, action_class="cut_competitors",
        description=(
            "Focal sits clearly in the presence band (2-5 kHz). "
            "Usually achieved by cutting competing tracks there, not "
            "by pushing the focal harder."
        ),
        notes=("primary fix: 2-3 dB cut at ~3 kHz on the loudest "
               "competitor in that band",),
    ),
    MixDescriptor(
        name="buried",
        aliases=("lost_in_mix", "drowning", "hidden", "obscured"),
        band_low_hz=2000.0, band_high_hz=5000.0,
        sign=+1, action_class="cut_competitors",
        description=(
            "Inverse of cuts_through. Focal is masked by competitors "
            "in the presence band. Same fix direction: clear the band "
            "on the offenders."
        ),
    ),
    MixDescriptor(
        name="muddy",
        aliases=("mud", "thick_lowmid", "woolly"),
        band_low_hz=200.0, band_high_hz=400.0,
        sign=-1, action_class="cut_competitors",
        description=(
            "Too much energy in the low-mid band (200-400 Hz) across "
            "multiple tracks. Cut 2-3 dB at 250 Hz on the offenders."
        ),
    ),
    MixDescriptor(
        name="boomy",
        aliases=("booming", "low_heavy", "subby"),
        band_low_hz=60.0, band_high_hz=120.0,
        sign=-1, action_class="high_pass_non_bass",
        description=(
            "Too much low-bass energy on tracks that aren't kick or "
            "bass. High-pass everything else at 80-120 Hz to clear "
            "the low end."
        ),
    ),
    MixDescriptor(
        name="honky",
        aliases=("nasal", "midrangey"),
        band_low_hz=400.0, band_high_hz=800.0,
        sign=-1, action_class="cut_competitors",
        description=(
            "Boxy mid-frequency build-up around 500 Hz. Cut 2-3 dB at "
            "500-700 Hz on the loudest offender."
        ),
    ),
    MixDescriptor(
        name="boxy",
        aliases=("cardboard",),
        band_low_hz=400.0, band_high_hz=800.0,
        sign=-1, action_class="cut_competitors",
        description=(
            "Cousin of honky — same band, slightly more about resonant "
            "build-up than nasal tone. Same fix.",
        ),
    ),
    MixDescriptor(
        name="harsh",
        aliases=("brittle", "edgy", "abrasive"),
        band_low_hz=2000.0, band_high_hz=5000.0,
        sign=-1, action_class="cut_focal",
        description=(
            "Focal is too loud in the upper presence band, fatiguing. "
            "Cut 2-3 dB at 3-4 kHz on the focal."
        ),
    ),
    MixDescriptor(
        name="sibilant",
        aliases=("essy", "hissy"),
        band_low_hz=5000.0, band_high_hz=9000.0,
        sign=-1, action_class="de_ess_focal",
        description=(
            "Vocal sibilance / cymbal hiss. De-ess the focal in 6-9 kHz "
            "with a dynamic cut rather than a static EQ."
        ),
    ),
    MixDescriptor(
        name="airy",
        aliases=("open", "sparkly", "shimmery"),
        band_low_hz=10000.0, band_high_hz=16000.0,
        sign=+1, action_class="high_shelf_focal",
        description=(
            "Top-end clarity. High shelf +2-3 dB above 10 kHz on the "
            "focal."
        ),
    ),
    MixDescriptor(
        name="punchy",
        aliases=("snappy", "transient_forward"),
        band_low_hz=80.0, band_high_hz=4000.0,
        sign=+1, action_class="compress_focal_transients",
        description=(
            "Strong attack / transient prominence. Adjust focal "
            "compressor attack to ~30-50 ms so transients pass through, "
            "or boost ~3-5 kHz to bring out stick/pick attack."
        ),
    ),
    MixDescriptor(
        name="thick",
        aliases=("full_bodied", "weighty"),
        band_low_hz=80.0, band_high_hz=300.0,
        sign=+1, action_class="low_shelf_focal",
        description=(
            "More low-mid body. Low shelf +2 dB below 200 Hz on the "
            "focal (use sparingly — easy to cause muddiness)."
        ),
    ),
    MixDescriptor(
        name="thin",
        aliases=("weak", "scrawny", "hollow"),
        band_low_hz=80.0, band_high_hz=300.0,
        sign=+1, action_class="low_shelf_focal",
        description=(
            "Inverse of thin: focal lacks low-mid body. Same fix as "
            "thick — add it. (Descriptor present so the user can ask "
            "for it that way.)"
        ),
    ),
]


# Lookup keyed by canonical name AND each alias (post-normalisation).
DESCRIPTORS: dict[str, MixDescriptor] = {d.name: d for d in _DESCRIPTOR_LIST}
_ALIAS_INDEX: dict[str, str] = {}
for _d in _DESCRIPTOR_LIST:
    for _a in _d.aliases:
        _ALIAS_INDEX[_norm(_a)] = _d.name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_descriptors() -> list[MixDescriptor]:
    """Return all registered descriptors (stable order)."""
    return list(_DESCRIPTOR_LIST)


def resolve_descriptor(name: str) -> MixDescriptor:
    """Look up a descriptor by canonical name or any registered alias.

    Normalisation: case-folded, whitespace + hyphens collapsed to
    underscores. ``"Cut Through"`` and ``"cut-through"`` both resolve
    to ``"cuts_through"``.

    Raises:
        KeyError: if no descriptor (canonical or alias) matches.
    """
    key = _norm(name)
    if key in DESCRIPTORS:
        return DESCRIPTORS[key]
    if key in _ALIAS_INDEX:
        return DESCRIPTORS[_ALIAS_INDEX[key]]
    raise KeyError(f"unknown mix descriptor: {name!r}")


def bands_in_descriptor_range(
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[int]:
    """Return the indices of ``bands`` whose centre frequencies fall
    inside the descriptor's band range.

    Used by Layer 4 to figure out which third-octave bands the
    descriptor "covers" in the spectrum result from Layer 2.
    """
    return [
        i for i, b in enumerate(bands)
        if descriptor.band_low_hz <= b.center_hz <= descriptor.band_high_hz
    ]
