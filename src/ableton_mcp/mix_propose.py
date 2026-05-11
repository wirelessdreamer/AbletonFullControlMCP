"""Mix proposal generator — Layer 4.1 of the mix-aware shaping stack.

Given:
- a masking-analysis result from :mod:`mix_masking` (L2.2), and
- an intent descriptor from :mod:`mix_descriptors` (L3),

produce a structured **proposal** of EQ / filter / shelf actions. This
module does NOT apply anything to Live — Layer 4.2 will do that. The
separation matters because:

1. The user can review the proposal before any state changes.
2. The proposal is easy to render in chat ("propose to cut Rhythm Gtr
   by 3 dB at 3 kHz because it masks the lead by 4.1 dB there").
3. Layer 5 (verification) can use the same proposal shape to diff
   "expected change" vs "measured change."

Action vocabulary
-----------------

Every proposed action is one of:

- ``eq_cut`` / ``eq_boost``: parametric EQ band on a specific track.
- ``high_pass``: HPF on a track at ``freq_hz``.
- ``high_shelf`` / ``low_shelf``: shelving filter.
- ``compress_attack`` (placeholder for L4.2): adjust compressor attack
  time on the focal.
- ``de_ess``: dynamic high-frequency cut (de-esser); for v1 we emit it
  as a structured intent and the apply layer maps it to whatever Live
  device it ends up using.

Each action carries a ``rationale`` string referencing the actual
numbers from the masking analysis so the user sees *why* the proposal
exists, not just *what*.

Sizing the moves
----------------

Gain magnitudes are deliberately small (2-4 dB cuts/boosts) — mixing
moves should be conservative. The competitor cut size scales with the
masker's masking score:

    gain_db = - clamp(2.0 + 4.0 * masking_score, 2.0, 6.0)

so a 0.5-score competitor gets ~-4 dB; a 0.9 competitor gets ~-5.5 dB.
Below ``MIN_COMPETITOR_SCORE`` the competitor is omitted — at 0.1 it
isn't really masking anything.

The Q values target gentle bell cuts/boosts (Q ≈ 1.2-2.0) — narrow
enough to not affect adjacent bands but wide enough to be musical.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from .mix_analysis import BandSpec, make_third_octave_bands
from .mix_descriptors import MixDescriptor, resolve_descriptor
from .mix_masking import mix_masking_at_region

log = logging.getLogger(__name__)


# Tunables — small intentional changes, not "wreck the mix" moves.
MIN_COMPETITOR_SCORE: float = 0.15
"""Below this masking score, the competitor isn't really competing —
omit it from the proposal."""

MAX_COMPETITOR_ACTIONS: int = 4
"""Cap on how many competitor cuts to emit — mix moves should be
focused; flooding the user with 12 EQ changes isn't helpful."""

DEFAULT_Q: float = 1.5
"""Default EQ Q for bell cuts/boosts — musical, neither narrow nor wide."""

BASS_FAMILY_THRESHOLD_HZ: float = 200.0
"""A track whose top energy is below this is treated as 'bass family' —
protected from high_pass_non_bass."""


@dataclass(frozen=True)
class MixAction:
    """One structured mix action.

    Attributes:
        track_index: which track to act on. None for global actions.
        kind: action class ("eq_cut", "eq_boost", "high_pass",
            "high_shelf", "low_shelf", "de_ess", "compress_attack").
        device_hint: which Live device this is meant for (Layer 4.2
            will use this to pick the device to insert or modify).
        freq_hz: filter centre / shelf knee / HPF cutoff frequency.
        q: filter Q (for bell filters; None for shelves / HPFs).
        gain_db: dB gain (positive = boost, negative = cut).
        rationale: human-readable explanation tying the action to the
            masking analysis numbers.
        extra: free-form additional fields (e.g. de-esser ratio).
    """

    track_index: int | None
    kind: str
    device_hint: str
    freq_hz: float | None = None
    q: float | None = None
    gain_db: float | None = None
    rationale: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop None fields for cleaner JSON.
        return {k: v for k, v in d.items() if v is not None and v != {}}


