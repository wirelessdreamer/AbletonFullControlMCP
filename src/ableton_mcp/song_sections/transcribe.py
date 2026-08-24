"""stable-ts transcription + known-text alignment for the vocal stem.

The approach is lifted from the user's PsalmsKaraoke project: stable-ts
(``stable_whisper``) on 16 kHz mono audio, ``model.align(audio, text,
original_split=True)`` for known-text forced alignment. Free transcription
(``model.transcribe`` with word timestamps) feeds the discovery pass in
:mod:`.reconcile`; alignment refines the discovered order's line timings.

Heavy imports (``stable_whisper``, ``torch``, ``librosa``) happen inside
functions so this module imports cleanly without them (same discipline as
:mod:`ableton_mcp.click_track`).

Transcripts are cached next to the stem (``vocals.transcript.json``) keyed
on source size+mtime and model size — Whisper on a 4-minute stem costs real
GPU seconds, and reconciliation-threshold tuning shouldn't re-pay it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .model import Transcript, TranscriptWord

log = logging.getLogger(__name__)

TRANSCRIPT_SCHEMA_VERSION = 1
DEFAULT_MODEL_SIZE = "small"
"""PsalmsKaraoke's proven default; bump to "large-v3" for accuracy at ~4x cost."""

MIN_WORD_PROB = 0.35
"""Words below this probability are hallucination-suspect (Demucs bleed makes
Whisper invent text in instrumental gaps) and are excluded from matching."""


def _load_stable_whisper() -> Any:
    try:
        import stable_whisper
    except ImportError as exc:  # pragma: no cover - exercised via fake-module tests
        raise RuntimeError(
            "stable-ts not installed. Add via "
            "`pip install ableton-full-control-mcp[song_sections]` or "
            "`pip install stable-ts`."
        ) from exc
    return stable_whisper


def _default_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def load_model(model_size: str = DEFAULT_MODEL_SIZE, device: str | None = None) -> Any:
    """Load a stable-ts Whisper model (downloads the checkpoint on first use)."""
    stable_whisper = _load_stable_whisper()
    resolved = device or _default_device()
    log.info("stable-ts: loading %s on %s", model_size, resolved)
    return stable_whisper.load_model(model_size, device=resolved)


def load_audio_16k(audio_path: str | Path) -> Any:
    """Load audio as 16 kHz mono float32 (what Whisper expects).

    Done with librosa/soundfile (already project deps) instead of shelling
    out to ffmpeg — passing the array to stable-ts skips its own ffmpeg
    decode entirely.
    """
    import librosa

    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"audio file not found: {path}")
    audio, _sr = librosa.load(str(path), sr=16000, mono=True)
    return audio


# ---------------------------------------------------------------------------
# Transcript cache
# ---------------------------------------------------------------------------


def transcript_cache_path(vocal_stem_path: str | Path) -> Path:
    p = Path(vocal_stem_path)
    return p.with_name(p.stem + ".transcript.json")


def _source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _read_cache(cache_path: Path, fingerprint: dict[str, Any], model_size: str) -> Transcript | None:
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    src = data.get("source") or {}
    if (
        data.get("schema_version") != TRANSCRIPT_SCHEMA_VERSION
        or data.get("model_size") != model_size
        or src.get("size_bytes") != fingerprint["size_bytes"]
        or src.get("mtime_ns") != fingerprint["mtime_ns"]
    ):
        return None
    words = tuple(
        TranscriptWord(
            text=str(w["text"]),
            start=float(w["start"]),
            end=float(w["end"]),
            prob=float(w.get("prob", 1.0)),
        )
        for w in data.get("words", [])
    )
    return Transcript(
        words=words,
        language=str(data.get("language", "en")),
        model_size=model_size,
        source_path=fingerprint["path"],
    )


def _write_cache(cache_path: Path, transcript: Transcript, fingerprint: dict[str, Any]) -> None:
    payload = {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "engine": "stable-ts",
        "model_size": transcript.model_size,
        "language": transcript.language,
        "source": fingerprint,
        "words": [w.to_dict() for w in transcript.words],
    }
    cache_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Transcription + alignment
# ---------------------------------------------------------------------------


