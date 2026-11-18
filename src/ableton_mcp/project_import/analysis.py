"""Conservative timing diagnostics: unknown evidence is never a passing result."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .timing import TempoMap


def grid_check(path: Path, bpm: float | None, tempo_points: list | None,
               downbeat_sec: float | None) -> tuple[TempoMap, dict[str, Any]]:
    import librosa

    audio, sr = librosa.load(path, sr=22050, mono=True)
    onset = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=256)
    estimate, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=256,
                                            trim=False)
    times = librosa.frames_to_time(frames, sr=sr, hop_length=256)
    detected = float(np.asarray(estimate).reshape(-1)[0])
    chosen = float(bpm if bpm is not None else detected if detected > 0 else 120)
    tempo = TempoMap([(float(b), float(v)) for b, v in tempo_points]
                     if tempo_points else [(0.0, chosen)])
    result: dict[str, Any] = {"status": "needs_review", "bpm": tempo.points[0][1],
                              "source": "supplied_map" if tempo_points else "supplied" if bpm else "estimated",
                              "detected_bpm": detected, "beat_count": len(times),
                              "downbeat_source": "supplied" if downbeat_sec is not None else "unknown"}
    if len(times) < 8 or not np.any(audio):
        result["reason"] = "Insufficient beat evidence; audio playback is preserved"
        return tempo, result
    beats = np.array([tempo.seconds_to_beat(float(t)) for t in times])
    # A pickup or spurious first beat must not choose the phase for the song.
    # Circular consensus estimates beat phase only, never the bar downbeat.
    phase = float(np.angle(np.mean(np.exp(2j * np.pi * beats))) / (2 * np.pi)) % 1
    origin = tempo.seconds_to_beat(downbeat_sec) if downbeat_sec is not None else phase
    residual = beats - origin - np.round(beats - origin)
    local_bpm = np.array([tempo.points[max(0, np.searchsorted(tempo.beats, b, side="right") - 1)][1]
                          for b in beats])
    errors_ms = np.abs(residual) * 60000 / local_bpm
    result.update({"p95_error_ms": float(np.percentile(errors_ms, 95)),
                   "median_error_ms": float(np.median(errors_ms)),
                   "coverage": float((times[-1] - times[0]) / max(len(audio) / sr, 0.001)),
                   "checkpoints_sec": [float(times[i]) for i in (0, len(times) // 2, -1)],
                   "phase_sec": tempo.beat_to_seconds(phase) if downbeat_sec is None else downbeat_sec})
    # An estimated downbeat is not established by a beat tracker. Make this actionable.
    if result["p95_error_ms"] <= 40 and result["coverage"] >= 0.65:
        result["status"] = "passed" if downbeat_sec is not None else "needs_review"
        result["reason"] = ("Distributed beat checks passed" if downbeat_sec is not None
                            else "Beat timing is consistent; supply downbeat_sec to confirm bar alignment")
    else:
        result["reason"] = "Beat timing conflicts with the grid or drifts; supply a shared tempo map"
    return tempo, result


def midi_alignment(path: Path, parts: list[dict], tempo: TempoMap,
                   audio_offset: float) -> dict[str, Any]:
    import librosa

    audio, sr = librosa.load(path, sr=22050, mono=True)
    onsets = librosa.onset.onset_detect(y=audio, sr=sr, hop_length=256, units="time")
    note_times = sorted({round(tempo.beat_to_seconds(n["start"]) - audio_offset, 6)
                         for part in parts for n in part["notes"]})
    result: dict[str, Any] = {"status": "needs_review", "note_onsets": len(note_times)}
    if len(onsets) < 8 or len(note_times) < 8:
        result["reason"] = "Sparse or sustained material cannot establish onset alignment automatically"
        return result
    times = np.asarray(note_times)
    indices = np.searchsorted(onsets, times)
    left = onsets[np.clip(indices - 1, 0, len(onsets) - 1)]
    right = onsets[np.clip(indices, 0, len(onsets) - 1)]
    closest = np.where(np.abs(left - times) < np.abs(right - times), left, right)
    delta = closest - times
    match = np.abs(delta) <= 0.05
    coverage = float(np.ptp(times) / max(len(audio) / sr, 0.001))
    result.update({"matched_fraction": float(np.mean(match)), "coverage": coverage,
                   "median_offset_ms": float(np.median(delta) * 1000),
                   "early_offset_ms": float(np.median(delta[:max(1, len(delta) // 3)]) * 1000),
                   "late_offset_ms": float(np.median(delta[-max(1, len(delta) // 3):]) * 1000)})
    # Compare complete onset patterns at competing offsets. Repeated metronomic
    # patterns cannot disambiguate a whole-beat shift and must remain reviewable.
    candidates = np.arange(-2.0, 2.001, 0.02)
    scores = []
    for shift in candidates:
        shifted = times + shift
        positions = np.searchsorted(onsets, shifted)
        a = onsets[np.clip(positions - 1, 0, len(onsets) - 1)]
        b = onsets[np.clip(positions, 0, len(onsets) - 1)]
        distance = np.minimum(np.abs(a - shifted), np.abs(b - shifted))
        scores.append(float(np.mean(np.exp(-(distance / 0.04) ** 2))))
    best = int(np.argmax(scores))
    competing = max(score for shift, score in zip(candidates, scores)
                    if abs(shift - candidates[best]) > 0.12)
    margin = scores[best] - competing
    unique_matches = len(set(closest[match])) / len(times)
    thirds = np.array_split(match, 3)
    result.update(best_offset_sec=float(candidates[best]), ambiguity_margin=margin,
                  unique_matched_fraction=unique_matches)
    if (abs(candidates[best]) <= 0.021 and margin >= 0.1 and coverage >= 0.65
            and unique_matches >= 0.85 and all(np.mean(region) >= 0.85 for region in thirds)):
        result["status"] = "passed"
        result["reason"] = "Distinct onset pattern aligns at early, middle and late checkpoints"
    elif abs(candidates[best]) > 0.021 and margin >= 0.1:
        result["reason"] = "Distinct onset pattern suggests a constant offset; supply offsets_seconds to correct it"
    elif margin < 0.1:
        result["reason"] = "Repeated onset patterns cannot disambiguate timing; confirm the source origin"
    else:
        result["reason"] = "MIDI onset timing disagrees with audio or changes across the song"
    return result
