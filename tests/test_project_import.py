"""Project import regressions: real files, timing, hostile archives and honest status."""

from __future__ import annotations

import gzip
import io
import json
import math
import xml.etree.ElementTree as ET
import zipfile
from email.message import Message
from pathlib import Path

import mido
import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.project_import.assets import ImportError, extract_zip
from ableton_mcp.project_import.pipeline import prepare_project
from ableton_mcp.project_import.timing import TempoMap, read_midi
from ableton_mcp.tools.midi_files import _midi_to_clip_notes


@pytest.fixture
def template(tmp_path):
    from tests.test_als_synth import _minimal_template_xml
    root = ET.fromstring(_minimal_template_xml())
    root.set("Creator", "Ableton Live 11.3.43")
    live = root.find("LiveSet")
    ET.SubElement(live, "OverwriteProtectionNumber", Value="2819")
    master = ET.SubElement(live, "MasterTrack")
    mixer = ET.SubElement(ET.SubElement(master, "DeviceChain"), "Mixer")
    for name, ident in (("Tempo", "10"), ("TimeSignature", "11")):
        ET.SubElement(ET.SubElement(mixer, name), "AutomationTarget", Id=ident)
    tracks = live.find("Tracks")
    track = ET.SubElement(tracks, "MidiTrack", Id="200")
    chain = ET.SubElement(track, "DeviceChain")
    ET.SubElement(ET.SubElement(chain, "DeviceChain"), "Devices")
    seq = ET.SubElement(chain, "MainSequencer")
    ET.SubElement(ET.SubElement(ET.SubElement(seq, "ClipTimeable"), "ArrangerAutomation"), "Events")
    controllers = ET.SubElement(seq, "MidiControllers")
    for index in range(130):
        ET.SubElement(controllers, f"ControllerTargets.{index}", Id=str(index + 1000))
    path = tmp_path / "template.als"
    path.write_bytes(gzip.compress(ET.tostring(root)))
    return path


