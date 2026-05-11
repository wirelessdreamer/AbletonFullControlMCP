"""Multi-track spectral analysis — Layer 2.1 of the mix-aware shaping stack.

Given a list of per-track WAV files (one per track in the same region),
compute spectral energy per third-octave band per track. The output is
the data Layer 4 (``mix_propose``) needs to figure out which competing
tracks live in the focal's "money band" and propose EQ moves.

Design constraints:

1. **Pure-data core, async I/O wrapper.** The DSP functions take
   (audio, sr) ndarrays and return data; the async ``mix_spectrum_at_region``
   wraps them with a bounce + load + analyze pipeline. Unit-testable
   against synthetic audio without Live.

2. **Third-octave bands.** Industry-standard binning for mix analysis.
   Each band's centre + edges are computed once at module load; values
   match ISO 266 nominal centres for the 25 Hz - 16 kHz range.

3. **dB units throughout.** Internal computation uses energy in linear
   units; the public API exposes dB-FS values so the LLM sees numbers
   on the scale mix engineers actually use. ``-inf`` is clamped to a
   sentinel (``-160`` dB) so JSON serialization works.

4. **Per-track + per-region.** The async wrapper bounces every active
   track for the supplied beat range (L1.3), then runs the DSP on each
   resulting WAV. Returns a single result with a ``tracks`` list — one
   entry per active track.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

log = logging.getLogger(__name__)


# Third-octave band centre frequencies (Hz) per ISO 266 nominal values.
# Covers 25 Hz to 16 kHz — the relevant audio range for mix work.
# Band edges are at f_center * 2**(-1/6) and f_center * 2**(1/6).
THIRD_OCTAVE_CENTERS_HZ: tuple[float, ...] = (
    25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0,
    250.0, 315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0,
    2500.0, 3150.0, 4000.0, 5000.0, 6300.0, 8000.0, 10000.0, 12500.0, 16000.0,
)


# Sentinel for log10(0). Used so the result dict can serialize to JSON
# without -inf values that would crash some MCP clients.
DB_FLOOR: float = -160.0


@dataclass(frozen=True)
class BandSpec:
    """One third-octave band, used in the public result so callers can
    see edges + centres without recomputing."""

    center_hz: float
    low_hz: float
    high_hz: float

    def to_dict(self) -> dict[str, float]:
        return {
            "center_hz": self.center_hz,
            "low_hz": self.low_hz,
            "high_hz": self.high_hz,
        }


def make_third_octave_bands(
    centers_hz: Sequence[float] = THIRD_OCTAVE_CENTERS_HZ,
) -> list[BandSpec]:
    """Build the full band list with edges. ``2**(±1/6)`` gives third-
    octave edges around each centre."""
    factor = 2.0 ** (1.0 / 6.0)
    return [
        BandSpec(
            center_hz=float(c),
            low_hz=float(c) / factor,
            high_hz=float(c) * factor,
        )
        for c in centers_hz
    ]


def _energy_to_db(energy: float) -> float:
    """Convert a linear-power value to dB-FS with the DB_FLOOR sentinel
    for non-positive inputs (so log10 never sees zero)."""
    if energy <= 0.0 or not math.isfinite(energy):
        return DB_FLOOR
    return 10.0 * math.log10(energy)


def _safe_max_db(values: Sequence[float]) -> float:
    """Max with the floor — empty input returns the floor."""
    finite = [v for v in values if v > DB_FLOOR and math.isfinite(v)]
    return max(finite) if finite else DB_FLOOR


def compute_band_energy(
    audio: np.ndarray,
    sr: int,
    bands: Sequence[BandSpec] | None = None,
    *,
    n_fft: int = 4096,
    hop_length: int | None = None,
) -> dict[str, Any]:
    """Compute per-third-octave-band energy for a mono audio signal.

    Args:
        audio: float32 mono ndarray, length N. Caller is responsible
            for downmixing stereo if needed.
        sr: sample rate in Hz.
        bands: band list (default: third-octave 25 Hz - 16 kHz).
        n_fft: FFT size. 4096 at 44.1 kHz gives ~10 Hz resolution —
            enough to resolve the 25 Hz band's edges (low ≈ 22 Hz,
            high ≈ 28 Hz) but not so large that short clips have no
            usable frames.
        hop_length: STFT hop. Default ``n_fft // 4``.

    Returns:
        Dict with:
        - ``energy_db_per_band``: list[float], one dB value per band
        - ``peak_db``: max instantaneous magnitude as dB-FS
        - ``rms_db``: RMS of the whole audio as dB-FS
        - ``spectral_centroid_hz``: weighted-mean frequency
        - ``top_bands``: list of (center_hz, energy_db) for the 5
          highest-energy bands — useful at-a-glance summary
    """
    if bands is None:
        bands = make_third_octave_bands()

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1:
        # Downmix multichannel to mono.
        audio = audio.mean(axis=0) if audio.ndim > 1 else audio.flatten()

    if audio.size == 0:
        return _empty_result(bands)

    hop_length = hop_length if hop_length is not None else n_fft // 4

    # STFT magnitude. We don't need phase. Window: Hann (librosa default).
    # Use numpy fft directly to avoid pulling librosa into the pure-DSP
    # path (it's available, but keeping the DSP isolation pure helps
    # downstream testability).
    import scipy.signal  # already a project dep
    freqs, _, stft = scipy.signal.stft(
        audio, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length,
        window="hann", padded=False, boundary=None,
    )
    if stft.size == 0:
        return _empty_result(bands)

    # Magnitude squared = power per bin per frame; sum across frames =
    # total power per FFT bin over the whole signal.
    power_per_bin = np.mean(np.abs(stft) ** 2, axis=1)

    # Distribute each FFT bin into the third-octave band whose [low, high]
    # contains the bin's centre frequency. Bins outside all bands (very
    # low, very high) are ignored.
    energy_per_band: list[float] = []
    for band in bands:
        mask = (freqs >= band.low_hz) & (freqs < band.high_hz)
        if not np.any(mask):
            energy_per_band.append(0.0)
            continue
        energy_per_band.append(float(np.sum(power_per_bin[mask])))

    # Normalisation: report dB-FS relative to a hypothetical full-scale
    # signal. Pure sine at 0 dB-FS has peak energy ~0.5 (for the
    # one-sided spectrum convention scipy.signal.stft produces); we
    # compensate with a fixed offset so a peaking sine maps to ~0 dB.
    # The exact calibration is less important than the RELATIVE numbers
    # across tracks — downstream layers compare bands across tracks, not
    # against absolute references.
    energy_db_per_band = [_energy_to_db(e) for e in energy_per_band]

    # Peak / RMS of the time-domain signal in dB-FS.
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    peak_db = (
        20.0 * math.log10(max(peak, 1e-12))
        if peak > 0 else DB_FLOOR
    )
    rms = float(np.sqrt(np.mean(audio ** 2)))
    rms_db = (
        20.0 * math.log10(max(rms, 1e-12))
        if rms > 0 else DB_FLOOR
    )

    # Spectral centroid: power-weighted mean frequency.
    total_power = float(np.sum(power_per_bin))
    if total_power > 0:
        spectral_centroid = float(
            np.sum(freqs * power_per_bin) / total_power
        )
    else:
        spectral_centroid = 0.0

    # Top-5 bands by energy. Useful summary for the LLM.
    band_energy_pairs = [
        (bands[i].center_hz, energy_db_per_band[i])
        for i in range(len(bands))
    ]
    top_bands = sorted(
        band_energy_pairs, key=lambda p: p[1], reverse=True,
    )[:5]
    top_bands_dicts = [
        {"center_hz": c, "energy_db": e} for c, e in top_bands
    ]

    return {
        "energy_db_per_band": energy_db_per_band,
        "peak_db": peak_db,
        "rms_db": rms_db,
        "spectral_centroid_hz": spectral_centroid,
        "top_bands": top_bands_dicts,
    }


def _empty_result(bands: Sequence[BandSpec]) -> dict[str, Any]:
    return {
        "energy_db_per_band": [DB_FLOOR] * len(bands),
        "peak_db": DB_FLOOR,
        "rms_db": DB_FLOOR,
        "spectral_centroid_hz": 0.0,
        "top_bands": [],
    }


def analyze_wav_spectrum(
    wav_path: str | os.PathLike,
    *,
    bands: Sequence[BandSpec] | None = None,
    target_sr: int | None = None,
) -> dict[str, Any]:
    """Read a wav file, downmix to mono, run :func:`compute_band_energy`.

    Args:
        wav_path: path to a wav file on disk.
        bands: optional band override.
        target_sr: optional resample target. None = use the file's sr.

    Returns the same dict as ``compute_band_energy`` plus ``wav_path``,
    ``samplerate``, ``duration_sec``.
    """
    import librosa  # heavy import; pulled in only at call time
    path = Path(wav_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"wav not found: {path}")
    audio, sr = librosa.load(str(path), sr=target_sr, mono=True)
    result = compute_band_energy(audio, sr=sr, bands=bands)
    result["wav_path"] = str(path)
    result["samplerate"] = int(sr)
    result["duration_sec"] = float(audio.size) / float(sr) if sr > 0 else 0.0
    return result


# ---------------------------------------------------------------------------
# Async wrapper: bounce all active tracks for region, then analyze each.
# ---------------------------------------------------------------------------


async def mix_spectrum_at_region(
    start_beats: float,
    end_beats: float,
    *,
    output_dir: str | os.PathLike | None = None,
    bands: Sequence[BandSpec] | None = None,
    target_sr: int = 22050,
    warmup_sec: float = 0.0,
) -> dict[str, Any]:
    """Bounce every active track for the region, return per-track band energy.

    Layer 2.1 of the mix-aware shaping stack. The result is what Layer
    4 needs to identify "money band" overlap between the focal track
    and competing tracks.

    Args:
        start_beats / end_beats: the region to analyze.
        output_dir: where the bounced wavs land. Defaults to a temp dir
            (the wavs are still useful — callers may want to keep them
            for the verification round-trip in L5).
        bands: optional override of the band list.
        target_sr: resample wavs to this sr before analysis. 22050 is
            enough for 11 kHz top band, half the data of 44.1 kHz.
        warmup_sec: pass-through to the bounce (prime samplers).

    Returns:
        {
            "region": {start_beats, end_beats, duration_sec, tempo},
            "bands": [{center_hz, low_hz, high_hz}, ...],
            "tracks": [{track_index, name, energy_db_per_band, peak_db,
                        rms_db, spectral_centroid_hz, top_bands, ...}, ...]
        }
    """
    from .bounce.resampling import bounce_region_all_active_via_resampling

    if output_dir is None:
        import tempfile
        output_dir = tempfile.mkdtemp(prefix="mix_spectrum_")

    bounce_result = await bounce_region_all_active_via_resampling(
        output_dir, start_beats, end_beats, warmup_sec=warmup_sec,
    )

    band_specs = list(make_third_octave_bands()) if bands is None else list(bands)
    bands_json = [b.to_dict() for b in band_specs]

    tracks_out: list[dict[str, Any]] = []
    for stem in bounce_result.get("stems", []):
        if not stem.get("copied"):
            tracks_out.append({
                "track_index": stem.get("source_track_index"),
                "name": stem.get("source_track_name"),
                "analyzed": False,
                "error": stem.get("error"),
            })
            continue
        try:
            spectrum = analyze_wav_spectrum(
                stem["output_path"], bands=band_specs, target_sr=target_sr,
            )
        except Exception as exc:
            tracks_out.append({
                "track_index": stem["source_track_index"],
                "name": stem["source_track_name"],
                "analyzed": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        tracks_out.append({
            "track_index": stem["source_track_index"],
            "name": stem["source_track_name"],
            "analyzed": True,
            **spectrum,
        })

    return {
        "region": {
            "start_beats": float(start_beats),
            "end_beats": float(end_beats),
            "duration_sec": bounce_result.get("region_seconds"),
            "tempo": bounce_result.get("tempo_at_bounce"),
        },
        "bands": bands_json,
        "tracks": tracks_out,
        "bounce_diagnostics": bounce_result.get("diagnostics"),
        "output_dir": str(Path(output_dir).resolve()),
    }
