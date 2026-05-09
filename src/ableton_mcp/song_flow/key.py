"""Key/pitch-class arithmetic for the song-flow transpose path.

Pitch classes are 0..11 starting at C. We accept sharps and flats
interchangeably (Eb == D#) and normalise input via :func:`normalize_key`.
``audio_analyze`` reports keys as sharps; users typing "Bb" should still
work, hence the alias table.
"""

from __future__ import annotations

from typing import Literal

Direction = Literal["auto", "up", "down"]

PITCH_CLASS: dict[str, int] = {
    "C": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11,
}


def normalize_key(name: str) -> str:
    """Canonicalise a key name. Strips whitespace, capitalises the letter,
    keeps a trailing ``#`` or ``b`` accidental. Strips any ``major``/``minor``
    suffix (we only care about the tonic for transpose).

    Examples: ``"f#"`` → ``"F#"``, ``"bb minor"`` → ``"Bb"``,
              ``" g sharp"`` → ``"G#"`` (we don't accept "sharp"; only "#"/"b").
    """
    raw = (name or "").strip()
    if not raw:
        raise ValueError("key name is empty")
    head = raw.split()[0]
    # Strip everything after the tonic letter+accidental.
    letter = head[0].upper()
    accidental = ""
    if len(head) >= 2 and head[1] in ("#", "b"):
        accidental = head[1]
    canonical = letter + accidental
    if canonical not in PITCH_CLASS:
        raise ValueError(
            f"unknown key {name!r}; expected one of "
            f"{sorted(set(PITCH_CLASS))} (case-insensitive, accidentals as # or b)"
        )
    return canonical


def semitone_delta(source: str, target: str, direction: Direction = "auto") -> int:
    """Compute the semitone shift to take ``source`` key → ``target`` key.

    - ``direction="up"`` → result is in ``[0, 11]``.
    - ``direction="down"`` → result is in ``[-11, 0]``.
    - ``direction="auto"`` → shortest signed path; ties (±6) resolve to the
      negative direction (so a tritone shift goes down, keeping vocals in
      a more singable range).
    """
    s = PITCH_CLASS[normalize_key(source)]
    t = PITCH_CLASS[normalize_key(target)]
    raw = (t - s) % 12  # 0..11
    if direction == "up":
        return raw
    if direction == "down":
        return raw - 12 if raw != 0 else 0
    # auto: shortest signed path; tritone (raw==6) goes down by convention.
    return raw if raw < 6 else raw - 12
