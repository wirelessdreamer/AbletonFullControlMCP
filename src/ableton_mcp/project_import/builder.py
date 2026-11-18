"""Assemble a clean Live 11 set using an installed or supplied schema template.

No factory files or user sessions are bundled with the distribution.
"""

from __future__ import annotations

import copy
import gzip
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..local_automation.als_synth import _collect_used_ids, _load_als_xml
from .assets import ImportError
from .timing import TempoMap
from .templates import selected_template


def clear_track_content(track: ET.Element) -> None:
    for path in ("AutomationEnvelopes/Envelopes", "DeviceChain/Devices", "TakeLanes/TakeLanes"):
        element = track.find(path)
        if element is not None:
            element.clear()
    devices = device_container(track)
    if devices is not None:
        devices.clear()
    for parent in track.iter():
        for child in list(parent):
            if child.tag in {"AudioClip", "MidiClip"}:
                parent.remove(child)


def set_value(root: ET.Element, path: str, value: Any) -> ET.Element:
    node = root
    for tag in path.split("/"):
        child = node.find(tag)
        if child is None:
            child = ET.SubElement(node, tag)
        node = child
    node.set("Value", str(value).lower() if isinstance(value, bool) else str(value))
    return node


def remap_global_ids(root: ET.Element, allocate) -> None:
    """Allocate references by schema role, including preset Id=0 placeholders.

    Presets contain many distinct targets whose Id is zero. They each need a
    fresh ID; mapping all zeroes to one value would corrupt the Live set.
    Parameter values, local clip IDs and note IDs are never rewritten.
    """
    mapping = {}
    for node in root.iter():
        if is_global_target(node):
            old = node.get("Id")
            value = allocate()
            node.set("Id", str(value))
            if old and old != "0":
                mapping[old] = str(value)
    for node in root.iter():
        if node.tag.endswith("Id") and node.get("Value") in mapping:
            node.set("Value", mapping[node.get("Value")])


def is_global_target(node: ET.Element) -> bool:
    return (node.tag == "Pointee" or node.tag.endswith(("AutomationTarget", "ModulationTarget"))
            or node.tag.startswith("ControllerTargets."))


def device_container(track: ET.Element) -> ET.Element | None:
    devices = track.find("DeviceChain/DeviceChain/Devices")
    if devices is None:
        devices = track.find("DeviceChain/Devices")
    return devices


def installed_resources() -> list[Path]:
    paths: list[Path] = []
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Ableton"
        paths.extend(sorted(base.glob("Live 11*/Resources"), reverse=True))
    else:
        paths.extend(sorted(Path("/Applications").glob("Ableton Live 11*.app/Contents/App-Resources")))
    return paths


def find_template(requested: str | None) -> Path:
    configured = requested or os.environ.get("ABLETON_IMPORT_TEMPLATE")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ImportError(f"Template not found: {path}")
        return path
    selected = selected_template()
    if selected:
        return Path(selected["path"])
    candidates = []
    for resource in installed_resources():
        paths = [resource / "Builtin/Templates/DefaultLiveSet.als",
                 *sorted((resource / "Core Library/Templates").glob("*.als"))]
        for path in paths:
            if not path.is_file():
                continue
            root = _load_als_xml(str(path))
            tracks = root.find("LiveSet/Tracks")
            # The shipped default can lag behind the installed release. Choose
            # a complete newer schema, preserving its genuine format metadata.
            if (root.get("MinorVersion", "").startswith("11.") and tracks is not None
                    and tracks.find("AudioTrack") is not None and tracks.find("MidiTrack") is not None):
                schema = tuple(int(n) for n in re.findall(r"\d+", root.get("MinorVersion", "")))
                candidates.append((schema, path))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    raise ImportError("Supply template_als_path or set ABLETON_IMPORT_TEMPLATE to a Live 11 set")