@pytest.fixture
def stems(tmp_path):
    source = tmp_path / "stems"
    source.mkdir()
    for name, rate, frames in (("drums", 48000, 48000), ("bass", 44100, 50000)):
        x = np.zeros(frames, np.float32)
        x[rate // 4:rate // 2] = 0.75
        sf.write(source / f"{name}.wav", x, rate, subtype="FLOAT")
    return source


@pytest.fixture(autouse=True)
def grid(monkeypatch, tmp_path):
    monkeypatch.setenv("ABLETON_MCP_DATA_DIR", str(tmp_path / "data"))
    def check(path, bpm, points, downbeat):
        return TempoMap(points or [(0, bpm or 120)]), {"status": "passed", "reason": "fixture"}
    monkeypatch.setattr("ableton_mcp.project_import.analysis.grid_check", check)


def test_prepared_project_preserves_media_and_native_time(stems, template, tmp_path):
    destination = tmp_path / "Ready Project"
    result = prepare_project([str(stems)], str(destination), "prepared_stems", bpm=108,
                             time_signature=[6, 8], template_als_path=str(template))
    assert result["status"] == "needs_review"  # No Live evidence has been supplied.
    root = ET.fromstring(gzip.decompress((destination / "Song.als").read_bytes()))
    assert float(root.find(".//MasterTrack/DeviceChain/Mixer/Tempo/Manual").get("Value")) == 108
    assert root.find(".//MasterTrack/DeviceChain/Mixer/TimeSignature/Manual").get("Value") == "302"
    assert len(root.findall("LiveSet/Tracks/AudioTrack")) == 2
    assert float(root.find("LiveSet/MasterTrack/DeviceChain/Mixer/Volume/Manual").get("Value")) == 1.0
    for track in root.findall("LiveSet/Tracks/AudioTrack"):
        assert float(track.find("DeviceChain/Mixer/Volume/Manual").get("Value")) == 1.0
        assert 0 < float(track.find(".//AudioClip/SampleVolume").get("Value")) < 1
    for clip in root.findall(".//AudioClip"):
        assert clip.find("IsWarped").get("Value") == "false"
        assert clip.find("Loop/LoopOn").get("Value") == "false"
        assert clip.find("SampleRef/FileRef/Path").get("Value") == ""
        path = destination / clip.find("SampleRef/FileRef/RelativePath").get("Value")
        assert path.is_file()
        assert sf.info(path).duration == pytest.approx(float(clip.find("Loop/LoopEnd").get("Value")))
    moved = tmp_path / "Moved Project"
    destination.rename(moved)
    assert all((moved / c.find("SampleRef/FileRef/RelativePath").get("Value")).is_file()
               for c in root.findall(".//AudioClip"))


def test_existing_destination_is_never_overwritten(stems, template, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "edits.txt"
    marker.write_text("user edits")
    with pytest.raises(ImportError, match="Destination exists"):
        prepare_project([str(stems)], str(destination), template_als_path=str(template))
    assert marker.read_text() == "user edits"


@pytest.mark.asyncio
async def test_suno_mcp_handoff_builds_portable_stems_and_midi(stems, template, tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.project_import import jobs, suno_export
    from ableton_mcp.tools.project_import import register
    from tests.test_high_level_ops import _invoke

    midi_file = io.BytesIO()
    midi = mido.MidiFile(ticks_per_beat=480)
    midi.add_track().extend([mido.MetaMessage("set_tempo", tempo=500000),
                             mido.Message("note_on", note=48, velocity=96),
                             mido.Message("note_off", note=48, time=480)])
    midi.save(file=midi_file)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for f in stems.glob("*.wav"):
            bundle.write(f, f.name)
        bundle.writestr("bass.mid", midi_file.getvalue())

    class Response(io.BytesIO):
        status = 200
        headers = Message()

    Response.headers["Content-Type"] = "application/zip"
    Response.headers["Content-Length"] = str(len(archive.getvalue()))
    observed = []

    class Opener:
        def open(self, req, **_):
            observed.append(req)
            return Response(archive.getvalue())

    monkeypatch.setattr(suno_export, "build_opener", lambda *_: Opener())
    mcp = FastMCP("test-suno-handoff")
    register(mcp)
    target = tmp_path / "Suno export Project"
    with monkeypatch.context() as launch:
        launch.setattr(jobs.subprocess, "Popen", lambda *_, **__: None)
        result = await _invoke(mcp, "suno_import_mcp_export", destination=str(target), verify_live=False,
                               export_files=[{"filename": "stems.zip",
                                              "url": "https://cdn1.suno.ai/export?signature=private"}])
    assert result["status"] == "started", result
    assert observed[0].get_header("Authorization") is None
    folder = jobs.job_folder(result["job_id"])
    raw = (folder / "request.json").read_text(encoding="utf-8")
    assert "signature=private" not in raw and "https://" not in raw
    request = json.loads(raw)
    assert request.pop("verify_live") is False and request["require_midi"] is True
    assert Path(request["source_paths"][0]).read_bytes() == archive.getvalue()
    built = prepare_project(**request, template_als_path=str(template))
    assert (target / "Song.als").is_file()
    midi_part, = [track for track in built["tracks"] if track["role"] == "midi"]
    assert midi_part["notes"] == 1 and midi_part["muted"] is True
    assert midi_part["paired_audio"].endswith("_bass.wav")


def test_required_midi_refuses_an_audio_only_export(stems, template, tmp_path):
    target = tmp_path / "Project"
    with pytest.raises(ImportError, match="contains no MIDI"):
        prepare_project([str(stems)], str(target), "prepared_stems",
                        require_midi=True, template_als_path=str(template))
    assert not target.exists()


@pytest.mark.parametrize("filename,url", [
    ("../Bass.wav", "https://cdn1.suno.ai/stem"),
    ("Bass.wav", "https://cdn1.suno.ai.example.com/stem"),
    ("Bass.wav", "https://user:password@cdn1.suno.ai/stem"),
    ("Bass.wav", "http://cdn1.suno.ai/stem"),
    ("Bass.wav", "https://cdn1.suno.ai:444/stem"),
])
def test_suno_handoff_rejects_invalid_sources_before_network(filename, url, tmp_path, monkeypatch):
    from ableton_mcp.project_import import suno_export

    def no_network(*_):
        pytest.fail("Invalid sources must not reach the network")

    monkeypatch.setattr(suno_export, "build_opener", no_network)
    with pytest.raises(ImportError):
        suno_export.stage_export([suno_export.ExportFile(filename=filename, url=url)], tmp_path)


def test_suno_redirect_cannot_leave_provider_hosts():
    from ableton_mcp.project_import.suno_export import _SunoRedirect

    with pytest.raises(ImportError, match="Suno-owned host"):
        _SunoRedirect().redirect_request(None, None, 302, "Found", {}, "https://127.0.0.1/secret")


@pytest.mark.parametrize("name", ["../escape.wav", "C:/escape.wav", "a/../../escape.wav", "evil. /x.wav"])
def test_unsafe_zip_is_rejected_before_extraction(tmp_path, name):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("safe.wav", "data")
        z.writestr(name, "data")
    with pytest.raises(ImportError):
        extract_zip(archive, tmp_path / "extracted")
    assert not (tmp_path / "extracted/safe.wav").exists()


def test_suno_zip_and_companion_midi(stems, template, tmp_path):
    path = stems / "bass.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage("set_tempo", tempo=500000),
                  mido.Message("note_on", note=48, velocity=96, time=240),
                  mido.Message("control_change", control=64, value=127, time=0),
                  mido.Message("pitchwheel", pitch=100, time=0),
                  mido.Message("note_off", note=48, time=480),
                  mido.Message("control_change", control=64, value=0)])
    midi.save(path)
    archive = tmp_path / "Suno.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for p in stems.iterdir():
            z.write(p, f"Song/{p.name}")
    output = tmp_path / "out"
    result = prepare_project([str(archive)], str(output), "prepared_stems", bpm=120,
                             time_signature=[4, 4], template_als_path=str(template))
    assert result["status"] == "needs_review"  # Sparse MIDI cannot establish reliable alignment.
    root = ET.fromstring(gzip.decompress((output / "Song.als").read_bytes()))
    midi_track = root.find("LiveSet/Tracks/MidiTrack")
    assert midi_track.find("DeviceChain/Mixer/Speaker/Manual").get("Value") == "false"
    note = midi_track.find(".//MidiNoteEvent")
    assert float(note.get("Time")) == 0.5
    assert float(note.get("Duration")) == 1
    assert len(midi_track.findall(".//ClipEnvelope")) == 2  # Sustain + pitch wheel.
    manifest = json.loads((output / "import_manifest.json").read_text())
    assert len([a for a in manifest["assets"] if a["role"] == "midi"]) == 1
    assert manifest["common_gain"] < 1


def test_tempo_changes_convert_both_note_endpoints(tmp_path):
    path = tmp_path / "variable.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage("set_tempo", tempo=500000),
                  mido.Message("note_on", note=60, velocity=100, time=480),
                  mido.MetaMessage("set_tempo", tempo=1000000, time=480),
                  mido.Message("note_off", note=60, time=480)])
    midi.save(path)
    parsed = read_midi(path, TempoMap([(0, 120)]))
    note = parsed["parts"][0]["notes"][0]
    assert note["start"] == 1
    assert note["duration"] == 3  # .5 s before change + 1 s after, at 120 destination BPM.
    import pretty_midi
    native = _midi_to_clip_notes(pretty_midi.PrettyMIDI(str(path)))[0]
    assert native["start"] == 1
    assert native["duration"] == 2  # Original musical position, across two different tempos.


