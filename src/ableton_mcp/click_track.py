"""Click track generation: audio → beat + downbeat detection → WAV of clicks.

Uses `beat_this <https://github.com/CPJKU/beat_this>`_ (Foscarin et al.,
JKU) for beat/downbeat detection — its pretrained model handles any
music style and returns timestamps in seconds. We then synthesize a
short click sample at each beat (with a louder, higher-pitched variant
on downbeats) and mix them into a silent buffer matching the source
audio's length.

Design choices:

- **Sine burst clicks**, not sampled wood-block hits. Sine bursts are
  cleaner (no license baggage, no bundled binary blob), and their
  envelope + frequency can be tuned per taste without dragging in a
  sampler.
- **Downbeat emphasis**: higher frequency + longer duration + louder
  than regular beats. Reproduces the classic "TICK-tock-tock-tock"
  metronome pattern most musicians expect.
- **Sample rate matches the source** so the click track can be played
  in perfect sync alongside the source in any DAW / audio player.
- **Mono output** by default (a click track doesn't need stereo). The
  practice_app plays it alongside the source via a separate ``<audio>``
  element with matched playback rate.
- **Beat detection runs on GPU when available** (via torch); falls back
  to CPU. Inference is fast either way — ~7s on GPU for a 6-minute
  song.

Public surface:

- :func:`generate_click_track` — one-shot: audio path → click WAV path.
- :func:`detect_beats` — audio path → ``BeatDetection`` (beats +
  downbeats + BPM). Exposed separately for callers that want to
  visualise or transform the beat grid without writing a WAV.
- :func:`synthesize_clicks_wav` — given a ``BeatDetection`` and a
  source-audio length, build the mono click track in memory as an
  ndarray. Exposed for testing without hitting the neural model.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BeatDetection:
    """Beat / downbeat / BPM detection result."""

    beats_sec: tuple[float, ...]
    """Beat timestamps in seconds."""
    downbeats_sec: tuple[float, ...]
    """Downbeat (bar-start) timestamps in seconds. Subset of ``beats_sec``."""
    bpm_estimate: float | None
    """Median BPM computed from beat spacing. None if fewer than 2 beats."""


@dataclass(frozen=True)
class ClickTrackResult:
    """What :func:`generate_click_track` produces."""

    click_wav_path: str
    duration_sec: float
    samplerate: int
    beat_count: int
    downbeat_count: int
    bpm_estimate: float | None


# ---------------------------------------------------------------------------
# Tunables — sane defaults for practice-track use
# ---------------------------------------------------------------------------


BEAT_FREQ_HZ: float = 1000.0
"""Frequency of the regular-beat click tone. 1 kHz sits above most
guitar / vocal fundamentals so it's easy to hear over a mix."""

DOWNBEAT_FREQ_HZ: float = 1500.0
"""Frequency of the downbeat (bar-start) click. Higher pitch marks
the '1' beat clearly."""

BEAT_DURATION_MS: float = 20.0
DOWNBEAT_DURATION_MS: float = 30.0
"""Click duration. Short enough to be percussive, long enough to be
audible on small speakers."""

BEAT_AMPLITUDE: float = 0.35
DOWNBEAT_AMPLITUDE: float = 0.55
"""Peak amplitude relative to full-scale [0, 1]. Downbeat is louder
than the regular beat to reinforce the accent."""

DEFAULT_MODEL_CHECKPOINT: str = "final0"
"""Which beat_this checkpoint to load. ``final0`` is the model paper's
recommended default. Alternatives are documented at
https://github.com/CPJKU/beat_this."""


# ---------------------------------------------------------------------------
# Beat detection
# ---------------------------------------------------------------------------


