"""Synthesize Ableton Live Set (.als) files with audio clips pre-loaded.

The whole reason this module exists: Live 11.3.43's LOM
``Track.create_audio_clip(file_path, position)`` is broken for absolute
paths (returns None silently). Live's window doesn't accept
WM_DROPFILES. Live's browser doesn't index new files without UI
interaction. Every "load this wav into arrangement" path requires a
user gesture — UNLESS we control the file format itself.

This module does exactly that: build a valid ``.als`` Live Set with
audio clips pre-loaded, write it to disk, and let Live open it. 100%
deterministic when the XML schema is right — no probing, no race
conditions, no user gestures.

Trade-off: opening the synthesized set in Live replaces the active
session. Fine for batch processing where the synthesized set IS the
session being processed.

How it works
------------

1. Take a **template** ``.als`` file (a Live Set you've saved with at
   least one AudioTrack containing one AudioClip). The template gives
   us the surrounding schema scaffolding (Tracks, Mixer, MasterTrack,
   Returns, etc.) without us having to reverse-engineer all of it.
2. Clone the template's reference AudioTrack once per wav we want to
   load, mutate the clone's fields to point at our wav, then append
   the clones to the document's Tracks container.
3. Strip existing AudioClips from the template tracks (the user
   doesn't want their reference Psalm 10 hitchhiking on every batch).
4. Reserialize as gzipped UTF-8 XML — same format Live writes.

The synthesized ``.als`` opens identically in Live to one the user
manually built by dragging the same wavs in.

Tested against Live 11.3.43 (Suite). The bundled fixture template was
captured from that build. Other major Live versions may have schema
differences that need a fresh template capture — see
``capture_template`` and ``docs/PRACTICE_PACK_WAV_LOAD_DEBUG.md``.
"""

from __future__ import annotations

import copy
import gzip
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


# Default tempo used to convert wav-duration-in-seconds to clip-length-
# in-beats for unwarped (IsWarped=false) clips. Live's CurrentEnd is
# in beats, and unwarped clips play at their native sample rate, so
# the beat length is just duration_sec * tempo / 60. 120 is Live's
# default tempo; this only affects the visual length of the clip on
# the arrangement timeline — playback still uses the wav's true length.
DEFAULT_TEMPO: float = 120.0


@dataclass(frozen=True)
class WavSpec:
    """One wav to load. ``path`` is required, everything else has sensible defaults."""

    path: str
    track_name: str | None = None
    position_beats: float = 0.0
    """Where on the arrangement timeline (in beats) to place this clip."""


@dataclass(frozen=True)
class SynthesisResult:
    """What ``synthesize_als`` returned."""

    output_path: str
    tracks_added: int
    template_path: str
    """The template that was used as schema reference."""


class AlsSynthesisError(RuntimeError):
    """Raised when synthesis can't proceed (bad template, missing wav, etc)."""


def _load_als_xml(als_path: str) -> ET.Element:
    """Decompress + parse an ``.als`` file into an ElementTree root."""
    if not os.path.exists(als_path):
        raise AlsSynthesisError(f"als file not found: {als_path}")
    with gzip.open(als_path, "rb") as f:
        xml_bytes = f.read()
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AlsSynthesisError(
            f"failed to parse {als_path} as XML: {exc}"
        ) from exc


def _find_reference_track(root: ET.Element) -> ET.Element:
    """Find the first ``<AudioTrack>`` whose arrangement has at least one
    AudioClip. We need a populated track as a reference because the empty
    audio track in Live's default scaffold doesn't have a populated
    ArrangerAutomation we can clone from."""
    tracks_container = root.find(".//LiveSet/Tracks")
    if tracks_container is None:
        raise AlsSynthesisError(
            "template missing <LiveSet><Tracks> — not a valid Live Set"
        )
    for at in tracks_container.findall("AudioTrack"):
        events = at.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        if events is not None and events.find("AudioClip") is not None:
            return at
    raise AlsSynthesisError(
        "template must contain at least one AudioTrack with an AudioClip — "
        "Live needs a populated example to clone from. Drag any wav onto a "
        "fresh Set and save it as the template."
    )


