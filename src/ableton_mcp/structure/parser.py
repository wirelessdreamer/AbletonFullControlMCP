"""Parse the user's bar-counted section dialect into a :class:`Structure`.

Accepted forms (all whitespace/punctuation tolerant; section names are
lowercased on parse):

- Slash form (preferred): ``"intro 4 / verse 8 / chorus 8"``
- Comma form: ``"intro 4, verse 8, chorus 8"``
- Numbered: ``"4 bars intro, 8 bars verse, 8 bars chorus"``
- With total: ``"intro 4 / verse 8 = 12 bars"`` — validates the sum
- ``bar``, ``bars``, and ``b`` are interchangeable units.

See ``docs/SONG_STRUCTURE.md`` for the full grammar.
"""

from __future__ import annotations

import re
from typing import Iterable

from .model import Section, Structure, VALID_ROLES


class StructureParseError(ValueError):
    """Raised when a structure string cannot be parsed cleanly."""


# Role keywords map. Order matters — the parser checks each entry in this
# order and the first substring match wins.  Compound keywords ("pre chorus")
# come before the simple ones ("chorus").
#
# Document any change here in docs/SONG_STRUCTURE.md §2.
_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    # Pre/post chorus — must run before "chorus".
    ("pre chorus", "pre_chorus"),
    ("pre-chorus", "pre_chorus"),
    ("pre_chorus", "pre_chorus"),
    ("prechorus", "pre_chorus"),
    ("post chorus", "post_chorus"),
    ("post-chorus", "post_chorus"),
    ("post_chorus", "post_chorus"),
    ("postchorus", "post_chorus"),
    # Bridge / breakdown / drop / build / etc.
    ("bridge", "bridge"),
    ("middle 8", "bridge"),
    ("middle eight", "bridge"),
    ("breakdown", "breakdown"),
    ("drop", "drop"),
    ("buildup", "build"),
    ("build-up", "build"),
    ("build up", "build"),
    ("riser", "build"),
    ("build", "build"),
    # Interlude / solo / fill / hit / tag.
    ("interlude", "interlude"),
    ("solo", "solo"),
    ("fill-in", "fill"),
    ("fill", "fill"),
    ("stab", "hit"),
    ("hit", "hit"),
    ("tag", "tag"),
    # Outros / endings.
    ("outro", "outro"),
    ("ending", "outro"),
    ("coda", "outro"),
    # Verse / chorus / refrain / hook.
    ("verse", "verse"),
    ("chorus", "chorus"),
    ("refrain", "refrain"),
    ("hook", "chorus"),
    # Half/double-time tags.
    ("half-time", "half_time"),
    ("half time", "half_time"),
    ("halftime", "half_time"),
    ("double-time", "double_time"),
    ("double time", "double_time"),
    ("doubletime", "double_time"),
    # Vamp/groove/loop/main/final — jam-band shorthand.
    ("vamp", "vamp"),
    ("groove", "vamp"),
    ("main", "vamp"),
    ("final", "vamp"),
    ("loop", "vamp"),
    # Intro — done last because "intro" is short and wouldn't conflict
    # but keeping it near the end keeps the table readable.
    ("intro", "intro"),
)


