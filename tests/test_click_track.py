"""Tests for click_track — beat detection + click synthesis.

The pure-DSP path (`synthesize_clicks_wav`, `_median_bpm`, click sample
shape) is testable without any model. Tests that need beat detection
mock the beat_this import so we don't download the 77 MB model or hit
torch in the CI env.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from ableton_mcp.click_track import (
    BeatDetection,
    ClickTrackResult,
    _make_click,
    _median_bpm,
    detect_beats,
    generate_click_track,
    synthesize_clicks_wav,
)


# ---------------------------------------------------------------------------
# Pure DSP: BPM computation
# ---------------------------------------------------------------------------


def test_median_bpm_120bpm_from_regular_beats() -> None:
    """120 BPM = 0.5s per beat; the median-based estimator should
    return 120.0 exactly for a metronomic input."""
    beats = tuple(0.5 * i for i in range(10))
    bpm = _median_bpm(beats)
    assert bpm == pytest.approx(120.0)


def test_median_bpm_ignores_outlier_gaps() -> None:
    """A single missed beat (double gap) shouldn't drag the estimate
    down — that's the whole point of using the median."""
    normal = [0.5 * i for i in range(20)]
    # Introduce a big gap: skip beat 10.
    with_gap = normal[:10] + normal[11:]
    bpm = _median_bpm(tuple(with_gap))
    assert bpm == pytest.approx(120.0)


def test_median_bpm_returns_none_for_zero_or_one_beat() -> None:
    assert _median_bpm(()) is None
    assert _median_bpm((0.5,)) is None


def test_median_bpm_returns_none_when_all_intervals_are_zero() -> None:
    """Pathological case: all beats at t=0. No BPM meaningful."""
    assert _median_bpm((0.0, 0.0, 0.0)) is None


# ---------------------------------------------------------------------------
# Click sample shape
# ---------------------------------------------------------------------------


def test_make_click_returns_expected_length() -> None:
    """20 ms at 44.1 kHz = 882 samples."""
    click = _make_click(freq_hz=1000, duration_ms=20, amplitude=0.5, samplerate=44100)
    assert len(click) == int(20 * 44100 / 1000)


def test_make_click_has_attack_and_decay() -> None:
    """Envelope should start near zero, peak, then decay."""
    click = _make_click(freq_hz=1000, duration_ms=20, amplitude=1.0, samplerate=44100)
    # First sample is 0 or near-zero (linear attack starting at 0).
    assert abs(click[0]) < 0.1
    # Peak is reached in the first few ms.
    peak_idx = int(np.argmax(np.abs(click)))
    assert peak_idx < len(click) // 2, "expected peak in first half of click"
    # Tail is quieter than the peak.
    assert abs(click[-1]) < abs(click[peak_idx])


def test_make_click_amplitude_scales_output() -> None:
    a = _make_click(freq_hz=1000, duration_ms=20, amplitude=0.5, samplerate=44100)
    b = _make_click(freq_hz=1000, duration_ms=20, amplitude=1.0, samplerate=44100)
    assert np.max(np.abs(b)) == pytest.approx(2 * np.max(np.abs(a)), rel=1e-5)


def test_make_click_zero_duration_returns_empty() -> None:
    click = _make_click(freq_hz=1000, duration_ms=0, amplitude=1.0, samplerate=44100)
    assert click.size == 0


# ---------------------------------------------------------------------------
# Click track synthesis
# ---------------------------------------------------------------------------


def test_synthesize_clicks_wav_places_events_at_correct_samples() -> None:
    """Beat at t=1.0s with samplerate 44100 → sample index 44100.
    Verify the click sample has energy near that index."""
    detection = BeatDetection(
        beats_sec=(1.0,),
        downbeats_sec=(),
        bpm_estimate=None,
    )
    out = synthesize_clicks_wav(detection, duration_sec=2.0, samplerate=44100)
    # Sample 44100 should have click energy; samples far away should not.
    click_region = out[44100:44100 + 1000]
    quiet_region = out[5000:5500]  # well before the first beat
    assert np.max(np.abs(click_region)) > 0.1
    assert np.max(np.abs(quiet_region)) < 0.01


def test_synthesize_clicks_wav_downbeat_louder_than_beat() -> None:
    """Downbeat click amplitude > regular beat click amplitude."""
    beat_only = BeatDetection(
        beats_sec=(1.0,),
        downbeats_sec=(),
        bpm_estimate=None,
    )
    downbeat_only = BeatDetection(
        beats_sec=(1.0,),  # downbeat is also a beat
        downbeats_sec=(1.0,),
        bpm_estimate=None,
    )
    out_beat = synthesize_clicks_wav(beat_only, duration_sec=2.0, samplerate=44100)
    out_db = synthesize_clicks_wav(downbeat_only, duration_sec=2.0, samplerate=44100)
    assert np.max(np.abs(out_db)) > np.max(np.abs(out_beat))


def test_synthesize_clicks_wav_skips_beat_click_at_downbeat_time() -> None:
    """A downbeat is also a beat — we should get exactly ONE click
    (the downbeat), not two overlapping. Compare against a case where
    the downbeat is at a slightly different time (so both clicks fire)."""
    both = BeatDetection(
        beats_sec=(1.0,),
        downbeats_sec=(1.0,),
        bpm_estimate=None,
    )
    separate = BeatDetection(
        beats_sec=(1.0, 1.5),
        downbeats_sec=(1.5,),
        bpm_estimate=None,
    )
    only_downbeat = synthesize_clicks_wav(both, duration_sec=2.5, samplerate=44100)
    both_events = synthesize_clicks_wav(separate, duration_sec=2.5, samplerate=44100)
    # Both-events case should have MORE energy than only-downbeat case.
    assert float(np.sum(np.abs(both_events))) > float(np.sum(np.abs(only_downbeat)))