def _words_from_result(result: Any) -> list[TranscriptWord]:
    """Flatten a stable-ts WhisperResult into timed words."""
    words: list[TranscriptWord] = []
    for seg in result.segments_to_dicts():
        for w in seg.get("words") or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            words.append(
                TranscriptWord(
                    text=text,
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    prob=float(w.get("probability", 1.0)),
                )
            )
    return words


def transcribe_vocal(
    vocal_stem_path: str | Path,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    device: str | None = None,
    language: str = "en",
    use_cache: bool = True,
    model: Any = None,
) -> Transcript:
    """Free-transcribe the vocal stem with word timestamps (cached).

    Args:
        vocal_stem_path: the Demucs ``vocals.wav``.
        model_size: Whisper checkpoint name (``small`` default).
        device: ``cuda``/``cpu``; auto-detected when None.
        language: forced language for Whisper.
        use_cache: read/write ``<stem>.transcript.json`` next to the stem.
        model: pre-loaded model (the pipeline loads once and shares it).
    """
    path = Path(vocal_stem_path)
    if not path.is_file():
        raise FileNotFoundError(f"vocal stem not found: {path}")
    fingerprint = _source_fingerprint(path)
    cache_path = transcript_cache_path(path)

    if use_cache:
        cached = _read_cache(cache_path, fingerprint, model_size)
        if cached is not None:
            log.info("transcript cache hit: %s", cache_path)
            return cached

    if model is None:
        model = load_model(model_size, device)
    audio = load_audio_16k(path)
    result = model.transcribe(audio, language=language, word_timestamps=True)
    transcript = Transcript(
        words=tuple(_words_from_result(result)),
        language=language,
        model_size=model_size,
        source_path=str(path),
    )
    if use_cache:
        _write_cache(cache_path, transcript, fingerprint)
    return transcript


def align_lyrics(
    vocal_stem_path: str | Path,
    text: str,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    device: str | None = None,
    language: str = "en",
    model: Any = None,
) -> list[dict[str, Any]]:
    """Force-align known lyric ``text`` against the stem (PsalmsKaraoke style).

    ``original_split=True`` keeps the input's line structure so each lyric
    line comes back as one segment dict with ``start``/``end``/``words``.
    """
    if model is None:
        model = load_model(model_size, device)
    audio = load_audio_16k(vocal_stem_path)
    result = model.align(audio, text, language=language, original_split=True)
    return list(result.segments_to_dicts())


# ---------------------------------------------------------------------------
# Degenerate-segment detection (pure)
# ---------------------------------------------------------------------------


def find_degenerate_segments(
    segments: list[dict[str, Any]],
    *,
    min_duration_sec: float = 0.2,
    min_words_for_duration_check: int = 3,
    compressed_run_len: int = 2,
    compressed_run_sec_per_segment: float = 0.5,
) -> set[int]:
    """Indices of alignment segments that cannot be trusted.

    stable-whisper's known failure mode (documented in PsalmsKaraoke):
    when audio has long instrumental stretches or low-confidence regions it
    compresses multiple lyric lines into tiny time-slices. Flags:

    - a segment squeezing ≥ ``min_words_for_duration_check`` words into
      under ``min_duration_sec``;
    - non-monotonic timestamps (segment starts before its predecessor, or
      ends before it starts);
    - runs of ≥ ``compressed_run_len`` consecutive segments averaging under
      ``compressed_run_sec_per_segment`` each.
    """
    bad: set[int] = set()
    prev_start = float("-inf")
    for i, seg in enumerate(segments):
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        n_words = len(seg.get("words") or []) or len(str(seg.get("text", "")).split())
        if end < start or start < prev_start:
            bad.add(i)
        if (end - start) < min_duration_sec and n_words >= min_words_for_duration_check:
            bad.add(i)
        prev_start = start

    # Compressed runs.
    run_start = 0
    while run_start < len(segments):
        run_end = run_start
        total = 0.0
        while run_end < len(segments):
            seg = segments[run_end]
            dur = float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))
            if dur >= compressed_run_sec_per_segment:
                break
            total += max(dur, 0.0)
            run_end += 1
        run_len = run_end - run_start
        if run_len >= compressed_run_len and total < compressed_run_sec_per_segment * run_len:
            bad.update(range(run_start, run_end))
        run_start = max(run_end, run_start + 1)
    return bad