def detect_role(name: str) -> str:
    """Auto-detect the role for a section name.

    Walks the keyword table twice: first against the lowercased name as-is
    (so `"middle 8"` can match the literal keyword `"middle 8"`), then
    against a digit-stripped stem (so `"verse 2b"` resolves via `"verse"`).
    Returns ``"other"`` if nothing matches.
    """
    if not name:
        return "other"
    raw = name.lower()
    # Squash separators to spaces so "main_groove_a" matches "main".
    raw = re.sub(r"[_\-]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    # Pass 1: literal keyword match against the (still digit-bearing) name.
    for keyword, role in _ROLE_KEYWORDS:
        if keyword in raw:
            return role
    # Pass 2: strip trailing numbers / qualifiers ("verse 1", "chorus a", "verse2b").
    stem = re.sub(r"\d+", " ", raw)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return "other"
    for keyword, role in _ROLE_KEYWORDS:
        if keyword in stem:
            return role
    return "other"


# --- Tokenising ---

_TOTAL_PAT = re.compile(
    r"=\s*(\d+)\s*(?:bars?|b)\b\s*$",
    re.IGNORECASE,
)
# A section is either:
#   - "<name> <count>"  (count after name)
#   - "<count> bars <name>"  (count before name)
# Where:
#   <count>   = positive integer
#   <name>    = one or more words; words may include letters, digits,
#               hyphens, apostrophes, underscores. They cannot be the
#               literal "bars"/"bar"/"b".
_BARS_RE = r"(?:bars?|b)"

_SECTION_NAME_AFTER_RE = re.compile(
    rf"""
    ^\s*
    (?P<name>[A-Za-z][A-Za-z0-9'\-_ ]*?)
    \s+
    (?P<count>\d+)
    (?:\s+{_BARS_RE}\b)?
    \s*$
    """,
    re.VERBOSE,
)
_SECTION_NAME_BEFORE_RE = re.compile(
    rf"""
    ^\s*
    (?P<count>\d+)
    \s*
    {_BARS_RE}\s+
    (?P<name>[A-Za-z][A-Za-z0-9'\-_ ]*?)
    \s*$
    """,
    re.VERBOSE,
)


def _split_chunks(text: str) -> list[str]:
    """Split on slashes, commas, semicolons, ' then ', or '->' / '→'."""
    # Replace flow-arrow markers with a single delimiter.
    text = re.sub(r"->|→", "/", text)
    # Split on /, ;, or ', '.
    parts = re.split(r"[/;,]+", text)
    # Also split on the word " then " as a soft delimiter.
    expanded: list[str] = []
    for part in parts:
        sub = re.split(r"\s+then\s+", part, flags=re.IGNORECASE)
        expanded.extend(sub)
    return [p.strip() for p in expanded if p and p.strip()]


def _parse_chunk(chunk: str) -> Section:
    """Parse one section chunk into a :class:`Section`."""
    m = _SECTION_NAME_BEFORE_RE.match(chunk)
    if not m:
        m = _SECTION_NAME_AFTER_RE.match(chunk)
    if not m:
        raise StructureParseError(
            f"could not parse section chunk {chunk!r}; "
            "expected '<name> <bars>' or '<bars> bars <name>'"
        )
    name = m.group("name").strip()
    # Trim trailing "bars"/"bar"/"b" if it slipped past the regex (rare —
    # happens when name itself contains it, but cheap to guard).
    name = re.sub(rf"\s+{_BARS_RE}\s*$", "", name, flags=re.IGNORECASE).strip()
    count = int(m.group("count"))
    if count <= 0:
        raise StructureParseError(f"section bar count must be > 0 in {chunk!r}")
    role = detect_role(name)
    return Section(name=name, bars=count, role=role)


def parse_structure(
    text: str,
    time_signature: tuple[int, int] = (4, 4),
    tempo: float = 120.0,
) -> Structure:
    """Parse ``text`` into a :class:`Structure`.

    Raises :class:`StructureParseError` on syntax errors or total-mismatch.
    """
    if not isinstance(text, str):
        raise StructureParseError(
            f"text must be str, got {type(text).__name__}"
        )
    raw = text.strip()
    if not raw:
        raise StructureParseError("structure text is empty")

    # Pull out the optional "= N bars" assertion.
    expected_total: int | None = None
    m = _TOTAL_PAT.search(raw)
    if m:
        expected_total = int(m.group(1))
        raw = raw[: m.start()].rstrip().rstrip("=").rstrip()

    chunks = _split_chunks(raw)
    if not chunks:
        raise StructureParseError(f"no section chunks parsed from {text!r}")

    sections = [_parse_chunk(c) for c in chunks]
    structure = Structure(
        sections=sections, time_signature=time_signature, tempo=tempo
    )

    if expected_total is not None and structure.total_bars != expected_total:
        raise StructureParseError(
            f"total mismatch: declared = {expected_total} bars, "
            f"section sum = {structure.total_bars} bars "
            f"({' + '.join(f'{s.name} {s.bars}' for s in sections)})"
        )

    return structure


__all__ = [
    "StructureParseError",
    "detect_role",
    "parse_structure",
]
