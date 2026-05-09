"""song_make_variations: instrument-up remixes from a stem set.

Given the ``[{name, path}]`` shape that ``stems_split`` already returns,
produce:

- ``original.wav`` — sum of every stem at unity gain (sanity-check that
  the stems still recombine close to the source mix).
- ``instrumental.wav`` — sum of every stem except ``vocals`` at unity.
- ``<stem>_up.wav`` — sum where the focal stem is +``boost_db`` and every
  other stem sits at ``attenuation_db``. One file per stem.

For a 6-stem htdemucs_6s split (``drums / bass / other / vocals / guitar
/ piano``) that's 1 + 1 + 6 = **8 variations**.

mp3 encoding is opt-in and best-effort — if ffmpeg isn't on PATH the
wav still lands; we surface ``mp3_skipped`` per variation rather than
failing the whole batch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from ..bounce.mix import mix_stems_to_master
from ..bounce.mp3 import FFmpegMissing, encode_wav_to_mp3

log = logging.getLogger(__name__)

VOCAL_STEM_NAMES = {"vocals", "vocal", "voice"}


def _safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in (s or "stem"))[:40] or "stem"


def _maybe_encode_mp3(wav_path: Path, bitrate_kbps: int) -> dict[str, Any]:
    mp3_path = wav_path.with_suffix(".mp3")
    try:
        encode_wav_to_mp3(str(wav_path), str(mp3_path), bitrate_kbps=bitrate_kbps)
        return {"mp3_path": str(mp3_path.resolve())}
    except FFmpegMissing as exc:
        return {"mp3_skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001 — surface ffmpeg errors as data
        return {"mp3_skipped": f"{type(exc).__name__}: {exc}"}


def make_variations(
    stems: Sequence[dict[str, Any]],
    output_dir: str | Path,
    *,
    boost_db: float = 6.0,
    attenuation_db: float = -3.0,
    encode_mp3: bool = True,
    bitrate_kbps: int = 192,
    normalize: bool = True,
    headroom_db: float = 0.1,
) -> dict[str, Any]:
    """Produce instrument-up remixes from a stem set.

    Args:
        stems: list of ``{"name": <stem name>, "path": <wav path>}``.
        output_dir: directory to write the variation wavs (and optional mp3s).
        boost_db: gain applied to the focal stem in each ``<name>_up`` mix.
        attenuation_db: gain applied to non-focal stems in those mixes.
        encode_mp3: emit an mp3 next to each wav if ffmpeg is on PATH.
        bitrate_kbps: mp3 bitrate when encoding.
        normalize / headroom_db: passed to ``mix_stems_to_master``.

    Returns:
        ``{status, variations: [{label, kind, wav_path, mp3_path?, mp3_skipped?, ...}]}``.
    """
    if not stems:
        return {"status": "error", "error": "stems list is empty"}

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = [str(s["path"]) for s in stems]
    names = [str(s["name"]) for s in stems]
    n = len(stems)

    variations: list[dict[str, Any]] = []

    def _record(label: str, kind: str, gains: list[float] | None) -> None:
        wav_path = out_dir / f"{_safe_filename(label)}.wav"
        mix_info = mix_stems_to_master(
            paths, wav_path,
            normalize=normalize, headroom_db=headroom_db,
            gains_db=gains,
        )
        entry: dict[str, Any] = {
            "label": label,
            "kind": kind,
            "wav_path": mix_info["output_path"],
            "duration_sec": mix_info.get("duration_sec"),
            "samplerate": mix_info.get("samplerate"),
        }
        if encode_mp3:
            entry.update(_maybe_encode_mp3(wav_path, bitrate_kbps))
        variations.append(entry)

    # 1. Original — every stem at unity, useful as a sanity-check that the
    #    stems still recombine to ~the source mix.
    _record("original", "original", gains=None)

    # 2. Instrumental — drop vocals.
    non_vocal_idxs = [
        i for i, name in enumerate(names) if name.lower() not in VOCAL_STEM_NAMES
    ]
    if non_vocal_idxs and len(non_vocal_idxs) < n:
        instrumental_paths = [paths[i] for i in non_vocal_idxs]
        wav_path = out_dir / "instrumental.wav"
        mix_info = mix_stems_to_master(
            instrumental_paths, wav_path,
            normalize=normalize, headroom_db=headroom_db,
        )
        entry = {
            "label": "instrumental",
            "kind": "instrumental",
            "wav_path": mix_info["output_path"],
            "duration_sec": mix_info.get("duration_sec"),
            "samplerate": mix_info.get("samplerate"),
        }
        if encode_mp3:
            entry.update(_maybe_encode_mp3(wav_path, bitrate_kbps))
        variations.append(entry)
    elif not non_vocal_idxs:
        log.warning("every stem appears to be a vocal stem; skipping instrumental")

    # 3. Per-stem instrument-up remixes.
    for i, name in enumerate(names):
        gains = [
            boost_db if j == i else attenuation_db
            for j in range(n)
        ]
        _record(f"{name}_up", "instrument_up", gains=gains)

    return {
        "status": "ok",
        "output_dir": str(out_dir.resolve()),
        "boost_db": boost_db,
        "attenuation_db": attenuation_db,
        "n_stems": n,
        "n_variations": len(variations),
        "variations": variations,
    }
