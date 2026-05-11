"""Intent-verification — Layer 5 of the mix-aware shaping stack.

After ``mix_apply`` (L4.2) pushes a proposal to Live, this module
answers *"did the intent actually happen?"* It re-bounces the region,
recomputes the same masking analysis, and diffs the before/after.

The output is the natural-language answer the LLM passes to the user:

    "The lead's energy in 2-5 kHz increased by 4.6 dB and the rhythm
    guitar's energy there dropped by 3.1 dB — measurable cut-through
    improvement."

Workflow::

    snapshot = await mix_snapshot_for_verification(...)   # BEFORE apply
    await mix_apply_proposal(proposal)                    # ... apply ...
    result = await mix_verify_intent(
        focal_track_index=...,
        intent="cuts_through",
        start_beats=..., end_beats=...,
        baseline_snapshot=snapshot,                       # for diff
    )
    # result["intent_achieved"] -> bool
    # result["summary"]         -> natural-language summary
    # result["per_competitor_diffs"] -> structured numbers

The pure ``diff_masking`` function is testable against synthesized
masking dicts — no bounce, no Live. The async wrapper bounces once for
the after-snapshot (twice if no baseline was passed — but in that case
the diff is degenerate).

Intent-success rules
--------------------

Different descriptors win in different ways. The diff function reads
``descriptor.action_class`` and ``descriptor.sign`` to decide what to
test:

- ``cut_competitors`` (cuts_through, buried, muddy, honky, boxy):
  competitors' energy in the descriptor's band must DECREASE.
- ``cut_focal`` (harsh): focal energy in the descriptor's band must
  DECREASE.
- ``boost_focal`` / ``high_shelf_focal`` (sign +1, airy, thick, thin):
  focal energy in the descriptor's band must INCREASE.
- ``low_shelf_focal`` (sign +1): focal energy in descriptor's band
  must INCREASE (same direction).
- ``high_pass_non_bass`` (boomy): non-bass competitors' low-end energy
  must DECREASE.
- ``de_ess_focal`` / ``compress_focal_transients``: not applied in v1
  via the EQ path; verification falls back to "did the focal's energy
  in the band move in the descriptor's preferred direction?".

The thresholds for "achieved" are conservative — a 0.5 dB drift either
way is noise, not signal. A clear change is at least ``MIN_DELTA_DB``
(default 1.5 dB) in the correct direction.
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

from .mix_descriptors import MixDescriptor, resolve_descriptor
from .mix_masking import mix_masking_at_region

log = logging.getLogger(__name__)


MIN_DELTA_DB: float = 1.5
"""Below this, an energy delta is ambient drift, not the intended effect."""


# Action classes where success = "focal band energy goes UP."
_INCREASE_FOCAL_CLASSES: set[str] = {
    "boost_focal", "high_shelf_focal", "low_shelf_focal",
}

# Action classes where success = "focal band energy goes DOWN."
_DECREASE_FOCAL_CLASSES: set[str] = {
    "cut_focal", "de_ess_focal",
}

# Action classes where success = "competitors' band energy goes DOWN."
_DECREASE_COMPETITORS_CLASSES: set[str] = {
    "cut_competitors", "high_pass_non_bass",
}


# ---------------------------------------------------------------------------
# Pure-data: diff two masking snapshots
# ---------------------------------------------------------------------------


def _band_energies_in_range(
    money_bands: list[dict[str, Any]],
    low_hz: float, high_hz: float,
) -> list[float]:
    """Pluck the ``energy_db`` values from the money-band list whose
    centre frequency falls in ``[low_hz, high_hz]``."""
    return [
        float(b["energy_db"])
        for b in money_bands
        if low_hz <= float(b["center_hz"]) <= high_hz
    ]


def _avg_focal_band_db(
    masking: dict[str, Any], descriptor: MixDescriptor,
) -> float | None:
    """Average focal energy across the descriptor's band range. Returns
    None when the focal has no money bands in that range."""
    energies = _band_energies_in_range(
        masking.get("focal_money_bands", []),
        descriptor.band_low_hz, descriptor.band_high_hz,
    )
    if not energies:
        return None
    return statistics.fmean(energies)


def _competitor_band_db(
    comp: dict[str, Any], descriptor: MixDescriptor,
) -> float | None:
    """Average ``other_energy_db`` across the descriptor's band range
    for one competitor's per_band entries."""
    energies = [
        float(b["other_energy_db"])
        for b in comp.get("per_band", [])
        if descriptor.band_low_hz <= float(b["center_hz"]) <= descriptor.band_high_hz
    ]
    if not energies:
        return None
    return statistics.fmean(energies)


