"""Mix masking analysis — Layer 2.2 of the mix-aware shaping stack.

Given the per-track third-octave spectra from :mod:`mix_analysis`, figure
out which non-focal tracks are *masking* the focal track in its
high-energy ("money") bands. This is the diagnostic that turns a vague
"the lead doesn't cut through" into a concrete "rhythm guitar is +2 dB
louder than the lead at 2.5 kHz — cut it by 3 dB there."

Layered design:

1. **Pure-data core.** Takes precomputed spectra (lists of dB-per-band).
   Functions are deterministic and unit-testable against synthetic
   spectra — no Live, no audio, no I/O.

2. **Async wrapper.** :func:`mix_masking_at_region` runs the L2.1 bounce
   + spectrum step then feeds the result through the pure-data core.

The masking score uses a simple heuristic that's good enough for "is
this track competing for the same frequency range as the focal":

- For each focal money band, compute the per-band masking contribution
  from the other track. If the other track is well below the focal
  (>30 dB), it contributes 0. As it climbs toward the focal level, the
  contribution rises smoothly to 1.0 (saturating when other ≥ focal).

- Weight each band by perceptual importance (presence band 2-5 kHz
  weighted highest — that's where "cut through" lives).

- Sum across the focal's money bands and normalize.

The exact numbers are tunable; what matters is the ranking — which
competing tracks live in the focal's money bands more than others. That
ranking is what Layer 4 uses to pick EQ-cut targets.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Sequence

from .mix_analysis import (
    DB_FLOOR,
    BandSpec,
    make_third_octave_bands,
    mix_spectrum_at_region,
)

log = logging.getLogger(__name__)


# Width of the masking transition window (dB). An "other" track this far
# below the focal contributes nothing; an "other" track equal to or
# louder than the focal contributes 1.0.
MASK_WINDOW_DB: float = 30.0

# Perceptual weighting: presence band peak (Hz) and decay width.
# These shape band_perceptual_weight(). Defaults match typical "cut
# through" intent — the presence/intelligibility band centred ~3 kHz.
PRESENCE_PEAK_HZ: float = 3000.0
PRESENCE_DECADE_FALLOFF: float = 0.7  # how far from peak before weight drops to ~0.3


def find_focal_money_bands(
    energy_db_per_band: Sequence[float],
    bands: Sequence[BandSpec],
    *,
    top_n: int = 5,
    min_db_above_floor: float = 20.0,
) -> list[dict[str, float]]:
    """Identify the focal track's highest-energy bands.

    A "money band" is a band where the focal has notable energy that's
    worth defending against masking. We take the top ``top_n`` bands
    ranked by dB, dropping any that are within ``min_db_above_floor``
    of the noise floor (those are likely numerical noise, not signal).

    Returns a list of ``{center_hz, low_hz, high_hz, energy_db}`` dicts,
    sorted from loudest to quietest.
    """
    if len(energy_db_per_band) != len(bands):
        raise ValueError(
            f"spectrum/band mismatch: {len(energy_db_per_band)} vs {len(bands)}"
        )
    floor_cutoff = DB_FLOOR + min_db_above_floor
    indexed = [
        (i, energy_db_per_band[i])
        for i in range(len(bands))
        if energy_db_per_band[i] > floor_cutoff
    ]
    indexed.sort(key=lambda t: t[1], reverse=True)
    top = indexed[:top_n]
    return [
        {
            "center_hz": bands[i].center_hz,
            "low_hz": bands[i].low_hz,
            "high_hz": bands[i].high_hz,
            "energy_db": float(db),
        }
        for i, db in top
    ]


def compute_masking_score(
    *,
    focal_db: float,
    other_db: float,
    mask_window_db: float = MASK_WINDOW_DB,
) -> float:
    """How much does ``other`` mask ``focal`` at one band?

    Score in [0, 1]:
    - 0.0 when other is ``mask_window_db`` or more below focal.
    - 0.5 when other is ``mask_window_db / 2`` below focal.
    - 1.0 when other is at or above the focal level.

    Handles the floor case: if either side is essentially silent
    (``≤ DB_FLOOR + 1``), returns 0.
    """
    floor_threshold = DB_FLOOR + 1.0
    if focal_db <= floor_threshold or other_db <= floor_threshold:
        return 0.0
    delta = other_db - focal_db  # negative if other quieter than focal
    if delta >= 0.0:
        return 1.0
    if delta <= -mask_window_db:
        return 0.0
    # Linear ramp from -mask_window_db (→ 0.0) to 0.0 (→ 1.0).
    return 1.0 + delta / mask_window_db


def band_perceptual_weight(
    center_hz: float,
    *,
    peak_hz: float = PRESENCE_PEAK_HZ,
    octave_falloff: float = PRESENCE_DECADE_FALLOFF,
) -> float:
    """Perceptual weight for a band's masking contribution.

    Peaks at ``peak_hz`` (the presence / intelligibility band, default
    3 kHz — where "cut through" lives) and decays with distance from it
    measured in octaves. Octaves are the natural unit for frequency
    perception, so a band one octave off (1.5 kHz or 6 kHz) gets a
    proportionally lower weight.

    Returns a value in [0, 1]; the exact shape is a Gaussian in log2
    space:
        weight(f) = exp( -((log2(f) - log2(peak)) / falloff)^2 / 2 )
    """
    if center_hz <= 0:
        return 0.0
    distance_octaves = math.log2(center_hz / peak_hz)
    z = distance_octaves / max(octave_falloff, 1e-6)
    return math.exp(-0.5 * z * z)


def compute_masking(
    *,
    focal_spectrum: Sequence[float],
    focal_meta: dict[str, Any],
    other_spectra: Sequence[tuple[dict[str, Any], Sequence[float]]],
    bands: Sequence[BandSpec],
    top_n_money_bands: int = 5,
    min_db_above_floor: float = 20.0,
) -> dict[str, Any]:
    """Score how much each non-focal track masks the focal.

    Args:
        focal_spectrum: ``energy_db_per_band`` for the focal track.
        focal_meta: at least ``{"track_index", "name"}`` for the focal.
        other_spectra: list of ``(meta_dict, energy_db_per_band)``
            tuples for every other (non-focal) track.
        bands: band table the spectra were computed against.
        top_n_money_bands: how many of the focal's loudest bands to
            score against.
        min_db_above_floor: bands within this many dB of the floor are
            considered noise and excluded from money bands.

    Returns:
        Dict with:
        - ``focal_track``: focal_meta["track_index"]
        - ``focal_name``: focal_meta["name"]
        - ``focal_money_bands``: list of {center_hz, energy_db, ...}
        - ``competing_tracks``: ranked list by masking_score descending.
          Each entry has ``track_index``, ``name``, ``masking_score``
          (weighted average over money bands), and ``per_band``: list of
          per-money-band {center_hz, focal_energy_db, other_energy_db,
          overlap_db, score, weight}.
    """
    money_bands = find_focal_money_bands(
        focal_spectrum, bands,
        top_n=top_n_money_bands,
        min_db_above_floor=min_db_above_floor,
    )
    # Pre-compute the band-index of each money band and the perceptual
    # weight — used for every competitor.
    band_index_by_center = {b.center_hz: i for i, b in enumerate(bands)}
    money_band_indices = [
        band_index_by_center[mb["center_hz"]] for mb in money_bands
    ]
    band_weights = [
        band_perceptual_weight(mb["center_hz"]) for mb in money_bands
    ]
    total_weight = sum(band_weights) if band_weights else 0.0

    competitors: list[dict[str, Any]] = []
    for meta, other_spectrum in other_spectra:
        per_band: list[dict[str, Any]] = []
        weighted_sum = 0.0
        for mb, idx, weight in zip(money_bands, money_band_indices, band_weights):
            focal_db = focal_spectrum[idx]
            other_db = other_spectrum[idx]
            score = compute_masking_score(focal_db=focal_db, other_db=other_db)
            weighted_sum += score * weight
            per_band.append({
                "center_hz": mb["center_hz"],
                "focal_energy_db": float(focal_db),
                "other_energy_db": float(other_db),
                "overlap_db": float(other_db - focal_db),
                "score": float(score),
                "weight": float(weight),
            })
        masking_score = (
            weighted_sum / total_weight if total_weight > 0 else 0.0
        )
        competitors.append({
            "track_index": meta.get("track_index"),
            "name": meta.get("name"),
            "masking_score": float(masking_score),
            "per_band": per_band,
        })

    competitors.sort(key=lambda c: c["masking_score"], reverse=True)

    return {
        "focal_track": focal_meta.get("track_index"),
        "focal_name": focal_meta.get("name"),
        "focal_money_bands": money_bands,
        "competing_tracks": competitors,
    }


# ---------------------------------------------------------------------------
# Async wrapper — bounce + analyze + score in one call.
# ---------------------------------------------------------------------------


async def mix_masking_at_region(
    focal_track_index: int,
    start_beats: float,
    end_beats: float,
    *,
    output_dir: str | None = None,
    top_n_money_bands: int = 5,
    target_sr: int = 22050,
    warmup_sec: float = 0.0,
) -> dict[str, Any]:
    """Bounce + analyze + mask in one call.

    Layer 2.2 of the mix-aware shaping stack. The result is what Layer 4
    (``mix_propose``) needs to pick EQ-cut targets.

    Args:
        focal_track_index: index of the focal track (the one you want
            to "cut through" or otherwise feature).
        start_beats / end_beats: the region to analyze.
        output_dir: where the bounced wavs land (defaults to a temp dir
            via L2.1).
        top_n_money_bands: how many of the focal's loudest bands to use.
        target_sr: resample target for the analysis.
        warmup_sec: pass-through to the bounce.

    Returns the same dict as :func:`compute_masking`, plus:
    - ``region``: {start_beats, end_beats, duration_sec, tempo}
    - ``bands``: list of band specs (parity with L2.1)
    - ``skipped_tracks``: list of tracks that failed analysis (passed
      through from L2.1, surfaced here so the user knows what was
      excluded from the masking score)

    Raises:
        ValueError: if ``focal_track_index`` isn't among the analyzed
            tracks (likely user typo or focal track was muted).
    """
    spectrum_result = await mix_spectrum_at_region(
        start_beats=start_beats, end_beats=end_beats,
        output_dir=output_dir, target_sr=target_sr, warmup_sec=warmup_sec,
    )

    bands = make_third_octave_bands()
    analyzed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for t in spectrum_result.get("tracks", []):
        if t.get("analyzed"):
            analyzed.append(t)
        else:
            skipped.append(t)

    # Find the focal track among the analyzed ones.
    focal = next(
        (t for t in analyzed if t.get("track_index") == focal_track_index),
        None,
    )
    if focal is None:
        raise ValueError(
            f"focal_track_index={focal_track_index} not found in analyzed "
            f"tracks (available: "
            f"{[t.get('track_index') for t in analyzed]})"
        )

    other_spectra = [
        (
            {"track_index": t["track_index"], "name": t.get("name")},
            t["energy_db_per_band"],
        )
        for t in analyzed
        if t.get("track_index") != focal_track_index
    ]

    masking = compute_masking(
        focal_spectrum=focal["energy_db_per_band"],
        focal_meta={
            "track_index": focal["track_index"],
            "name": focal.get("name"),
        },
        other_spectra=other_spectra,
        bands=bands,
        top_n_money_bands=top_n_money_bands,
    )

    return {
        **masking,
        "region": spectrum_result.get("region"),
        "bands": spectrum_result.get("bands"),
        "skipped_tracks": [
            {
                "track_index": s.get("track_index"),
                "name": s.get("name"),
                "error": s.get("error"),
            }
            for s in skipped
        ],
    }