def _collect_used_ids(root: ET.Element) -> set[int]:
    """Walk the document and gather every numeric ``Id="..."`` attribute
    so we can generate fresh non-colliding IDs for our new tracks."""
    used = set()
    for elem in root.iter():
        v = elem.get("Id")
        if v and v.lstrip("-").isdigit():
            used.add(int(v))
    return used


# IDs at or above this threshold are document-global references
# (AutomationTarget, ModulationTarget, Pointee, etc) and MUST be unique
# across the whole .als file. IDs below this are positional/local —
# track-internal indices for clip slots, sends, warp markers, etc — and
# preserving them across cloned tracks is correct (each track has its
# own ClipSlot 0, 1, 2... 7, independent of other tracks).
#
# Live writes high-IDs starting around 8630 in the templates we've
# captured. We pick 100 as the threshold conservatively — well above any
# realistic positional index, well below any observed global ID.
_GLOBAL_ID_THRESHOLD: int = 100


def _renumber_global_ids_in_clone(
    clone: ET.Element,
    next_id_callable,
) -> dict[int, int]:
    """Walk a cloned subtree, find every ``Id="N"`` where N >= the
    global threshold, allocate a fresh document-unique replacement, and
    rewrite the attribute in place. Returns the old → new mapping so
    the caller can update references that point AT these IDs (Pointees
    et al)."""
    mapping: dict[int, int] = {}
    for elem in clone.iter():
        v = elem.get("Id")
        if not v or not v.lstrip("-").isdigit():
            continue
        old = int(v)
        if old < _GLOBAL_ID_THRESHOLD:
            continue
        if old not in mapping:
            mapping[old] = next_id_callable()
        elem.set("Id", str(mapping[old]))
    return mapping


def _rewrite_id_references_in_clone(
    clone: ET.Element,
    id_mapping: dict[int, int],
) -> None:
    """Walk the cloned subtree and rewrite any ``Value="N"`` where N is
    a key in ``id_mapping`` — those are reference-to-ID attributes
    (Pointee Value, automation Targets pointing back at automation lanes,
    etc). We don't have Live's schema so we can't know which Value
    attributes are reference targets — match every numeric Value against
    the mapping and only rewrite when it hits.

    A bit aggressive, but safe: the global ID range is well above any
    common numeric "value" (gains, frequencies, etc — which are floats
    or small ints), so false matches are extremely rare in practice."""
    if not id_mapping:
        return
    for elem in clone.iter():
        v = elem.get("Value")
        if not v or not v.lstrip("-").isdigit():
            continue
        old = int(v)
        if old in id_mapping:
            elem.set("Value", str(id_mapping[old]))


def _strip_audio_clips_from_track(track: ET.Element) -> None:
    """Remove every ``<AudioClip>`` from an AudioTrack's arrangement."""
    events = track.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    if events is None:
        return
    for clip in list(events.findall("AudioClip")):
        events.remove(clip)


def _normalize_path_for_live(p: str) -> str:
    """Live writes forward slashes even on Windows; match that."""
    return str(Path(p).resolve()).replace("\\", "/")


def _wav_duration_sec(path: str) -> float:
    """Read wav duration via soundfile (already a project dep)."""
    import soundfile as sf
    info = sf.info(path)
    return float(info.duration)


def _set_value(parent: ET.Element, tag: str, value: str) -> None:
    """If ``<tag Value="...">`` exists under parent, set its Value attr.
    No-op when the child doesn't exist (some Live versions omit fields)."""
    el = parent.find(tag)
    if el is not None:
        el.set("Value", value)


def _set_track_name(track: ET.Element, name: str) -> None:
    """Set both EffectiveName and UserName on an AudioTrack."""
    for path in ("Name/EffectiveName", "Name/UserName"):
        el = track.find(path)
        if el is not None:
            el.set("Value", name)