def diff_masking(
    baseline: dict[str, Any],
    after: dict[str, Any],
    descriptor: MixDescriptor,
    *,
    min_delta_db: float = MIN_DELTA_DB,
) -> dict[str, Any]:
    """Diff two masking-analysis results in the context of an intent.

    Args:
        baseline: snapshot taken BEFORE ``mix_apply``.
        after: snapshot taken AFTER ``mix_apply``.
        descriptor: the intent descriptor (from :func:`resolve_descriptor`).
        min_delta_db: minimum dB delta to count as a real change in the
            correct direction; smaller changes are treated as drift.

    Returns:
        Dict with:
        - ``intent``: descriptor.name
        - ``focal_track``: int
        - ``focal_band_energy_delta_db``: float | None
        - ``average_competitor_band_energy_delta_db``: float | None
        - ``per_competitor_diffs``: list of per-competitor diffs
        - ``intent_achieved``: bool
        - ``regressed``: bool (True if the change went the wrong way)
        - ``summary``: short natural-language string
    """
    focal_baseline = _avg_focal_band_db(baseline, descriptor)
    focal_after = _avg_focal_band_db(after, descriptor)
    focal_delta = (
        (focal_after - focal_baseline)
        if focal_baseline is not None and focal_after is not None
        else None
    )

    # Per-competitor diffs.
    by_index_after = {
        c["track_index"]: c for c in after.get("competing_tracks", [])
    }
    per_comp_diffs: list[dict[str, Any]] = []
    competitor_band_deltas: list[float] = []
    for c_before in baseline.get("competing_tracks", []):
        ti = c_before["track_index"]
        c_after = by_index_after.get(ti)
        if c_after is None:
            per_comp_diffs.append({
                "track_index": ti,
                "name": c_before.get("name"),
                "removed": True,
            })
            continue
        band_before = _competitor_band_db(c_before, descriptor)
        band_after = _competitor_band_db(c_after, descriptor)
        band_delta = (
            (band_after - band_before)
            if band_before is not None and band_after is not None
            else None
        )
        if band_delta is not None:
            competitor_band_deltas.append(band_delta)
        per_comp_diffs.append({
            "track_index": ti,
            "name": c_before.get("name"),
            "masking_score_delta": float(
                c_after.get("masking_score", 0.0)
                - c_before.get("masking_score", 0.0)
            ),
            "band_energy_baseline_db": band_before,
            "band_energy_after_db": band_after,
            "band_energy_delta_db": band_delta,
        })

    avg_comp_delta = (
        statistics.fmean(competitor_band_deltas)
        if competitor_band_deltas else None
    )

    # Decide intent_achieved + regressed based on action_class.
    achieved, regressed = _evaluate_intent(
        descriptor, focal_delta, avg_comp_delta, min_delta_db,
    )

    summary = _build_summary(
        descriptor, focal_delta, avg_comp_delta, achieved, regressed,
    )

    return {
        "intent": descriptor.name,
        "focal_track": baseline.get("focal_track"),
        "focal_band_energy_delta_db": focal_delta,
        "average_competitor_band_energy_delta_db": avg_comp_delta,
        "per_competitor_diffs": per_comp_diffs,
        "intent_achieved": achieved,
        "regressed": regressed,
        "summary": summary,
    }


