"""Live verification has explicit evidence gates and preserves an existing set."""

from __future__ import annotations

import asyncio
import gzip
import json
import math
import os
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..bridge_client import AbletonBridgeClient
from ..paths import data_dir
from .assets import digest
from .pipeline import write_json
from .timing import TempoMap
from .templates import selected_template, format_metadata


async def wait_set(bridge: AbletonBridgeClient, path: Path, rows: list[dict],
                   timeout: float = 60, switch_from: Path | None = None) -> dict:
    deadline = time.monotonic() + timeout
    last = "No response"
    while time.monotonic() < deadline:
        try:
            snapshot = await bridge.call("project.import_snapshot", timeout=3, expected_set_path=str(path))
            if snapshot.get("file_path") and Path(snapshot["file_path"]).resolve() == path.resolve():
                return snapshot
            if snapshot.get("open_dialog_count"):
                if switch_from is None:
                    raise RuntimeError("Live has an unowned dialog; the requested set was not opened")
                await bridge.call("project.import_accept_switch", expected_set_path=str(switch_from))
                switch_from = None  # Never press a dialog button twice after an unknown outcome.
            last = f"Live has not confirmed the requested set path: {snapshot.get('file_path')}"
        except Exception as exc:
            last = str(exc)
        await asyncio.sleep(0.5)
    raise RuntimeError(last)


def gain_matches(display: str, linear_gain: float) -> bool:
    """XML gain is amplitude; compare the API's displayed dB, not its fader."""
    observed = re.search(r"[-+]?\d+(?:\.\d+)?", str(display).replace(",", "."))
    return bool(observed) and abs(float(observed.group()) - 20 * math.log10(linear_gain)) <= 0.11


def check_save_format(path: Path, live_version: str) -> dict:
    """Compare genuine file metadata with Live's compatibility release family.

    Loading and playing an older set does not prove it can be saved in place.
    This is a format preflight, not evidence of a native save operation.
    """
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    creator = root.get("Creator", "")
    exported = re.search(r"Ableton Live (\d+)\.(\d+)", creator)
    running = re.match(r"(\d+)\.(\d+)", live_version)
    matches = bool(exported and running and exported.groups() == running.groups())
    reference = selected_template()
    actual = format_metadata(path)
    keys = ("MajorVersion", "MinorVersion", "SchemaChangeCount", "OverwriteProtectionNumber")
    reference_matches = bool(reference and reference["live_version"] == live_version
                             and all(actual.get(key) == reference["format"].get(key) for key in keys))
    matches = matches and reference_matches
    protection = root.find("LiveSet/OverwriteProtectionNumber")
    return {"status": "release_matches" if matches else "requires_review",
            "creator": creator, "schema": root.get("MinorVersion"),
            "overwrite_protection": protection.get("Value") if protection is not None else None,
            "live_version": live_version, "native_reference_matches": reference_matches,
            "native_save_tested": False,
            "reason": ("Schema and overwrite protection match a set saved by this Live build; native save was not invoked"
                       if matches else "Exported format does not match a saved reference from this Live build. "
                       "Preserve edits with Save As, select it with song_use_import_template, then rebuild imports.")}