def _populate_audio_clip(
    clip: ET.Element,
    *,
    wav_path: str,
    clip_name: str,
    current_start: float,
    current_end: float,
    file_size: int,
) -> None:
    """Mutate an ``<AudioClip>`` clone in-place to point at our wav."""
    clip.set("Id", "0")
    clip.set("Time", str(current_start))

    _set_value(clip, "CurrentStart", str(current_start))
    _set_value(clip, "CurrentEnd", str(current_end))
    _set_value(clip, "Name", clip_name)
    # Force unwarped — keeps playback at native rate regardless of tempo.
    _set_value(clip, "IsWarped", "false")

    file_ref = clip.find("SampleRef/FileRef")
    if file_ref is None:
        raise AlsSynthesisError("reference clip missing SampleRef/FileRef")

    # RelativePathType=0 means "absolute path" in Live's schema.
    _set_value(file_ref, "RelativePathType", "0")
    _set_value(file_ref, "RelativePath", "")
    _set_value(file_ref, "Path", wav_path)
    _set_value(file_ref, "Type", "1")
    _set_value(file_ref, "LivePackName", "")
    _set_value(file_ref, "LivePackId", "")
    _set_value(file_ref, "OriginalFileSize", str(file_size))
    # Clear the CRC; Live recomputes/checks on load.
    crc = file_ref.find("OriginalCrc")
    if crc is not None:
        file_ref.remove(crc)