def _evaluate_intent(
    descriptor: MixDescriptor,
    focal_delta: float | None,
    avg_comp_delta: float | None,
    min_delta_db: float,
) -> tuple[bool, bool]:
    """Decide (intent_achieved, regressed) from the deltas + descriptor."""
    ac = descriptor.action_class
    achieved = False
    regressed = False

    if ac in _INCREASE_FOCAL_CLASSES:
        if focal_delta is None:
            achieved = False
        elif focal_delta >= min_delta_db:
            achieved = True
        elif focal_delta <= -min_delta_db:
            regressed = True

    elif ac in _DECREASE_FOCAL_CLASSES:
        if focal_delta is None:
            achieved = False
        elif focal_delta <= -min_delta_db:
            achieved = True
        elif focal_delta >= min_delta_db:
            regressed = True

    elif ac in _DECREASE_COMPETITORS_CLASSES:
        if avg_comp_delta is None:
            achieved = False
        elif avg_comp_delta <= -min_delta_db:
            achieved = True
        elif avg_comp_delta >= min_delta_db:
            regressed = True

    else:
        # Unknown action class — fall back to "did the focal move in
        # the descriptor's sign direction?"
        if focal_delta is not None:
            if descriptor.sign > 0 and focal_delta >= min_delta_db:
                achieved = True
            elif descriptor.sign < 0 and focal_delta <= -min_delta_db:
                achieved = True

    return achieved, regressed


def _build_summary(
    descriptor: MixDescriptor,
    focal_delta: float | None,
    avg_comp_delta: float | None,
    achieved: bool,
    regressed: bool,
) -> str:
    """Build a short natural-language summary of the result."""
    bits: list[str] = []
    bits.append(
        f"intent={descriptor.name};"
    )
    if focal_delta is not None:
        bits.append(f"focal band Δ {focal_delta:+.1f} dB;")
    if avg_comp_delta is not None:
        bits.append(f"avg competitor band Δ {avg_comp_delta:+.1f} dB;")
    bits.append(
        "intent ACHIEVED" if achieved else
        ("REGRESSED — change went the wrong way" if regressed else
         "no clear change")
    )
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def mix_snapshot_for_verification(
    focal_track_index: int,
    start_beats: float,
    end_beats: float,
    **kwargs: Any,
) -> dict[str, Any]:
    """Take a baseline snapshot for later verification.

    Thin wrapper around :func:`mix_masking_at_region` — exists so the
    workflow reads naturally:

        baseline = await mix_snapshot_for_verification(...)
        await mix_apply_proposal(...)
        result = await mix_verify_intent(..., baseline_snapshot=baseline)
    """
    return await mix_masking_at_region(
        focal_track_index=focal_track_index,
        start_beats=start_beats, end_beats=end_beats,
        **kwargs,
    )


async def mix_verify_intent(
    focal_track_index: int,
    intent: str,
    start_beats: float,
    end_beats: float,
    *,
    baseline_snapshot: dict[str, Any] | None = None,
    output_dir: str | None = None,
    target_sr: int = 22050,
    warmup_sec: float = 0.0,
) -> dict[str, Any]:
    """Verify whether a mix intent was achieved.

    Args:
        focal_track_index: the focal track we tried to shape.
        intent: the descriptor word (validated up-front; KeyError if
            unknown — fail-fast before the expensive bounce).
        start_beats / end_beats: the region.
        baseline_snapshot: result of ``mix_snapshot_for_verification``
            taken BEFORE the ``mix_apply`` call. Without it, we can
            only return the after-snapshot — no diff is possible.

    Returns:
        - With a baseline: the dict from :func:`diff_masking` + region
          metadata.
        - Without a baseline: ``{"baseline_missing": True,
          "after_snapshot": <masking dict>}`` so the caller can record
          it for next time.

    Raises:
        KeyError: if ``intent`` doesn't resolve to a descriptor.
    """
    # Fail fast on bad intent — before bouncing.
    descriptor = resolve_descriptor(intent)

    after = await mix_masking_at_region(
        focal_track_index=focal_track_index,
        start_beats=start_beats, end_beats=end_beats,
        output_dir=output_dir, target_sr=target_sr, warmup_sec=warmup_sec,
    )

    if baseline_snapshot is None:
        return {
            "baseline_missing": True,
            "intent": descriptor.name,
            "after_snapshot": after,
            "note": (
                "No baseline_snapshot supplied; can't diff. Pass the "
                "result of mix_snapshot_for_verification taken BEFORE "
                "mix_apply_proposal to enable diff."
            ),
        }

    result = diff_masking(baseline_snapshot, after, descriptor)
    result["region"] = after.get("region")
    result["after_snapshot"] = after
    return result
