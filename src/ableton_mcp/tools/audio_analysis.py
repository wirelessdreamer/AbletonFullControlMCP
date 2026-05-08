"""Audio feature extraction (librosa)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def audio_analyze(path: str, sr: int = 22050) -> dict[str, Any]:
        """Extract a feature summary from an audio file: tempo, key estimate, MFCC mean, spectral stats."""
        import librosa

        p = Path(path)
        if not p.exists():
            return {"error": f"file not found: {path}"}

        y, sample_rate = librosa.load(str(p), sr=sr, mono=True)
        if y.size == 0:
            return {"error": "empty audio"}

        duration = float(librosa.get_duration(y=y, sr=sample_rate))
        tempo, _ = librosa.beat.beat_track(y=y, sr=sample_rate)

        # Key estimate via chroma → most prominent pitch class.
        chroma = librosa.feature.chroma_cqt(y=y, sr=sample_rate)
        chroma_mean = chroma.mean(axis=1)
        pitch_classes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key_pc = int(np.argmax(chroma_mean))

        # Spectral features.
        centroid = float(librosa.feature.spectral_centroid(y=y, sr=sample_rate).mean())
        bandwidth = float(librosa.feature.spectral_bandwidth(y=y, sr=sample_rate).mean())
        rolloff = float(librosa.feature.spectral_rolloff(y=y, sr=sample_rate).mean())
        zcr = float(librosa.feature.zero_crossing_rate(y).mean())
        rms = float(librosa.feature.rms(y=y).mean())

        # MFCC mean (13 coeffs) — useful as a compact timbre fingerprint.
        mfcc = librosa.feature.mfcc(y=y, sr=sample_rate, n_mfcc=13)
        mfcc_mean = mfcc.mean(axis=1).tolist()

        return {
            "path": str(p.resolve()),
            "duration_sec": duration,
            "sample_rate": int(sample_rate),
            "tempo_bpm": float(tempo),
            "estimated_key_pitch_class": pitch_classes[key_pc],
            "spectral": {
                "centroid_hz": centroid,
                "bandwidth_hz": bandwidth,
                "rolloff_hz": rolloff,
                "zero_crossing_rate": zcr,
                "rms": rms,
            },
            "mfcc_mean_13": mfcc_mean,
        }

    @mcp.tool()
    async def audio_compare(path_a: str, path_b: str, sr: int = 22050) -> dict[str, Any]:
        """Compare two audio files via cosine similarity of MFCC means (1.0 = identical timbre)."""
        import librosa

        def feat(p: str) -> np.ndarray:
            y, _ = librosa.load(p, sr=sr, mono=True)
            return librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13).mean(axis=1)

        a = feat(path_a)
        b = feat(path_b)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        sim = float(a @ b / denom)
        return {"similarity": sim, "path_a": path_a, "path_b": path_b}
