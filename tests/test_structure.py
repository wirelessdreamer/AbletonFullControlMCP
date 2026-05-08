"""Tests for the bar-counted song-structure dialect.

Covers: parser round-tripping, total-bar assertion failure modes, time-
signature math (4/4 vs 6/8 vs 7/8), all section operations (extend,
shrink, duplicate, insert, remove, rename, move, replace), MCP tool
registration, and the section_range / live_bridge math.

No Live dependency — every test runs offline.
"""

from __future__ import annotations

import pytest

from ableton_mcp.structure import (
    Section,
    Structure,
    StructureParseError,
    beats_per_bar,
    detect_role,
    duplicate_section,
    extend_section,
    insert_section,
    move_section,
    parse_structure,
    remove_section,
    rename_section,
    replace_section,
    section_range_dict,
    section_to_beat_range,
    shrink_section,
)
from ableton_mcp.tools import structure as structure_tools


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_simple_slash_form() -> None:
    s = parse_structure("intro 4 / verse 8 / chorus 8")
    assert [sec.name for sec in s] == ["intro", "verse", "chorus"]
    assert [sec.bars for sec in s] == [4, 8, 8]
    assert s.total_bars == 20


def test_parse_with_total_assertion_passes() -> None:
    s = parse_structure("intro 4 / verse 8 / chorus 8 = 20 bars")
    assert s.total_bars == 20


def test_parse_with_total_assertion_fails_clearly() -> None:
    with pytest.raises(StructureParseError) as ei:
        parse_structure("intro 4 / verse 8 / chorus 8 = 99 bars")
    msg = str(ei.value)
    assert "total mismatch" in msg
    assert "99" in msg
    assert "20" in msg


def test_parse_numbered_form() -> None:
    s = parse_structure("4 bars intro, 8 bars verse, 8 bars chorus")
    assert [sec.name for sec in s] == ["intro", "verse", "chorus"]
    assert [sec.bars for sec in s] == [4, 8, 8]


def test_parse_short_unit_b() -> None:
    s = parse_structure("intro 4 b / verse 8 b")
    assert [sec.bars for sec in s] == [4, 8]


def test_parse_lowercases_names() -> None:
    s = parse_structure("Intro 4 / Verse 8 / CHORUS 8")
    assert [sec.name for sec in s] == ["intro", "verse", "chorus"]


def test_parse_preserves_user_dialect_round_trip() -> None:
    """The seed example from jrock_composition.py."""
    text = "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"
    s = parse_structure(text)
    assert s.total_bars == 35
    assert s.to_text() == text


def test_parse_round_trip_normalises_whitespace() -> None:
    s = parse_structure("intro   4  ,   verse  8 ,chorus 8")
    assert s.to_text() == "intro 4 / verse 8 / chorus 8 = 20 bars"


def test_parse_empty_raises() -> None:
    with pytest.raises(StructureParseError):
        parse_structure("")


def test_parse_garbage_raises() -> None:
    with pytest.raises(StructureParseError):
        parse_structure("intro / verse 8")  # missing bar count on intro


def test_parse_zero_bars_raises() -> None:
    with pytest.raises(StructureParseError):
        parse_structure("intro 0 / verse 8")


def test_parse_with_arrow_separator() -> None:
    s = parse_structure("intro 4 -> verse 8 -> chorus 8")
    assert s.total_bars == 20


def test_parse_with_then_separator() -> None:
    s = parse_structure("intro 4 then verse 8 then chorus 8")
    assert [sec.name for sec in s] == ["intro", "verse", "chorus"]