# ---------------------------------------------------------------------------
# Action-class implementations — each takes (masking, descriptor, bands)
# and returns a list[MixAction].
# ---------------------------------------------------------------------------


def _band_centroid(descriptor: MixDescriptor) -> float:
    """Geometric centre of the descriptor's band range — the "natural"
    place to put a bell EQ for that descriptor."""
    return math.sqrt(descriptor.band_low_hz * descriptor.band_high_hz)


def _competitor_gain_db(masking_score: float) -> float:
    """Map masking_score → cut depth. Conservative ramp."""
    return -min(6.0, max(2.0, 2.0 + 4.0 * masking_score))


def _propose_cut_competitors(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """For each top masker (by masking_score), propose an EQ cut at the
    descriptor's band centroid. Magnitude scales with masking_score."""
    centroid = _band_centroid(descriptor)
    actions: list[MixAction] = []
    for comp in masking.get("competing_tracks", []):
        score = float(comp.get("masking_score", 0.0))
        if score < MIN_COMPETITOR_SCORE:
            continue
        if len(actions) >= MAX_COMPETITOR_ACTIONS:
            break
        gain = _competitor_gain_db(score)
        # Pick the per-band entry with the largest overlap_db to
        # localise the cut where it'll do the most good.
        per_band = comp.get("per_band", [])
        if per_band:
            top_band = max(per_band, key=lambda b: b.get("overlap_db", -999))
            freq = float(top_band.get("center_hz", centroid))
            overlap_db = float(top_band.get("overlap_db", 0.0))
        else:
            freq = centroid
            overlap_db = 0.0
        rationale = (
            f"{comp.get('name', '?')} masks the focal by "
            f"{overlap_db:+.1f} dB at {freq:.0f} Hz "
            f"(masking score {score:.2f}); cutting frees the "
            f"{descriptor.name.replace('_', ' ')} band."
        )
        actions.append(MixAction(
            track_index=comp.get("track_index"),
            kind="eq_cut",
            device_hint="EQ Eight",
            freq_hz=freq,
            q=DEFAULT_Q + 0.5 * score,  # higher score → slightly tighter cut
            gain_db=gain,
            rationale=rationale,
        ))
    return actions


def _propose_cut_focal(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """One eq_cut on the focal at the descriptor's band centroid.

    Used for ``harsh`` and similar "focal has too much in this band"
    descriptors."""
    focal = masking.get("focal_track")
    centroid = _band_centroid(descriptor)
    rationale = (
        f"{masking.get('focal_name', 'focal')} reads as "
        f"{descriptor.name} in the {descriptor.band_low_hz:.0f}-"
        f"{descriptor.band_high_hz:.0f} Hz range; a gentle cut on the "
        f"focal there reduces the offending energy."
    )
    return [MixAction(
        track_index=focal,
        kind="eq_cut",
        device_hint="EQ Eight",
        freq_hz=centroid,
        q=DEFAULT_Q,
        gain_db=-3.0,
        rationale=rationale,
    )]


def _propose_boost_focal(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """One eq_boost on the focal at the descriptor's band centroid."""
    focal = masking.get("focal_track")
    centroid = _band_centroid(descriptor)
    rationale = (
        f"Boost {masking.get('focal_name', 'focal')} at "
        f"{centroid:.0f} Hz to add {descriptor.name.replace('_', ' ')}."
    )
    return [MixAction(
        track_index=focal,
        kind="eq_boost",
        device_hint="EQ Eight",
        freq_hz=centroid,
        q=DEFAULT_Q,
        gain_db=+2.5,
        rationale=rationale,
    )]


def _propose_high_pass_non_bass(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """High-pass every non-bass-family track at the descriptor's high
    edge. A track is bass-family if its top energy lives below
    ``BASS_FAMILY_THRESHOLD_HZ``.

    Note: this requires the masking dict to include each competitor's
    spectrum (or a hint about its dominant band). We fall back to
    "everyone gets the HPF" if we can't tell — Layer 4.2 will refine."""
    cutoff = descriptor.band_high_hz
    actions: list[MixAction] = []
    for comp in masking.get("competing_tracks", []):
        # Try to determine whether this is a bass-family track.
        is_bass = False
        spectrum = comp.get("spectrum")
        if spectrum is not None:
            # Find this spectrum's top band centre frequency.
            paired = list(zip(bands, spectrum))
            paired = [(b, db) for b, db in paired if db > -120.0]
            if paired:
                top = max(paired, key=lambda p: p[1])
                is_bass = top[0].center_hz < BASS_FAMILY_THRESHOLD_HZ
        if is_bass:
            continue
        rationale = (
            f"High-pass {comp.get('name', '?')} at {cutoff:.0f} Hz to "
            f"clear low-bass build-up. Bass family tracks are protected."
        )
        actions.append(MixAction(
            track_index=comp.get("track_index"),
            kind="high_pass",
            device_hint="EQ Eight",
            freq_hz=cutoff,
            rationale=rationale,
        ))
    return actions


def _propose_high_shelf_focal(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """High shelf on the focal at the descriptor's low edge — sign
    determines direction."""
    focal = masking.get("focal_track")
    knee = descriptor.band_low_hz
    gain = +2.5 if descriptor.sign > 0 else -2.5
    rationale = (
        f"{'Add' if gain > 0 else 'Reduce'} top-end on "
        f"{masking.get('focal_name', 'focal')} via a high shelf at "
        f"{knee:.0f} Hz ({descriptor.name})."
    )
    return [MixAction(
        track_index=focal,
        kind="high_shelf",
        device_hint="EQ Eight",
        freq_hz=knee,
        gain_db=gain,
        rationale=rationale,
    )]


def _propose_low_shelf_focal(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """Low shelf on the focal at the descriptor's high edge."""
    focal = masking.get("focal_track")
    knee = descriptor.band_high_hz
    gain = +2.0 if descriptor.sign > 0 else -2.0
    rationale = (
        f"{'Add' if gain > 0 else 'Reduce'} low-end body on "
        f"{masking.get('focal_name', 'focal')} via a low shelf at "
        f"{knee:.0f} Hz ({descriptor.name})."
    )
    return [MixAction(
        track_index=focal,
        kind="low_shelf",
        device_hint="EQ Eight",
        freq_hz=knee,
        gain_db=gain,
        rationale=rationale,
    )]


def _propose_de_ess_focal(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """Emit a de_ess intent for the focal in the descriptor's band.
    L4.2 maps this to either an EQ dynamic band or a dedicated de-esser
    device."""
    focal = masking.get("focal_track")
    centroid = _band_centroid(descriptor)
    rationale = (
        f"De-ess {masking.get('focal_name', 'focal')} in the "
        f"{descriptor.band_low_hz:.0f}-{descriptor.band_high_hz:.0f} Hz "
        f"range to address {descriptor.name}."
    )
    return [MixAction(
        track_index=focal,
        kind="de_ess",
        device_hint="EQ Eight (dynamic) or Dynamic Tube",
        freq_hz=centroid,
        gain_db=-4.0,
        rationale=rationale,
    )]


def _propose_compress_focal_transients(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    bands: Sequence[BandSpec],
) -> list[MixAction]:
    """Shape the focal's transients via compressor attack time."""
    focal = masking.get("focal_track")
    return [MixAction(
        track_index=focal,
        kind="compress_attack",
        device_hint="Compressor",
        rationale=(
            f"Slow the compressor attack on "
            f"{masking.get('focal_name', 'focal')} so transients pass "
            f"through ({descriptor.name})."
        ),
        extra={"attack_ms_hint": 30.0},
    )]


# Action-class → handler dispatch table.
_HANDLERS = {
    "cut_competitors": _propose_cut_competitors,
    "cut_focal": _propose_cut_focal,
    "boost_focal": _propose_boost_focal,
    "high_pass_non_bass": _propose_high_pass_non_bass,
    "high_shelf_focal": _propose_high_shelf_focal,
    "low_shelf_focal": _propose_low_shelf_focal,
    "de_ess_focal": _propose_de_ess_focal,
    "compress_focal_transients": _propose_compress_focal_transients,
}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def propose_actions(
    masking: dict[str, Any],
    descriptor: MixDescriptor,
    *,
    bands: Sequence[BandSpec] | None = None,
) -> dict[str, Any]:
    """Generate a proposal dict from a masking result + descriptor.

    Pure function: no I/O, no Live calls. Easy to unit-test against
    synthesised masking inputs.

    Returns:
        {
            "intent": descriptor.name,
            "descriptor": {name, band_low_hz, band_high_hz, sign,
                           action_class, description},
            "focal_track": int,
            "focal_name": str,
            "actions": list[MixAction]   (sorted by track for stability)
        }
    """
    if bands is None:
        bands = make_third_octave_bands()
    handler = _HANDLERS.get(descriptor.action_class)
    if handler is None:
        log.warning(
            "no handler for action_class=%s; emitting empty proposal",
            descriptor.action_class,
        )
        actions: list[MixAction] = []
    else:
        actions = handler(masking, descriptor, bands)

    return {
        "intent": descriptor.name,
        "descriptor": {
            "name": descriptor.name,
            "band_low_hz": descriptor.band_low_hz,
            "band_high_hz": descriptor.band_high_hz,
            "sign": descriptor.sign,
            "action_class": descriptor.action_class,
            "description": descriptor.description,
        },
        "focal_track": masking.get("focal_track"),
        "focal_name": masking.get("focal_name"),
        "actions": actions,
    }


async def mix_propose_at_region(
    focal_track_index: int,
    intent: str,
    start_beats: float,
    end_beats: float,
    *,
    output_dir: str | None = None,
    target_sr: int = 22050,
    warmup_sec: float = 0.0,
) -> dict[str, Any]:
    """End-to-end: bounce + analyze + mask + propose, all from a focal
    track + intent string.

    The function validates the intent up-front (resolving the
    descriptor) BEFORE running the expensive bounce, so a typo doesn't
    waste a full bounce pass.

    Args:
        focal_track_index: index of the focal track.
        intent: free-form intent word, resolved via mix_descriptors.
            Accepts any canonical name or alias (case/whitespace-tolerant).
        start_beats / end_beats: region.
        output_dir / target_sr / warmup_sec: pass-through to L2.x.

    Returns:
        The same shape as :func:`propose_actions`, plus ``region`` and
        ``masking_summary`` (a slim summary of the masking analysis so
        the caller doesn't have to re-run it for context).

    Raises:
        KeyError: if ``intent`` doesn't resolve to a known descriptor.
        ValueError: if ``focal_track_index`` isn't analyzed.
    """
    # Validate intent BEFORE bouncing — fail fast.
    descriptor = resolve_descriptor(intent)

    masking_result = await mix_masking_at_region(
        focal_track_index=focal_track_index,
        start_beats=start_beats, end_beats=end_beats,
        output_dir=output_dir, target_sr=target_sr, warmup_sec=warmup_sec,
    )

    proposal = propose_actions(masking_result, descriptor)
    # Convert MixAction → dict for serialization at the boundary.
    proposal["actions"] = [a.to_dict() for a in proposal["actions"]]
    proposal["region"] = masking_result.get("region")
    proposal["masking_summary"] = {
        "focal_money_bands": masking_result.get("focal_money_bands"),
        "top_competitors": [
            {"track_index": c["track_index"], "name": c["name"],
             "masking_score": c["masking_score"]}
            for c in masking_result.get("competing_tracks", [])[:5]
        ],
        "skipped_tracks": masking_result.get("skipped_tracks", []),
    }
    return proposal
