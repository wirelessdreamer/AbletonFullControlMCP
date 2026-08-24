"""Tests for song_sections.transcribe — cache, word flattening, degeneracy.

No real Whisper model is ever loaded: a fake model object is injected via
the ``model=`` parameter (the pipeline's own model-sharing hook).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import pytest

from ableton_mcp.song_sections.model import Transcript, TranscriptWord
from ableton_mcp.song_sections.transcribe import (
    find_degenerate_segments,
    transcribe_vocal,
    transcript_cache_path,
)


def _write_fake_wav(path: Path, duration_sec: float = 2.0, sr: int = 16000) -> None:
    n_frames = int(sr * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n_frames)


class FakeResult:
    def __init__(self, segments: list[dict[str, Any]]):
        self._segments = segments

    def segments_to_dicts(self) -> list[dict[str, Any]]:
        return self._segments


class FakeModel:
    """Counting fake for model.transcribe()."""

    def __init__(self, segments: list[dict[str, Any]] | None = None):
        self.calls = 0
        self._segments = segments or [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "I love You Lord",
                "words": [
                    {"word": " I", "start": 0.0, "end": 0.2, "probability": 0.98},
                    {"word": " love", "start": 0.2, "end": 0.5, "probability": 0.95},
                    {"word": " You", "start": 0.5, "end": 0.8, "probability": 0.9},
                    {"word": " Lord", "start": 0.8, "end": 1.2, "probability": 0.2},
                ],
            }
        ]

    def transcribe(self, audio: Any, language: str = "en", word_timestamps: bool = True):
        self.calls += 1
        return FakeResult(self._segments)


class PoisonedModel:
    """Fails the test if the model is invoked at all (cache must hit)."""

    def transcribe(self, *a: Any, **k: Any):  # pragma: no cover
        raise AssertionError("model.transcribe called despite expected cache hit")


# ---------------------------------------------------------------------------
# transcribe_vocal + cache
# ---------------------------------------------------------------------------


def test_transcribe_flattens_words(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    t = transcribe_vocal(stem, model=FakeModel(), use_cache=False)
    assert [w.text for w in t.words] == ["I", "love", "You", "Lord"]
    assert t.words[1].start == pytest.approx(0.2)
    assert t.words[3].prob == pytest.approx(0.2)


def test_usable_words_filters_low_prob(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    t = transcribe_vocal(stem, model=FakeModel(), use_cache=False)
    usable = t.usable_words(min_prob=0.35)
    assert [w.text for w in usable] == ["I", "love", "You"]  # "Lord" @0.2 dropped


def test_cache_write_and_hit(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    fake = FakeModel()
    first = transcribe_vocal(stem, model=fake)
    cache = transcript_cache_path(stem)
    assert cache.is_file()
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data["engine"] == "stable-ts"
    assert data["model_size"] == "small"
    assert len(data["words"]) == 4

    # Second call must be served from cache — poisoned model never invoked.
    second = transcribe_vocal(stem, model=PoisonedModel())
    assert second.words == first.words
    assert fake.calls == 1


def test_cache_invalidated_on_source_change(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    transcribe_vocal(stem, model=FakeModel())
    # Rewrite the stem with different content (size + mtime change).
    _write_fake_wav(stem, duration_sec=3.0)
    fake2 = FakeModel()
    transcribe_vocal(stem, model=fake2)
    assert fake2.calls == 1  # cache miss → re-transcribed


def test_cache_invalidated_on_model_size_change(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    transcribe_vocal(stem, model=FakeModel(), model_size="small")
    fake2 = FakeModel()
    transcribe_vocal(stem, model=fake2, model_size="large-v3")
    assert fake2.calls == 1


def test_corrupt_cache_is_ignored(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    transcript_cache_path(stem).write_text("{not json", encoding="utf-8")
    fake = FakeModel()
    t = transcribe_vocal(stem, model=fake)
    assert fake.calls == 1
    assert len(t.words) == 4


def test_missing_stem_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="vocal stem not found"):
        transcribe_vocal(tmp_path / "nope.wav", model=FakeModel())


def test_cache_round_trip_preserves_transcript(tmp_path: Path) -> None:
    stem = tmp_path / "vocals.wav"
    _write_fake_wav(stem)
    original = transcribe_vocal(stem, model=FakeModel())
    reloaded = transcribe_vocal(stem, model=PoisonedModel())
    assert isinstance(reloaded, Transcript)
    assert reloaded.words == original.words
    assert all(isinstance(w, TranscriptWord) for w in reloaded.words)


# ---------------------------------------------------------------------------
# find_degenerate_segments (pure)
# ---------------------------------------------------------------------------


def _seg(start: float, end: float, n_words: int = 4) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "words": [{"word": f"w{i}", "start": start, "end": end} for i in range(n_words)],
    }


def test_degenerate_none_for_healthy_segments() -> None:
    segments = [_seg(0.0, 3.0), _seg(3.5, 6.0), _seg(6.5, 9.0)]
    assert find_degenerate_segments(segments) == set()


def test_degenerate_tiny_duration_with_words() -> None:
    segments = [_seg(0.0, 3.0), _seg(3.0, 3.05), _seg(4.0, 7.0)]
    assert find_degenerate_segments(segments) == {1}


def test_degenerate_tiny_duration_few_words_ok() -> None:
    # A 2-word interjection squeezed into 0.15 s is plausible singing.
    segments = [_seg(0.0, 3.0), _seg(3.0, 3.15, n_words=2), _seg(4.0, 7.0)]
    assert find_degenerate_segments(segments) == set()


def test_degenerate_non_monotonic() -> None:
    segments = [_seg(5.0, 8.0), _seg(2.0, 4.0)]  # second starts before first
    assert 1 in find_degenerate_segments(segments)


def test_degenerate_end_before_start() -> None:
    segments = [_seg(2.0, 1.0)]
    assert 0 in find_degenerate_segments(segments)


def test_degenerate_compressed_run() -> None:
    # PsalmsKaraoke failure mode: several lines squashed into slivers after a
    # long instrumental intro. Each individually passes the word-count rule
    # (2 words) but the run is collectively degenerate.
    segments = [
        _seg(0.0, 3.0),
        _seg(30.0, 30.3, n_words=2),
        _seg(30.3, 30.6, n_words=2),
        _seg(30.6, 30.9, n_words=2),
        _seg(31.0, 34.0),
    ]
    bad = find_degenerate_segments(segments)
    assert {1, 2, 3} <= bad
    assert 0 not in bad and 4 not in bad
