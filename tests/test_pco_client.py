"""Tests for the Planning Center Services client (ableton_mcp.pco).

All network I/O is monkeypatched at PCOClient._get — no wire traffic.
"""

from __future__ import annotations

from typing import Any

import pytest

from ableton_mcp.pco import (
    ChartModel,
    ChartSection,
    PCOClient,
    PCOError,
    PCONotConfigured,
    _extract_sections,
    sections_from_chordpro,
)


# ---------------------------------------------------------------------------
# Fixture payloads (JSON:API shapes)
# ---------------------------------------------------------------------------


def _song_payload() -> dict[str, Any]:
    return {
        "data": {
            "type": "Song",
            "id": "s1",
            "attributes": {
                "title": "Goodness of God",
                "author": "Bethel Music",
                "ccli_number": "7117726",
            },
        }
    }


def _arrangements_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "type": "Arrangement",
                "id": "a1",
                "attributes": {
                    "name": "Default",
                    "bpm": 63.0,
                    "meter": "4/4",
                    "length": 296,
                    "sequence": ["Verse 1", "Chorus", "Verse 2", "Chorus", "Bridge", "Chorus"],
                    "chord_chart": None,
                },
            }
        ]
    }


def _sections_payload_list_shape() -> dict[str, Any]:
    return {
        "data": [
            {
                "type": "Section",
                "id": "sec1",
                "attributes": {"label": "Verse 1", "lyrics": "I love You Lord\nOh Your mercy never fails me"},
            },
            {
                "type": "Section",
                "id": "sec2",
                "attributes": {"label": "Chorus", "lyrics": "All my life You have been faithful"},
            },
        ]
    }


def _sections_payload_nested_shape() -> dict[str, Any]:
    return {
        "data": {
            "type": "ArrangementSections",
            "id": "a1",
            "attributes": {
                "sections": [
                    {"label": "Verse 1", "lyrics": "I love You Lord"},
                    {"label": "Chorus", "lyrics": "All my life You have been faithful"},
                ]
            },
        }
    }


def _keys_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "type": "Key",
                "id": "k1",
                "attributes": {"name": "Default", "starting_key": "Ab", "ending_key": "Ab"},
            }
        ]
    }


def _route(payloads: dict[str, Any]):
    """Build a fake _get keyed on path substring."""

    calls: list[str] = []

    async def fake_get(self: PCOClient, path: str, params: dict | None = None) -> dict:
        calls.append(path)
        for fragment, payload in payloads.items():
            if fragment in path:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected PCO GET {path!r}")

    fake_get.calls = calls  # type: ignore[attr-defined]
    return fake_get


def _client() -> PCOClient:
    return PCOClient(app_id="app", secret="sec")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_not_configured_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PCO_APP_ID", raising=False)
    monkeypatch.delenv("PCO_SECRET", raising=False)
    client = PCOClient()
    assert not client.is_configured()
    with pytest.raises(PCONotConfigured, match="PCO_APP_ID"):
        client._require_creds()


def test_configured_via_args() -> None:
    assert _client().is_configured()


def test_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCO_APP_ID", "x")
    monkeypatch.setenv("PCO_SECRET", "y")
    assert PCOClient().is_configured()


# ---------------------------------------------------------------------------
# Section extraction (both endpoint shapes + schema notes)
# ---------------------------------------------------------------------------


def test_extract_sections_list_shape() -> None:
    sections, notes = _extract_sections(_sections_payload_list_shape())
    assert [s.label for s in sections] == ["Verse 1", "Chorus"]
    assert sections[0].role == "verse"
    assert sections[1].role == "chorus"
    assert "mercy never fails" in sections[0].lyrics
    assert notes == []


def test_extract_sections_nested_shape() -> None:
    sections, notes = _extract_sections(_sections_payload_nested_shape())
    assert [s.label for s in sections] == ["Verse 1", "Chorus"]
    assert notes == []


def test_extract_sections_unknown_shape_reports_keys() -> None:
    payload = {
        "data": {
            "type": "ArrangementSections",
            "id": "a1",
            "attributes": {"weird_key": []},
        }
    }
    sections, notes = _extract_sections(payload)
    assert sections == []
    assert any("weird_key" in n for n in notes)


