"""Mix-engineering knowledge base — money bands per instrument family
+ canonical "standard fixes."

This is a *data file* rather than a tool. It codifies the kind of
information a working mix engineer keeps in their head:

- Where each instrument family lives in the spectrum (body band +
  presence/attack band).
- Stock fixes mix engineers reach for first ("HPF everything except
  bass and kick", "carve a 3 dB dip in the focal's presence band on
  every other track", etc).

Downstream layers read from here:

- ``classify_track_by_name(name)``: a track named "Bass Gtr" returns
  the bass entry. Most-specific keywords win (so "Bass Drum" classifies
  as kick, not bass). Substring + word-boundary matching.

- ``classify_track_by_spectrum(spectrum, bands)``: best-effort fallback
  when the name doesn't classify. Looks at where the bulk of the
  spectral energy lives.

Both classifiers may return None — the caller decides what to do with
"unknown."

Money-band numbers come from the plan in
``docs/MIX_AWARE_SHAPING.md``. Treat them as starting points, not
gospel — every mix is different. The point is having a defensible
default for proposals + verification.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from .mix_analysis import BandSpec, DB_FLOOR


@dataclass(frozen=True)
class InstrumentMoneyBands:
    """Money-band data for one instrument family.

    Attributes:
        name: canonical snake_case identifier.
        aliases: tuple of normalised name fragments. Each is matched
            (whole-word, case-insensitive) against the track name.
        body_low_hz / body_high_hz: the "body" / "weight" band. May be
            None for instruments without a fundamental body (e.g.
            hi-hat).
        presence_low_hz / presence_high_hz: the "attack" / "presence"
            band — typically where the instrument announces itself in
            the mix.
        notes: short LLM-readable rationale.
    """

    name: str
    aliases: tuple[str, ...]
    presence_low_hz: float
    presence_high_hz: float
    body_low_hz: float | None = None
    body_high_hz: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class StandardFix:
    """A canonical mix move keyed by a short name. Used as the
    vocabulary for "default first-pass cleanups" that don't need an
    intent descriptor."""

    name: str
    description: str
    action_class: str
    extra: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Money-band registry
# ---------------------------------------------------------------------------


# Ordered list; classifier scans most-specific aliases first so
# multi-word terms ("Bass Drum", "Lead Guitar") win over single words.
_INSTRUMENT_LIST: list[InstrumentMoneyBands] = [
    InstrumentMoneyBands(
        name="kick",
        aliases=("kick_drum", "bass_drum", "kick", "bd"),
        body_low_hz=60.0, body_high_hz=80.0,
        presence_low_hz=2000.0, presence_high_hz=5000.0,
        notes="Body = thump (60-80 Hz fundamental). Presence = beater "
              "click (~3 kHz). 200-300 Hz often muddies — cut there if "
              "the kick fights the bass.",
    ),
    InstrumentMoneyBands(
        name="bass",
        aliases=("sub_bass", "bass_guitar", "bass_gtr", "bass"),
        body_low_hz=60.0, body_high_hz=100.0,
        presence_low_hz=700.0, presence_high_hz=2000.0,
        notes="Body = fundamental (60-100 Hz). Presence = pick/finger "
              "attack (700 Hz-2 kHz). Sidechain to kick for tight low end.",
    ),
    InstrumentMoneyBands(
        name="snare",
        aliases=("snare_drum", "snare", "sd", "sn"),
        body_low_hz=150.0, body_high_hz=250.0,
        presence_low_hz=4000.0, presence_high_hz=6000.0,
        notes="Body = the 'thwack' around 200 Hz. Presence = 'crack' "
              "around 5 kHz. 400-600 Hz can sound boxy.",
    ),
    InstrumentMoneyBands(
        name="hihat",
        aliases=("hi_hat", "hihat", "hat", "hh", "hats"),
        # No body band — hi-hats are pure top end.
        body_low_hz=None, body_high_hz=None,
        presence_low_hz=8000.0, presence_high_hz=12000.0,
        notes="Pure top-end instrument. High-pass aggressively (>200 Hz) "
              "to keep the kit's low end clean.",
    ),
    InstrumentMoneyBands(
        name="lead_vocal",
        aliases=("lead_vocal", "lead_vox", "vocal", "vocals", "vox", "singer"),
        body_low_hz=200.0, body_high_hz=300.0,
        presence_low_hz=2000.0, presence_high_hz=4000.0,
        notes="Body = chest resonance (200-300 Hz). Presence = "
              "intelligibility band (2-4 kHz). De-ess 6-9 kHz.",
    ),
    InstrumentMoneyBands(
        name="lead_guitar",
        aliases=(
            "lead_guitar", "lead_gtr", "solo_guitar", "solo_gtr",
            "guitar_solo", "lead_axe",
        ),
        body_low_hz=150.0, body_high_hz=400.0,
        presence_low_hz=1000.0, presence_high_hz=4000.0,
        notes="Body around 200-400 Hz. Presence wider than rhythm "
              "(1-4 kHz). For solos to cut, dip 2-3 kHz on rhythm gtr.",
    ),
    InstrumentMoneyBands(
        name="rhythm_guitar",
        aliases=(
            "rhythm_guitar", "rhythm_gtr", "rh_gtr", "guitar", "gtr",
            "electric_guitar", "acoustic_guitar",
        ),
        body_low_hz=100.0, body_high_hz=400.0,
        presence_low_hz=2000.0, presence_high_hz=5000.0,
        notes="Body 100-400 Hz; presence 2-5 kHz (often overlaps the "
              "vocal — carve a 3 dB dip at 3 kHz on rhythm to clear "
              "the lead).",
    ),
    InstrumentMoneyBands(
        name="piano",
        aliases=("piano", "keys", "rhodes", "wurli", "wurlitzer", "epiano"),
        body_low_hz=80.0, body_high_hz=1000.0,
        presence_low_hz=2000.0, presence_high_hz=5000.0,
        notes="Body is wide (80 Hz-1 kHz spread). Presence around "
              "2-5 kHz overlaps vocal. Often benefits from a 200-300 Hz "
              "dip to reduce muddiness.",
    ),
]


INSTRUMENT_MONEY_BANDS: dict[str, InstrumentMoneyBands] = {
    i.name: i for i in _INSTRUMENT_LIST
}


# ---------------------------------------------------------------------------
# Standard fixes (canonical mix moves)
# ---------------------------------------------------------------------------


STANDARD_FIXES: list[StandardFix] = [
    StandardFix(
        name="high_pass_non_bass",
        description=(
            "High-pass every track except bass/kick at 80-120 Hz to "
            "clear sub-bass rumble. The single most common mix cleanup."
        ),
        action_class="high_pass_non_bass",
        extra={"cutoff_hz": 100.0},
    ),
    StandardFix(
        name="carve_focal_presence",
        description=(
            "Cut 2-4 dB at the focal's presence band centre on every "
            "competing track. Creates space for the focal without "
            "boosting it (no extra gain stage)."
        ),
        action_class="cut_competitors",
        extra={"depth_db": -3.0},
    ),
    StandardFix(
        name="sidechain_to_kick",
        description=(
            "Sidechain bass / keys / pads to the kick so the low end "
            "ducks ~3 dB on every kick hit. Tightens the rhythm bed."
        ),
        action_class="compress_sidechain",
        extra={"source": "kick", "ducking_db": -3.0},
    ),
    StandardFix(
        name="dip_low_mids_on_pads",
        description=(
            "Dip 2-3 dB at 250-350 Hz on pad / synth / keys tracks to "
            "reduce low-mid build-up ('muddy' mix)."
        ),
        action_class="cut_competitors",
        extra={"freq_hz": 300.0, "depth_db": -3.0},
    ),
]


# ---------------------------------------------------------------------------
# Name-based classifier
# ---------------------------------------------------------------------------


def _norm_for_match(name: str) -> str:
    """Lower, replace separators with spaces — gives us word boundaries
    we can grep with ``\\b``."""
    s = name.lower()
    s = re.sub(r"[\s\-_./()\[\]]+", " ", s)
    return s.strip()


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Build a whole-word regex for one alias, treating underscores as
    optional whitespace. ``"lead_guitar"`` matches ``"lead guitar"``,
    ``"lead gtr"`` (after norm), or ``"leadguitar"``."""
    tokens = alias.split("_")
    pat = r"\b" + r"[\s_]*".join(re.escape(t) for t in tokens) + r"\b"
    return re.compile(pat, re.IGNORECASE)


