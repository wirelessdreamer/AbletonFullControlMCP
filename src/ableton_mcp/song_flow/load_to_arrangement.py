"""song_load_wav_to_arrangement: drop a wav onto a fresh arrangement audio track.

This is the bridge between "wavs on disk" and the rest of the song-flow
(``song_transpose``, ``stems_split``, etc.) — those tools operate on the
*active Live arrangement*, so a standalone wav has to land in arrangement
view first.

Live 11.3.43's LOM doesn't expose a clean single-call "drop wav onto
arrangement" — `Track.create_audio_clip(name, position)` creates an empty
clip, and `clip.file_path` is read-only on most builds. The bridge
handler `clip.create_arrangement_audio_clip` probes a few signature
variants and reports honestly when none work, so we can route the user
to "drag the wav manually" instead of failing silently.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..bridge_client import get_bridge_client
from ..osc_client import get_client

log = logging.getLogger(__name__)

TEMP_TRACK_SUFFIX = " [song-flow-load]"


async def _create_audio_track(name: str) -> int:
    """Create an audio track at the end of the song, name it, return its index."""
    client = await get_client()
    client.send("/live/song/create_audio_track", -1)
    n = int((await client.request("/live/song/get/num_tracks"))[0])
    new_index = n - 1
    client.send("/live/track/set/name", new_index, name)
    return new_index


async def _delete_track(track_index: int) -> None:
    client = await get_client()
    client.send("/live/song/delete_track", int(track_index))


async def load_wav_to_arrangement(
    wav_path: str | Path,
    *,
    track_name: str | None = None,
    position_beats: float = 0.0,
) -> dict[str, Any]:
    """Create a fresh audio track and drop ``wav_path`` onto its arrangement
    timeline at ``position_beats``.

    Returns:
        ``{status, track_index, track_name, file_path, position, loaded,
           via, supported, workaround?}``.

        - ``status="ok"`` and ``loaded=True`` → wav is on the arrangement,
          ready for ``song_transpose`` to act on.
        - ``status="not_supported"`` → Live's LOM rejected every signature
          we tried. ``workaround`` tells the user what to do (drag manually).
          The temp track has been cleaned up.
        - ``status="error"`` → something else went wrong. Temp track
          cleaned up if possible.
    """
    p = Path(wav_path)
    if not p.exists():
        return {"status": "error", "error": f"wav not found: {wav_path}"}

    if track_name is None:
        track_name = p.stem + TEMP_TRACK_SUFFIX

    bridge = get_bridge_client()
    new_index: int | None = None
    try:
        new_index = await _create_audio_track(track_name)
        result = await bridge.call(
            "clip.create_arrangement_audio_clip",
            track_index=new_index,
            file_path=str(p.resolve()),
            position=float(position_beats),
        )
        if result.get("loaded"):
            return {
                "status": "ok",
                "track_index": new_index,
                "track_name": track_name,
                "file_path": str(p.resolve()),
                "position": float(position_beats),
                "length": result.get("length"),
                "loaded": True,
                "via": result.get("via"),
                "clip_file_path": result.get("clip_file_path"),
            }
        # Couldn't load. Clean up the empty track so we don't leave
        # detritus in the user's session.
        if new_index is not None:
            await _delete_track(new_index)
            new_index = None
        return {
            "status": "not_supported",
            "track_index": None,
            "track_name": track_name,
            "file_path": str(p.resolve()),
            "loaded": False,
            "attempt_errors": result.get("attempt_errors"),
            "workaround": result.get("workaround") or (
                "Live's LOM in this build doesn't accept programmatic "
                "wav-into-arrangement loading. Drag the wav onto the "
                "arrangement timeline manually."
            ),
        }
    except Exception as exc:
        log.exception("load_wav_to_arrangement failed")
        # Best-effort cleanup so a partial failure doesn't leave a track behind.
        if new_index is not None:
            try:
                await _delete_track(new_index)
            except Exception:
                pass
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "track_name": track_name,
            "file_path": str(p.resolve()),
        }
