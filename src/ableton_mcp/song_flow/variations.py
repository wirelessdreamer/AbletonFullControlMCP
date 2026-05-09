"""song_make_variations: instrument-up remixes from a stem set.

Two output styles, selected via ``output_set``:

**``output_set="remix"`` (default)** — the original conversational/remix shape:

- ``original.wav`` — sum of every stem at unity gain (sanity-check that
  the stems still recombine close to the source mix).
- ``instrumental.wav`` — sum of every stem except ``vocals`` at unity.
- ``<stem>_up.wav`` — sum where the focal stem is +``boost_db`` and every
  other stem sits at ``attenuation_db``. One file per stem.

For a 6-stem htdemucs_6s split (``drums / bass / other / vocals / guitar
/ piano``) that's 1 + 1 + 6 = **8 variations**.

**``output_set="practice_pack"``** — practice-track shape, with two variants
per non-vocal instrument so a player can rehearse with or without the
singer's part audible:

- ``no_vocals.wav`` — sum of every non-vocal stem at unity (the
  instrumental backing track).
- ``<stem>_boost_no_vocals.wav`` — focal stem +``boost_db``, other
  non-vocal stems at ``attenuation_db``, vocals dropped entirely.
- ``<stem>_boost_with_vocals.wav`` — same focal/non-vocal balance but
  with **vocals at unity** so the song still feels like the original
  recording while the player's instrument is forward.

For a 6-stem split with one vocals stem (5 instrument stems × 2 + 1) that's
**11 variations**. Vocals stems do not get their own boost track in
practice-pack mode.

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


def _is_vocal_stem(name: str) -> bool:
    """Match exact or substring vocal labels (e.g. 'Backing Vocals', 'Lead Vocal')."""
    n = (name or "").lower()
    if n in VOCAL_STEM_NAMES:
        return True
    return any(token in n for token in ("vocal", "voice"))


def _safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in (s or "stem"))[:60] or "stem"


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
    output_set: str = "remix",
    name_prefix: str = "",
    encode_mp3: bool = True,
    bitrate_kbps: int = 192,
    normalize: bool = True,
    headroom_db: float = 0.1,
) -> dict[str, Any]:
    """Produce instrument-up remixes (or practice-pack tracks) from a stem set.

    Args:
        stems: list of ``{"name": <stem name>, "path": <wav path>}``.
        output_dir: directory to write the variation wavs (and optional mp3s).
        boost_db: gain applied to the focal stem in each boosted mix.
        attenuation_db: gain applied to non-focal non-vocal stems in those mixes.
        output_set: ``"remix"`` (default) emits ``original`` + ``instrumental``
            + ``<stem>_up`` for every stem, vocals included as a stem (current
            behaviour, used by the conversational song-flow). ``"practice_pack"``
            emits ``no_vocals`` plus, for each non-vocal stem, two boosted
            variants — one with vocals dropped and one with vocals re-added at
            unity. See module docstring for full details.
        name_prefix: prepended to every output filename (sans extension).
            Useful for batch runs where multiple songs share an output dir;
            pass e.g. ``"Reasons - "`` to namespace files.
        encode_mp3: emit an mp3 next to each wav if ffmpeg is on PATH.
        bitrate_kbps: mp3 bitrate when encoding.
        normalize / headroom_db: passed to ``mix_stems_to_master``.

    Returns:
        ``{status, variations: [{label, kind, wav_path, mp3_path?, mp3_skipped?, ...}]}``.
    """
    if output_set not in ("remix", "practice_pack"):
        return {
            "status": "error",
            "error": f"Unknown output_set: {output_set!r}; must be 'remix' or 'practice_pack'",
        }
    if not stems:
        return {"status": "error", "error": "stems list is empty"}

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = [str(s["path"]) for s in stems]
    names = [str(s["name"]) for s in stems]
    n = len(stems)

    variations: list[dict[str, Any]] = []

    def _record(label: str, kind: str, gains: list[float] | None,
                stem_subset: list[int] | None = None) -> None:
        """Mix and record one variation.

        If ``stem_subset`` is provided, only those stem indices participate
        in the mix (and ``gains`` must be the same length as ``stem_subset``).
        Otherwise all stems are included.
        """
        if stem_subset is not None:
            mix_paths = [paths[i] for i in stem_subset]
            mix_gains = gains
        else:
            mix_paths = paths
            mix_gains = gains
        filename = f"{name_prefix}{_safe_filename(label)}.wav"
        wav_path = out_dir / filename
        mix_info = mix_stems_to_master(
            mix_paths, wav_path,
            normalize=normalize, headroom_db=headroom_db,
            gains_db=mix_gains,
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

    vocal_idxs = [i for i, nm in enumerate(names) if _is_vocal_stem(nm)]
    non_vocal_idxs = [i for i, nm in enumerate(names) if not _is_vocal_stem(nm)]

    if output_set == "remix":
        # 1. Original — every stem at unity, useful as a sanity-check that the
        #    stems still recombine to ~the source mix.
        _record("original", "original", gains=None)

        # 2. Instrumental — drop vocals.
        if non_vocal_idxs and len(non_vocal_idxs) < n:
            _record("instrumental", "instrumental",
                    gains=None, stem_subset=non_vocal_idxs)
        elif not non_vocal_idxs:
            log.warning("every stem appears to be a vocal stem; skipping instrumental")

        # 3. Per-stem instrument-up remixes (vocals included at attenuation_db
        #    when not the focal stem; gets its own boost track too).
        for i, name in enumerate(names):
            gains = [
                boost_db if j == i else attenuation_db
                for j in range(n)
            ]
            _record(f"{name}_up", "instrument_up", gains=gains)

    else:  # output_set == "practice_pack"
        if not non_vocal_idxs:
            return {
                "status": "error",
                "error": "practice_pack mode requires at least one non-vocal stem",
            }
        if not vocal_idxs:
            log.warning(
                "practice_pack: no vocal stem detected — 'with vocals' variants "
                "will be identical to 'no vocals' variants"
            )

        # 1. No Vocals — sum of every non-vocal stem at unity. The full
        #    instrumental backing track.
        _record("no_vocals", "no_vocals",
                gains=None, stem_subset=non_vocal_idxs)

        # 2. Per-instrument boost variants. For each non-vocal stem, produce:
        #    - <stem>_boost_no_vocals: focal +boost, other non-vocals -duck,
        #      vocals dropped entirely.
        #    - <stem>_boost_with_vocals: same but with vocals at unity (0 dB).
        for focal in non_vocal_idxs:
            focal_name = names[focal]

            # No-vocals variant (vocals dropped, only non-vocal stems mixed)
            nv_gains = [
                boost_db if i == focal else attenuation_db
                for i in non_vocal_idxs
            ]
            _record(
                f"{focal_name}_boost_no_vocals", "instrument_boost_no_vocals",
                gains=nv_gains, stem_subset=non_vocal_idxs,
            )

            # With-vocals variant (vocals at unity on top of the bed)
            wv_subset = non_vocal_idxs + vocal_idxs
            wv_gains = (
                [boost_db if i == focal else attenuation_db for i in non_vocal_idxs]
                + [0.0] * len(vocal_idxs)  # vocals at unity (0 dB)
            )
            _record(
                f"{focal_name}_boost_with_vocals", "instrument_boost_with_vocals",
                gains=wv_gains, stem_subset=wv_subset,
            )

    return {
        "status": "ok",
        "output_dir": str(out_dir.resolve()),
        "output_set": output_set,
        "boost_db": boost_db,
        "attenuation_db": attenuation_db,
        "n_stems": n,
        "n_variations": len(variations),
        "variations": variations,
    }
