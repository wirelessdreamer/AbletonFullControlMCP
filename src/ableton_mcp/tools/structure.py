"""MCP tools for the bar-counted section dialect.

Talk to a song in the user's preferred shorthand:

    "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"

Tools here parse the dialect into a :class:`Structure`, edit it (extend,
shrink, duplicate, insert, remove), introspect ranges (in bars, beats,
seconds), and translate to Live's arrangement timeline (loop a section,
jump to a section).

All ``structure_dict`` parameters accept the dict returned by
``structure_parse`` (or any equivalent shape). All edits are functional —
the input dict is never mutated; the new structure is returned.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from ..structure import (
    Section,
    Structure,
    StructureParseError,
    apply_loop_to_section,
    duplicate_section,
    extend_section,
    insert_section,
    parse_structure,
    remove_section,
    section_range_dict,
    select_section,
    shrink_section,
)
from ..structure.parser import detect_role


def _structure_from_dict(d: dict[str, Any]) -> Structure:
    """Tolerant deserialisation: accepts either the rich dict from
    ``Structure.to_dict()`` or a minimal ``{sections, time_signature, tempo}``."""
    if not isinstance(d, dict):
        raise ValueError(
            f"structure_dict must be a dict, got {type(d).__name__}"
        )
    return Structure.from_dict(d)


def _ok(structure: Structure, **extra: Any) -> dict[str, Any]:
    out = {"status": "ok", "structure": structure.to_dict()}
    out.update(extra)
    return out


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def structure_parse(
        text: str,
        time_signature_num: int = 4,
        time_signature_den: int = 4,
        tempo: float = 120.0,
    ) -> dict[str, Any]:
        """Parse the bar-counted dialect into a Structure dict.

        Accepts forms like ``"intro 4 / verse 8 / chorus 8"`` or
        ``"4 bars intro, 8 bars verse"``. An optional ``= N bars`` total
        validates the section sum. Returns ``{status, structure}`` where
        ``structure`` is the JSON form of :class:`Structure` (round-trip
        safe with the other ``structure_*`` tools).
        """
        try:
            s = parse_structure(
                text,
                time_signature=(int(time_signature_num), int(time_signature_den)),
                tempo=float(tempo),
            )
        except StructureParseError as exc:
            return {"status": "error", "error": "parse_error", "message": str(exc)}
        return _ok(s)

    @mcp.tool()
    async def structure_to_text(structure_dict: dict[str, Any]) -> dict[str, Any]:
        """Render a structure back to the canonical slash notation."""
        try:
            s = _structure_from_dict(structure_dict)
        except Exception as exc:
            return {"status": "error", "error": "bad_structure", "message": str(exc)}
        return {"status": "ok", "text": s.to_text()}

    @mcp.tool()
    async def structure_summary(structure_dict: dict[str, Any]) -> dict[str, Any]:
        """Multi-line musician-friendly summary of a structure."""
        try:
            s = _structure_from_dict(structure_dict)
        except Exception as exc:
            return {"status": "error", "error": "bad_structure", "message": str(exc)}
        return {
            "status": "ok",
            "summary": s.summary(),
            "text": s.to_text(),
            "total_bars": s.total_bars,
            "total_beats": s.total_beats,
            "total_seconds": s.total_seconds,
        }

    @mcp.tool()
    async def structure_extend(
        structure_dict: dict[str, Any], section_name: str, by_bars: int
    ) -> dict[str, Any]:
        """Add ``by_bars`` to the named section. Returns the updated structure."""
        try:
            s = _structure_from_dict(structure_dict)
            new = extend_section(s, section_name, int(by_bars))
        except (KeyError, ValueError) as exc:
            return {"status": "error", "error": type(exc).__name__, "message": str(exc)}
        return _ok(new, edit=f"extended {section_name!r} by {int(by_bars)} bars")

    @mcp.tool()
    async def structure_shrink(
        structure_dict: dict[str, Any], section_name: str, by_bars: int
    ) -> dict[str, Any]:
        """Subtract ``by_bars`` from the named section."""
        try:
            s = _structure_from_dict(structure_dict)
            new = shrink_section(s, section_name, int(by_bars))
        except (KeyError, ValueError) as exc:
            return {"status": "error", "error": type(exc).__name__, "message": str(exc)}
        return _ok(new, edit=f"shrank {section_name!r} by {int(by_bars)} bars")

    @mcp.tool()
    async def structure_duplicate(
        structure_dict: dict[str, Any], section_name: str
    ) -> dict[str, Any]:
        """Insert a copy of the named section right after itself."""
        try:
            s = _structure_from_dict(structure_dict)
            new = duplicate_section(s, section_name)
        except (KeyError, ValueError) as exc:
            return {"status": "error", "error": type(exc).__name__, "message": str(exc)}
        return _ok(new, edit=f"duplicated {section_name!r}")

    @mcp.tool()
    async def structure_insert(
        structure_dict: dict[str, Any],
        after_section_name: Optional[str],
        new_section_name: str,
        bars: int,
        role: Optional[str] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Insert a new section after the named anchor.

        Pass ``after_section_name=None`` (or empty string) to prepend at
        the start. ``role`` is auto-detected from ``new_section_name``
        when not supplied.
        """
        try:
            s = _structure_from_dict(structure_dict)
            normalised_name = " ".join(new_section_name.lower().split())
            chosen_role = role if role else detect_role(normalised_name)
            section = Section(
                name=normalised_name,
                bars=int(bars),
                role=chosen_role,
                notes=str(notes),
            )
            anchor = after_section_name or None
            new = insert_section(s, anchor, section)
        except (KeyError, ValueError, TypeError) as exc:
            return {"status": "error", "error": type(exc).__name__, "message": str(exc)}
        anchor_label = after_section_name if after_section_name else "<start>"
        return _ok(new, edit=f"inserted {normalised_name!r} ({bars} bars) after {anchor_label!r}")

    @mcp.tool()
    async def structure_remove(
        structure_dict: dict[str, Any], section_name: str
    ) -> dict[str, Any]:
        """Remove the named section."""
        try:
            s = _structure_from_dict(structure_dict)
            new = remove_section(s, section_name)
        except (KeyError, ValueError) as exc:
            return {"status": "error", "error": type(exc).__name__, "message": str(exc)}
        return _ok(new, edit=f"removed {section_name!r}")

    @mcp.tool()
    async def structure_section_range(
        structure_dict: dict[str, Any], section_name: str
    ) -> dict[str, Any]:
        """Return ``{start_beat, length_beats, end_beat, start_bar, end_bar}``
        for the named section (does not touch Live)."""
        try:
            s = _structure_from_dict(structure_dict)
            info = section_range_dict(s, section_name)
        except KeyError as exc:
            return {"status": "error", "error": "KeyError", "message": str(exc)}
        info["status"] = "ok"
        return info

    @mcp.tool()
    async def structure_loop_section(
        structure_dict: dict[str, Any],
        section_name: str,
        enable: bool = True,
    ) -> dict[str, Any]:
        """Set Live's arrangement loop to span the named section.

        Sends ``/live/song/set/loop_start`` + ``loop_length`` (and
        optionally toggles loop on). Returns the beat range that was set.
        """
        try:
            s = _structure_from_dict(structure_dict)
            result = await apply_loop_to_section(s, section_name, enable=bool(enable))
        except KeyError as exc:
            return {"status": "error", "error": "KeyError", "message": str(exc)}
        result["status"] = "ok"
        return result

    @mcp.tool()
    async def structure_jump_to_section(
        structure_dict: dict[str, Any], section_name: str
    ) -> dict[str, Any]:
        """Jump arrangement playback to the start of the named section."""
        try:
            s = _structure_from_dict(structure_dict)
            result = await select_section(s, section_name)
        except KeyError as exc:
            return {"status": "error", "error": "KeyError", "message": str(exc)}
        result["status"] = "ok"
        return result