# ---------------------------------------------------------------------------
# Role detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("intro", "intro"),
        ("verse", "verse"),
        ("verse 1", "verse"),
        ("verse 2", "verse"),
        ("Verse 2B", "verse"),
        ("pre-chorus", "pre_chorus"),
        ("pre chorus", "pre_chorus"),
        ("prechorus", "pre_chorus"),
        ("post-chorus", "post_chorus"),
        ("chorus", "chorus"),
        ("hook", "chorus"),
        ("refrain", "refrain"),
        ("bridge", "bridge"),
        ("middle 8", "bridge"),
        ("middle eight", "bridge"),
        ("breakdown", "breakdown"),
        ("drop", "drop"),
        ("buildup", "build"),
        ("build-up", "build"),
        ("riser", "build"),
        ("interlude", "interlude"),
        ("solo", "solo"),
        ("outro", "outro"),
        ("ending", "outro"),
        ("coda", "outro"),
        ("fill", "fill"),
        ("hit", "hit"),
        ("tag", "tag"),
        ("vamp", "vamp"),
        ("groove", "vamp"),
        ("main groove", "vamp"),
        ("final", "vamp"),
        ("main groove A", "vamp"),
        ("half-time", "half_time"),
        ("doubletime", "double_time"),
        ("nonsense", "other"),
        ("flügelhorn", "other"),
    ],
)
def test_detect_role(name: str, expected: str) -> None:
    assert detect_role(name) == expected


def test_parser_assigns_role_via_detection() -> None:
    s = parse_structure("intro 4 / main groove 8 / breakdown 6 / final 8 = 26 bars")
    roles = {sec.name: sec.role for sec in s}
    assert roles == {
        "intro": "intro",
        "main groove": "vamp",
        "breakdown": "breakdown",
        "final": "vamp",
    }


# ---------------------------------------------------------------------------
# Time-signature math
# ---------------------------------------------------------------------------


def test_beats_per_bar_basic() -> None:
    assert beats_per_bar(4, 4) == 4.0
    assert beats_per_bar(3, 4) == 3.0
    assert beats_per_bar(6, 8) == 3.0
    assert beats_per_bar(7, 8) == 3.5
    assert beats_per_bar(12, 8) == 6.0
    assert beats_per_bar(5, 4) == 5.0


def test_six_eight_at_144_bpm_section_math() -> None:
    """A section of 4 bars in 6/8 at 144 BPM:

    - 4 bars × 3 beats/bar = 12 beats
    - 12 beats / 144 BPM × 60 = 5.0 seconds
    """
    s = Structure(
        sections=[Section("intro", 4)],
        time_signature=(6, 8),
        tempo=144.0,
    )
    assert s.beats_per_bar == pytest.approx(3.0)
    assert s.total_beats == pytest.approx(12.0)
    assert s.total_seconds == pytest.approx(5.0)


def test_seven_eight_section_math() -> None:
    s = parse_structure("intro 4 / verse 8", time_signature=(7, 8), tempo=120.0)
    assert s.beats_per_bar == pytest.approx(3.5)
    # 12 bars * 3.5 = 42 beats
    assert s.total_beats == pytest.approx(42.0)
    assert s.start_beat("verse") == pytest.approx(14.0)


def test_jrock_full_song_math_six_eight_144_bpm() -> None:
    """The seed example: 35 bars in 6/8 @ 144 BPM = 105 beats = ~43.75s."""
    text = "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"
    s = parse_structure(text, time_signature=(6, 8), tempo=144.0)
    assert s.total_bars == 35
    assert s.total_beats == pytest.approx(105.0)
    assert s.total_seconds == pytest.approx(43.75)
    # Each section's start_beat:
    assert s.start_beat("intro") == 0.0
    assert s.start_beat("groove") == pytest.approx(12.0)
    assert s.start_beat("breakdown") == pytest.approx(36.0)
    assert s.start_beat("interlude") == pytest.approx(54.0)
    assert s.start_beat("buildup") == pytest.approx(72.0)
    assert s.start_beat("final") == pytest.approx(81.0)
    # Bar numbering (1-based):
    assert s.start_bar("intro") == 1
    assert s.start_bar("groove") == 5
    assert s.start_bar("breakdown") == 13
    assert s.end_bar("final") == 35