def test_recording_path_requires_six_complete_outputs(stems, template, tmp_path, monkeypatch):
    from ableton_mcp.stems.demucs import StemFile
    async def separate(*args, **kwargs):
        assert kwargs["model"] == "htdemucs_6s"
        return [StemFile("vocals", str(stems / "drums.wav"))]
    monkeypatch.setattr("ableton_mcp.stems.demucs.split_stems", separate)
    with pytest.raises(ImportError, match="all six"):
        prepare_project([str(stems / "drums.wav")], str(tmp_path / "out"),
                        template_als_path=str(template))


def test_recording_converges_on_shared_project_builder(stems, template, tmp_path, monkeypatch):
    from ableton_mcp.stems.demucs import StemFile
    async def separate(*args, **kwargs):
        return [StemFile(name, str(stems / "drums.wav"))
                for name in ("vocals", "drums", "bass", "guitar", "piano", "other")]
    monkeypatch.setattr("ableton_mcp.stems.demucs.split_stems", separate)
    output = tmp_path / "out"
    result = prepare_project([str(stems / "drums.wav")], str(output), bpm=120,
                             time_signature=[4, 4], template_als_path=str(template))
    assert len(result["tracks"]) == 7
    assert result["tracks"][-1]["role"] == "reference"
    assert result["tracks"][-1]["muted"]
    manifest = json.loads((output / "import_manifest.json").read_text())
    assert manifest["source_mode"] == "separate_recording_6stems"
    assert {a["name"] for a in manifest["assets"] if a["role"] == "stem"} == {
        "Vocals", "Drums", "Bass", "Guitar", "Piano", "Other"}


