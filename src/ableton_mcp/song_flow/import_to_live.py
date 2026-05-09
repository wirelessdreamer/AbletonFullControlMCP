"""song_import_variations_to_live: bulk-import variation wavs into the
active Live set.

For each ``{label, wav_path}`` we create a fresh audio track named
``"Variation: <label>"`` and load the wav into clip slot 0 via
``browser.load_sample``. Failures are recorded per-variation rather than
aborting the batch — typical failure mode is "wav at unexpected path",
which is recoverable without restarting the whole flow.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from ..bridge_client import AbletonBridgeError, get_bridge_client
from ..osc_client import get_client

log = logging.getLogger(__name__)


async def _create_audio_track(at_index: int, name: str) -> int:
    """Create an audio track at ``at_index`` (-1 = end), name it, return its index.

    Mirrors the helper in ``tools/suno.py`` (kept inline to avoid creating a
    cross-tool dependency on a private name).
    """
    client = await get_client()
    client.send("/live/song/create_audio_track", int(at_index))
    n = int((await client.request("/live/song/get/num_tracks"))[0])
    new_index = n - 1 if at_index < 0 else at_index
    client.send("/live/track/set/name", new_index, name)
    return new_index


async def import_variations_to_live(
    variations: Sequence[dict[str, Any]],
    *,
    track_name_prefix: str = "Variation: ",
    clip_index: int = 0,
) -> dict[str, Any]:
    """Create one new audio track per variation and load its wav into clip slot 0.

    Args:
        variations: list of dicts with at minimum ``label`` and ``wav_path``.
                    Other keys are passed through unchanged.
        track_name_prefix: prefix for the new tracks. Empty string = bare label.
        clip_index: which session clip slot to load into. Default 0.

    Returns:
        ``{status, tracks: [{label, wav_path, track_index, clip_index, ...}]}``.
        Per-variation errors land in ``tracks[i]["error"]`` rather than
        raising.
    """
    if not variations:
        return {"status": "error", "error": "variations list is empty"}

    bridge = get_bridge_client()
    out_tracks: list[dict[str, Any]] = []

    for v in variations:
        label = str(v.get("label") or "variation")
        wav_path = str(v.get("wav_path") or "")
        if not wav_path:
            out_tracks.append({"label": label, "error": "missing wav_path"})
            continue
        track_name = f"{track_name_prefix}{label}"
        try:
            new_index = await _create_audio_track(-1, track_name)
        except Exception as exc:  # noqa: BLE001 — surface as per-track failure
            out_tracks.append({
                "label": label, "wav_path": wav_path,
                "error": f"track create failed: {exc!r}",
            })
            continue

        try:
            await bridge.call(
                "browser.load_sample",
                path=wav_path, track_index=new_index, clip_index=int(clip_index),
            )
        except (AbletonBridgeError, Exception) as exc:  # noqa: BLE001
            out_tracks.append({
                "label": label, "wav_path": wav_path,
                "track_index": new_index,
                "error": f"browser.load_sample failed: {exc!r}",
            })
            continue

        out_tracks.append({
            "label": label,
            "wav_path": wav_path,
            "track_index": new_index,
            "clip_index": int(clip_index),
            "track_name": track_name,
        })

    n_ok = sum(1 for t in out_tracks if "error" not in t)
    return {
        "status": "ok" if n_ok == len(variations) else "partial",
        "n_variations": len(variations),
        "n_imported": n_ok,
        "tracks": out_tracks,
    }