# ---------------------------------------------------------------------------
# Operations — every op returns a new Structure; original unmodified.
# ---------------------------------------------------------------------------


def _seed() -> Structure:
    return parse_structure(
        "intro 4 / verse 8 / chorus 8 / verse 8 / chorus 8 / outro 4 = 40 bars"
    )


def test_extend_returns_new_structure_unmodified_original() -> None:
    original = _seed()
    snapshot = original.to_text()
    new = extend_section(original, "chorus", 4)
    # Original unchanged.
    assert original.to_text() == snapshot
    # New has the chorus extended (first occurrence wins).
    assert new.find("chorus").bars == 12
    assert new.total_bars == original.total_bars + 4
    # Different object.
    assert new is not original
    assert new.sections is not original.sections


def test_shrink_returns_new_structure_unmodified_original() -> None:
    original = _seed()
    snap = original.to_text()
    new = shrink_section(original, "verse", 2)
    assert original.to_text() == snap
    assert new.find("verse").bars == 6


def test_shrink_to_zero_or_below_raises() -> None:
    s = _seed()
    with pytest.raises(ValueError):
        shrink_section(s, "outro", 4)  # outro is 4; shrinking by 4 → 0


def test_extend_negative_raises() -> None:
    s = _seed()
    with pytest.raises(ValueError):
        extend_section(s, "verse", -2)


def test_duplicate_returns_new_structure() -> None:
    original = _seed()
    snap = original.to_text()
    new = duplicate_section(original, "outro")
    assert original.to_text() == snap
    # Two outros now, back-to-back, at the end.
    names = [sec.name for sec in new]
    assert names[-2:] == ["outro", "outro"]
    assert new.total_bars == original.total_bars + 4


def test_insert_section_after_named_anchor() -> None:
    original = _seed()
    snap = original.to_text()
    fill = Section(name="fill", bars=2, role="fill")
    new = insert_section(original, "verse", fill)
    assert original.to_text() == snap
    # First "verse" is at index 1, so "fill" goes at index 2.
    names = [sec.name for sec in new]
    assert names[:4] == ["intro", "verse", "fill", "chorus"]


def test_insert_section_at_start() -> None:
    original = _seed()
    hit = Section(name="hit", bars=1, role="hit")
    new = insert_section(original, None, hit)
    assert [sec.name for sec in new][:2] == ["hit", "intro"]
    # Original unmodified
    assert original.section_names()[0] == "intro"


def test_remove_returns_new_structure() -> None:
    original = _seed()
    snap = original.to_text()
    new = remove_section(original, "outro")
    assert original.to_text() == snap
    assert "outro" not in [sec.name for sec in new]
    assert new.total_bars == original.total_bars - 4


def test_rename_section() -> None:
    original = _seed()
    new = rename_section(original, "outro", "ending tag")
    # First "outro" renamed; original untouched.
    assert "outro" in [sec.name for sec in original]
    assert "ending tag" in [sec.name for sec in new]


def test_move_section() -> None:
    original = _seed()
    new = move_section(original, "outro", 0)
    assert new.section_names()[0] == "outro"
    # Original unchanged.
    assert original.section_names()[0] == "intro"


def test_replace_section() -> None:
    original = _seed()
    new = replace_section(original, "verse", Section(name="vamp", bars=16, role="vamp"))
    assert original.section_names() == [
        "intro",
        "verse",
        "chorus",
        "verse",
        "chorus",
        "outro",
    ]
    # Only the FIRST verse is replaced.
    assert new.section_names() == [
        "intro",
        "vamp",
        "chorus",
        "verse",
        "chorus",
        "outro",
    ]


def test_lookup_unknown_section_raises_keyerror() -> None:
    s = _seed()
    with pytest.raises(KeyError):
        extend_section(s, "drop", 4)


# ---------------------------------------------------------------------------
# Section model — invariants, serialisation, beat/bar math
# ---------------------------------------------------------------------------


