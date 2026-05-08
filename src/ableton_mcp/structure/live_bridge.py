"""Translate :class:`Structure` ranges onto Live's arrangement timeline.

The data model knows nothing about Live; this module is the only place
section names get turned into AbletonOSC calls. Functions are async so
they sit comfortably alongside the rest of the tool surface.

OSC contract reminders (see ``docs/LIVE_API_GOTCHAS.md`` §6 + the OSC
client header):

- ``/live/song/get/tempo`` and ``/live/song/set/tempo`` speak quarter-note
  BPM. Our ``Section`` math is in quarter-note beats, so the mapping is
  1:1.
- ``/live/song/set/loop_start`` and ``/live/song/set/loop_length`` accept
  floats in beats.
- ``/live/song/set/current_song_time`` jumps the playhead.
- ``/live/song/set/loop`` toggles the arrangement loop on/off.
"""

from __future__ import annotations

from typing import Any

from ..osc_client import get_client
from .model import Structure


def section_to_beat_range(structure: Structure, name: str) -> tuple[float, float]:
    """Return ``(start_beat, length_beats)`` for the named section.

    Raises :class:`KeyError` if the section is not present.
    """
    return structure.start_beat(name), structure.length_beats(name)


def section_range_dict(structure: Structure, name: str) -> dict[str, Any]:
    """Verbose range info: start_beat, length_beats, end_beat, start_bar, end_bar."""
    start_beat = structure.start_beat(name)
    length = structure.length_beats(name)
    return {
        "section": name,
        "start_beat": float(start_beat),
        "length_beats": float(length),
        "end_beat": float(start_beat + length),
        "start_bar": int(structure.start_bar(name)),
        "end_bar": int(structure.end_bar(name)),
        "bars": int(structure.find(name).bars),
        "time_signature": "{}/{}".format(*structure.time_signature),
    }


async def apply_loop_to_section(
    structure: Structure, name: str, enable: bool = True
) -> dict[str, Any]:
    """Set Live's arrangement loop region to the named section.

    Sends ``loop_start``, ``loop_length``, then optionally toggles
    ``loop`` on. Mirrors the contract of ``transport.live_set_loop``.
    """
    start_beat, length = section_to_beat_range(structure, name)
    client = await get_client()
    client.send("/live/song/set/loop_start", float(start_beat))
    client.send("/live/song/set/loop_length", float(length))
    if enable:
        client.send("/live/song/set/loop", 1)
    return {
        "section": name,
        "loop_start_beats": float(start_beat),
        "loop_length_beats": float(length),
        "loop_enabled": bool(enable),
    }


async def select_section(structure: Structure, name: str) -> dict[str, Any]:
    """Jump arrangement playback to the start of the named section."""
    start_beat = structure.start_beat(name)
    client = await get_client()
    client.send("/live/song/set/current_song_time", float(start_beat))
    return {
        "section": name,
        "jumped_to_beat": float(start_beat),
        "start_bar": int(structure.start_bar(name)),
    }


__all__ = [
    "section_to_beat_range",
    "section_range_dict",
    "apply_loop_to_section",
    "select_section",
]
