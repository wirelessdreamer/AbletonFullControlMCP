"""Tests for ``ableton_mcp.mix_analysis`` — multi-track spectral analysis.

The pure DSP is testable against synthesized audio (sine waves at known
frequencies land in expected third-octave bands; pink-ish noise lands
distributed). The async ``mix_spectrum_at_region`` wrapper is exercised
with mocked bounce + a small fixture wav.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.mix_analysis import (
    BandSpec,
    DB_FLOOR,
    THIRD_OCTAVE_CENTERS_HZ,
    analyze_wav_spectrum,
    compute_band_energy,
    make_third_octave_bands,
    mix_spectrum_at_region,
)


# ---------------------------------------------------------------------------
# Band table sanity
# ---------------------------------------------------------------------------


def test_third_octave_centers_match_iso_266_subset() -> None:
    """A few well-known centres should be present."""
    centers = set(THIRD_OCTAVE_CENTERS_HZ)
    for c in (31.5, 100.0, 1000.0, 4000.0, 16000.0):
        assert c in centers


def test_make_third_octave_bands_edges_make_sense() -> None:
    """For each band, low < center < high, and consecutive bands
    overlap or touch (third-octave bands butt up against each other)."""
    bands = make_third_octave_bands()
    for b in bands:
        assert b.low_hz < b.center_hz < b.high_hz
    for a, b in zip(bands, bands[1:]):
        # The high edge of one band ≈ the low edge of the next. ISO 266
        # nominal centres are rounded for human readability (e.g. 31.5 →
        # 40 instead of the exact 39.69), so consecutive band edges land
        # within ~1% of each other rather than exactly touching.
        assert math.isclose(a.high_hz, b.low_hz, rel_tol=0.02)


def test_band_spec_to_dict_keys() -> None:
    band = BandSpec(center_hz=1000.0, low_hz=890.0, high_hz=1120.0)
    d = band.to_dict()
    assert set(d.keys()) == {"center_hz", "low_hz", "high_hz"}


# ---------------------------------------------------------------------------
# compute_band_energy: synthetic audio
# ---------------------------------------------------------------------------


def _sine(freq_hz: float, duration_sec: float, sr: int = 22050,
          amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(duration_sec * sr), dtype=np.float32) / sr
    return (amp * np.sin(2 * math.pi * freq_hz * t)).astype(np.float32)


def test_sine_at_1khz_concentrates_in_1khz_band() -> None:
    """Pure 1 kHz sine should put almost all energy in the 1000 Hz band
    and very little in distant bands."""
    audio = _sine(1000.0, duration_sec=1.0, sr=22050)
    result = compute_band_energy(audio, sr=22050)
    bands = make_third_octave_bands()
    # Find the 1000 Hz band's index.
    idx_1k = next(i for i, b in enumerate(bands) if b.center_hz == 1000.0)
    band_energies = result["energy_db_per_band"]
    # The 1000 Hz band should be the loudest. (The two adjacent bands
    # may catch some spectral leakage but the centre wins.)
    assert band_energies[idx_1k] == max(band_energies)
    # Distant bands should be much quieter — > 30 dB below.
    idx_far = next(i for i, b in enumerate(bands) if b.center_hz == 8000.0)
    assert band_energies[idx_1k] - band_energies[idx_far] > 30


def test_sine_top_bands_includes_target_frequency() -> None:
    """The top_bands summary should put the sine's frequency at #1."""
    audio = _sine(3150.0, duration_sec=0.5, sr=22050)
    result = compute_band_energy(audio, sr=22050)
    top = result["top_bands"]
    assert len(top) > 0
    assert top[0]["center_hz"] == 3150.0


def test_silence_returns_floor_for_every_band() -> None:
    audio = np.zeros(22050, dtype=np.float32)
    result = compute_band_energy(audio, sr=22050)
    for v in result["energy_db_per_band"]:
        assert v == DB_FLOOR
    assert result["peak_db"] == DB_FLOOR
    assert result["rms_db"] == DB_FLOOR


