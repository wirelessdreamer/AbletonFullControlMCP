"""Section / Structure data model for bar-counted song layouts.

A ``Section`` is a named, fixed-length piece of the arrangement (intro,
verse, breakdown, etc.) measured in **bars**. A ``Structure`` is an ordered
list of sections plus a time signature and tempo — enough to translate
section names into beat ranges on Live's arrangement timeline.

Everything here is pure data. No OSC, no asyncio, no Live. The
``live_bridge`` module is the only thing that talks to Live; this module
is freely importable from tests, scripts, and other engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator


VALID_ROLES = (
    "intro",
    "verse",
    "pre_chorus",
    "chorus",
    "bridge",
    "breakdown",
    "drop",
    "build",
    "buildup",  # alias for build; kept distinct so to_text() preserves user wording
    "interlude",
    "solo",
    "outro",
    "fill",
    "hit",
    "tag",
    "vamp",
    "refrain",
    "post_chorus",
    "half_time",
    "double_time",
    "other",
)


def beats_per_bar(numerator: int, denominator: int) -> float:
    """Return the number of Live quarter-note beats in one bar.

    Live's transport speaks quarter-note beats regardless of the
    time-signature display. So a bar of 6/8 = 6 eighths = 3 quarter-note
    beats; a bar of 7/8 = 3.5; a bar of 4/4 = 4.

    See ``docs/LIVE_API_GOTCHAS.md`` §6 for the verified Live convention.
    """
    if numerator <= 0 or denominator <= 0:
        raise ValueError(f"invalid time signature {numerator}/{denominator}")
    return float(numerator) * (4.0 / float(denominator))


@dataclass(frozen=True)
class Section:
    """One section of an arrangement.

    - ``name``: lowercased free-form name (``"intro"``, ``"verse 1"``,
      ``"main groove"``).
    - ``bars``: positive integer number of bars.
    - ``role``: one of :data:`VALID_ROLES`. Auto-detected from ``name``
      by the parser; ``"other"`` if unrecognised.
    - ``notes``: optional free-form annotation (chord changes, dynamics,
      "drum fill on the last bar", etc.).
    """

    name: str
    bars: int
    role: str = "other"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("section name cannot be empty")
        if not isinstance(self.bars, int):
            raise TypeError(f"bars must be int, got {type(self.bars).__name__}")
        if self.bars <= 0:
            raise ValueError(f"section bars must be > 0, got {self.bars}")
        if self.role not in VALID_ROLES:
            raise ValueError(
                f"invalid role {self.role!r}; valid roles: {VALID_ROLES}"
            )
        # Normalise name to lowercase / single-spaced.
        normalised = " ".join(self.name.lower().split())
        if normalised != self.name:
            object.__setattr__(self, "name", normalised)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bars": int(self.bars),
            "role": self.role,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Section":
        return cls(
            name=str(d["name"]),
            bars=int(d["bars"]),
            role=str(d.get("role", "other")),
            notes=str(d.get("notes", "")),
        )

    def with_bars(self, bars: int) -> "Section":
        """Return a copy with a new bar count."""
        return replace(self, bars=int(bars))


@dataclass
class Structure:
    """Ordered list of :class:`Section` s plus a time signature and tempo."""

    sections: list[Section] = field(default_factory=list)
    time_signature: tuple[int, int] = (4, 4)
    tempo: float = 120.0

    def __post_init__(self) -> None:
        # Defensive: coerce sections list to be a fresh list of Section.
        coerced: list[Section] = []
        for s in self.sections:
            if isinstance(s, Section):
                coerced.append(s)
            elif isinstance(s, dict):
                coerced.append(Section.from_dict(s))
            else:
                raise TypeError(
                    f"sections must be Section or dict, got {type(s).__name__}"
                )
        self.sections = coerced
        num, den = self.time_signature
        if num <= 0 or den <= 0:
            raise ValueError(f"invalid time signature {num}/{den}")
        self.time_signature = (int(num), int(den))
        self.tempo = float(self.tempo)
        if self.tempo <= 0:
            raise ValueError(f"tempo must be > 0, got {self.tempo}")

    # --- size queries ---

    def __iter__(self) -> Iterator[Section]:
        return iter(self.sections)

    def __len__(self) -> int:
        return len(self.sections)

    @property
    def beats_per_bar(self) -> float:
        return beats_per_bar(*self.time_signature)

    @property
    def total_bars(self) -> int:
        return sum(s.bars for s in self.sections)

    @property
    def total_beats(self) -> float:
        return self.total_bars * self.beats_per_bar

    @property
    def total_seconds(self) -> float:
        # tempo is quarter-note BPM (Live convention).
        return (self.total_beats / self.tempo) * 60.0

    # --- name lookups ---

    def find_index(self, name: str) -> int:
        """Return the index of the first section whose name matches.

        Raises :class:`KeyError` if no match. Match is exact on the
        normalised (lowercase, single-spaced) section name.
        """
        target = " ".join(name.lower().split())
        for i, s in enumerate(self.sections):
            if s.name == target:
                return i
        raise KeyError(f"no section named {name!r}; sections: {self.section_names()}")

    def find(self, name: str) -> Section:
        return self.sections[self.find_index(name)]

    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]

    def has_section(self, name: str) -> bool:
        try:
            self.find_index(name)
            return True
        except KeyError:
            return False

    # --- timeline math ---

    def start_bar(self, name: str) -> int:
        """1-based bar number where this section starts.

        Bars are 1-indexed in musician-talk: "bar 12" is the 12th bar.
        Returns the bar number of this section's first downbeat.
        """
        idx = self.find_index(name)
        return 1 + sum(s.bars for s in self.sections[:idx])

    def end_bar(self, name: str) -> int:
        """1-based bar number of this section's LAST bar (inclusive)."""
        idx = self.find_index(name)
        return sum(s.bars for s in self.sections[: idx + 1])

    def start_beat(self, name: str) -> float:
        """0-indexed beat at which this section starts on the timeline."""
        idx = self.find_index(name)
        bars_before = sum(s.bars for s in self.sections[:idx])
        return bars_before * self.beats_per_bar

    def length_beats(self, name: str) -> float:
        return self.find(name).bars * self.beats_per_bar

    def end_beat(self, name: str) -> float:
        return self.start_beat(name) + self.length_beats(name)

    # --- rendering ---

    def to_text(self) -> str:
        """Render the structure as the canonical slash-separated form.

        Always includes the ``= N bars`` total assertion. The result is the
        user's preferred dialect:

            "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"
        """
        if not self.sections:
            return "= 0 bars"
        parts = [f"{s.name} {s.bars}" for s in self.sections]
        return " / ".join(parts) + f" = {self.total_bars} bars"

    def summary(self) -> str:
        """Multi-line musician-friendly summary, one line per section."""
        num, den = self.time_signature
        lines = [
            f"Structure: {len(self.sections)} sections, {self.total_bars} bars, "
            f"{num}/{den} @ {self.tempo:g} BPM "
            f"({self.total_beats:g} beats, {self.total_seconds:.2f}s)"
        ]
        for i, s in enumerate(self.sections, start=1):
            sb = self.start_bar(s.name)
            eb = self.end_bar(s.name)
            stb = self.start_beat(s.name)
            lb = self.length_beats(s.name)
            line = (
                f"  {i:>2}. {s.name:<18} {s.bars} bar{'s' if s.bars != 1 else ' '}  "
                f"[{s.role}]  bars {sb}-{eb}  beats {stb:g}+{lb:g}"
            )
            if s.notes:
                line += f"  — {s.notes}"
            lines.append(line)
        return "\n".join(lines)

    # --- serialisation ---

    def to_dict(self) -> dict:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "time_signature": list(self.time_signature),
            "tempo": float(self.tempo),
            "total_bars": self.total_bars,
            "total_beats": self.total_beats,
            "total_seconds": self.total_seconds,
            "text": self.to_text(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Structure":
        ts = d.get("time_signature", (4, 4))
        if isinstance(ts, str) and "/" in ts:
            num_s, den_s = ts.split("/", 1)
            ts = (int(num_s), int(den_s))
        else:
            ts = (int(ts[0]), int(ts[1]))
        return cls(
            sections=[Section.from_dict(s) for s in d.get("sections", [])],
            time_signature=ts,
            tempo=float(d.get("tempo", 120.0)),
        )

    def clone(self) -> "Structure":
        """Return a deep-ish copy. Sections are frozen dataclasses so a
        shallow copy of the list is enough."""
        return Structure(
            sections=list(self.sections),
            time_signature=tuple(self.time_signature),
            tempo=float(self.tempo),
        )
