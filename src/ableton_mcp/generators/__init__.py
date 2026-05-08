"""Pluggable AI music generators (Phase 6).

A `Generator` is a thin async adapter over an external/local model that can
turn a prompt (and optional lyrics/duration) into an audio file on disk.

Usage:
    from ableton_mcp.generators.registry import get
    gen = get("suno")  # or "musicgen", "stable_audio"
    result = await gen.generate("lo-fi hip hop with rain")
"""

from __future__ import annotations

from .base import GenResult, Generator, GeneratorError, GeneratorNotConfigured

__all__ = ["GenResult", "Generator", "GeneratorError", "GeneratorNotConfigured"]
