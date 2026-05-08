"""Audio capture (tape) layer.

Two complementary capture paths are exposed here:

- :class:`TapeClient` — talks to a Max for Live "tape" device that listens on
  UDP/11003 and replies on UDP/11004. Sample-accurate, captures the device's
  parent track output directly, no extra audio routing required. **Preferred.**

- :class:`LoopbackCapture` — uses the optional :mod:`sounddevice` dependency
  to record from a system loopback input (VB-Audio Cable on Windows, BlackHole
  on macOS). Slower setup for the user but does not require Max for Live.

:func:`pick_capture_backend` resolves the configured backend lazily so callers
get whichever is available without importing both at module load.
"""

from __future__ import annotations

from typing import Any

from .client import TapeClient, TapeError, TapeTimeout
from .config import CaptureConfig
from .loopback import LoopbackCapture, LoopbackNotAvailable


def pick_capture_backend(cfg: CaptureConfig | None = None) -> Any:
    """Return whichever capture backend is configured.

    Resolution order:
      1. If ``cfg.backend == "tape"``: return a :class:`TapeClient`.
      2. If ``cfg.backend == "loopback"``: return a :class:`LoopbackCapture`.
         Raises :class:`LoopbackNotAvailable` lazily on first record() if the
         optional ``sounddevice`` dep is missing.
    """
    cfg = cfg or CaptureConfig.from_env()
    if cfg.backend == "tape":
        return TapeClient(cfg)
    if cfg.backend == "loopback":
        return LoopbackCapture(cfg)
    raise ValueError(
        f"Unknown capture backend {cfg.backend!r}; expected 'tape' or 'loopback'"
    )


__all__ = [
    "CaptureConfig",
    "LoopbackCapture",
    "LoopbackNotAvailable",
    "TapeClient",
    "TapeError",
    "TapeTimeout",
    "pick_capture_backend",
]