def base_clip(kind: str) -> ET.Element:
    clip = ET.Element(kind, Id="0", Time="0")
    defaults = {
        "LomId": 0, "LomIdView": 0, "CurrentStart": 0, "CurrentEnd": 4,
        "Loop/LoopStart": 0, "Loop/LoopEnd": 4, "Loop/StartRelative": 0,
        "Loop/LoopOn": False, "Loop/OutMarker": 4, "Loop/HiddenLoopStart": 0,
        "Loop/HiddenLoopEnd": 4, "Name": "", "Annotation": "", "Color": 16,
        "LaunchMode": 0, "LaunchQuantisation": 0, "Legato": False, "Ram": False,
        "GrooveSettings/GrooveId": -1, "Disabled": False, "VelocityAmount": 0,
        "FollowAction/FollowTime": 4, "FollowAction/IsLinked": True,
        "FollowAction/LoopIterations": 1, "FollowAction/FollowActionA": 4,
        "FollowAction/FollowActionB": 0, "FollowAction/FollowChanceA": 100,
        "FollowAction/FollowChanceB": 0, "FollowAction/JumpIndexA": 1,
        "FollowAction/JumpIndexB": 1, "FollowAction/FollowActionEnabled": False,
        "Grid/FixedNumerator": 1, "Grid/FixedDenominator": 16,
        "Grid/GridIntervalPixel": 20, "Grid/Ntoles": 2, "Grid/SnapToGrid": True,
        "Grid/Fixed": False, "FreezeStart": 0, "FreezeEnd": 0,
        "IsWarped": kind == "MidiClip", "TakeId": 1,
        "ScrollerTimePreserver/LeftTime": 0, "ScrollerTimePreserver/RightTime": 0,
        "TimeSelection/AnchorTime": 0, "TimeSelection/OtherTime": 0,
    }
    for path, value in defaults.items():
        set_value(clip, path, value)
    ET.SubElement(ET.SubElement(clip, "Envelopes"), "Envelopes")
    signature = ET.SubElement(ET.SubElement(ET.SubElement(clip, "TimeSignature"),
                                           "TimeSignatures"), "RemoteableTimeSignature", Id="0")
    for key, value in (("Numerator", 4), ("Denominator", 4), ("Time", 0)):
        set_value(signature, key, value)
    return clip


def audio_clip(asset: dict, tempo: TempoMap, sample_gain: float = 1.0) -> ET.Element:
    clip = base_clip("AudioClip")
    start = tempo.seconds_to_beat(asset["offset_sec"])
    end = tempo.seconds_to_beat(asset["offset_sec"] + asset["duration_sec"])
    clip.set("Time", str(start))
    for path, value in {
        "CurrentStart": start, "CurrentEnd": end, "Name": asset["name"],
        # Native-time audio loop/sample markers are seconds; arrangement is beats.
        "Loop/LoopEnd": asset["duration_sec"], "Loop/OutMarker": asset["duration_sec"],
        "Loop/HiddenLoopEnd": asset["duration_sec"], "WarpMode": 0,
        "GranularityTones": 30, "GranularityTexture": 65, "FluctuationTexture": 25,
        "TransientResolution": 6, "TransientLoopMode": 2, "TransientEnvelope": 100,
        "ComplexProFormants": 100, "ComplexProEnvelope": 128,
        "Sync": True, "HiQ": True, "Fade": False, "PitchCoarse": 0, "PitchFine": 0,
        "SampleVolume": sample_gain, "MarkersGenerated": True, "IsSongTempoMaster": False,
        "SampleRef/FileRef/RelativePathType": 1,
        "SampleRef/FileRef/RelativePath": asset["path"],
        "SampleRef/FileRef/Path": "", "SampleRef/FileRef/Type": 1,
        "SampleRef/FileRef/LivePackName": "", "SampleRef/FileRef/LivePackId": "",
        "SampleRef/FileRef/OriginalFileSize": asset["size_bytes"],
        "SampleRef/LastModDate": 0, "SampleRef/SampleUsageHint": 0,
        "SampleRef/DefaultDuration": asset["frames"],
        "SampleRef/DefaultSampleRate": asset["sample_rate"],
        "Onsets/HasUserOnsets": False, "Fades/FadeInLength": 0, "Fades/FadeOutLength": 0,
        "Fades/ClipFadesAreInitialized": True, "Fades/CrossfadeInState": 0,
        "Fades/FadeInCurveSkew": 0, "Fades/FadeInCurveSlope": 0,
        "Fades/FadeOutCurveSkew": 0, "Fades/FadeOutCurveSlope": 0,
        "Fades/IsDefaultFadeIn": False, "Fades/IsDefaultFadeOut": False,
    }.items():
        set_value(clip, path, value)
    ET.SubElement(clip.find("SampleRef"), "SourceContext")
    ET.SubElement(clip.find("Onsets"), "UserOnsets")
    markers = ET.SubElement(clip, "WarpMarkers")
    ET.SubElement(markers, "WarpMarker", Id="0", SecTime="0", BeatTime="0")
    ET.SubElement(markers, "WarpMarker", Id="1", SecTime="1", BeatTime="2")
    ET.SubElement(clip, "SavedWarpMarkersForStretched")
    return clip


