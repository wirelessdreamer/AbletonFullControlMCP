"""Runtime configuration for the audio-capture (tape) backends.

Reads from environment variables with sensible defaults. The defaults match
the M4L tape device shipped under :mod:`live_max_for_live.AbletonFullControlTape`.

Env vars:
    ABLETON_MCP_CAPTURE_BACKEND   "tape" (default) or "loopback"
    ABLETON_MCP_TAPE_HOST         tape device host, default 127.0.0.1
    ABLETON_MCP_TAPE_SEND_PORT    Python -> Max, default 11003
    ABLETON_MCP_TAPE_RECV_PORT    Max -> Python, default 11004
    ABLETON_MCP_TAPE_TIMEOUT      seconds, default 30 (records can be long)
    ABLETON_MCP_LOOPBACK_DEVICE   sounddevice input device name (substring match)
    ABLETON_MCP_SAMPLE_RATE       capture sample rate, default 44100
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Literal

Backend = Literal["tape", "loopback"]


@dataclass(frozen=True)
class CaptureConfig:
    backend: Backend = "tape"
    tape_host: str = "127.0.0.1"
    tape_send_port: int = 11003   # Python -> Max
    tape_recv_port: int = 11004   # Max -> Python
    tape_timeout: float = 30.0
    loopback_device: str | None = None
    sample_rate: int = 44100

    @classmethod
    def from_env(cls) -> "CaptureConfig":
        backend_raw = os.environ.get("ABLETON_MCP_CAPTURE_BACKEND", cls.backend).strip().lower()
        backend: Backend = "loopback" if backend_raw == "loopback" else "tape"
        return cls(
            backend=backend,
            tape_host=os.environ.get("ABLETON_MCP_TAPE_HOST", cls.tape_host),
            tape_send_port=int(os.environ.get("ABLETON_MCP_TAPE_SEND_PORT", cls.tape_send_port)),
            tape_recv_port=int(os.environ.get("ABLETON_MCP_TAPE_RECV_PORT", cls.tape_recv_port)),
            tape_timeout=float(os.environ.get("ABLETON_MCP_TAPE_TIMEOUT", cls.tape_timeout)),
            loopback_device=os.environ.get("ABLETON_MCP_LOOPBACK_DEVICE") or None,
            sample_rate=int(os.environ.get("ABLETON_MCP_SAMPLE_RATE", cls.sample_rate)),
        )

    def with_overrides(self, **changes: object) -> "CaptureConfig":
        """Return a new CaptureConfig with a subset of fields replaced."""
        return replace(self, **changes)  # type: ignore[arg-type]
