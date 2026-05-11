"""Section detection — find regions where a focal track is "featured."

Layer 1.2 of the mix-aware shaping stack. The motivating use case is
*"the lead doesn't cut through during the solo"* — the system needs to
locate "the solo" (a region on the timeline) before it can analyze
why the lead isn't cutting through there.

Three detection strategies, ordered by cost and increasing robustness:

1. **By clip name** — match each focal-track clip's name against
   keywords like ``"solo"``, ``"lead"``, ``"break"``, ``"bridge"``,
   ``"feature"``. Zero round-trips beyond reading the clip list.
   When it works, it's the highest-confidence signal — the user
   labeled it deliberately.
2. **By clip overlap** — a section is "featured" when the focal track
   has a clip but ≤ N other tracks have overlapping clips. Captures
   common drop-out + solo arrangements where supporting instruments
   pause. Pure metadata, no DSP.
3. **By RMS energy** *(deferred to a follow-up PR)* — bounce each
   active track, compute windowed RMS, find ranges where focal RMS
   exceeds threshold and others sit below. Catches solos where every
   track is still playing but the focal got an automation boost.

This module implements (1) and (2). The data sources are
``arrangement_clips_list`` and ``track_list`` — both already shipped.
No bridge handlers added.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

log = logging.getLogger(__name__)


# Keywords commonly used by producers to label featured sections.
# Match is case-insensitive substring; ``"my lead bit"`` matches.
DEFAULT_LEAD_KEYWORDS: tuple[str, ...] = (
    "solo", "lead", "break", "bridge", "feature", "fill", "interlude",
)


@dataclass(frozen=True)
class Section:
    """One detected region where the focal track is featured.

    Fields:
        track_index: the focal track this section was detected for.
        start_beats: region start (beats, 0-based).
        end_beats: region end (beats, exclusive).
        confidence: ``[0.0, 1.0]`` — 1.0 for unambiguous (named clip),
            graded down for inferred sections (overlap-based).
        kind: which detector found this section. One of
            ``"clip_name"``, ``"clip_overlap"``, ``"audio_rms"``
            (last one reserved for a follow-up).
        details: detector-specific data (the matched clip name, the
            overlap-count threshold used, etc.).
    """

    track_index: int
    start_beats: float
    end_beats: float
    confidence: float
    kind: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def length_beats(self) -> float:
        return self.end_beats - self.start_beats

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_index": self.track_index,
            "start_beats": self.start_beats,
            "end_beats": self.end_beats,
            "length_beats": self.length_beats,
            "confidence": self.confidence,
            "kind": self.kind,
            "details": dict(self.details),
        }


# ---------------------------------------------------------------------------
# Detection strategies (pure-data — take the clips list, return Sections)
# ---------------------------------------------------------------------------


def find_sections_by_clip_name(
    focal_track_index: int,
    focal_clips: Sequence[dict[str, Any]],
    *,
    keywords: Sequence[str] = DEFAULT_LEAD_KEYWORDS,
    min_length_beats: float = 1.0,
) -> list[Section]:
    """Pick clips whose name contains any of ``keywords`` (case-insensitive).

    The match is substring — ``"Lead Solo"`` matches both ``"lead"`` and
    ``"solo"``. We dedupe so a single clip with multiple matches yields
    one section, attributed to the strongest keyword (first match wins).

    Args:
        focal_track_index: track index this section list will be tagged with.
        focal_clips: list of clip dicts as returned by
            ``arrangement_clips_list``, each with ``name``,
            ``start_time_beats``, ``length_beats``.
        keywords: lowercase substrings to look for.
        min_length_beats: skip very short clips (likely fills/transients).
    """
    keywords_lower = tuple(k.lower() for k in keywords)
    sections: list[Section] = []
    for clip in focal_clips:
        name = (clip.get("name") or "").lower()
        if not name:
            continue
        length = float(clip.get("length_beats") or 0.0)
        if length < min_length_beats:
            continue
        matched = next((kw for kw in keywords_lower if kw in name), None)
        if matched is None:
            continue
        start = float(clip.get("start_time_beats") or 0.0)
        sections.append(Section(
            track_index=focal_track_index,
            start_beats=start,
            end_beats=start + length,
            confidence=1.0,  # user labeled it deliberately
            kind="clip_name",
            details={"matched_keyword": matched, "clip_name": clip.get("name")},
        ))
    return sections


def find_sections_by_clip_overlap(
    focal_track_index: int,
    focal_clips: Sequence[dict[str, Any]],
    other_tracks_clips: dict[int, Sequence[dict[str, Any]]],
    *,
    max_overlapping_other_tracks: int = 1,
    min_length_beats: float = 4.0,
) -> list[Section]:
    """Find focal-track clips that have ≤ ``max_overlapping_other_tracks``
    other tracks playing simultaneously — i.e. "alone or near-alone."

    Common arrangement pattern for solos: the focal track keeps playing
    while most of the band drops out. This detector finds those.

    Args:
        focal_track_index: track being analyzed.
        focal_clips: clip list for the focal track.
        other_tracks_clips: ``{track_index: clips_list}`` for every OTHER
            audio-producing track (caller filters out the focal track,
            muted tracks, and tracks with no audio output).
        max_overlapping_other_tracks: a focal clip qualifies if at most
            this many other tracks have an overlapping clip. Default 1
            (allows one accompanist + the focal).
        min_length_beats: skip short focal clips.
    """
    sections: list[Section] = []
    for clip in focal_clips:
        length = float(clip.get("length_beats") or 0.0)
        if length < min_length_beats:
            continue
        start = float(clip.get("start_time_beats") or 0.0)
        end = start + length
        overlapping_tracks: list[int] = []
        for other_ti, other_clips in other_tracks_clips.items():
            for oc in other_clips:
                o_start = float(oc.get("start_time_beats") or 0.0)
                o_end = o_start + float(oc.get("length_beats") or 0.0)
                if o_end > start and o_start < end:  # any intersection
                    overlapping_tracks.append(other_ti)
                    break  # one match per track is enough
        if len(overlapping_tracks) <= max_overlapping_other_tracks:
            # Confidence scales: fewer overlaps = more confident this is
            # a featured section. Zero overlaps → 0.9 (highest for this
            # detector; clip_name remains the only 1.0 source). N
            # overlaps → grade down.
            confidence = 0.9 - 0.1 * len(overlapping_tracks)
            sections.append(Section(
                track_index=focal_track_index,
                start_beats=start,
                end_beats=end,
                confidence=max(0.5, confidence),
                kind="clip_overlap",
                details={
                    "overlapping_track_count": len(overlapping_tracks),
                    "overlapping_tracks": overlapping_tracks,
                    "clip_name": clip.get("name"),
                },
            ))
    return sections


def merge_overlapping_sections(sections: Sequence[Section]) -> list[Section]:
    """Collapse overlapping / adjacent sections into single ranges.

    When two sections cover overlapping beat ranges (e.g. one clip
    named ``"Solo"`` plus an overlap-detected section that contains the
    same clip), we want one merged section per detected region — not
    one per detector.

    Merging rules:

    - Sort by start.
    - Two sections that overlap (``a.end > b.start``) or touch
      (``a.end == b.start``) merge.
    - Merged section's confidence = ``max(a.confidence, b.confidence)``
      so user-labeled wins over inferred.
    - Merged kind = highest-confidence one's; ties go to the earlier.
    - Merged details get a ``merged_from`` list of contributing kinds +
      original ranges.
    """
    if not sections:
        return []
    sorted_secs = sorted(sections, key=lambda s: (s.start_beats, s.end_beats))
    merged: list[Section] = []
    current = sorted_secs[0]
    contributions: list[Section] = [current]

    def _flush() -> None:
        if len(contributions) == 1:
            merged.append(contributions[0])
            return
        best = max(contributions, key=lambda s: s.confidence)
        merged.append(Section(
            track_index=best.track_index,
            start_beats=min(s.start_beats for s in contributions),
            end_beats=max(s.end_beats for s in contributions),
            confidence=best.confidence,
            kind=best.kind,
            details={
                "merged_from": [
                    {"kind": s.kind, "start": s.start_beats,
                     "end": s.end_beats, "confidence": s.confidence,
                     **{k: v for k, v in s.details.items() if k != "merged_from"}}
                    for s in contributions
                ],
            },
        ))

    for nxt in sorted_secs[1:]:
        if nxt.start_beats <= current.end_beats:
            # Overlap or touching — extend current.
            current = Section(
                track_index=current.track_index,
                start_beats=current.start_beats,
                end_beats=max(current.end_beats, nxt.end_beats),
                confidence=current.confidence,  # placeholder; _flush picks the real one
                kind=current.kind,
            )
            contributions.append(nxt)
        else:
            _flush()
            current = nxt
            contributions = [nxt]
    _flush()
    return merged


# ---------------------------------------------------------------------------
# Async orchestrator — reads clip lists, runs detectors, returns sections
# ---------------------------------------------------------------------------


async def find_lead_sections(
    focal_track_index: int,
    *,
    method: str = "auto",
    keywords: Sequence[str] = DEFAULT_LEAD_KEYWORDS,
    min_length_beats: float = 1.0,
    max_overlapping_other_tracks: int = 1,
    osc_client: Any = None,
) -> list[Section]:
    """Find regions where ``focal_track_index`` is featured.

    Args:
        focal_track_index: track to analyze.
        method: ``"auto"`` (default) tries clip-name first, falls back
            to clip-overlap. ``"clip_name"`` / ``"clip_overlap"`` force
            one detector. ``"both"`` returns the union (post-merge).
        keywords: passed to the clip-name detector.
        min_length_beats: passed to both detectors.
        max_overlapping_other_tracks: passed to the overlap detector.
        osc_client: explicit OSC client (for tests). Otherwise pulled
            from ``get_client``.

    Returns:
        A list of :class:`Section` instances. Overlapping detections
        are merged via :func:`merge_overlapping_sections` so each
        returned section is a distinct beat range.
    """
    if method not in ("auto", "clip_name", "clip_overlap", "both"):
        raise ValueError(
            f"unknown method {method!r}; must be 'auto', 'clip_name', "
            f"'clip_overlap', or 'both'"
        )

    if osc_client is None:
        from .osc_client import get_client
        osc_client = await get_client()

    # 1. Read focal-track clips.
    focal_clips = await _read_arrangement_clips(osc_client, focal_track_index)

    # 2. Read other audio-producing tracks' clips (only needed for overlap path).
    other_clips: dict[int, list[dict[str, Any]]] = {}
    if method in ("auto", "clip_overlap", "both"):
        n_tracks = await _read_num_tracks(osc_client)
        for ti in range(n_tracks):
            if ti == focal_track_index:
                continue
            try:
                muted = bool(
                    (await osc_client.request("/live/track/get/mute", ti))[1]
                )
            except Exception:
                muted = False
            if muted:
                continue
            try:
                has_audio = bool(
                    (await osc_client.request("/live/track/get/has_audio_output", ti))[1]
                )
            except Exception:
                has_audio = True
            if not has_audio:
                continue
            other_clips[ti] = await _read_arrangement_clips(osc_client, ti)

    # 3. Run requested detectors.
    sections: list[Section] = []
    if method in ("clip_name", "auto", "both"):
        sections.extend(find_sections_by_clip_name(
            focal_track_index, focal_clips,
            keywords=keywords, min_length_beats=min_length_beats,
        ))
    if method == "clip_overlap" or (method == "auto" and not sections) or method == "both":
        sections.extend(find_sections_by_clip_overlap(
            focal_track_index, focal_clips, other_clips,
            max_overlapping_other_tracks=max_overlapping_other_tracks,
            min_length_beats=max(min_length_beats, 4.0),
        ))

    return merge_overlapping_sections(sections)


async def _read_arrangement_clips(
    osc_client: Any, track_index: int,
) -> list[dict[str, Any]]:
    """Read all arrangement clips on a track. Mirrors what the
    ``arrangement_clips_list`` MCP tool does, but module-level so this
    code doesn't depend on the FastMCP context."""
    names = await osc_client.request("/live/track/get/arrangement_clips/name", int(track_index))
    lengths = await osc_client.request("/live/track/get/arrangement_clips/length", int(track_index))
    starts = await osc_client.request("/live/track/get/arrangement_clips/start_time", int(track_index))

    def _strip(reply: tuple) -> list[Any]:
        # AbletonOSC returns (track_id, val_0, val_1, ...) — drop the leading id.
        return list(reply[1:]) if reply else []

    name_list = _strip(names)
    length_list = _strip(lengths)
    start_list = _strip(starts)
    n = max(len(name_list), len(length_list), len(start_list), 0)
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append({
            "track_index": track_index,
            "arrangement_clip_index": i,
            "name": name_list[i] if i < len(name_list) else None,
            "length_beats": float(length_list[i]) if i < len(length_list) else 0.0,
            "start_time_beats": float(start_list[i]) if i < len(start_list) else 0.0,
        })
    return out


async def _read_num_tracks(osc_client: Any) -> int:
    return int((await osc_client.request("/live/song/get/num_tracks"))[0])