def compare_snapshot(snapshot: dict, manifest: dict, project: Path) -> list[str]:
    errors = []
    expected = manifest["tracks"]
    observed = snapshot["tracks"]
    if snapshot.get("live_major") != 11:
        errors.append("Live version differs from supported Live 11 template schema")
    if len(observed) != len(expected):
        return errors + ["Track count changed"]
    if snapshot["meter"] != list(manifest["meter"]):
        errors.append("Saved meter differs from manifest")
    if abs(snapshot["tempo"] - manifest["tempo_map"][0][1]) > 0.01:
        errors.append("Saved initial tempo differs from manifest")
    if not gain_matches(snapshot.get("master_volume_display", ""), 1.0):
        errors.append("Master fader differs from unity gain")
    if any(snapshot.get(key) for key in ("loop", "recording", "overdub", "metronome", "back_to_arranger")):
        errors.append("Playback state is not configured for Arrangement playback")
    assets = {a["path"]: a for a in manifest["assets"]}
    for row, track in zip(expected, observed):
        if track["name"] != row["name"] or track["muted"] != row["muted"]:
            errors.append(f"Track state differs: {row['name']}")
        if track["solo"] or track["armed"]:
            errors.append(f"Track is soloed/armed: {row['name']}")
        if not gain_matches(track.get("volume_display", ""), row["gain"]) or abs(track["pan"]) > 1e-5:
            errors.append(f"Mixer gain/pan differs: {row['name']}")
        if track["output"].casefold() not in {"master", "main"}:
            errors.append(f"Track is not routed to Master: {row['name']}")
        if len(track["clips"]) != 1:
            errors.append(f"Expected one Arrangement clip: {row['name']}")
            continue
        clip = track["clips"][0]
        if clip["looping"] or clip["muted"]:
            errors.append(f"Clip loops or is disabled: {row['name']}")
        if abs(clip["start_beats"] - row["start_beats"]) > 1e-5:
            errors.append(f"Clip start differs: {row['name']}")
        if abs(clip["end_beats"] - row["end_beats"]) > 1e-4:
            errors.append(f"Clip end differs: {row['name']}")
        if row["role"] != "midi":
            asset = assets[row["path"]]
            if not gain_matches(clip.get("gain_display", ""), row["sample_gain"]):
                errors.append(f"Audio clip gain differs: {row['name']}")
            if abs(clip["start_marker"]) > 1 / asset["sample_rate"]:
                errors.append(f"Source start marker shifted: {row['name']}")
            if abs(clip["end_marker"] - asset["duration_sec"]) > 1 / asset["sample_rate"]:
                errors.append(f"Source end marker truncated/extended: {row['name']}")
            if clip["warping"] or clip["pitch_coarse"] or clip["pitch_fine"]:
                errors.append(f"Audio is warped or transposed: {row['name']}")
            path = Path(clip["file_path"]).resolve()
            if not clip["file_exists"] or not path.is_relative_to(project.resolve()):
                errors.append(f"Media is missing or outside project: {row['name']}")
            elif digest(path) != assets[row["path"]]["sha256"]:
                errors.append(f"Media content changed: {row['name']}")
        elif row["preview_instrument"] and not track["devices"]:
            errors.append(f"MIDI preview instrument is missing: {row['name']}")
    return errors


async def verify_notes(bridge: AbletonBridgeClient, manifest: dict) -> None:
    for row in manifest["tracks"]:
        if row["role"] != "midi":
            continue
        result = await bridge.call("clip.get_arrangement_notes", track_index=row["track_index"], clip_index=0)
        expected = sorted(row["note_data"], key=lambda n: (n["start"], n["pitch"], n["duration"]))
        actual = sorted(result["notes"], key=lambda n: (n["start"], n["pitch"], n["duration"]))
        if len(expected) != len(actual):
            raise RuntimeError(f"MIDI notes were lost/added in Live: {row['name']}")
        for a, b in zip(expected, actual):
            if (a["pitch"] != b["pitch"] or a["velocity"] != b["velocity"] or b["mute"]
                    or abs(a["start"] - b["start"]) > 1e-5
                    or abs(a["duration"] - b["duration"]) > 1e-5):
                raise RuntimeError(f"MIDI note timing/content changed in Live: {row['name']}")


