"""Pure functional edits on :class:`Structure`.

Every function here returns a *new* :class:`Structure`; the input is never
mutated. This keeps the model safe to share across tool calls and makes
unit tests trivially deterministic.

All section addressing is by **name** — first occurrence wins. To
disambiguate repeated sections (two ``verse`` s, three ``chorus`` es),
rename them at parse time (``verse 1``, ``verse 2``).
"""

from __future__ import annotations

from typing import Optional

from .model import Section, Structure
from .parser import detect_role


def _rebuild(structure: Structure, new_sections: list[Section]) -> Structure:
    return Structure(
        sections=new_sections,
        time_signature=structure.time_signature,
        tempo=structure.tempo,
    )


def extend_section(structure: Structure, name: str, by_bars: int) -> Structure:
    """Add ``by_bars`` to a section's length. Returns a new Structure."""
    if by_bars == 0:
        return structure.clone()
    if by_bars < 0:
        raise ValueError(
            f"extend_section by_bars must be > 0; got {by_bars} "
            f"(use shrink_section for negative deltas)"
        )
    idx = structure.find_index(name)
    new_sections = list(structure.sections)
    section = new_sections[idx]
    new_sections[idx] = section.with_bars(section.bars + int(by_bars))
    return _rebuild(structure, new_sections)


def shrink_section(structure: Structure, name: str, by_bars: int) -> Structure:
    """Subtract ``by_bars`` from a section's length. Returns a new Structure.

    Raises :class:`ValueError` if the result would be ≤ 0 bars.
    """
    if by_bars == 0:
        return structure.clone()
    if by_bars < 0:
        raise ValueError(
            f"shrink_section by_bars must be > 0; got {by_bars} "
            f"(use extend_section for positive deltas)"
        )
    idx = structure.find_index(name)
    section = structure.sections[idx]
    new_bars = section.bars - int(by_bars)
    if new_bars <= 0:
        raise ValueError(
            f"shrinking {name!r} by {by_bars} would leave {new_bars} bars; "
            "use remove_section instead"
        )
    new_sections = list(structure.sections)
    new_sections[idx] = section.with_bars(new_bars)
    return _rebuild(structure, new_sections)


def duplicate_section(structure: Structure, name: str) -> Structure:
    """Insert a copy of the named section right after itself.

    The copy keeps the same name; if you need disambiguation, rename it
    afterwards via :func:`rename_section`.
    """
    idx = structure.find_index(name)
    section = structure.sections[idx]
    new_sections = list(structure.sections)
    new_sections.insert(idx + 1, section)
    return _rebuild(structure, new_sections)


def insert_section(
    structure: Structure,
    after_name: Optional[str],
    new: Section,
) -> Structure:
    """Insert ``new`` after the section named ``after_name``.

    If ``after_name`` is ``None`` or an empty string, the new section is
    prepended at index 0.
    """
    if not isinstance(new, Section):
        raise TypeError(f"new must be Section, got {type(new).__name__}")

    new_sections = list(structure.sections)
    if after_name is None or after_name == "":
        new_sections.insert(0, new)
    else:
        idx = structure.find_index(after_name)
        new_sections.insert(idx + 1, new)
    return _rebuild(structure, new_sections)


def remove_section(structure: Structure, name: str) -> Structure:
    """Remove the named section. Returns a new Structure."""
    idx = structure.find_index(name)
    new_sections = list(structure.sections)
    del new_sections[idx]
    return _rebuild(structure, new_sections)


def rename_section(structure: Structure, old: str, new: str) -> Structure:
    """Rename a section. Role is re-detected from the new name.

    Note: if the user wants to keep an explicit role across a rename,
    they should follow up with :func:`replace_section` to set it back.
    """
    idx = structure.find_index(old)
    section = structure.sections[idx]
    new_normalised = " ".join(new.lower().split())
    new_role = detect_role(new_normalised)
    new_sections = list(structure.sections)
    new_sections[idx] = Section(
        name=new_normalised,
        bars=section.bars,
        role=new_role,
        notes=section.notes,
    )
    return _rebuild(structure, new_sections)


def move_section(structure: Structure, name: str, to_index: int) -> Structure:
    """Move a section to a new 0-based index in the section list.

    ``to_index`` is clamped to ``[0, len(sections) - 1]``.
    """
    idx = structure.find_index(name)
    new_sections = list(structure.sections)
    section = new_sections.pop(idx)
    target = max(0, min(int(to_index), len(new_sections)))
    new_sections.insert(target, section)
    return _rebuild(structure, new_sections)


def replace_section(structure: Structure, name: str, with_: Section) -> Structure:
    """Swap the section named ``name`` for ``with_``."""
    if not isinstance(with_, Section):
        raise TypeError(f"with_ must be Section, got {type(with_).__name__}")
    idx = structure.find_index(name)
    new_sections = list(structure.sections)
    new_sections[idx] = with_
    return _rebuild(structure, new_sections)


__all__ = [
    "extend_section",
    "shrink_section",
    "duplicate_section",
    "insert_section",
    "remove_section",
    "rename_section",
    "move_section",
    "replace_section",
]
