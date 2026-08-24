"""Lyric-aligned song section detection.

Pipeline: PCO chart (labels + lyrics + sequence) + Demucs vocal stem →
stable-ts transcription → fuzzy discovery of the performed section order →
forced-alignment refinement → instrumental gap inference → downbeat
snapping → ``sections.json`` sidecar + Ableton arrangement locators.
"""

from .model import (
    DetectedSection,
    MatchedBlock,
    SectionsResult,
    Transcript,
    TranscriptWord,
)

__all__ = [
    "DetectedSection",
    "MatchedBlock",
    "SectionsResult",
    "Transcript",
    "TranscriptWord",
]
