"""Bar-counted song-structure model + parser + editor.

Public surface — import from this package, not from the submodules:

>>> from ableton_mcp.structure import parse_structure, extend_section
>>> s = parse_structure("intro 4 / verse 8 / chorus 8 = 20 bars")
>>> s.to_text()
'intro 4 / verse 8 / chorus 8 = 20 bars'
>>> extend_section(s, "verse", 4).to_text()
'intro 4 / verse 12 / chorus 8 = 24 bars'

The data layer (``model``, ``parser``, ``operations``) has no Live
dependency and is freely importable from tests / scripts. The
``live_bridge`` module is the only piece that talks OSC.
"""

from __future__ import annotations

from .live_bridge import (
    apply_loop_to_section,
    section_range_dict,
    section_to_beat_range,
    select_section,
)
from .model import VALID_ROLES, Section, Structure, beats_per_bar
from .operations import (
    duplicate_section,
    extend_section,
    insert_section,
    move_section,
    remove_section,
    rename_section,
    replace_section,
    shrink_section,
)
from .parser import StructureParseError, detect_role, parse_structure


__all__ = [
    # model
    "Section",
    "Structure",
    "VALID_ROLES",
    "beats_per_bar",
    # parser
    "parse_structure",
    "detect_role",
    "StructureParseError",
    # operations
    "extend_section",
    "shrink_section",
    "duplicate_section",
    "insert_section",
    "remove_section",
    "rename_section",
    "move_section",
    "replace_section",
    # live bridge
    "section_to_beat_range",
    "section_range_dict",
    "apply_loop_to_section",
    "select_section",
]
