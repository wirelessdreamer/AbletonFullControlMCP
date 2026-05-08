"""Audio feature extraction for sound matching.

A :class:`Features` row condenses a piece of audio into a fixed-dimension
timbre fingerprint: 13 MFCC means + 13 MFCC stds + four spectral scalars +
RMS + ZCR. The ``feature_vector`` helper flattens that into a numpy array
suitable for kNN distance, sklearn, scipy.optimize, etc.

Anything that wants to compare two sounds should agree on
``FEATURE_VECTOR_DIM`` — change it here and the dataset round-trips remain
consistent because we serialise raw float32 bytes keyed by length.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

# Number of MFCC coefficients we keep. Matches ``audio_analysis.audio_analyze``
# so feature dimensions are consistent across the codebase.
N_MFCC = 13

# 13 mfcc_mean + 13 mfcc_std + centroid + bandwidth + rolloff + zcr + rms + flatness
FEATURE_VECTOR_DIM = N_MFCC * 2 + 6


@dataclass(frozen=True)
class Features:
    """Compact timbre fingerprint."""

    mfcc_mean: np.ndarray  # shape (13,)
    mfcc_std: np.ndarray  # shape (13,)
    spectral_centroid: float
    spectral_bandwidth: float
    spectral_rolloff: float
    zcr: float
    rms: float
    spectral_flatness: float
    sample_rate: int = 22050
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        """JSON-serialisable view (numpy arrays → lists)."""
        return {
            "mfcc_mean": self.mfcc_mean.tolist(),
            "mfcc_std": self.mfcc_std.tolist(),
            "spectral_centroid": float(self.spectral_centroid),
            "spectral_bandwidth": float(self.spectral_bandwidth),
            "spectral_rolloff": float(self.spectral_rolloff),
            "zcr": float(self.zcr),
            "rms": float(self.rms),
            "spectral_flatness": float(self.spectral_flatness),
            "sample_rate": int(self.sample_rate),
            "duration_sec": float(self.duration_sec),
        }


def extract_features(audio: np.ndarray, sr: int = 22050) -> Features:
    """Compute timbre features from a mono float audio buffer.

    Silent / empty inputs return a zero-filled :class:`Features` rather than
    raising — useful when a probe cell renders silence (e.g. cutoff at 0Hz).
    """
    import librosa  # local import keeps module import cheap for unit tests

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        # Down-mix to mono if the caller passed (channels, samples) or stereo.
        audio = audio.mean(axis=0) if audio.shape[0] < audio.shape[-1] else audio.mean(axis=-1)

    n = int(audio.shape[0])
    duration = n / float(sr) if sr > 0 else 0.0

    if n == 0 or not np.any(np.isfinite(audio)) or float(np.max(np.abs(audio))) < 1e-9:
        return Features(
            mfcc_mean=np.zeros(N_MFCC, dtype=np.float32),
            mfcc_std=np.zeros(N_MFCC, dtype=np.float32),
            spectral_centroid=0.0,
            spectral_bandwidth=0.0,
            spectral_rolloff=0.0,
            zcr=0.0,
            rms=0.0,
            spectral_flatness=0.0,
            sample_rate=int(sr),
            duration_sec=duration,
        )

    # Replace any non-finite samples to keep librosa happy.
    audio = np.nan_to_num(audio, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=N_MFCC)
    mfcc_mean = mfcc.mean(axis=1).astype(np.float32)
    mfcc_std = mfcc.std(axis=1).astype(np.float32)

    centroid = float(librosa.feature.spectral_centroid(y=audio, sr=sr).mean())
    bandwidth = float(librosa.feature.spectral_bandwidth(y=audio, sr=sr).mean())
    rolloff = float(librosa.feature.spectral_rolloff(y=audio, sr=sr).mean())
    zcr = float(librosa.feature.zero_crossing_rate(audio).mean())
    rms = float(librosa.feature.rms(y=audio).mean())
    flatness = float(librosa.feature.spectral_flatness(y=audio).mean())

    return Features(
        mfcc_mean=mfcc_mean,
        mfcc_std=mfcc_std,
        spectral_centroid=centroid,
        spectral_bandwidth=bandwidth,
        spectral_rolloff=rolloff,
        zcr=zcr,
        rms=rms,
        spectral_flatness=flatness,
        sample_rate=int(sr),
        duration_sec=duration,
    )


def feature_vector(features: Features) -> np.ndarray:
    """Flatten a :class:`Features` to a 1-D float32 numpy array.

    Layout: [mfcc_mean(13), mfcc_std(13), centroid, bandwidth, rolloff, zcr, rms, flatness]
    """
    parts: list[np.ndarray] = [
        np.asarray(features.mfcc_mean, dtype=np.float32),
        np.asarray(features.mfcc_std, dtype=np.float32),
        np.array(
            [
                features.spectral_centroid,
                features.spectral_bandwidth,
                features.spectral_rolloff,
                features.zcr,
                features.rms,
                features.spectral_flatness,
            ],
            dtype=np.float32,
        ),
    ]
    out = np.concatenate(parts, axis=0).astype(np.float32, copy=False)
    if out.shape[0] != FEATURE_VECTOR_DIM:  # pragma: no cover — invariant
        raise AssertionError(f"feature_vector dim {out.shape[0]} != {FEATURE_VECTOR_DIM}")
    return out


def feature_distance(a: np.ndarray, b: np.ndarray, metric: str = "cosine") -> float:
    """Distance between two feature vectors.

    ``cosine`` returns 1 - cos_sim (so 0 = identical, 1 = orthogonal). ``euclidean``
    is plain L2. Cosine is robust to overall loudness differences (which RMS already
    captures separately) so it's the default for kNN.
    """
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if metric == "euclidean":
        return float(np.linalg.norm(a - b))
    if metric == "cosine":
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom < 1e-12:
            return 1.0
        return float(1.0 - (a @ b) / denom)
    raise ValueError(f"unknown metric: {metric!r}")


def stack_feature_vectors(features: Iterable[Features]) -> np.ndarray:
    """Concatenate many :class:`Features` into an (N, FEATURE_VECTOR_DIM) matrix."""
    rows = [feature_vector(f) for f in features]
    if not rows:
        return np.zeros((0, FEATURE_VECTOR_DIM), dtype=np.float32)
    return np.stack(rows, axis=0).astype(np.float32, copy=False)


# Public list of names the planner / explainer use when reporting which feature
# dimensions moved most. Index-aligned with ``feature_vector``.
FEATURE_VECTOR_NAMES: list[str] = (
    [f"mfcc_mean_{i}" for i in range(N_MFCC)]
    + [f"mfcc_std_{i}" for i in range(N_MFCC)]
    + [
        "spectral_centroid",
        "spectral_bandwidth",
        "spectral_rolloff",
        "zcr",
        "rms",
        "spectral_flatness",
    ]
)


@dataclass
class _FeatureSummary:
    """Internal helper used by ``sound_explain_parameter`` to rank movement."""

    name: str
    delta: float
    rel_delta: float = field(default=0.0)