def test_inspect_does_not_separate_or_build(stems, tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("inspection must not separate")
    monkeypatch.setattr("ableton_mcp.stems.demucs.split_stems", forbidden)
    output = tmp_path / "out"
    result = prepare_project([str(stems / "drums.wav")], str(output), inspect_only=True)
    assert result["source_mode"] == "separate_recording_6stems"
    assert not output.exists()


@pytest.mark.parametrize("points", [[(1, 120)], [(0, float('nan'))], [(0, 120), (0, 130)]])
def test_invalid_maps_rejected(points):
    with pytest.raises(ImportError):
        TempoMap(points)


def test_ambiguous_reference_and_duplicate_parts_do_not_report_ready(stems, template, tmp_path):
    import shutil
    shutil.copy2(stems / "bass.wav", stems / "bass_copy.wav")
    result = prepare_project([str(stems)], str(tmp_path / "out"), "prepared_stems", bpm=120,
                             time_signature=[4, 4], template_als_path=str(template))
    assert any(i["code"] == "duplicate_audio" for i in result["issues"])
    assert result["status"] == "needs_review"


@pytest.mark.asyncio
async def test_live_unavailable_keeps_project_and_honest_report(stems, template, tmp_path, monkeypatch):
    from ableton_mcp.project_import.verify import verify_project
    class Bridge:
        async def require_handler(self, op):
            raise RuntimeError("Bridge not connected")
    monkeypatch.setattr("ableton_mcp.project_import.verify.AbletonBridgeClient", Bridge)
    monkeypatch.setenv("ABLETON_MCP_DATA_DIR", str(tmp_path / "data"))
    output = tmp_path / "out"
    prepare_project([str(stems)], str(output), "prepared_stems", bpm=120,
                    time_signature=[4, 4], template_als_path=str(template))
    result = await verify_project(str(output))
    assert result["status"] == "needs_review"
    assert result["verification_level"] == "static"
    assert "Bridge not connected" in result["pending"]
    assert (output / "Song.als").is_file()
    assert not (tmp_path / "data/project_import_jobs/live-verification.lock").exists()


def test_job_state_survives_registry_restart_and_cancel_is_a_request(tmp_path, monkeypatch):
    import importlib
    import time
    from ableton_mcp.project_import import jobs
    monkeypatch.setenv("ABLETON_MCP_DATA_DIR", str(tmp_path))
    ident = "a" * 32
    folder = jobs.job_folder(ident)
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"job_id": ident, "state": "running",
                                                "stage": "separating", "updated_at": time.time()}))
    importlib.reload(jobs)
    assert jobs.status(ident)["stage"] == "separating"
    assert jobs.cancel(ident)["state"] == "cancel_requested"
    assert (folder / "cancel").is_file()
    assert jobs.status(ident)["state"] == "running"  # Never claim the worker exited without evidence.
    with pytest.raises(ValueError):
        jobs.status("../escape")


def test_controller_release_after_last_note_is_not_truncated(stems, template, tmp_path):
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage("set_tempo", tempo=500000),
                  mido.Message("control_change", control=64, value=127),
                  mido.Message("note_on", note=60, velocity=100),
                  mido.Message("note_off", note=60, time=480),
                  mido.Message("control_change", control=64, value=0, time=480)])
    midi.save(stems / "bass.mid")
    output = tmp_path / "out"
    prepare_project([str(stems)], str(output), "prepared_stems", bpm=120,
                    time_signature=[4, 4], template_als_path=str(template))
    root = ET.fromstring(gzip.decompress((output / "Song.als").read_bytes()))
    clip = root.find(".//MidiClip")
    assert float(clip.find("CurrentEnd").get("Value")) > 2
    order = [t.find("Name/UserName").get("Value") for t in root.find("LiveSet/Tracks")]
    bass = order.index("bass")
    assert order[bass + 1].endswith("MIDI")


def test_output_inside_input_folder_is_rejected(stems, template):
    with pytest.raises(ImportError, match="outside the source"):
        prepare_project([str(stems)], str(stems / "output"), template_als_path=str(template))


