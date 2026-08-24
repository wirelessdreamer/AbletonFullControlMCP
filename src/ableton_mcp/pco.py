"""Planning Center Services (PCO) API client + normalized chart model.

Fetches the chart-side inputs for lyric-aligned section detection: a song's
arrangement ``sequence`` (ordered section labels), per-section lyrics, key,
BPM, and meter. Credentials follow the SunoGenerator pattern
(:mod:`ableton_mcp.generators.suno`): a Personal Access Token read from the
``PCO_APP_ID`` / ``PCO_SECRET`` environment variables, sent as HTTP Basic
auth. All network traffic goes through :meth:`PCOClient._get` so tests can
monkeypatch it without touching the wire.

API notes (Services v2, https://api.planningcenteronline.com/services/v2):

- JSON:API responses: ``{"data": [...], "links": {"next": ...}, "meta": ...}``.
- Rate limit is 100 requests per 20 s; 429 replies carry ``Retry-After``.
- The arrangement "sections" endpoint's exact attribute shape is the one
  thing prior research could not verify against a live account, so
  :func:`_extract_sections` is deliberately tolerant and
  ``pco_library_probe`` reports the raw keys it actually saw.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .structure.parser import detect_role


class PCOError(RuntimeError):
    """A PCO API call failed (non-2xx, malformed payload, ...)."""


class PCONotConfigured(PCOError):
    """Credentials missing — PCO_APP_ID / PCO_SECRET not set."""


# ---------------------------------------------------------------------------
# Chart model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChartSection:
    """One *unique* section definition from the chart (not one performance).

    ``lyrics`` is plain newline-separated text, ``""`` for instrumental
    sections (Intro/Interlude/...). ``role`` comes from
    :func:`ableton_mcp.structure.parser.detect_role` (``"other"`` fallback).
    """

    label: str
    role: str
    lyrics: str

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "role": self.role, "lyrics": self.lyrics}


@dataclass(frozen=True)
class ChartModel:
    """Normalized chart: everything section detection needs from PCO."""

    song_id: str
    arrangement_id: str
    title: str
    ccli_number: str | None = None
    key: str | None = None
    bpm: float | None = None
    meter: str | None = None
    sequence: tuple[str, ...] = ()
    sections: tuple[ChartSection, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "song_id": self.song_id,
            "arrangement_id": self.arrangement_id,
            "title": self.title,
            "ccli_number": self.ccli_number,
            "key": self.key,
            "bpm": self.bpm,
            "meter": self.meter,
            "sequence": list(self.sequence),
            "sections": [s.to_dict() for s in self.sections],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChartModel":
        return cls(
            song_id=str(d.get("song_id", "")),
            arrangement_id=str(d.get("arrangement_id", "")),
            title=str(d.get("title", "")),
            ccli_number=d.get("ccli_number"),
            key=d.get("key"),
            bpm=float(d["bpm"]) if d.get("bpm") is not None else None,
            meter=d.get("meter"),
            sequence=tuple(d.get("sequence") or ()),
            sections=tuple(
                ChartSection(
                    label=str(s.get("label", "")),
                    role=str(s.get("role") or detect_role(str(s.get("label", "")))),
                    lyrics=str(s.get("lyrics", "")),
                )
                for s in (d.get("sections") or ())
            ),
            warnings=tuple(d.get("warnings") or ()),
        )


# ---------------------------------------------------------------------------
# ChordPro fallback — used when the sections endpoint has no lyrics but the
# arrangement carries a chord_chart.
# ---------------------------------------------------------------------------

# {comment: Verse 1} / {c: Verse 1} / {comment_italic: ...}
_CHORDPRO_COMMENT_RE = re.compile(
    r"^\s*\{(?:comment|c|comment_italic|ci|comment_box|cb)\s*:\s*(?P<label>[^}]+)\}\s*$",
    re.IGNORECASE,
)
# Any other {directive} line — dropped from lyrics.
_CHORDPRO_DIRECTIVE_RE = re.compile(r"^\s*\{[^}]*\}\s*$")
# Inline [G] / [Am7/E] chord tokens — stripped from lyric lines.
_CHORD_TOKEN_RE = re.compile(r"\[[A-G][#b]?[^\]\s]*\]")
# Bare header line: "VERSE 1", "Chorus:", "[Bridge]", "(Tag)"
_BARE_HEADER_RE = re.compile(
    r"^\s*[\[\(]?\s*(?P<label>[A-Za-z][A-Za-z \-]{1,20}?\s*\d?[a-cA-C]?)\s*[\]\)]?\s*:?\s*$"
)


# Section-ish words detect_role doesn't know (it returns "other" for these,
# but as chart headers they clearly name instrumental sections).
_EXTRA_HEADER_WORDS = frozenset({"instrumental", "turnaround", "channel", "count in", "count-in"})


def _is_section_label(label: str) -> bool:
    if detect_role(label) != "other":
        return True
    stem = re.sub(r"\s*\d+[a-c]?\s*$", "", label.strip().lower())
    return stem in _EXTRA_HEADER_WORDS


def _looks_like_header(line: str) -> str | None:
    """Return the section label if ``line`` is a section header, else None.

    Both header forms are gated on the label *looking like* a section name —
    a ``{comment: repeat 2x}`` playing note must not become a section.
    """
    m = _CHORDPRO_COMMENT_RE.match(line)
    if not m:
        m = _BARE_HEADER_RE.match(line)
    if m:
        label = m.group("label").strip()
        if _is_section_label(label):
            return label
    return None


def sections_from_chordpro(chord_chart: str) -> list[ChartSection]:
    """Parse a ChordPro-ish chord chart into labeled lyric sections.

    Defensive fallback only — handles the SongSelect/PCO conventions
    (``{comment: Verse 1}`` directives, bare ``VERSE 1`` header lines,
    inline ``[G]`` chords) and drops everything it does not recognise.
    """
    sections: list[ChartSection] = []
    label: str | None = None
    lines: list[str] = []

    def _flush() -> None:
        nonlocal label, lines
        if label is not None:
            lyrics = "\n".join(ln for ln in (s.strip() for s in lines) if ln)
            sections.append(
                ChartSection(label=label, role=detect_role(label), lyrics=lyrics)
            )
        label, lines = None, []

    for raw in chord_chart.splitlines():
        header = _looks_like_header(raw)
        if header is not None:
            _flush()
            label = header
            continue
        if _CHORDPRO_DIRECTIVE_RE.match(raw):
            continue
        if label is not None:
            lines.append(_CHORD_TOKEN_RE.sub("", raw))
    _flush()
    return sections


# ---------------------------------------------------------------------------
# Tolerant extraction from the (unverified-shape) sections endpoint
# ---------------------------------------------------------------------------


def _extract_sections(payload: dict[str, Any]) -> tuple[list[ChartSection], list[str]]:
    """Pull ``(label, lyrics)`` pairs out of the arrangement-sections reply.

    Accepts both plausible JSON:API shapes:

    - ``data`` is a *list* of resources, each with ``attributes.label`` /
      ``attributes.lyrics``.
    - ``data`` is a *single* resource whose ``attributes.sections`` is a
      list of ``{label, lyrics}`` dicts.

    Returns the sections plus ``schema_notes`` describing anything odd
    (surfaced by ``pco_library_probe`` so shape mismatches are visible
    instead of silently yielding zero sections).
    """
    notes: list[str] = []
    data = payload.get("data")
    raw_items: list[dict[str, Any]] = []

    if isinstance(data, list):
        for res in data:
            attrs = res.get("attributes") if isinstance(res, dict) else None
            if isinstance(attrs, dict):
                raw_items.append(attrs)
    elif isinstance(data, dict):
        attrs = data.get("attributes")
        if isinstance(attrs, dict):
            inner = attrs.get("sections")
            if isinstance(inner, list):
                raw_items = [it for it in inner if isinstance(it, dict)]
            else:
                notes.append(
                    "sections endpoint: single resource without attributes.sections; "
                    f"attribute keys seen: {sorted(attrs.keys())}"
                )
    else:
        notes.append(f"sections endpoint: unexpected data type {type(data).__name__}")

    sections: list[ChartSection] = []
    for item in raw_items:
        label = item.get("label") or item.get("name")
        if not label:
            notes.append(
                f"sections endpoint: item without label; keys seen: {sorted(item.keys())}"
            )
            continue
        lyrics = item.get("lyrics")
        if lyrics is None:
            lyrics = ""
            if "lyrics" not in item:
                notes.append(
                    f"sections endpoint: item {label!r} has no 'lyrics' key; "
                    f"keys seen: {sorted(item.keys())}"
                )
        sections.append(
            ChartSection(
                label=str(label).strip(),
                role=detect_role(str(label)),
                lyrics=str(lyrics).strip(),
            )
        )
    return sections, notes


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class ProbeSongReport:
    """Per-song coverage facts collected by :meth:`PCOClient.library_probe`."""

    song_id: str
    title: str
    arrangement_id: str | None = None
    has_sequence: bool = False
    sequence_len: int = 0
    section_count: int = 0
    sections_with_lyrics: int = 0
    has_bpm: bool = False
    has_meter: bool = False
    has_chord_chart: bool = False
    error: str | None = None
    schema_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "song_id": self.song_id,
            "title": self.title,
            "arrangement_id": self.arrangement_id,
            "has_sequence": self.has_sequence,
            "sequence_len": self.sequence_len,
            "section_count": self.section_count,
            "sections_with_lyrics": self.sections_with_lyrics,
            "has_bpm": self.has_bpm,
            "has_meter": self.has_meter,
            "has_chord_chart": self.has_chord_chart,
            "error": self.error,
            "schema_notes": list(self.schema_notes),
        }


class PCOClient:
    """Minimal async Planning Center Services v2 client.

    Reads ``PCO_APP_ID`` / ``PCO_SECRET`` (a Personal Access Token pair from
    https://api.planningcenteronline.com/oauth/applications) unless explicit
    credentials are passed. Network I/O is confined to :meth:`_get`.
    """

    DEFAULT_BASE = "https://api.planningcenteronline.com/services/v2"
    MAX_RETRIES = 3

    def __init__(
        self,
        app_id: str | None = None,
        secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._app_id = app_id if app_id is not None else os.environ.get("PCO_APP_ID")
        self._secret = secret if secret is not None else os.environ.get("PCO_SECRET")
        self._base_url = (base_url or self.DEFAULT_BASE).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._app_id and self._secret)

    def _require_creds(self) -> tuple[str, str]:
        if not (self._app_id and self._secret):
            raise PCONotConfigured(
                "Planning Center is not configured: set the PCO_APP_ID and "
                "PCO_SECRET environment variables to a Personal Access Token "
                "(create one at https://api.planningcenteronline.com/oauth/applications)."
            )
        return self._app_id, self._secret

    # --- HTTP layer (mockable) ---------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` (relative to the base URL, or absolute) as JSON.

        Honours 429 ``Retry-After`` up to :data:`MAX_RETRIES` attempts.
        The only method that touches the network — tests monkeypatch it.
        """
        app_id, secret = self._require_creds()
        url = path if path.startswith("http") else f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(auth=(app_id, secret), timeout=30.0) as client:
            for attempt in range(self.MAX_RETRIES):
                resp = await client.get(url, params=params)
                if resp.status_code == 429 and attempt < self.MAX_RETRIES - 1:
                    delay = float(resp.headers.get("Retry-After", 2.0))
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code >= 400:
                    raise PCOError(
                        f"PCO GET {url} failed with {resp.status_code}: {resp.text[:300]}"
                    )
                return resp.json()
        raise PCOError(f"PCO GET {url} exhausted retries")  # pragma: no cover

    async def _get_all_pages(
        self, path: str, params: dict[str, Any] | None = None, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Collect ``data`` entries across JSON:API pages via ``links.next``."""
        out: list[dict[str, Any]] = []
        payload = await self._get(path, params)
        for _ in range(max_pages):
            data = payload.get("data") or []
            if isinstance(data, list):
                out.extend(data)
            else:
                out.append(data)
            next_url = (payload.get("links") or {}).get("next")
            if not next_url:
                break
            payload = await self._get(next_url)
        return out

    # --- API surface --------------------------------------------------------

    async def list_songs(
        self, query: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List songs, optionally filtered by title search."""
        params: dict[str, Any] = {"per_page": min(int(limit), 100)}
        if query:
            params["where[search_title]"] = query
        # max_pages is a safety cap, not the limit — the API may return fewer
        # items per page than per_page asked for; slicing below enforces limit.
        data = await self._get_all_pages("songs", params, max_pages=10)
        out = []
        for res in data[: int(limit)]:
            attrs = res.get("attributes") or {}
            out.append(
                {
                    "song_id": str(res.get("id", "")),
                    "title": attrs.get("title") or "",
                    "author": attrs.get("author"),
                    "ccli_number": attrs.get("ccli_number"),
                }
            )
        return out

    async def list_arrangements(self, song_id: str) -> list[dict[str, Any]]:
        data = await self._get_all_pages(f"songs/{song_id}/arrangements", None)
        out = []
        for res in data:
            attrs = res.get("attributes") or {}
            out.append(
                {
                    "arrangement_id": str(res.get("id", "")),
                    "name": attrs.get("name"),
                    "bpm": attrs.get("bpm"),
                    "meter": attrs.get("meter"),
                    "length_sec": attrs.get("length"),
                    "sequence": list(attrs.get("sequence") or []),
                    "chord_chart": attrs.get("chord_chart"),
                    "has_chord_chart": bool(attrs.get("chord_chart")),
                }
            )
        return out

    async def get_arrangement_sections_raw(
        self, song_id: str, arrangement_id: str
    ) -> dict[str, Any]:
        return await self._get(f"songs/{song_id}/arrangements/{arrangement_id}/sections")

    async def get_keys(self, song_id: str, arrangement_id: str) -> list[dict[str, Any]]:
        data = await self._get_all_pages(
            f"songs/{song_id}/arrangements/{arrangement_id}/keys", None
        )
        out = []
        for res in data:
            attrs = res.get("attributes") or {}
            out.append(
                {
                    "name": attrs.get("name"),
                    "starting_key": attrs.get("starting_key"),
                    "ending_key": attrs.get("ending_key"),
                }
            )
        return out

    async def get_song(self, song_id: str) -> dict[str, Any]:
        payload = await self._get(f"songs/{song_id}")
        res = payload.get("data") or {}
        attrs = res.get("attributes") or {}
        return {
            "song_id": str(res.get("id", song_id)),
            "title": attrs.get("title") or "",
            "author": attrs.get("author"),
            "ccli_number": attrs.get("ccli_number"),
        }

    async def get_chart(
        self, song_id: str, arrangement_id: str | None = None
    ) -> ChartModel:
        """Assemble the normalized :class:`ChartModel` for one arrangement."""
        warnings: list[str] = []
        song = await self.get_song(song_id)

        arrangements = await self.list_arrangements(song_id)
        if not arrangements:
            raise PCOError(f"song {song_id} ({song['title']!r}) has no arrangements")
        if arrangement_id is None:
            arr = arrangements[0]
            if len(arrangements) > 1:
                names = [a.get("name") or a["arrangement_id"] for a in arrangements]
                warnings.append(
                    f"song has {len(arrangements)} arrangements ({names}); using the "
                    f"first — pass arrangement_id to pick another"
                )
        else:
            matches = [a for a in arrangements if a["arrangement_id"] == str(arrangement_id)]
            if not matches:
                raise PCOError(
                    f"arrangement {arrangement_id} not found on song {song_id}; "
                    f"available: {[a['arrangement_id'] for a in arrangements]}"
                )
            arr = matches[0]

        sections_payload = await self.get_arrangement_sections_raw(
            song_id, arr["arrangement_id"]
        )
        sections, schema_notes = _extract_sections(sections_payload)
        warnings.extend(schema_notes)

        if not any(s.lyrics for s in sections) and arr.get("chord_chart"):
            fallback = sections_from_chordpro(str(arr["chord_chart"]))
            if any(s.lyrics for s in fallback):
                warnings.append(
                    "sections endpoint had no lyrics; parsed them from the "
                    "arrangement chord_chart instead"
                )
                sections = fallback

        key: str | None = None
        try:
            keys = await self.get_keys(song_id, arr["arrangement_id"])
        except PCOError as exc:
            warnings.append(f"keys endpoint failed: {exc}")
            keys = []
        if keys:
            key = keys[0].get("starting_key") or keys[0].get("name")

        return ChartModel(
            song_id=song["song_id"],
            arrangement_id=arr["arrangement_id"],
            title=song["title"],
            ccli_number=song.get("ccli_number"),
            key=key,
            bpm=float(arr["bpm"]) if arr.get("bpm") else None,
            meter=arr.get("meter"),
            sequence=tuple(arr.get("sequence") or ()),
            sections=tuple(sections),
            warnings=tuple(warnings),
        )

    # --- Probe ---------------------------------------------------------------

    async def library_probe(
        self, limit: int = 25, query: str | None = None, throttle_sec: float = 0.25
    ) -> dict[str, Any]:
        """Sample the song library and report data coverage.

        Run this FIRST against a real account — it validates every data
        assumption section detection depends on (sequences present?
        sections carry lyrics? bpm/meter set?) and reports the actual
        sections-endpoint attribute keys when the shape differs from what
        the parser expects.
        """
        songs = await self.list_songs(query=query, limit=limit)
        reports: list[ProbeSongReport] = []
        for song in songs:
            rep = ProbeSongReport(song_id=song["song_id"], title=song["title"])
            try:
                arrangements = await self.list_arrangements(song["song_id"])
                if arrangements:
                    arr = arrangements[0]
                    rep.arrangement_id = arr["arrangement_id"]
                    rep.has_sequence = bool(arr["sequence"])
                    rep.sequence_len = len(arr["sequence"])
                    rep.has_bpm = arr.get("bpm") is not None
                    rep.has_meter = bool(arr.get("meter"))
                    rep.has_chord_chart = bool(arr.get("has_chord_chart"))
                    payload = await self.get_arrangement_sections_raw(
                        song["song_id"], arr["arrangement_id"]
                    )
                    sections, notes = _extract_sections(payload)
                    rep.section_count = len(sections)
                    rep.sections_with_lyrics = sum(1 for s in sections if s.lyrics)
                    rep.schema_notes = notes
            except PCOError as exc:
                rep.error = str(exc)
            reports.append(rep)
            if throttle_sec > 0:
                await asyncio.sleep(throttle_sec)

        sampled = len(reports)
        ok = [r for r in reports if r.error is None]

        def _pct(pred: int) -> float:
            return round(100.0 * pred / sampled, 1) if sampled else 0.0

        coverage = {
            "pct_with_sequence": _pct(sum(1 for r in ok if r.has_sequence)),
            "pct_with_section_lyrics": _pct(
                sum(1 for r in ok if r.sections_with_lyrics > 0)
            ),
            "pct_with_bpm": _pct(sum(1 for r in ok if r.has_bpm)),
            "pct_with_meter": _pct(sum(1 for r in ok if r.has_meter)),
            "pct_with_chord_chart": _pct(sum(1 for r in ok if r.has_chord_chart)),
            "errors": sum(1 for r in reports if r.error is not None),
        }
        schema_notes = sorted({note for r in reports for note in r.schema_notes})
        warnings: list[str] = []
        if sampled and coverage["pct_with_section_lyrics"] < 50.0:
            warnings.append(
                "fewer than half the sampled songs have per-section lyrics — "
                "the ChordPro fallback (or SongSelect files) may need to be primary"
            )
        return {
            "sampled": sampled,
            "coverage": coverage,
            "songs": [r.to_dict() for r in reports],
            "schema_notes": schema_notes,
            "warnings": warnings,
        }