# Pre-compile alias patterns. Order matters: scan most-specific first.
_ALIAS_PATTERNS: list[tuple[re.Pattern[str], InstrumentMoneyBands]] = []
for _inst in _INSTRUMENT_LIST:
    # Sort aliases longest-first so multi-word matches win over single words.
    for _alias in sorted(_inst.aliases, key=len, reverse=True):
        _ALIAS_PATTERNS.append((_alias_pattern(_alias), _inst))


def classify_track_by_name(track_name: str) -> InstrumentMoneyBands | None:
    """Classify a track based on its name. Most-specific alias wins.

    Returns the :class:`InstrumentMoneyBands` entry or None if no alias
    matches.
    """
    if not track_name:
        return None
    norm = _norm_for_match(track_name)
    for pat, inst in _ALIAS_PATTERNS:
        if pat.search(norm):
            return inst
    return None


# ---------------------------------------------------------------------------
# Spectrum-based classifier (fallback)
# ---------------------------------------------------------------------------


# Rough mapping: where each instrument family's loudest band typically
# sits. Used by the spectrum classifier as a "nearest peak" heuristic.
_SPECTRUM_PEAK_HZ: dict[str, float] = {
    "kick": 70.0,
    "bass": 100.0,
    "snare": 200.0,
    "hihat": 10000.0,
    "lead_vocal": 800.0,
    "lead_guitar": 1500.0,
    "rhythm_guitar": 1500.0,
    "piano": 500.0,
}