def test_unclassified_mix_is_muted_unless_role_is_supplied(stems, template, tmp_path):
    sf.write(stems / "Song.wav", np.ones(48000, np.float32) * 0.1, 48000)
    result = prepare_project([str(stems)], str(tmp_path / "out"), "prepared_stems", bpm=120,
                             time_signature=[4, 4], template_als_path=str(template))
    row = next(r for r in result["tracks"] if "Song" in r["name"])
    assert row["muted"]
    assert any(i["code"] == "unclassified_audio" for i in result["issues"])
    selected = prepare_project([str(stems)], str(tmp_path / "selected"), "prepared_stems", bpm=120,
                               time_signature=[4, 4], template_als_path=str(template),
                               audio_roles={"Song.wav": "reference"})
    assert not any(i["code"] == "unclassified_audio" for i in selected["issues"])


def test_midi_onset_alignment_rejects_offset_and_progressive_drift(tmp_path):
    from ableton_mcp.project_import.analysis import midi_alignment
    rng = np.random.default_rng(24)
    onsets = np.cumsum(rng.uniform(0.25, 0.9, 50))
    sr = 22050
    duration = float(onsets[-1]) + 0.5
    audio = np.zeros(int(duration * sr), np.float32)
    for onset in onsets:
        index = round(onset * sr)
        pulse = rng.uniform(-0.5, 0.5, 441).astype(np.float32) * np.linspace(1, 0, 441)
        audio[index:index + len(pulse)] += pulse
    path = tmp_path / "irregular.wav"
    sf.write(path, audio, sr)
    tempo = TempoMap([(0, 120)])
    def parts(offset=0, scale=1):
        return [{"notes": [{"start": float((onset * scale + offset) * 2)} for onset in onsets]}]
    aligned = midi_alignment(path, parts(), tempo, 0)
    assert aligned["status"] == "passed", aligned
    offset = midi_alignment(path, parts(0.15), tempo, 0)
    assert offset["status"] == "needs_review", offset
    drifting = midi_alignment(path, parts(scale=1.02), tempo, 0)
    assert drifting["status"] == "needs_review", drifting


