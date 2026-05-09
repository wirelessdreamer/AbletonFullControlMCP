"""Sum stem WAVs into a single mix WAV.

Pure offline operation — no Live, no audio routing. Useful when the bounce
backend produces per-track stems and you also want a single full-mix file.

The default mixdown sums all stems with unit gain (sum_of_stems), then
optionally normalises so peak = 0.99 * full-scale to prevent clipping.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import soundfile as sf


def mix_stems_to_master(
    stem_paths: Sequence[str | os.PathLike],
    output_path: str | os.PathLike,
    *,
    normalize: bool = True,
    headroom_db: float = 0.1,
    target_samplerate: int | None = None,
    gains_db: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Sum N stem WAVs into one mix WAV.

    Args:
        stem_paths: list of input WAVs. Must all have the same sample rate
                    unless `target_samplerate` is given (in which case stems
                    are read at their own rate; mismatches will raise).
        output_path: where to write the mix.
        normalize: if True, scale so the peak hits `-headroom_db` dBFS.
                   If False, you keep the raw sum (which may clip).
        headroom_db: dB of headroom under 0 dBFS when normalising.
        target_samplerate: if set, asserts all stems match this rate.
        gains_db: optional per-stem gain in dB applied before summing.
                  Length must equal len(stem_paths). Used by the song-flow
                  variations path to produce instrument-up remixes
                  (focal stem +6, others -3, etc.). None = unity gain.

    Returns dict with output info (path, samplerate, num_frames, peak_dbfs,
    num_stems_summed, channels).
    """
    if not stem_paths:
        raise ValueError("stem_paths is empty")
    if gains_db is not None and len(gains_db) != len(stem_paths):
        raise ValueError(
            f"gains_db length {len(gains_db)} must match stem_paths length {len(stem_paths)}"
        )
    inputs: list[tuple[np.ndarray, int]] = []
    for p in stem_paths:
        path = Path(p)
        if not path.exists():
            raise FileNotFoundError(f"stem not found: {path}")
        data, sr = sf.read(str(path), always_2d=True)
        inputs.append((data.astype(np.float32), int(sr)))

    sr_set = {sr for _, sr in inputs}
    if len(sr_set) != 1:
        raise ValueError(f"sample rates differ across stems: {sr_set}")
    sr = next(iter(sr_set))
    if target_samplerate is not None and target_samplerate != sr:
        raise ValueError(f"stems are {sr} Hz; expected {target_samplerate}")

    # Pad to longest, sum.
    max_len = max(d.shape[0] for d, _ in inputs)
    max_ch = max(d.shape[1] for d, _ in inputs)
    mix = np.zeros((max_len, max_ch), dtype=np.float32)
    for i, (d, _) in enumerate(inputs):
        # Broadcast mono to stereo if needed.
        if d.shape[1] == 1 and max_ch == 2:
            d = np.repeat(d, 2, axis=1)
        elif d.shape[1] == 2 and max_ch == 1:
            d = d.mean(axis=1, keepdims=True)
        if gains_db is not None:
            d = d * np.float32(10.0 ** (float(gains_db[i]) / 20.0))
        mix[: d.shape[0]] += d

    peak = float(np.max(np.abs(mix)) or 1e-12)
    peak_dbfs = 20.0 * float(np.log10(peak))
    if normalize:
        target_peak = 10.0 ** (-headroom_db / 20.0)
        gain = target_peak / peak
        mix = mix * gain
        peak = target_peak
        peak_dbfs = -headroom_db

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), mix, sr, subtype="PCM_24")

    return {
        "output_path": str(out),
        "samplerate": sr,
        "num_frames": int(mix.shape[0]),
        "channels": int(mix.shape[1]),
        "duration_sec": float(mix.shape[0] / sr),
        "num_stems_summed": len(inputs),
        "peak_dbfs": peak_dbfs,
        "normalized": normalize,
    }