async def _playback(bridge: AbletonBridgeClient, manifest: dict) -> list[dict]:
    tempo = TempoMap([tuple(p) for p in manifest["tempo_map"]])
    end = max(tempo.beat_to_seconds(t["end_beats"]) for t in manifest["tracks"])
    evidence = []
    expected = str(Path(manifest["_project"]) / manifest["set_path"])
    try:
        for seconds in (min(1.0, end * 0.1), end * 0.5, max(0, end - 2.0)):
            await bridge.call("project.import_seek", position=tempo.seconds_to_beat(seconds),
                              expected_set_path=expected)
            peak = 0.0
            for _ in range(8):
                await asyncio.sleep(0.125)
                snapshot = await bridge.call("project.import_snapshot")
                beat = snapshot["position"]
                active_tempo = next(v for b, v in reversed(tempo.points) if b <= beat)
                if abs(snapshot["tempo"] - active_tempo) > 0.01:
                    raise RuntimeError("Live tempo automation differs from the expected timing map")
                peak = max(peak, snapshot["master_left"], snapshot["master_right"])
            await bridge.call("project.import_stop", expected_set_path=expected)
            if peak >= 1:
                raise RuntimeError("Live playback clipped")
            evidence.append({"position_sec": seconds, "master_meter_peak": peak})
        if not any(x["master_meter_peak"] > 1e-6 for x in evidence):
            raise RuntimeError("Live produced no measurable audio at playback checkpoints")
        for row in manifest["tracks"]:
            if row["role"] != "midi":
                continue
            # Audition at first actual note, not at a possibly silent clip origin.
            first = min(n["start"] for n in row["note_data"])
            await bridge.call("project.import_seek", position=max(0, first - 0.05),
                              preview_track=row["track_index"], expected_set_path=expected)
            peak = 0.0
            for _ in range(8):
                await asyncio.sleep(0.125)
                snapshot = await bridge.call("project.import_snapshot")
                peak = max(peak, snapshot["master_left"], snapshot["master_right"])
            await bridge.call("project.import_stop", expected_set_path=expected)
            if peak <= 1e-6 or peak >= 1:
                raise RuntimeError(f"MIDI preview is silent or clipped: {row['name']}")
            evidence.append({"midi_track": row["track_index"], "master_meter_peak": peak})
    finally:
        await bridge.call("project.import_stop", expected_set_path=expected)
    return evidence


async def _checkpoint(bridge: AbletonBridgeClient, path: Path, manifest: dict) -> dict:
    result = await bridge.call("project.import_checkpoint", expected_set_path=str(path))
    if not result.get("checkpointed") or result.get("set_sha256") != manifest["set_sha256"]:
        raise RuntimeError("Live did not confirm the intact generated artifact")
    if digest(path) != manifest["set_sha256"]:
        raise RuntimeError("The generated artifact changed during verification")
    ET.fromstring(gzip.decompress(path.read_bytes()))
    return result


