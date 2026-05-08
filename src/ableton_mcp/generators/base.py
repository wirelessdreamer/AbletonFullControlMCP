"""Abstract Generator interface and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class GeneratorError(RuntimeError):
    """Base class for any failure raised by a Generator implementation."""


class GeneratorNotConfigured(GeneratorError):
    """Raised when the generator's required env var / dependency is missing.

    The message must clearly state what's needed (e.g. "set SUNO_API_KEY") so
    the user knows exactly what to fix.
    """


@dataclass
class GenResult:
    """Result of a successful generation call."""

    audio_path: str
    duration: float
    lyrics: str | None
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Generator(ABC):
    """Abstract base for a music generator.

    Subclasses set `name` as a class attribute and implement `generate`.
    `is_configured()` returns False if required env vars / executables are
    missing — call this from listing UIs to show readiness without raising.
    """

    name: str = "abstract"

    def is_configured(self) -> bool:
        """Cheap readiness check (no network calls). Subclasses override."""
        return True

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        lyrics: str | None = None,
        duration: float | None = None,
        **kwargs: Any,
    ) -> GenResult:
        """Render audio for `prompt`. Must raise GeneratorNotConfigured if unset."""
        raise NotImplementedError
