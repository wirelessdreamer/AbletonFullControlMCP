"""Runtime configuration for the AbletonMCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    osc_host: str = "127.0.0.1"
    osc_send_port: int = 11000
    osc_recv_port: int = 11001
    request_timeout: float = 5.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            osc_host=os.environ.get("ABLETON_OSC_HOST", cls.osc_host),
            osc_send_port=int(os.environ.get("ABLETON_OSC_SEND_PORT", cls.osc_send_port)),
            osc_recv_port=int(os.environ.get("ABLETON_OSC_RECV_PORT", cls.osc_recv_port)),
            request_timeout=float(os.environ.get("ABLETON_OSC_TIMEOUT", cls.request_timeout)),
            log_level=os.environ.get("ABLETON_MCP_LOG", cls.log_level),
        )