def test_midi_controller_track_is_preserved_on_its_channel(tmp_path):
    midi = mido.MidiFile(ticks_per_beat=480)
    controls = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.Message("control_change", channel=2, control=64, value=127),
        mido.Message("control_change", channel=2, control=64, value=0, time=960),
    ])
    notes = mido.MidiTrack([
        mido.Message("note_on", channel=2, note=60, velocity=100),
        mido.Message("note_off", channel=2, note=60, time=480),
    ])
    midi.tracks.extend([controls, notes])
    path = tmp_path / "separate_controllers.mid"
    midi.save(path)
    parsed = read_midi(path, TempoMap([(0, 120)]))
    assert parsed["parts"][0]["channel"] == 2
    assert [c["value"] for c in parsed["parts"][0]["controllers"]] == [127, 0]
    assert parsed["parts"][0]["controllers"][-1]["time"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_format", [False, True])
async def test_ready_requires_artifact_checkpoint_and_relocated_readback(stems, template, tmp_path, monkeypatch,
                                                                       legacy_format):
    from ableton_mcp.project_import import verify
    monkeypatch.setenv("ABLETON_MCP_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "Project"
    from ableton_mcp.project_import.templates import remember_saved_template
    remember_saved_template(template, "11.3.43")
    if legacy_format:
        root = ET.fromstring(gzip.decompress(template.read_bytes()))
        root.set("Creator", "Ableton Live 11.1d1")
        template.write_bytes(gzip.compress(ET.tostring(root)))
    prepare_project([str(stems)], str(project), "prepared_stems", bpm=120,
                    time_signature=[4, 4], template_als_path=str(template))
    manifest = json.loads((project / "import_manifest.json").read_text())
    current = {"path": project / "Song.als", "position": 0}
    opened = []
    class Bridge:
        async def require_handler(self, op):
            pass
        async def call(self, op, **kwargs):
            if op == "project.import_checkpoint":
                return {"checkpointed": True, "set_sha256": manifest["set_sha256"], "native_save": False}
            elif op == "project.open_set":
                opened.append(Path(kwargs["path"]))
                current["path"] = Path(kwargs["path"])
            elif op == "project.import_seek":
                current["position"] = kwargs["position"]
            elif op in {"project.import_stop", "project.import_configure"}:
                current["position"] = 0
            tracks = []
            for row in manifest["tracks"]:
                asset = next(a for a in manifest["assets"] if a["path"] == row["path"])
                tracks.append({"name": row["name"], "muted": row["muted"], "solo": False,
                               "armed": False, "volume": 0.85, "volume_display": f"{20 * math.log10(row['gain']):.3f} dB", "pan": 0, "output": "Master",
                               "clips": [{"start_beats": row["start_beats"], "end_beats": row["end_beats"],
                                          "looping": False, "muted": False, "warping": False,
                                          "pitch_coarse": 0, "pitch_fine": 0, "start_marker": 0,
                                          "end_marker": asset["duration_sec"], "file_exists": True,
                                          "gain_display": f"{20 * math.log10(row['sample_gain']):.2f} dB",
                                          "file_path": str(current["path"].parent / row["path"])}]})
            return {"file_path": str(current["path"]), "tracks": tracks, "tempo": 120,
                    "meter": [4, 4], "live_major": 11, "live_version": "11.3.43",
                    "playing": False, "position": current["position"],
                    "master_left": 0.2, "master_right": 0.2, "master_volume": 0.85, "master_volume_display": "0.0 dB", "loop": False,
                    "recording": False, "overdub": False, "metronome": False, "back_to_arranger": False}
    async def fast_sleep(seconds):
        pass
    monkeypatch.setattr(verify, "AbletonBridgeClient", Bridge)
    monkeypatch.setattr(verify.asyncio, "sleep", fast_sleep)
    result = await verify.verify_project(str(project))
    assert result["status"] == ("needs_review" if legacy_format else "ready_audio_only"), result
    assert result["verification_level"] == "live_reopened"
    assert len(opened) == 2  # Relocated set, then the original saved set.
    report = json.loads((project / "validation_report.json").read_text())
    assert report["checks"]["portable_reopened"]
    assert report["checks"]["live_reopened"]
    assert report["checks"]["save_format_matches_live"] is not legacy_format
    assert report["save_compatibility"]["native_save_tested"] is False
    assert bool(result["pending"]) is legacy_format


def test_bundled_newer_complete_template_preferred_over_old_default(template, tmp_path, monkeypatch):
    from ableton_mcp.project_import import builder
    monkeypatch.delenv("ABLETON_IMPORT_TEMPLATE", raising=False)
    resource = tmp_path / "Resources"
    default = resource / "Builtin/Templates/DefaultLiveSet.als"
    default.parent.mkdir(parents=True)
    root = ET.fromstring(gzip.decompress(template.read_bytes()))
    root.set("MinorVersion", "11.0_434")
    default.write_bytes(gzip.compress(ET.tostring(root)))
    current = resource / "Core Library/Templates/Demo & Sketch.als"
    current.parent.mkdir(parents=True)
    current.write_bytes(template.read_bytes())
    monkeypatch.setattr(builder, "installed_resources", lambda: [resource])
    assert builder.find_template(None) == current
    # Explicit templates still take precedence; their compatibility is gated in Live.
    assert builder.find_template(str(default)) == default


def test_unknown_template_version_cannot_pass_save_preflight(template):
    from ableton_mcp.project_import.verify import check_save_format
    from ableton_mcp.project_import.templates import remember_saved_template
    assert check_save_format(template, "11.3.43")['status'] == "requires_review"
    remember_saved_template(template, "11.3.43")
    assert check_save_format(template, "")['status'] == "requires_review"
    assert check_save_format(template, "11.2.11")['status'] == "requires_review"
    assert check_save_format(template, "11.3.43")['status'] == "release_matches"


def test_matching_release_with_different_edition_or_schema_still_needs_review(template, tmp_path):
    from ableton_mcp.project_import.verify import check_save_format
    from ableton_mcp.project_import.templates import remember_saved_template
    remember_saved_template(template, "11.3.43")
    root = ET.fromstring(gzip.decompress(template.read_bytes()))
    root.find("LiveSet/OverwriteProtectionNumber").set("Value", "2820")
    exported = tmp_path / "wrong-edition.als"
    exported.write_bytes(gzip.compress(ET.tostring(root)))
    assert check_save_format(exported, "11.3.43")['status'] == "requires_review"


def test_native_template_cache_is_immutable_and_preferred(template):
    from ableton_mcp.project_import.builder import find_template
    from ableton_mcp.project_import.templates import remember_saved_template, selected_template
    remembered = remember_saved_template(template, "11.3.43")
    assert find_template(None) == Path(remembered['path'])
    template.write_bytes(b"later user edits")
    assert selected_template()['sha256'] == remembered['sha256']
    Path(remembered['path']).write_bytes(b"bad cache")
    with pytest.raises(ImportError, match="Cached native template"):
        selected_template()


def test_preset_zero_ids_are_distinct_and_parameter_values_are_untouched():
    from ableton_mcp.project_import.builder import remap_global_ids
    import itertools
    root = ET.fromstring('<Operator><AutomationTarget Id="0"/><Pointee Id="0"/>'
                         '<Frequency Value="1000"/><ModulationTarget Id="1000"/>'
                         '<PointeeId Value="1000"/></Operator>')
    ids = itertools.count(2000)
    remap_global_ids(root, lambda: next(ids))
    assert [n.get("Id") for n in root if n.get("Id")] == ["2000", "2001", "2002"]
    assert root.find("Frequency").get("Value") == "1000"
    assert root.find("PointeeId").get("Value") == "2002"


def test_sequencer_prefixed_modulation_targets_are_global():
    from ableton_mcp.project_import.builder import remap_global_ids
    import itertools
    first = ET.fromstring('<AudioTrack><GrainSizeModulationTarget Id="81"/>'
                          '<FluxModulationTarget Id="82"/><VolumeModulationTarget Id="83"/>'
                          '<TranspositionModulationTarget Id="84"/>'
                          '<SampleOffsetModulationTarget Id="85"/></AudioTrack>')
    second = ET.fromstring(ET.tostring(first))
    ids = itertools.count(100)
    remap_global_ids(first, lambda: next(ids))
    remap_global_ids(second, lambda: next(ids))
    assert len({n.get("Id") for root in (first, second) for n in root}) == 10


def test_invalid_suno_key_metadata_preserves_performance_and_original(tmp_path):
    from ableton_mcp.project_import.timing import load_midi
    midi = mido.MidiFile(ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=600000),
        mido.MetaMessage("key_signature", key="C"),
        mido.Message("sysex", data=[1, 2, 3]),
        mido.Message("note_on", note=64, velocity=91, time=240),
        mido.Message("note_off", note=64, velocity=33, time=480),
    ]))
    path = tmp_path / "suno.mid"
    midi.save(path)
    raw = path.read_bytes().replace(bytes.fromhex("ff59020000"), bytes.fromhex("ff59021300"))
    path.write_bytes(raw)
    parsed = read_midi(path, TempoMap([(0, 120)]))
    assert path.read_bytes() == raw
    assert len(parsed["annotations"]) == 1
    assert parsed["parts"][0]["notes"][0] == pytest.approx({"pitch": 64, "start": 0.6, "duration": 1.2,
                                                               "velocity": 91, "off_velocity": 33})
    loaded, _ = load_midi(path)
    assert any(m.type == "sysex" and m.data == (1, 2, 3) for t in loaded.tracks for m in t)


