"""Dataclasses for lyric-aligned song section detection.

Named ``song_sections`` (and ``DetectedSection``) deliberately — the repo
already has two unrelated ``Section`` classes (:mod:`ableton_mcp.section`'s
featured-region detector and :mod:`ableton_mcp.structure.model`'s bar-counted
section); this module must not add a third.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptWord:
    """One recognized word with timing from stable-ts."""

    text: str
    start: float
    end: float
    prob: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": self.start, "end": self.end, "prob": self.prob}


@dataclass(frozen=True)
class Transcript:
    """Free transcription of a vocal stem (word-level timestamps)."""

    words: tuple[TranscriptWord, ...]
    language: str
    model_size: str
    source_path: str

    def usable_words(self, min_prob: float = 0.35) -> tuple[TranscriptWord, ...]:
        """Words above the hallucination-guard probability threshold."""
        return tuple(w for w in self.words if w.prob >= min_prob)


@dataclass(frozen=True)
class MatchedBlock:
    """One chart section matched to a span of transcript words."""

    label: str
    score: float           # rapidfuzz ratio, 0-100
    word_start: int        # index into the word list used for matching (inclusive)
    word_end: int          # exclusive
    start_sec: float
    end_sec: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "score": self.score,
            "word_span": [self.word_start, self.word_end],
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
        }


@dataclass(frozen=True)
class DetectedSection:
    """One section boundary in the recording, ready for locator writing.

    ``end_sec`` tiles the timeline (next section's start; audio duration for
    the last) — the actual sung span lives in ``match["sung_span_sec"]``.
    """

    label: str
    display_name: str      # repeat-suffixed: "Chorus", "Chorus 2", ...
    role: str
    kind: str              # "vocal" | "instrumental_gap"
    start_sec: float
    end_sec: float
    confidence: float
    status: str = "matched"          # "matched" | "extra"
    chart_index: int | None = None   # index into the chart sequence, None for extras
    start_sec_raw: float | None = None
    snap_delta_sec: float | None = None
    start_beats: float | None = None
    match: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "display_name": self.display_name,
            "role": self.role,
            "kind": self.kind,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "confidence": self.confidence,
            "status": self.status,
            "chart_index": self.chart_index,
            "start_sec_raw": self.start_sec_raw,
            "snap_delta_sec": self.snap_delta_sec,
            "start_beats": self.start_beats,
            "match": self.match,
            "warnings": list(self.warnings),
        }


@dataclass
class SectionsResult:
    """Full detection result — becomes the ``sections.json`` sidecar."""

    sections: list[DetectedSection]
    chart_sequence: list[str]
    performed_sequence: list[dict[str, Any]]
    tempo: dict[str, Any]
    source: dict[str, Any]
    pco: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": self.source,
            "pco": self.pco,
            "tempo": self.tempo,
            "chart_sequence": self.chart_sequence,
            "performed_sequence": self.performed_sequence,
            "sections": [s.to_dict() for s in self.sections],
            "warnings": self.warnings,
        }