def test_extract_sections_missing_lyrics_key_noted() -> None:
    payload = {
        "data": [
            {"type": "Section", "id": "s", "attributes": {"label": "Verse 1", "other": 1}}
        ]
    }
    sections, notes = _extract_sections(payload)
    assert sections[0].lyrics == ""
    assert any("no 'lyrics' key" in n for n in notes)


# ---------------------------------------------------------------------------
# ChordPro fallback parser
# ---------------------------------------------------------------------------


CHORDPRO = """{title: Goodness of God}
{key: Ab}

VERSE 1
I [Ab]love You [Db]Lord
Oh Your [Eb]mercy never [Ab]fails me

Chorus:
[Ab]All my [Eb]life You have been [Fm]faithful

{comment: Bridge}
Your [Db]goodness is running after
{comment_italic: repeat 2x}
"""


def test_sections_from_chordpro() -> None:
    sections = sections_from_chordpro(CHORDPRO)
    labels = [s.label for s in sections]
    assert labels == ["VERSE 1", "Chorus", "Bridge"]
    # Chords stripped, lyrics intact.
    assert sections[0].lyrics == "I love You Lord\nOh Your mercy never fails me"
    assert "[" not in sections[1].lyrics
    assert sections[2].role == "bridge"
    # The {comment_italic: repeat 2x} line is a header-ish directive but not a
    # recognized section — it must not leak into lyrics or create a section.
    assert "repeat" not in " ".join(s.lyrics for s in sections)


def test_chordpro_ignores_preamble_without_header() -> None:
    sections = sections_from_chordpro("just a line\nanother line\n")
    assert sections == []


# ---------------------------------------------------------------------------
# get_chart assembly
# ---------------------------------------------------------------------------


async def test_get_chart_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _route(
        {
            "/sections": _sections_payload_list_shape(),
            "/keys": _keys_payload(),
            "/arrangements": _arrangements_payload(),
            "songs/s1": _song_payload(),
        }
    )
    monkeypatch.setattr(PCOClient, "_get", fake)
    chart = await _client().get_chart("s1")
    assert chart.title == "Goodness of God"
    assert chart.arrangement_id == "a1"
    assert chart.key == "Ab"
    assert chart.bpm == 63.0
    assert chart.meter == "4/4"
    assert chart.sequence[0] == "Verse 1"
    assert len(chart.sections) == 2
    assert chart.warnings == ()


async def test_get_chart_multiple_arrangements_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrangements = _arrangements_payload()
    arrangements["data"].append(
        {
            "type": "Arrangement",
            "id": "a2",
            "attributes": {"name": "Acoustic", "sequence": []},
        }
    )
    fake = _route(
        {
            "/sections": _sections_payload_list_shape(),
            "/keys": _keys_payload(),
            "/arrangements": arrangements,
            "songs/s1": _song_payload(),
        }
    )
    monkeypatch.setattr(PCOClient, "_get", fake)
    chart = await _client().get_chart("s1")
    assert chart.arrangement_id == "a1"
    assert any("2 arrangements" in w for w in chart.warnings)


async def test_get_chart_explicit_arrangement_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _route(
        {
            "/arrangements": _arrangements_payload(),
            "songs/s1": _song_payload(),
        }
    )
    monkeypatch.setattr(PCOClient, "_get", fake)
    with pytest.raises(PCOError, match="not found"):
        await _client().get_chart("s1", arrangement_id="nope")


async def test_get_chart_chordpro_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    arrangements = _arrangements_payload()
    arrangements["data"][0]["attributes"]["chord_chart"] = CHORDPRO
    # Sections endpoint returns labels but NO lyrics.
    empty_sections = {
        "data": [
            {"type": "Section", "id": "x", "attributes": {"label": "Verse 1", "lyrics": ""}}
        ]
    }
    fake = _route(
        {
            "/sections": empty_sections,
            "/keys": _keys_payload(),
            "/arrangements": arrangements,
            "songs/s1": _song_payload(),
        }
    )
    monkeypatch.setattr(PCOClient, "_get", fake)
    chart = await _client().get_chart("s1")
    assert any("chord_chart" in w for w in chart.warnings)
    assert any(s.lyrics for s in chart.sections)
    assert [s.label for s in chart.sections] == ["VERSE 1", "Chorus", "Bridge"]