def test_mp3_gapless_decode_keeps_frames_and_pulse_timing(tmp_path):
    import shutil
    import subprocess
    from ableton_mcp.project_import.assets import collect, digest
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required for MP3 integration")
    rate = 48000
    samples = np.zeros((rate * 2 + 137, 2), np.float32)
    samples[12000:12032] = 0.5
    source = tmp_path / "pulse.wav"
    sf.write(source, samples, rate, subtype="FLOAT")
    mp3 = tmp_path / "pulse.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), str(mp3)], check=True)
    project = tmp_path / "project"
    asset = collect(mp3, project, "stem")
    decoded, observed_rate = sf.read(project / asset["path"], always_2d=True)
    assert observed_rate == rate and len(decoded) == len(samples)
    assert abs(int(np.argmax(np.abs(decoded[:, 0]))) - 12016) < 32
    assert digest(project / asset["original_path"]) == digest(mp3)
    assert asset["decode"]["gapless_side_data"][0]["skip_samples"] > 0
    assert asset["subtype"] == "FLOAT"
    assert collect(mp3, project, "stem")["sha256"] == asset["sha256"]



def test_gain_readback_uses_db_instead_of_nonlinear_fader_values():
    from ableton_mcp.project_import.verify import gain_matches
    assert gain_matches("0.0 dB", 1.0)
    assert gain_matches("-12.041 dB", 0.25)
    assert gain_matches("-1,412 dB", 0.85)
    assert not gain_matches("-1.412 dB", 1.0)
    assert not gain_matches("unavailable", 1.0)
