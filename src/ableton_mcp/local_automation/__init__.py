"""Local OS-level automation (Win32 API, OLE drag-drop, .als synthesis).

These modules run in the MCP server's Python (not Live's sandboxed
Remote-Scripts Python), and contain the workarounds for things Live's
LOM / OSC can't do programmatically — primarily wav-into-arrangement
loading on Live 11.3.43, where ``Track.create_audio_clip(file_path)``
is broken.

Public surface (all Windows-only at runtime; modules import cleanly on
any platform but their functions raise on non-Windows):

- :mod:`als_synth` — :func:`synthesize_als` builds a valid ``.als``
  Live Set file with audio clips pre-loaded, for ``os.startfile``-style
  open-in-Live. 100% reliable, replaces current session.
- :mod:`ole_dragdrop` — :func:`drop_files_on_live_via_ole` programmatically
  simulates the Windows shell drag-drop into Live's IDropTarget. ~99%
  reliable; preserves the current Live session (no replace).
"""

from .als_synth import (  # noqa: F401
    AlsSynthesisError,
    SynthesisResult,
    WavSpec,
    synthesize_als,
)
from .ole_dragdrop import (  # noqa: F401
    OleDropError,
    OleDropResult,
    drop_files_on_live_via_ole,
)