async def verify_project(project_path: str) -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    manifest_path = project / "import_manifest.json"
    report_path = project / "validation_report.json"
    if not manifest_path.is_file() or not report_path.is_file():
        return {"status": "failed", "error": "Project is missing its import manifest/report"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest["_project"] = str(project)
    bridge = AbletonBridgeClient()
    lock_root = data_dir("project_import_jobs")
    lock_root.mkdir(parents=True, exist_ok=True)
    locked = lock_root / "live-verification.lock"
    try:
        lock = locked.open("x")
        lock.write(str(os.getpid()))
        lock.flush()
    except FileExistsError:
        return {"status": "needs_review", "error": "Another Live verification owns the transport"}
    relocation = None
    opened = False
    transaction = False
    try:
        # Require the new read-back handler before switching an active application.
        await bridge.require_handler("project.import_snapshot")
        current = await bridge.call("project.import_snapshot", expected_set_path=str(project / manifest["set_path"]))
        if current.get("playing") or current.get("recording"):
            raise RuntimeError("Stop the active playback/recording before verifying another project")
        target = project / manifest["set_path"]
        current_path = current.get("file_path")
        if not current_path or Path(current_path).resolve() != target:
            await bridge.call("project.open_set", path=str(target))
        snapshot = await wait_set(bridge, target, manifest["tracks"])
        opened = True
        live_version = snapshot.get("live_version")
        if not live_version:
            live_version = (await bridge.call("project.info")).get("live_version", "")
        report["save_compatibility"] = check_save_format(target, live_version)
        report["checks"]["save_format_matches_live"] = report["save_compatibility"]["status"] == "release_matches"
        await bridge.call("project.import_begin", expected_set_path=str(target))
        transaction = True
        await bridge.call("project.import_configure", expected_set_path=str(target))
        snapshot = await bridge.call("project.import_snapshot")
        problems = compare_snapshot(snapshot, manifest, project)
        if problems:
            raise RuntimeError("; ".join(problems))
        report["playback"] = await _playback(bridge, manifest)
        await verify_notes(bridge, manifest)
        report["artifact_checkpoint"] = await _checkpoint(bridge, target, manifest)
        await bridge.call("project.import_restore", expected_set_path=str(target))
        transaction = False
        problems = compare_snapshot(await bridge.call("project.import_snapshot"), manifest, project)
        if problems:
            raise RuntimeError("Restore: " + "; ".join(problems))
        await bridge.call("project.import_arm_switch", expected_set_path=str(target))
        # Use a relocated copy with a distinct name/path to force an actual reload.
        # Every observed media path must resolve inside the relocated folder.
        relocation = Path(tempfile.mkdtemp(prefix="ableton-import-verify-")) / "Project"
        shutil.copytree(project, relocation, ignore=shutil.ignore_patterns(".live-verification.lock"))
        await bridge.call("project.open_set", path=str(relocation / manifest["set_path"]))
        relocated = await wait_set(bridge, relocation / manifest["set_path"], manifest["tracks"], switch_from=target)
        if digest(target) != manifest["set_sha256"]:
            raise RuntimeError("Source artifact was changed while switching sets")
        problems = compare_snapshot(relocated, manifest, relocation)
        if problems:
            raise RuntimeError("Relocation: " + "; ".join(problems))
        await _checkpoint(bridge, relocation / manifest["set_path"], manifest)
        await bridge.call("project.import_arm_switch", expected_set_path=str(relocation / manifest["set_path"]))
        await bridge.call("project.open_set", path=str(target))
        reopened = await wait_set(bridge, target, manifest["tracks"], switch_from=relocation / manifest["set_path"])
        problems = compare_snapshot(reopened, manifest, project)
        if problems:
            raise RuntimeError("Reopen: " + "; ".join(problems))
        await verify_notes(bridge, manifest)
        report["checks"].update(live_reopened=True, portable_reopened=True)
        report["verification_level"] = "live_reopened"
        report["pending"] = ([] if report["checks"]["save_format_matches_live"]
                             else [report["save_compatibility"]["reason"]])
        # Never downgrade failed supplied MIDI to audio-only completion.
        report["status"] = ("needs_review" if report["pending"] or any(i.get("severity") != "warning" for i in report["issues"]) else
                            "ready" if any(a["role"] == "midi" for a in manifest["assets"])
                            else "ready_audio_only")
    except Exception as exc:
        report["status"] = "needs_review"
        report["pending"] = [str(exc)]
    finally:
        if opened and transaction:
            try:
                await bridge.call("project.import_stop", expected_set_path=str(project / manifest["set_path"]))
                await bridge.call("project.import_restore", expected_set_path=str(project / manifest["set_path"]))
            except Exception:
                pass
        lock.close()
        locked.unlink(missing_ok=True)
        # A failed relocated reopen can leave Live using its media. Preserve it for recovery.
        if relocation and report["checks"].get("portable_reopened"):
            shutil.rmtree(relocation.parent)
        write_json(report_path, report)
    return {"status": report["status"], "project_path": str(project),
            "set_path": str(project / manifest["set_path"]),
            "verification_level": report["verification_level"],
            "issues": report["issues"], "pending": report["pending"], "report_path": str(report_path)}