def midi_clip(part: dict, track: ET.Element, end: float) -> ET.Element:
    clip = base_clip("MidiClip")
    for path in ("CurrentEnd", "Loop/LoopEnd", "Loop/OutMarker", "Loop/HiddenLoopEnd"):
        set_value(clip, path, end)
    set_value(clip, "Name", part["name"])
    for path, value in {"BankSelectCoarse": -1, "BankSelectFine": -1,
                        "ProgramChange": -1, "IsInKey": False,
                        "NoteSpellingPreference": 0, "PreferFlatRootNote": False}.items():
        set_value(clip, path, value)
    notes = ET.SubElement(clip, "Notes")
    keys = ET.SubElement(notes, "KeyTracks")
    for pitch in sorted({n["pitch"] for n in part["notes"]}):
        key = ET.SubElement(keys, "KeyTrack", Id=str(pitch))
        events = ET.SubElement(key, "Notes")
        for i, note in enumerate(part["notes"]):
            if note["pitch"] == pitch:
                ET.SubElement(events, "MidiNoteEvent", Time=str(note["start"]),
                              Duration=str(note["duration"]), Velocity=str(note["velocity"]),
                              VelocityDeviation="0", OffVelocity=str(note["off_velocity"]),
                              Probability="1", IsEnabled="true", NoteId=str(i + 1))
        set_value(key, "MidiKey", pitch)
    ET.SubElement(notes, "PerNoteEventStore")
    set_value(notes, "NoteIdGenerator/NextId", len(part["notes"]) + 1)
    envelopes = clip.find("Envelopes/Envelopes")
    groups: dict[int, list[dict]] = {}
    for controller in part["controllers"]:
        groups.setdefault(controller["control"] + 2, []).append(controller)
    if part["pitch_bends"]:
        groups[0] = part["pitch_bends"]
    for index, events in groups.items():
        target = track.find(f"DeviceChain/MainSequencer/MidiControllers/ControllerTargets.{index}")
        if target is None:
            raise ImportError(f"Template lacks MIDI controller target {index}")
        envelope = ET.SubElement(envelopes, "ClipEnvelope", Id=str(len(envelopes)))
        set_value(envelope, "EnvelopeTarget/PointeeId", target.get("Id"))
        automation = ET.SubElement(envelope, "Automation")
        container = ET.SubElement(automation, "Events")
        default = 8192 if index == 0 else 0
        ET.SubElement(container, "FloatEvent", Id="0", Time="-63072000", Value=str(default))
        for i, event in enumerate(events):
            # Duplicate at each change to retain a stepped controller instead of ramps.
            ET.SubElement(container, "FloatEvent", Id=str(2 * i + 1),
                          Time=str(event["time"]), Value=str(default))
            ET.SubElement(container, "FloatEvent", Id=str(2 * i + 2),
                          Time=str(event["time"]), Value=str(event["value"]))
            default = event["value"]
    return clip


