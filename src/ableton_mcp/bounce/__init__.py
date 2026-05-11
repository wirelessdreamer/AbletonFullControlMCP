"""Bounce / export pipeline.

Public surface:
    encode_wav_to_mp3 — ffmpeg subprocess wrapper (no Live needed)
    mix_stems_to_master — sum N stem WAVs into a single mix WAV (no Live needed)
    bounce_song_via_resampling — Live-built-in Resampling input → wav, master mix
    bounce_tracks_via_resampling — same, but per-track stems in one playback pass
    bounce_enabled_via_resampling — every un-muted track in one pass

The realtime path is Live's own Resampling track input — no Max for Live, no
loopback driver, no UI automation. We create one temp audio track per source
(input set to Resampling for the master, or to the source track for stems),
arm them, run arrangement record for ``duration_sec``, then copy the captured
wavs out of Live's ``Samples/Recorded/`` folder and delete the temp tracks.

Offline (no Live needed): ``encode_wav_to_mp3`` and ``mix_stems_to_master``
work against any wavs on disk.
"""

from .mp3 import encode_wav_to_mp3, FFmpegMissing  # noqa: F401
from .mix import mix_stems_to_master  # noqa: F401
from .resampling import (  # noqa: F401
    bounce_song_via_resampling,
    bounce_tracks_via_resampling,
    bounce_enabled_via_resampling,
    bounce_region_via_resampling,
    bounce_region_all_active_via_resampling,
)
from .freeze import (  # noqa: F401
    bounce_tracks_via_freeze,
    bounce_enabled_via_freeze,
    FreezeBounceError,
)