def test_synthesize_clicks_wav_clips_events_past_end() -> None:
    """A beat at t=1.99s with duration 20ms + samplerate 44100 places
    the last few samples past t=2.0s. The tail should be clipped, not
    error, and the total length stays fixed."""
    detection = BeatDetection(
        beats_sec=(1.99,),
        downbeats_sec=(),
        bpm_estimate=None,
    )
    out = synthesize_clicks_wav(detection, duration_sec=2.0, samplerate=44100)
    assert len(out) == 2 * 44100


def test_synthesize_clicks_wav_no_beats_returns_silence() -> None:
    detection = BeatDetection(
        beats_sec=(),
        downbeats_sec=(),
        bpm_estimate=None,
    )
    out = synthesize_clicks_wav(detection, duration_sec=1.0, samplerate=44100)
    assert np.max(np.abs(out)) == 0.0


def test_synthesize_clicks_wav_output_stays_in_range() -> None:
    """Clipping guard: overlapping clicks shouldn't push output past
    [-1, 1]. Even densely-placed beats at unrealistic amplitudes should
    clip cleanly."""
    detection = BeatDetection(
        beats_sec=tuple(0.01 * i for i in range(50)),
        downbeats_sec=tuple(0.01 * i for i in range(0, 50, 4)),
        bpm_estimate=None,
    )
    out = synthesize_clicks_wav(
        detection, duration_sec=1.0, samplerate=44100,
        beat_amp=1.0, downbeat_amp=1.0,
    )
    assert np.max(out) <= 1.0
    assert np.min(out) >= -1.0


# ---------------------------------------------------------------------------
# detect_beats — mocked beat_this
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_beat_this(monkeypatch: pytest.MonkeyPatch):
    """Mock beat_this.inference.File2Beats so tests don't need the model."""
    fake_module = MagicMock()

    # File2Beats callable returns (beats_np, downbeats_np).
    class FakeF2B:
        def __init__(self, checkpoint_path="final0", device="cpu",
                     float16=False, dbn=False):
            self.checkpoint = checkpoint_path
            self.device = device

        def __call__(self, audio_path):
            # 120 BPM for 4 seconds, downbeats every 4 beats.
            beats = np.array([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
            downbeats = np.array([0.5, 2.5])
            return beats, downbeats

    fake_inference = MagicMock()
    fake_inference.File2Beats = FakeF2B
    fake_module.inference = fake_inference
    monkeypatch.setitem(sys.modules, "beat_this", fake_module)
    monkeypatch.setitem(sys.modules, "beat_this.inference", fake_inference)
    return fake_module


def _write_fake_wav(path: Path, duration_sec: float = 4.0) -> None:
    """Write a valid silent WAV of the given duration."""
    sr = 44100
    n_frames = int(sr * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n_frames)


def test_detect_beats_returns_beat_detection(
    fake_beat_this, tmp_path: Path,
) -> None:
    audio = tmp_path / "song.wav"
    _write_fake_wav(audio, duration_sec=4.5)

    result = detect_beats(audio)
    assert isinstance(result, BeatDetection)
    assert len(result.beats_sec) == 8
    assert len(result.downbeats_sec) == 2
    assert result.bpm_estimate == pytest.approx(120.0)


def test_detect_beats_missing_file_raises(fake_beat_this, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        detect_beats(tmp_path / "definitely-not-here.wav")


def test_detect_beats_missing_beat_this_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When beat_this isn't installed, we surface a friendly install hint."""
    audio = tmp_path / "song.wav"
    _write_fake_wav(audio, duration_sec=1.0)

    # Force the import to fail.
    import builtins
    real_import = builtins.__import__

    def raising_import(name, *a, **kw):
        if name == "beat_this.inference" or name.startswith("beat_this"):
            raise ImportError("simulated missing beat_this")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", raising_import)
    with pytest.raises(RuntimeError, match="beat_this not installed"):
        detect_beats(audio)


# ---------------------------------------------------------------------------
# generate_click_track — end-to-end with mocked beat_this
# ---------------------------------------------------------------------------


def test_generate_click_track_end_to_end(
    fake_beat_this, tmp_path: Path,
) -> None:
    audio = tmp_path / "song.wav"
    _write_fake_wav(audio, duration_sec=4.5)

    result = generate_click_track(audio, output_path=str(tmp_path / "click.wav"))
    assert isinstance(result, ClickTrackResult)
    assert Path(result.click_wav_path).exists()
    assert result.samplerate == 44100
    assert result.duration_sec == pytest.approx(4.5, abs=0.01)
    assert result.beat_count == 8
    assert result.downbeat_count == 2
    assert result.bpm_estimate == pytest.approx(120.0)


def test_generate_click_track_defaults_output_next_to_source(
    fake_beat_this, tmp_path: Path,
) -> None:
    """Without an explicit output_path, we write ``<audio_dir>/click.wav``."""
    audio = tmp_path / "song.wav"
    _write_fake_wav(audio, duration_sec=1.0)
    result = generate_click_track(audio)
    assert result.click_wav_path == str((tmp_path / "click.wav").resolve())
    assert (tmp_path / "click.wav").exists()


def test_generate_click_track_missing_audio_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        generate_click_track(tmp_path / "nope.wav")


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tools_registered() -> None:
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import click_track as ct_tools

    mcp = FastMCP("t")
    ct_tools.register(mcp)
    listed = await mcp.list_tools()
    names = {t.name for t in listed}
    assert "song_click_track" in names
    assert "song_detect_beats" in names