def detect_beats(
    audio_path: str | os.PathLike[str],
    *,
    checkpoint: str = DEFAULT_MODEL_CHECKPOINT,
    device: str | None = None,
    float16: bool = False,
    use_dbn: bool = False,
) -> BeatDetection:
    """Run beat_this on ``audio_path`` and return the parsed detection.

    Args:
        audio_path: path to a wav/mp3/etc. audio file. Any format
            librosa/torchaudio can read is fine.
        checkpoint: beat_this checkpoint name. Default ``"final0"`` is
            the paper's recommended model.
        device: torch device (``"cuda"``, ``"cpu"``, or ``"cuda:0"``).
            Default None auto-detects CUDA availability.
        float16: run inference in half precision. Faster on modern GPUs;
            no meaningful accuracy loss for beat detection. Default False
            because CPU inference doesn't benefit and downstream code
            shouldn't care.
        use_dbn: enable the dynamic Bayesian network post-processor.
            Improves accuracy on messy audio at ~2x inference time.
            Default False for speed.

    Returns:
        :class:`BeatDetection` with the parsed timestamps + BPM estimate.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    try:
        from beat_this.inference import File2Beats
    except ImportError as exc:
        raise RuntimeError(
            "beat_this not installed. Add via "
            "`pip install ableton-full-control-mcp[click_track]` or "
            "`pip install beat_this`."
        ) from exc

    if device is None:
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    log.info("beat_this: loading checkpoint %s on %s", checkpoint, device)
    f2b = File2Beats(
        checkpoint_path=checkpoint,
        device=device,
        float16=float16,
        dbn=use_dbn,
    )
    beats_np, downbeats_np = f2b(str(path))

    beats_tuple = tuple(float(x) for x in beats_np)
    downbeats_tuple = tuple(float(x) for x in downbeats_np)

    bpm = _median_bpm(beats_tuple)

    return BeatDetection(
        beats_sec=beats_tuple,
        downbeats_sec=downbeats_tuple,
        bpm_estimate=bpm,
    )


def _median_bpm(beats_sec: tuple[float, ...]) -> float | None:
    """Compute BPM from the median inter-beat interval. Robust to
    occasional missed / doubled beats compared to a mean."""
    if len(beats_sec) < 2:
        return None
    diffs = np.diff(np.asarray(beats_sec, dtype=np.float64))
    diffs = diffs[diffs > 0]  # guard against pathological cases
    if diffs.size == 0:
        return None
    median_interval = float(np.median(diffs))
    if median_interval <= 0:
        return None
    return round(60.0 / median_interval, 2)


# ---------------------------------------------------------------------------
# Click synthesis
# ---------------------------------------------------------------------------


def _make_click(
    freq_hz: float,
    duration_ms: float,
    amplitude: float,
    samplerate: int,
) -> np.ndarray:
    """One click: sine at ``freq_hz`` for ``duration_ms``, shaped with a
    short attack + exponential decay so it sounds like a "tick" not a
    "beep"."""
    n_samples = int(duration_ms * samplerate / 1000.0)
    if n_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n_samples, dtype=np.float32) / samplerate
    # Envelope: 2ms linear attack, exponential decay for the rest.
    attack_samples = max(1, int(0.002 * samplerate))
    env = np.ones(n_samples, dtype=np.float32)
    env[:attack_samples] = np.linspace(0.0, 1.0, attack_samples, dtype=np.float32)
    # Exp decay reaching 5% by end.
    decay = np.exp(
        np.linspace(0.0, math.log(0.05), n_samples - attack_samples,
                    dtype=np.float32)
    )
    env[attack_samples:] = decay
    return (amplitude * env * np.sin(2 * math.pi * freq_hz * t)).astype(np.float32)


def synthesize_clicks_wav(
    detection: BeatDetection,
    duration_sec: float,
    samplerate: int = 44100,
    *,
    beat_freq_hz: float = BEAT_FREQ_HZ,
    downbeat_freq_hz: float = DOWNBEAT_FREQ_HZ,
    beat_amp: float = BEAT_AMPLITUDE,
    downbeat_amp: float = DOWNBEAT_AMPLITUDE,
    beat_ms: float = BEAT_DURATION_MS,
    downbeat_ms: float = DOWNBEAT_DURATION_MS,
) -> np.ndarray:
    """Build a mono click-track ndarray from a :class:`BeatDetection`.

    Regular beats and downbeats each get their own click sample. Since
    every downbeat is also in ``beats_sec``, we skip the beat click at
    downbeat times to avoid double-triggering.

    Returns a ``float32`` array of length ``int(duration_sec * samplerate)``.
    """
    total_samples = max(1, int(duration_sec * samplerate))
    out = np.zeros(total_samples, dtype=np.float32)

    beat_click = _make_click(beat_freq_hz, beat_ms, beat_amp, samplerate)
    downbeat_click = _make_click(
        downbeat_freq_hz, downbeat_ms, downbeat_amp, samplerate,
    )
    downbeat_set = set(detection.downbeats_sec)

    def _place(sample_index: int, click: np.ndarray) -> None:
        """Mix a click into ``out`` at ``sample_index``, clipping the
        tail if it would run past the end of the buffer."""
        end = min(sample_index + click.size, total_samples)
        n = end - sample_index
        if n <= 0:
            return
        out[sample_index:end] += click[:n]

    # Regular beats (skip if this beat is also a downbeat).
    for t in detection.beats_sec:
        if t in downbeat_set:
            continue
        _place(int(round(t * samplerate)), beat_click)

    # Downbeats (louder / higher click).
    for t in detection.downbeats_sec:
        _place(int(round(t * samplerate)), downbeat_click)

    # Clip to [-1, 1] in case any beats-plus-downbeat overlap pushed
    # over. Rare but cheap to guard against.
    np.clip(out, -1.0, 1.0, out=out)
    return out


# ---------------------------------------------------------------------------
# Top-level function
# ---------------------------------------------------------------------------


def _read_audio_duration_and_sr(path: str | os.PathLike[str]) -> tuple[float, int]:
    """Return ``(duration_sec, samplerate)`` for an audio file via
    ``soundfile`` (already a project dep)."""
    import soundfile as sf
    info = sf.info(str(path))
    return float(info.duration), int(info.samplerate)


def generate_click_track(
    audio_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str] | None = None,
    *,
    checkpoint: str = DEFAULT_MODEL_CHECKPOINT,
    device: str | None = None,
    use_dbn: bool = False,
    beat_freq_hz: float = BEAT_FREQ_HZ,
    downbeat_freq_hz: float = DOWNBEAT_FREQ_HZ,
) -> ClickTrackResult:
    """One-shot: audio in, click-track WAV out.

    Args:
        audio_path: path to source audio.
        output_path: where to write the click WAV. Defaults to
            ``<audio_dir>/click.wav``.
        checkpoint: beat_this checkpoint. Default ``"final0"``.
        device: ``"cuda"``, ``"cpu"``, or None to auto-detect.
        use_dbn: enable the DBN post-processor for messier audio at
            ~2x inference cost.
        beat_freq_hz / downbeat_freq_hz: click pitch overrides.

    Returns:
        :class:`ClickTrackResult` with the output path + stats.
    """
    audio_p = Path(audio_path)
    if not audio_p.exists():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    if output_path is None:
        output_path = audio_p.parent / "click.wav"
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    detection = detect_beats(
        audio_p,
        checkpoint=checkpoint,
        device=device,
        use_dbn=use_dbn,
    )
    duration_sec, samplerate = _read_audio_duration_and_sr(audio_p)

    click_wav = synthesize_clicks_wav(
        detection,
        duration_sec=duration_sec,
        samplerate=samplerate,
        beat_freq_hz=beat_freq_hz,
        downbeat_freq_hz=downbeat_freq_hz,
    )

    import soundfile as sf
    sf.write(str(out_p), click_wav, samplerate, subtype="PCM_16")

    return ClickTrackResult(
        click_wav_path=str(out_p.resolve()),
        duration_sec=duration_sec,
        samplerate=samplerate,
        beat_count=len(detection.beats_sec),
        downbeat_count=len(detection.downbeats_sec),
        bpm_estimate=detection.bpm_estimate,
    )
