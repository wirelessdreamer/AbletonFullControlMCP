"""Bounce / export pipeline.

Public surface:
    encode_wav_to_mp3 — ffmpeg subprocess wrapper (no Live needed)
    mix_stems_to_master — sum N stem WAVs into a single mix WAV (no Live needed)
    bounce_master_realtime — drive M4L tape device on Master to capture full mix
    bounce_stems_realtime  — capture each track in turn via solo + tape device

Two complementary bounce strategies:

* Realtime (the tape backend): plays the arrangement at real speed and captures
  via the AbletonFullControlTape M4L device. Requires the user to have done the
  Save-As-Device step in Max once. Works track-by-track or master-bus.
* Offline (post-process): once you have stems on disk (from any source — the
  tape device, manual export, freeze-and-flatten), the mix and mp3 encoder
  combine them into deliverables.

`encode_wav_to_mp3` and `mix_stems_to_master` work today against any wav files
on disk — no Live required.
"""

from .mp3 import encode_wav_to_mp3, FFmpegMissing  # noqa: F401
from .mix import mix_stems_to_master  # noqa: F401
from .tape_orchestrator import (  # noqa: F401
    bounce_master_realtime,
    bounce_stems_realtime,
    BounceError,
)
from .resampling import (  # noqa: F401
    bounce_song_via_resampling,
    bounce_tracks_via_resampling,
    bounce_enabled_via_resampling,
)