def classify_track_by_spectrum(
    energy_db_per_band: Sequence[float],
    bands: Sequence[BandSpec],
    *,
    min_db_above_floor: float = 20.0,
) -> InstrumentMoneyBands | None:
    """Best-effort spectrum-based classifier.

    Strategy: find the band with the most energy in the focal spectrum.
    Map its centre frequency to the nearest instrument family in
    ``_SPECTRUM_PEAK_HZ`` (log2 distance — frequencies are perceptually
    logarithmic).

    Returns None if the spectrum is essentially silent.
    """
    if not energy_db_per_band or not bands:
        return None
    floor_cutoff = DB_FLOOR + min_db_above_floor
    above = [
        (i, e) for i, e in enumerate(energy_db_per_band) if e > floor_cutoff
    ]
    if not above:
        return None
    # Find loudest band.
    loudest_idx, _ = max(above, key=lambda t: t[1])
    peak_hz = bands[loudest_idx].center_hz

    best_name: str | None = None
    best_distance = float("inf")
    for name, ref_hz in _SPECTRUM_PEAK_HZ.items():
        d = abs(math.log2(peak_hz / ref_hz))
        if d < best_distance:
            best_distance = d
            best_name = name
    if best_name is None:
        return None
    return INSTRUMENT_MONEY_BANDS[best_name]


# ---------------------------------------------------------------------------
# Utility: money-band tuples for a known instrument
# ---------------------------------------------------------------------------


def money_bands_for_instrument(
    instrument_name: str,
) -> list[tuple[float, float, str]]:
    """Return the (low_hz, high_hz, role) tuples for an instrument.

    ``role`` is one of ``"body"`` / ``"presence"``. Instruments without
    a body band (e.g. hi-hat) only return the presence tuple.

    Raises:
        KeyError: if the instrument isn't in the registry.
    """
    inst = INSTRUMENT_MONEY_BANDS[instrument_name]
    out: list[tuple[float, float, str]] = []
    if inst.body_low_hz is not None and inst.body_high_hz is not None:
        out.append((inst.body_low_hz, inst.body_high_hz, "body"))
    out.append((inst.presence_low_hz, inst.presence_high_hz, "presence"))
    return out
