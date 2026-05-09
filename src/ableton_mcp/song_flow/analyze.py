"""song_analyze: snapshot the active Ableton song.

Returns the Live-reported tempo + length and a librosa key estimate from a
short bounced slice. Two ground-truth sources for "what key is this in?":

- ``scale_hint`` — Live's own Scale UI feature (root_note + scale_name from
  the LOM). May be unset (the user might not have configured it).
- ``detected_key`` — librosa chroma estimate from a 30-second bounce. This
  is what ``song_transpose`` defaults to using as ``source_key`` if the
  caller doesn't supply one.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..bounce.resampling import bounce_song_via_resampling
from ..osc_client import get_client

log = logging.getLogger(__name__)

PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# How many seconds to bounce for the key-estimation slice. Long enough to
# settle chroma over a typical chord progression, short enough to keep the
# realtime cost down.
SLICE_DURATION_SEC = 30.0


async def _live_state() -> dict[str, Any]:
    """Read tempo + length + Live scale UI hint via OSC.

    Mirrors the small slice of ``live_get_state`` that the song flow needs,
    avoiding the full transport snapshot.
    """
    client = await get_client()

    async def g(addr: str) -> Any:
        return (await client.request(addr))[0]

    tempo = float(await g("/live/song/get/tempo"))
    length_beats = float(await g("/live/song/get/song_length"))
    try:
        root_note = int(await g("/live/song/get/root_note"))
    except Exception:
        root_note = -1
    try:
        scale_name = str(await g("/live/song/get/scale_name"))
    except Exception:
        scale_name = ""

    scale_hint: dict[str, Any] | None = None
    if 0 <= root_note < 12:
        scale_hint = {"root": PITCH_CLASS_NAMES[root_note], "scale": scale_name or None}

    return {
        "tempo": tempo,
        "length_beats": length_beats,
        "length_sec": length_beats * 60.0 / tempo if tempo > 0 else 0.0,
        "scale_hint": scale_hint,
    }


def _estimate_key(audio_path: str, sr: int = 22050) -> str:
    """Run librosa chroma key estimation on a wav. Returns e.g. ``"C#"``."""
    import librosa  # local import: keep song_flow import-light for tests
    import numpy as np

    y, _sample_rate = librosa.load(audio_path, sr=sr, mono=True)
    if y.size == 0:
        raise ValueError(f"empty audio: {audio_path}")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    return PITCH_CLASS_NAMES[int(np.argmax(chroma.mean(axis=1)))]


async def analyze_song(
    output_dir: str | Path | None = None,
    slice_duration_sec: float | None = None,
) -> dict[str, Any]:
    """Bounce a short slice and report tempo + length + estimated key.

    Args:
        output_dir: where to write the slice wav. Defaults to
                    ``data/song_flow/<timestamp>/``.
        slice_duration_sec: override the default 30 s slice. Capped at the
                            actual song length.

    Returns:
        ``{tempo, length_beats, length_sec, detected_key, scale_hint,
           sample_path}``.
    """
    state = await _live_state()
    if state["tempo"] <= 0 or state["length_beats"] <= 0:
        return {
            "status": "error",
            "error": "Live reports tempo/length=0 — is a Set actually loaded?",
            **state,
        }

    target_dur = float(slice_duration_sec or SLICE_DURATION_SEC)
    duration = min(target_dur, state["length_sec"])
    if duration <= 0:
        return {
            "status": "error",
            "error": f"computed slice duration {duration}s is non-positive",
            **state,
        }

    if output_dir is None:
        output_dir = Path("data/song_flow") / time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_path = out_dir / "sample.wav"

    bounce_result = await bounce_song_via_resampling(
        str(sample_path), duration_sec=duration
    )
    if not bounce_result.get("copied"):
        return {
            "status": "error",
            "error": f"bounce failed: {bounce_result.get('reason') or bounce_result}",
            **state,
        }

    detected_key = _estimate_key(str(sample_path))

    return {
        "status": "ok",
        "tempo": state["tempo"],
        "length_beats": state["length_beats"],
        "length_sec": state["length_sec"],
        "detected_key": detected_key,
        "scale_hint": state["scale_hint"],
        "sample_path": str(sample_path.resolve()),
        "slice_duration_sec": duration,
    }