def build_set(project: Path, assets: list[dict], parts: list[dict], tempo: TempoMap,
              meter: tuple[int, int], gain: float, template_path: str | None) -> dict[str, Any]:
    template = find_template(template_path)
    root = _load_als_xml(str(template))
    if not root.get("MinorVersion", "").startswith("11."):
        raise ImportError("Only Live 11 template schemas are currently supported")
    live = root.find("LiveSet")
    tracks = live.find("Tracks") if live is not None else None
    if tracks is None or tracks.find("AudioTrack") is None:
        raise ImportError("Template needs an AudioTrack schema")
    audio_reference = copy.deepcopy(tracks.find("AudioTrack"))
    midi_reference = copy.deepcopy(tracks.find("MidiTrack"))
    clear_track_content(audio_reference)
    if midi_reference is not None:
        clear_track_content(midi_reference)
    ids = _collect_used_ids(root)
    next_id = max(ids, default=1000) + 1

    def allocate() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    def clone(reference: ET.Element, name: str, muted: bool) -> ET.Element:
        track = copy.deepcopy(reference)
        track.set("Id", str(allocate()))
        remap_global_ids(track, allocate)
        for path, value in {
            "Name/EffectiveName": name, "Name/UserName": name, "Color": 16 if not muted else 13,
            "TrackGroupId": -1, "Freeze": False, "SavedPlayingSlot": -1,
            "TrackDelay/Value": 0, "DeviceChain/Mixer/On/Manual": True,
            "DeviceChain/Mixer/Speaker/Manual": not muted,
            "DeviceChain/Mixer/SoloSink": False, "DeviceChain/Mixer/Pan/Manual": 0,
            # XML stores linear amplitude; the API exposes a nonlinear fader.
            # XML 1.0 = 0 dB = API value ~0.85. Keep attenuation on SampleVolume.
            "DeviceChain/Mixer/Volume/Manual": 1.0 if not muted else 0.25,
            "DeviceChain/MainSequencer/MonitoringEnum": 2,
            "DeviceChain/MainSequencer/Recorder/IsArmed": False,
            "DeviceChain/AudioOutputRouting/Target": "AudioOut/Master",
            "DeviceChain/AudioOutputRouting/UpperDisplayString": "Master",
            "DeviceChain/AudioOutputRouting/LowerDisplayString": "",
        }.items():
            set_value(track, path, value)
        sends = track.find("DeviceChain/Mixer/Sends")
        if sends is not None:
            sends.clear()
        tracks.append(track)
        return track

    # All routing is to Master; no template return effects or sends are inherited.
    for track in list(tracks):
        tracks.remove(track)
    rows: list[dict[str, Any]] = []
    for asset in assets:
        if asset["role"] == "midi":
            continue
        muted = asset["role"] != "stem"
        prefix = "Reference: " if asset["role"] == "reference" else "Review: " if muted else ""
        track = clone(audio_reference, prefix + asset["name"], muted)
        events = track.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
        if events is None:
            raise ImportError("Template lacks audio Arrangement event container")
        sample_gain = gain if asset["role"] == "stem" else 1.0
        events.append(audio_clip(asset, tempo, sample_gain))
        rows.append({"track_index": len(rows), "name": track.find("Name/UserName").get("Value"),
                     "role": asset["role"], "path": asset["path"], "muted": muted,
                     "sample_gain": sample_gain,
                     "start_beats": tempo.seconds_to_beat(asset["offset_sec"]),
                     "end_beats": tempo.seconds_to_beat(asset["offset_sec"] + asset["duration_sec"])})

    preset = None
    for resource in installed_resources():
        candidate = resource / "Core Library/Devices/Instruments/Operator/Piano & Keys/FM Piano Simple.adv"
        if candidate.is_file():
            preset = candidate
            break
    for part in parts:
        if midi_reference is None:
            raise ImportError("Template lacks a MidiTrack schema")
        track = clone(midi_reference, part["name"] + " MIDI", True)
        devices = device_container(track)
        preview_loaded = False
        if devices is not None and preset is not None:
            instrument = copy.deepcopy(_load_als_xml(str(preset)).find("Operator"))
            instrument.set("Id", "0")
            for node in instrument.iter():
                for child in list(node):
                    if child.tag in {"SourceContext", "LastPresetRef"}:
                        node.remove(child)
            remap_global_ids(instrument, allocate)
            devices.append(instrument)
            preview_loaded = True
        end = max([n["start"] + n["duration"] for n in part["notes"]]
                  + [e["time"] + 1e-5 for e in part["controllers"] + part["pitch_bends"]])
        events = track.find("DeviceChain/MainSequencer/ClipTimeable/ArrangerAutomation/Events")
        if events is None:
            raise ImportError("Template lacks MIDI Arrangement event container")
        events.append(midi_clip(part, track, end))
        rows.append({"track_index": len(rows), "name": part["name"] + " MIDI", "role": "midi",
                     "muted": True, "start_beats": 0, "end_beats": end,
                     "notes": len(part["notes"]), "preview_instrument": "Operator" if preview_loaded else None,
                     "source_path": part.get("source_path"), "paired_audio": part.get("paired_audio"),
                     "midi_channel": part["channel"], "source_program": part["program"],
                     "note_data": part["notes"], "controller_data": part["controllers"],
                     "pitch_bend_data": part["pitch_bends"]})

    # Keep paired MIDI adjacent to its audio and update stable manifest indices.
    track_elements = list(tracks)
    order = []
    for index, row in enumerate(rows):
        if row["role"] != "midi":
            order.append(index)
            order.extend(i for i, other in enumerate(rows)
                         if other["role"] == "midi" and other.get("paired_audio") == row.get("path"))
    order.extend(i for i in range(len(rows)) if i not in order)
    tracks.clear()
    rows = [rows[i] for i in order]
    for new_index, old_index in enumerate(order):
        tracks.append(track_elements[old_index])
        rows[new_index]["track_index"] = new_index
        rows[new_index]["gain"] = 1.0 if rows[new_index]["role"] == "stem" else 0.25
        for signature in track_elements[old_index].findall(".//RemoteableTimeSignature"):
            set_value(signature, "Numerator", meter[0])
            set_value(signature, "Denominator", meter[1])
    sends_pre = live.find("SendsPre")
    if sends_pre is not None:
        sends_pre.clear()

    master = live.find("MasterTrack")
    if master is None:
        raise ImportError("Template lacks MasterTrack")
    for path, value in {"DeviceChain/Mixer/Tempo/Manual": tempo.points[0][1],
                        "DeviceChain/Mixer/Volume/Manual": 1.0,
                        "DeviceChain/Mixer/On/Manual": True,
                        "DeviceChain/Mixer/Speaker/Manual": True,
                        "DeviceChain/Mixer/SoloSink": False,
                        "DeviceChain/Mixer/TimeSignature/Manual":
                            int(math.log2(meter[1])) * 99 + meter[0] - 1}.items():
        set_value(master, path, value)
    devices = device_container(master)
    if devices is not None:
        devices.clear()
    envelope_container = master.find("AutomationEnvelopes/Envelopes")
    if envelope_container is None:
        envelope_container = ET.SubElement(ET.SubElement(master, "AutomationEnvelopes"), "Envelopes")
    envelope_container.clear()
    for parameter, points, event_tag in (
        ("Tempo", tempo.points, "FloatEvent"),
        ("TimeSignature", [(0, int(math.log2(meter[1])) * 99 + meter[0] - 1)], "EnumEvent"),
    ):
        target = master.find(f"DeviceChain/Mixer/{parameter}/AutomationTarget")
        if target is None:
            raise ImportError(f"Template lacks {parameter} automation target")
        envelope = ET.SubElement(envelope_container, "AutomationEnvelope", Id=str(len(envelope_container)))
        set_value(envelope, "EnvelopeTarget/PointeeId", target.get("Id"))
        events = ET.SubElement(ET.SubElement(envelope, "Automation"), "Events")
        ET.SubElement(events, event_tag, Id="0", Time="-63072000", Value=str(points[0][1]))
        previous = points[0][1]
        for i, (beat, value) in enumerate(points[1:], 1):
            ET.SubElement(events, event_tag, Id=str(i * 2 - 1), Time=str(beat), Value=str(previous))
            ET.SubElement(events, event_tag, Id=str(i * 2), Time=str(beat), Value=str(value))
            previous = value
    for path, value in {
        "Transport/CurrentTime": 0, "Transport/LoopOn": False, "Transport/LoopStart": 0,
        "Transport/LoopLength": max(r["end_beats"] for r in rows),
        "Transport/PunchIn": False, "Transport/PunchOut": False,
        "ArrangementOverdub": False, "GlobalQuantisation": 0, "MidiPrelisten": False,
        "ChooserBar": 0, "ViewStateDetailIsSample": True,
    }.items():
        set_value(live, path, value)
    # Remove template cue points, external file contexts, automation, frozen clips and session state.
    for parent in root.iter():
        for node in list(parent):
            if node.tag in {"CuePoint", "SourceContext", "LastPresetRef"}:
                parent.remove(node)
    for node in root.iter():
        if node.tag.startswith("Next") and node.tag.endswith("Id"):
            node.set("Value", str(max(int(node.get("Value", "0")), next_id + 1)))
    global_ids = [node.get("Id") for node in root.iter() if is_global_target(node)]
    nonzero = [value for value in global_ids if value not in {None, "0"}]
    if len(nonzero) != len(set(nonzero)):
        raise ImportError("Template introduced duplicate document-global references")
    output = project / "Song.als"
    with gzip.open(output, "wb") as stream:
        stream.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    protection = live.find("OverwriteProtectionNumber")
    return {"set_path": "Song.als", "template": str(template),
            "template_creator": root.get("Creator"),
            "template_format": {**root.attrib, "OverwriteProtectionNumber":
                                protection.get("Value") if protection is not None else None},
            "tracks": rows}