def test_empty_audio_returns_floor() -> None:
    audio = np.zeros(0, dtype=np.float32)
    result = compute_band_energy(audio, sr=22050)
    assert all(v == DB_FLOOR for v in result["energy_db_per_band"])


def test_spectral_centroid_matches_sine_frequency() -> None:
    """A pure sine's spectral centroid should land near its
    fundamental (within ~5% — depends on FFT resolution)."""
    target_hz = 2000.0
    audio = _sine(target_hz, duration_sec=1.0, sr=22050)
    result = compute_band_energy(audio, sr=22050)
    centroid = result["spectral_centroid_hz"]
    assert abs(centroid - target_hz) / target_hz < 0.05


def test_peak_db_for_half_amplitude_sine() -> None:
    """A sine at amplitude 0.5 has peak ≈ 0.5, peak_db ≈ -6 dB-FS."""
    audio = _sine(1000.0, duration_sec=0.5, sr=22050, amp=0.5)
    result = compute_band_energy(audio, sr=22050)
    # 20 * log10(0.5) ≈ -6.02 dB
    assert result["peak_db"] == pytest.approx(-6.02, abs=0.1)


def test_rms_db_for_half_amplitude_sine() -> None:
    """A sine at amplitude 0.5 has RMS ≈ 0.354, rms_db ≈ -9 dB-FS."""
    audio = _sine(1000.0, duration_sec=0.5, sr=22050, amp=0.5)
    result = compute_band_energy(audio, sr=22050)
    # 20 * log10(0.5 / sqrt(2)) ≈ -9.03 dB
    assert result["rms_db"] == pytest.approx(-9.03, abs=0.2)


def test_stereo_audio_is_downmixed_to_mono() -> None:
    """Pass a 2-D ndarray (stereo); function should downmix rather than crash."""
    sr = 22050
    left = _sine(1000.0, duration_sec=0.5, sr=sr, amp=0.5)
    right = _sine(1000.0, duration_sec=0.5, sr=sr, amp=0.5)
    stereo = np.stack([left, right])
    result = compute_band_energy(stereo, sr=sr)
    # Should produce the same band breakdown as the mono signal.
    assert len(result["energy_db_per_band"]) == len(make_third_octave_bands())


def test_two_sines_summed_register_in_both_bands() -> None:
    """A 1 kHz + 4 kHz mix should have BOTH the 1000 Hz and 4000 Hz
    bands in the top-5 (and both well above the noise floor)."""
    sr = 22050
    audio = _sine(1000.0, 0.5, sr, amp=0.3) + _sine(4000.0, 0.5, sr, amp=0.3)
    result = compute_band_energy(audio, sr=sr)
    top_centers = {t["center_hz"] for t in result["top_bands"]}
    assert 1000.0 in top_centers
    assert 4000.0 in top_centers


# ---------------------------------------------------------------------------
# analyze_wav_spectrum: file-on-disk variant
# ---------------------------------------------------------------------------


def test_analyze_wav_spectrum_roundtrip(tmp_path: Path) -> None:
    """Write a synthetic wav, read it back through analyze_wav_spectrum,
    verify the result matches what compute_band_energy reports."""
    sr = 22050
    audio = _sine(1000.0, duration_sec=0.5, sr=sr, amp=0.5)
    wav_path = tmp_path / "test.wav"
    sf.write(str(wav_path), audio, sr, subtype="PCM_16")
    result = analyze_wav_spectrum(wav_path, target_sr=sr)
    assert result["wav_path"] == str(wav_path.resolve())
    assert result["samplerate"] == sr
    assert result["duration_sec"] == pytest.approx(0.5, abs=0.01)
    # 1 kHz band should still be the loudest.
    bands = make_third_octave_bands()
    idx_1k = next(i for i, b in enumerate(bands) if b.center_hz == 1000.0)
    assert result["energy_db_per_band"][idx_1k] == max(
        result["energy_db_per_band"]
    )


