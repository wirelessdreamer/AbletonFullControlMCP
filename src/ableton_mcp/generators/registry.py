"""Registry of available Generator implementations."""

from __future__ import annotations

from .base import Generator
from .musicgen import MusicGenGenerator
from .stable_audio import StableAudioGenerator
from .suno import SunoGenerator

REGISTRY: dict[str, type[Generator]] = {
    SunoGenerator.name: SunoGenerator,
    MusicGenGenerator.name: MusicGenGenerator,
    StableAudioGenerator.name: StableAudioGenerator,
}


def list_names() -> list[str]:
    return sorted(REGISTRY.keys())


def get(name: str) -> Generator:
    """Return an instance of the named generator.

    Raises KeyError with the list of available names if `name` is unknown.
    """
    cls = REGISTRY.get(name)
    if cls is None:
        available = ", ".join(list_names()) or "(none)"
        raise KeyError(
            f"Unknown generator {name!r}. Available providers: {available}."
        )
    return cls()