async def test_get_chart_keys_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _route(
        {
            "/sections": _sections_payload_list_shape(),
            "/keys": PCOError("keys endpoint 500"),
            "/arrangements": _arrangements_payload(),
            "songs/s1": _song_payload(),
        }
    )
    monkeypatch.setattr(PCOClient, "_get", fake)
    chart = await _client().get_chart("s1")
    assert chart.key is None
    assert any("keys endpoint failed" in w for w in chart.warnings)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


async def test_get_all_pages_follows_next(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = {
        "data": [{"id": "1", "attributes": {"title": "A"}}],
        "links": {"next": "https://api.planningcenteronline.com/services/v2/songs?offset=1"},
    }
    page2 = {"data": [{"id": "2", "attributes": {"title": "B"}}], "links": {}}

    async def fake_get(self: PCOClient, path: str, params: dict | None = None) -> dict:
        return page2 if "offset=1" in path else page1

    monkeypatch.setattr(PCOClient, "_get", fake_get)
    songs = await _client().list_songs()
    assert [s["title"] for s in songs] == ["A", "B"]


# ---------------------------------------------------------------------------
# library_probe
# ---------------------------------------------------------------------------


async def test_library_probe_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    songs_payload = {
        "data": [
            {"id": "s1", "attributes": {"title": "Song One"}},
            {"id": "s2", "attributes": {"title": "Song Two"}},
        ],
        "links": {},
    }
    arr_no_sequence = {
        "data": [
            {"id": "a2", "attributes": {"name": "Default", "sequence": [], "bpm": None}}
        ]
    }

    async def fake_get(self: PCOClient, path: str, params: dict | None = None) -> dict:
        if path.endswith("songs") or "songs?" in path:
            return songs_payload
        if "s1/arrangements/a1/sections" in path:
            return _sections_payload_list_shape()
        if "s2/arrangements/a2/sections" in path:
            return {"data": []}
        if "s1/arrangements" in path:
            return _arrangements_payload()
        if "s2/arrangements" in path:
            return arr_no_sequence
        raise AssertionError(f"unexpected GET {path}")

    monkeypatch.setattr(PCOClient, "_get", fake_get)
    report = await _client().library_probe(limit=10, throttle_sec=0.0)

    assert report["sampled"] == 2
    cov = report["coverage"]
    assert cov["pct_with_sequence"] == 50.0
    assert cov["pct_with_section_lyrics"] == 50.0
    assert cov["pct_with_bpm"] == 50.0
    assert cov["errors"] == 0
    # 50% < the warning threshold is not hit (50 is not < 50).
    songs = {s["song_id"]: s for s in report["songs"]}
    assert songs["s1"]["sections_with_lyrics"] == 2
    assert songs["s2"]["section_count"] == 0


async def test_library_probe_records_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    songs_payload = {"data": [{"id": "s1", "attributes": {"title": "Broken"}}], "links": {}}

    async def fake_get(self: PCOClient, path: str, params: dict | None = None) -> dict:
        if path.endswith("songs") or "songs?" in path:
            return songs_payload
        raise PCOError("boom")

    monkeypatch.setattr(PCOClient, "_get", fake_get)
    report = await _client().library_probe(limit=5, throttle_sec=0.0)
    assert report["coverage"]["errors"] == 1
    assert report["songs"][0]["error"] == "PCOError: boom" or "boom" in report["songs"][0]["error"]


# ---------------------------------------------------------------------------
# ChartModel round-trip
# ---------------------------------------------------------------------------


def test_chart_model_round_trip() -> None:
    chart = ChartModel(
        song_id="s1",
        arrangement_id="a1",
        title="T",
        key="G",
        bpm=72.0,
        meter="4/4",
        sequence=("Verse 1", "Chorus"),
        sections=(ChartSection(label="Verse 1", role="verse", lyrics="line"),),
        warnings=("w",),
    )
    again = ChartModel.from_dict(chart.to_dict())
    assert again == chart


def test_chart_model_from_dict_detects_role() -> None:
    chart = ChartModel.from_dict(
        {
            "song_id": "s",
            "arrangement_id": "a",
            "title": "T",
            "sections": [{"label": "Bridge", "lyrics": "x"}],
        }
    )
    assert chart.sections[0].role == "bridge"