def synthesize_als(
    wavs: Sequence[WavSpec | str],
    *,
    template_als_path: str,
    output_als_path: str,
    tempo: float = DEFAULT_TEMPO,
    strip_existing_clips: bool = True,
) -> SynthesisResult:
    """Build a Live Set (.als) with audio clips loaded from ``wavs``.

    Args:
        wavs: list of :class:`WavSpec` or absolute path strings. One wav
            per output AudioTrack — Live can load multiple clips on one
            track but downstream batch workflows want isolated tracks.
        template_als_path: path to a saved Live Set with at least one
            populated AudioTrack. Used as schema reference.
        output_als_path: where to write the synthesized ``.als``. Parent
            dirs auto-created.
        tempo: BPM (default 120, Live's default). Only affects the
            visual clip length on the timeline since clips are
            unwarped — playback duration is the wav's real duration.
        strip_existing_clips: when True (default), remove all AudioClips
            already in the template before adding ours. Set False to
            augment instead of replace.

    Returns:
        :class:`SynthesisResult` with the output path and a few stats.

    Raises:
        AlsSynthesisError: invalid template, missing wav, parse failure.
    """
    if not wavs:
        raise AlsSynthesisError("wavs list is empty")

    # Normalize input — accept bare paths or WavSpec.
    specs: list[WavSpec] = []
    for w in wavs:
        if isinstance(w, str):
            specs.append(WavSpec(path=w))
        else:
            specs.append(w)

    # Validate every wav exists up front (fail loudly before partial work).
    for s in specs:
        if not os.path.exists(s.path):
            raise AlsSynthesisError(f"wav not found: {s.path}")

    # Load template + locate reference scaffolding.
    root = _load_als_xml(template_als_path)
    tracks_container = root.find(".//LiveSet/Tracks")
    if tracks_container is None:
        raise AlsSynthesisError("template missing <LiveSet><Tracks>")
    ref_track = _find_reference_track(root)

    # SNAPSHOT the reference clip BEFORE stripping — we need a clean
    # copy to clone N times. ET elements are mutable in-place, so once
    # we strip we'd lose the reference.
    ref_clip_snapshot = copy.deepcopy(
        ref_track.find(
            "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
        )
    )
    if ref_clip_snapshot is None:
        raise AlsSynthesisError("reference track unexpectedly has no AudioClip")

    # Optionally strip every existing clip — replace not augment.
    if strip_existing_clips:
        for at in tracks_container.findall("AudioTrack"):
            _strip_audio_clips_from_track(at)

    used_ids = _collect_used_ids(root)
    next_id = (max(used_ids) + 1) if used_ids else 1000

    for spec in specs:
        wav_abs = _normalize_path_for_live(spec.path)
        wav_basename = Path(spec.path).stem
        track_name = spec.track_name or wav_basename
        duration_sec = _wav_duration_sec(spec.path)
        length_beats = duration_sec * tempo / 60.0
        position_beats = float(spec.position_beats)
        end_beats = position_beats + length_beats
        file_size = os.path.getsize(spec.path)

        new_track = copy.deepcopy(ref_track)
        new_track.set("Id", str(next_id))
        next_id += 1
        _set_track_name(new_track, track_name)

        # Renumber every document-global ID in the clone (Pointee,
        # AutomationTarget, ModulationTarget, ...) so the new track
        # doesn't collide with the original or with other clones. Then
        # rewrite any references that pointed AT those IDs.
        def _next() -> int:
            nonlocal next_id
            v = next_id
            next_id += 1
            return v

        id_mapping = _renumber_global_ids_in_clone(new_track, _next)
        _rewrite_id_references_in_clone(new_track, id_mapping)

        # Live requires Return tracks to come LAST in the Tracks
        # container. If we append at the end of the list, our new
        # AudioTrack lands AFTER the Return tracks and Live rejects the
        # file with "Return tracks out of order" on load. Insert BEFORE
        # the first ReturnTrack instead. We re-look-up the insert
        # position on every iteration so multiple appends stay grouped
        # together correctly.
        insert_index = len(list(tracks_container))
        for idx, child in enumerate(list(tracks_container)):
            if child.tag == "ReturnTrack":
                insert_index = idx
                break
        tracks_container.insert(insert_index, new_track)

        # Drop any clips that came along in the clone, then add ours.
        new_events = new_track.find(
            "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events"
        )
        if new_events is None:
            raise AlsSynthesisError(
                "cloned track missing ArrangerAutomation/Events — "
                "template schema unexpected"
            )
        for c in list(new_events.findall("AudioClip")):
            new_events.remove(c)

        new_clip = copy.deepcopy(ref_clip_snapshot)
        _populate_audio_clip(
            new_clip,
            wav_path=wav_abs,
            clip_name=wav_basename,
            current_start=position_beats,
            current_end=end_beats,
            file_size=file_size,
        )
        new_events.append(new_clip)
        # NOTE: the append-track-to-Tracks step happened earlier (above),
        # using `tracks_container.insert(insert_index, ...)` to keep
        # Return tracks last as Live's loader requires.

    # Bump the document-global ID counter. Live validates this field on
    # load: every <NextPointeeId Value="N"/> field must be strictly
    # greater than every Pointee Id actually used. Without this, opening
    # the file fails with "NextPointeeId is too low: N must be bigger
    # than M". The check applies to any field of the form Next<Foo>Id —
    # we update all of them rather than special-casing NextPointeeId.
    for elem in root.iter():
        if elem.tag.startswith("Next") and elem.tag.endswith("Id"):
            current = elem.get("Value", "0")
            if current.lstrip("-").isdigit() and int(current) < next_id:
                elem.set("Value", str(next_id + 1))

    # Serialize as gzipped UTF-8 XML — same wire format Live writes.
    Path(output_als_path).parent.mkdir(parents=True, exist_ok=True)
    xml_bytes = ET.tostring(
        root, encoding="utf-8", xml_declaration=True,
    )
    # Live writes \r\n line endings; ET produces no extra whitespace. The
    # parser accepts either, but mirror Live's style for closer parity.
    with gzip.open(output_als_path, "wb") as f:
        f.write(xml_bytes)

    return SynthesisResult(
        output_path=str(Path(output_als_path).resolve()),
        tracks_added=len(specs),
        template_path=str(Path(template_als_path).resolve()),
    )