def test_analyze_wav_spectrum_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        analyze_wav_spectrum(tmp_path / "nope.wav")


# ---------------------------------------------------------------------------
# Async wrapper mix_spectrum_at_region — mocked bounce
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mix_spectrum_at_region_analyzes_each_bounced_stem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Mock the bounce to return fake stems on disk; verify
    mix_spectrum_at_region runs the DSP on each one and returns
    structured per-track results."""
    sr = 22050
    stem_a = tmp_path / "stem_0_drums.wav"
    stem_b = tmp_path / "stem_1_bass.wav"
    # Distinct content per stem so we can tell the analyses apart.
    sf.write(str(stem_a), _sine(4000.0, 0.5, sr, amp=0.4), sr, subtype="PCM_16")
    sf.write(str(stem_b), _sine(80.0, 0.5, sr, amp=0.4), sr, subtype="PCM_16")

    async def fake_bounce(
        output_dir, start_beats, end_beats, *, warmup_sec=0.0, **_kw,
    ):
        return {
            "what": "stems_via_resampling",
            "duration_sec": (end_beats - start_beats) * 0.5,  # 120 BPM
            "region_start_beats": start_beats,
            "region_end_beats": end_beats,
            "region_seconds": (end_beats - start_beats) * 0.5,
            "tempo_at_bounce": 120.0,
            "stems": [
                {"source_track_index": 0, "source_track_name": "Drums",
                 "copied": True, "output_path": str(stem_a)},
                {"source_track_index": 1, "source_track_name": "Bass",
                 "copied": True, "output_path": str(stem_b)},
            ],
            "diagnostics": None,
        }

    monkeypatch.setattr(
        "ableton_mcp.bounce.resampling.bounce_region_all_active_via_resampling",
        fake_bounce,
    )

    result = await mix_spectrum_at_region(
        start_beats=0.0, end_beats=8.0, output_dir=tmp_path / "out",
    )
    assert result["region"]["start_beats"] == 0.0
    assert result["region"]["end_beats"] == 8.0
    assert len(result["bands"]) == len(make_third_octave_bands())
    assert len(result["tracks"]) == 2
    drums = next(t for t in result["tracks"] if t["track_index"] == 0)
    bass = next(t for t in result["tracks"] if t["track_index"] == 1)
    assert drums["analyzed"] is True
    assert bass["analyzed"] is True
    # Drums (4 kHz sine) should put 4000 Hz in top bands; bass (80 Hz
    # sine) should NOT.
    drums_top = {t["center_hz"] for t in drums["top_bands"]}
    bass_top = {t["center_hz"] for t in bass["top_bands"]}
    assert 4000.0 in drums_top
    assert 4000.0 not in bass_top
    assert 80.0 in bass_top


@pytest.mark.asyncio
async def test_mix_spectrum_at_region_handles_failed_stem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A stem that failed to bounce (copied=False) should land in the
    result as analyzed=False with the error, not blank or raise."""

    async def fake_bounce(
        output_dir, start_beats, end_beats, *, warmup_sec=0.0, **_kw,
    ):
        return {
            "stems": [
                {"source_track_index": 0, "source_track_name": "Broken",
                 "copied": False, "error": "simulated bounce failure"},
            ],
            "region_start_beats": start_beats,
            "region_end_beats": end_beats,
            "region_seconds": 0.0,
            "tempo_at_bounce": 120.0,
            "diagnostics": None,
        }

    monkeypatch.setattr(
        "ableton_mcp.bounce.resampling.bounce_region_all_active_via_resampling",
        fake_bounce,
    )

    result = await mix_spectrum_at_region(
        start_beats=0.0, end_beats=4.0, output_dir=tmp_path / "out",
    )
    assert len(result["tracks"]) == 1
    track = result["tracks"][0]
    assert track["analyzed"] is False
    assert "error" in track