def test_section_validates_inputs() -> None:
    with pytest.raises(ValueError):
        Section(name="", bars=4)
    with pytest.raises(ValueError):
        Section(name="intro", bars=0)
    with pytest.raises(ValueError):
        Section(name="intro", bars=4, role="not_a_role")


def test_section_normalises_name() -> None:
    s = Section(name="  Verse   1  ", bars=4)
    assert s.name == "verse 1"


def test_structure_to_dict_round_trip() -> None:
    s = parse_structure(
        "intro 4 / verse 8 / chorus 8 = 20 bars",
        time_signature=(6, 8),
        tempo=144.0,
    )
    d = s.to_dict()
    s2 = Structure.from_dict(d)
    assert s2.to_text() == s.to_text()
    assert s2.time_signature == s.time_signature
    assert s2.tempo == s.tempo


def test_section_range_dict_shape() -> None:
    s = parse_structure(
        "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars",
        time_signature=(6, 8),
        tempo=144.0,
    )
    info = section_range_dict(s, "breakdown")
    assert info["section"] == "breakdown"
    assert info["start_beat"] == pytest.approx(36.0)
    assert info["length_beats"] == pytest.approx(18.0)
    assert info["end_beat"] == pytest.approx(54.0)
    assert info["start_bar"] == 13
    assert info["end_bar"] == 18
    assert info["bars"] == 6
    assert info["time_signature"] == "6/8"


def test_section_to_beat_range() -> None:
    s = parse_structure("intro 4 / verse 8")
    assert section_to_beat_range(s, "verse") == (16.0, 32.0)


def test_summary_includes_section_lines_and_total() -> None:
    s = parse_structure("intro 4 / verse 8 = 12 bars", time_signature=(4, 4))
    summary = s.summary()
    assert "intro" in summary
    assert "verse" in summary
    assert "12 bars" in summary
    assert "4/4" in summary


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_registers_at_least_ten_tools() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-structure-only")
    structure_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "structure_parse",
        "structure_to_text",
        "structure_summary",
        "structure_extend",
        "structure_shrink",
        "structure_duplicate",
        "structure_insert",
        "structure_remove",
        "structure_section_range",
        "structure_loop_section",
        "structure_jump_to_section",
    }
    missing = expected - names
    assert not missing, f"missing tools: {sorted(missing)}"
    assert len(names) >= 10, f"expected at least 10 tools, got {len(names)}: {sorted(names)}"


# ---------------------------------------------------------------------------
# End-to-end: parse → edit → render the seed example back
# ---------------------------------------------------------------------------


def test_extend_breakdown_by_4_round_trip() -> None:
    """Worked example from the README:  'extend the breakdown by 4 bars'."""
    text = "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"
    s = parse_structure(text, time_signature=(6, 8), tempo=144.0)
    new = extend_section(s, "breakdown", 4)
    assert new.find("breakdown").bars == 10
    assert new.total_bars == 39
    assert (
        new.to_text()
        == "intro 4 / groove 8 / breakdown 10 / interlude 6 / buildup 3 / final 8 = 39 bars"
    )


def test_duplicate_final_round_trip() -> None:
    """'duplicate the final groove'."""
    text = "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"
    s = parse_structure(text, time_signature=(6, 8), tempo=144.0)
    new = duplicate_section(s, "final")
    assert new.section_names()[-2:] == ["final", "final"]
    assert new.total_bars == 43


def test_insert_2_bar_fill_before_buildup_round_trip() -> None:
    """'insert a 2-bar fill before the buildup' — anchor on the section
    immediately preceding 'buildup' (interlude)."""
    text = "intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars"
    s = parse_structure(text, time_signature=(6, 8), tempo=144.0)
    new = insert_section(s, "interlude", Section("fill", 2, role="fill"))
    assert new.section_names() == [
        "intro",
        "groove",
        "breakdown",
        "interlude",
        "fill",
        "buildup",
        "final",
    ]
    assert new.total_bars == 37
