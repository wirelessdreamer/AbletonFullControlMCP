"""The two source workflows converge before media placement and set validation."""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .assets import AUDIO, MIDI, ImportError, collect, digest, discover, safe_name, sum_peak
from .builder import build_set, find_template
from .timing import TempoMap, read_midi


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _lookup(mapping: dict, path: Path, default: Any = None) -> Any:
    return mapping.get(str(path), mapping.get(path.name, default))


def prepare_project(
    source_paths: list[str], destination: str, source_mode: str = "auto",
    bpm: float | None = None, time_signature: list[int] | None = None,
    downbeat_sec: float | None = None, tempo_map: list[list[float]] | None = None,
    reference_path: str | None = None, offsets_seconds: dict[str, float] | None = None,
    midi_pairings: dict[str, str] | None = None, template_als_path: str | None = None,
    audio_roles: dict[str, str] | None = None,
    inspect_only: bool = False, require_midi: bool = False, progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build on disk; Live verification is a separate explicit orchestration stage.

    Each destination must be new. Existing projects, including edited output from
    previous imports, are never replaced. All partial work remains in a uniquely
    named staging directory for diagnosis. Resume reuses separation's content cache.
    """
    from .analysis import grid_check, midi_alignment

    def note(stage: str) -> None:
        if progress:
            progress(stage)

    if source_mode not in {"auto", "prepared_stems", "separate_recording_6stems"}:
        raise ImportError("Unknown source_mode")
    if bpm is not None and (not math.isfinite(bpm) or not 20 <= bpm <= 999):
        raise ImportError("BPM must be finite and between 20 and 999")
    meter = tuple(time_signature or [4, 4])
    if (len(meter) != 2 or any(type(v) is not int for v in meter)
            or not 1 <= meter[0] <= 99 or meter[1] not in (1, 2, 4, 8, 16, 32)):
        raise ImportError("Invalid time_signature; expected [numerator, power-of-two denominator]")
    if downbeat_sec is not None and (not math.isfinite(downbeat_sec) or downbeat_sec < 0):
        raise ImportError("downbeat_sec must be finite and nonnegative")
    offsets = offsets_seconds or {}
    if any(v not in {"stem", "reference"} for v in (audio_roles or {}).values()):
        raise ImportError("audio_roles values must be stem or reference")
    if any(not math.isfinite(v) or v < 0 for v in offsets.values()):
        raise ImportError("Audio/MIDI offsets must be finite and nonnegative")
    destination_path = Path(destination).expanduser().resolve()
    for source in source_paths:
        source_path = Path(source).expanduser().resolve()
        if source_path.is_dir() and destination_path.is_relative_to(source_path):
            raise ImportError("Choose a destination outside the source folder to avoid importing output again")
    if destination_path.exists() and not inspect_only:
        raise ImportError(f"Destination exists; choose a new project folder: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".project-import-", dir=destination_path.parent))
    project = work / "project"
    project.mkdir()
    issues: list[dict[str, Any]] = []

    def issue(code: str, message: str, **detail: Any) -> None:
        issues.append({"code": code, "message": message, **detail})

    try:
        note("discovering")
        files, excluded = discover(source_paths, work / "sources")
        audio = [p for p in files if p.suffix.lower() in AUDIO]
        midi = [p for p in files if p.suffix.lower() in MIDI]
        if not audio:
            raise ImportError("No supported audio found; supply WAV, FLAC, AIFF or MP3")
        if require_midi and not midi:
            raise ImportError("The Suno export contains no MIDI; supply its MIDI export or request audio only")
        mode = source_mode
        if mode == "auto":
            mode = ("separate_recording_6stems" if len(audio) == 1
                    and all(Path(p).is_file() and Path(p).suffix.lower() in AUDIO | MIDI
                            for p in source_paths) else "prepared_stems")
        if mode == "separate_recording_6stems" and len(audio) != 1:
            raise ImportError("Recording separation requires exactly one mixed recording")
        inventory = {"source_mode": mode, "audio": [str(p) for p in audio],
                     "midi": [str(p) for p in midi], "excluded": excluded}
        # ZIP extraction paths are temporary; file names remain valid selectors
        # for reference_path, offsets and pairings on a later run.
        inventory["selectors"] = [p.name for p in files]
        if inspect_only:
            return {"status": "inspected", **inventory, "destination": str(destination_path)}
        for item in excluded:
            if Path(item["path"]).suffix.lower() in {".mp3", ".m4a", ".ogg", ".zip"}:
                issue("unsupported_media", "Excluded media needs conversion or explicit selection", **item)
        template_als_path = str(find_template(template_als_path))
        ref = None
        if reference_path:
            candidates = [p for p in audio if str(p) == reference_path or p.name == reference_path]
            if len(candidates) > 1:
                raise ImportError("Reference filename is ambiguous; use explicit source files")
            ref = candidates[0] if candidates else Path(reference_path).expanduser().resolve()
        if ref is None and mode == "prepared_stems":
            candidates = [p for p in audio if any(term in p.stem.lower().replace("_", " ")
                          for term in ("full mix", "full song", "reference", "master mix"))]
            if len(candidates) == 1:
                ref = candidates[0]
            elif len(candidates) > 1:
                raise ImportError("Multiple reference mixes; supply reference_path and explicit stem files")
        note("collecting_media")
        assets: list[dict[str, Any]] = []
        provenance: dict[str, Any] = {}
        if mode == "separate_recording_6stems":
            from ..stems.demucs import split_stems
            from ..paths import data_dir
            ref = audio[0]
            reference_asset = collect(ref, project, "reference")
            canonical_ref = project / reference_asset["path"]
            note("separating_six_stems")
            outputs = asyncio.run(split_stems(str(canonical_ref), model="htdemucs_6s",
                                              out_dir=data_dir("import_separation")))
            expected = {"vocals", "drums", "bass", "guitar", "piano", "other"}
            if len(outputs) != 6 or {s.name for s in outputs} != expected:
                raise ImportError("Separation did not produce all six expected stems")
            audio = [Path(s.path) for s in outputs]
            metadata_path = audio[0].parent / "separation.json"
            if metadata_path.is_file():
                provenance = json.loads(metadata_path.read_text(encoding="utf-8"))
            for output in outputs:
                asset = collect(Path(output.path), project, "stem")
                asset["name"] = output.name.title()
                assets.append(asset)
            if len({(a["frames"], a["sample_rate"]) for a in assets}) != 1:
                raise ImportError("Separated stems have inconsistent durations or sample rates")
        else:
            part_pattern = re.compile(
                r"\b(vocals?|drums?|bass|guitars?|piano|other|strings|brass|woodwinds?|synths?|"
                r"keys|keyboards?|percussion|fx|effects|backing|lead|instrumental)\b", re.I)
            named_parts = [p for p in audio if part_pattern.search(p.stem.replace("_", " "))]
            hashes: set[str] = set()
            for path in audio:
                if ref and path.resolve() == ref:
                    continue
                asset = collect(path, project, "stem")
                if asset["sha256"] in hashes:
                    issue("duplicate_audio", "Identical audio appears more than once; select the intended parts",
                          name=path.name)
                    continue
                hashes.add(asset["sha256"])
                selected_role = _lookup(audio_roles or {}, path)
                if selected_role:
                    asset["role"] = selected_role
                elif named_parts and path not in named_parts:
                    asset["role"] = "unclassified"
                    issue("unclassified_audio", "Audio role is ambiguous; track is muted pending selection",
                          name=path.name)
                asset["offset_sec"] = float(_lookup(offsets, path, 0))
                assets.append(asset)
        if not any(a["role"] == "stem" for a in assets):
            raise ImportError("No stems remain after reference/duplicate selection")
        if ref:
            assets.append(reference_asset if mode == "separate_recording_6stems"
                          else collect(ref, project, "reference"))
            if provenance.get("source_sha256") and assets[-1]["sha256"] != provenance["source_sha256"]:
                raise ImportError("Original recording changed during separation")
        for asset in assets:
            asset["size_bytes"] = (project / asset["path"]).stat().st_size
            if asset["peak"] < 1e-7:
                issue("silent_part", "Part is silent; retained for inspection", name=asset["name"],
                      severity="warning")
        if mode == "separate_recording_6stems":
            source_duration = assets[-1]["duration_sec"]
            stem_duration = assets[0]["duration_sec"]
            if abs(source_duration - stem_duration) > 1 / assets[0]["sample_rate"]:
                raise ImportError("Separation changed source duration beyond one output frame")
        note("checking_timing")
        # Companion files often carry a shared tempo map even when the archive
        # has no separate song metadata. Retain it instead of guessing a tempo
        # independently for each part. Conflicting maps still need review.
        midi_metadata = [read_midi(p, TempoMap([(0, 120)])) for p in midi]
        shared = [m["tempo_map"] for m in midi_metadata if m["explicit_tempo"]]
        if bpm is None and tempo_map is None and shared:
            if len(shared) == len(midi) and all(m == shared[0] for m in shared):
                tempo_map = shared[0]
            else:
                issue("midi_tempo_conflict", "Companion MIDI tempo maps differ; verify the project grid")
        signatures = [m["time_signatures"] for m in midi_metadata]
        if time_signature is None and signatures and all(len(s) == 1 and s == signatures[0]
                                                         for s in signatures):
            meter = (signatures[0][0]["numerator"], signatures[0][0]["denominator"])
            time_signature = list(meter)
        analysis_asset = next((a for a in assets if a["role"] == "reference"), None)
        if analysis_asset is None:
            analysis_asset = next((a for a in assets if a["role"] == "stem" and "drum" in a["name"].lower()),
                                  next(a for a in assets if a["role"] == "stem"))
        if downbeat_sec is not None and downbeat_sec >= analysis_asset["duration_sec"]:
            raise ImportError("downbeat_sec must lie within the source recording")
        if tempo_map and bpm is not None and abs(float(tempo_map[0][1]) - bpm) > 0.01:
            raise ImportError("BPM conflicts with the supplied tempo map's initial tempo")
        tempo, grid = grid_check(project / analysis_asset["path"], bpm, tempo_map, downbeat_sec)
        if grid["status"] != "passed":
            issue("grid_unverified", grid["reason"])
        if time_signature is None:
            issue("meter_assumed", "4/4 was assumed; supply time_signature to confirm the meter")
        # Move the complete source timebase together so its known downbeat lands on a bar.
        pre_roll = 0.0
        if downbeat_sec is not None:
            beat = tempo.seconds_to_beat(downbeat_sec)
            bar = meter[0] * 4 / meter[1]
            shift = math.ceil((beat - 1e-8) / bar) * bar - beat
            if shift > 1e-8:
                # A changing map needs shifting both tempo breakpoints and source media.
                pre_roll = shift * 60 / tempo.points[0][1]
                tempo = TempoMap([(0.0, tempo.points[0][1]),
                                  *[(b + shift, v) for b, v in tempo.points[1:]]])
        for asset in assets:
            asset["offset_sec"] += pre_roll
        parts: list[dict] = []
        midi_checks: list[dict] = []
        for path in midi:
            asset = collect(path, project, "midi")
            assets.append(asset)
            parsed = read_midi(project / asset["path"], tempo, float(_lookup(offsets, path, 0)) + pre_roll)
            asset["midi"] = {k: v for k, v in parsed.items() if k != "parts"}
            for message in parsed["annotations"]:
                issue("midi_annotation", message, path=path.name, severity="warning")
            if not parsed["parts"]:
                issue("empty_midi", "MIDI has no usable note parts", path=path.name)
            for message in parsed["issues"]:
                issue("midi_event", message, path=path.name)
            if not parsed["explicit_tempo"]:
                issue("implicit_midi_tempo", "MIDI omits tempo; verify its assumed 120 BPM timing basis",
                      path=path.name)
            if len(parsed["time_signatures"]) > 1:
                issue("meter_changes", "MIDI contains meter changes; project meter changes need review",
                      path=path.name)
            if parsed["time_signatures"] and any(
                [s["numerator"], s["denominator"]] != list(meter)
                for s in parsed["time_signatures"]
            ):
                issue("midi_meter_conflict", "MIDI meter differs from the project meter", path=path.name)
            pairing = _lookup(midi_pairings or {}, path)
            candidates = [a for a in assets if a["role"] == "stem" and (
                (pairing and (a["name"].casefold() == str(pairing).casefold()
                              or Path(a["source_path"]).name.casefold() == str(pairing).casefold()))
                or (not pairing and safe_name(path.stem).casefold() == safe_name(a["name"]).casefold()))]
            paired = candidates[0] if len(candidates) == 1 else None
            for part in parsed["parts"]:
                part["name"] = (f"{paired['name'] if paired else path.stem}: {part['name']} "
                                f"(Ch {part['channel'] + 1})")
                part["source_path"] = asset["path"]
                part["paired_audio"] = paired["path"] if paired else None
            if paired:
                check = midi_alignment(project / paired["path"], parsed["parts"], tempo,
                                       paired["offset_sec"])
            else:
                check = {"status": "needs_review", "reason": "MIDI pairing is ambiguous; supply midi_pairings"}
            check["path"] = asset["path"]
            midi_checks.append(check)
            if check["status"] != "passed":
                issue("midi_alignment", check["reason"], path=path.name)
            parts.extend(parsed["parts"])
        if require_midi and not parts:
            raise ImportError("The Suno export has no usable MIDI note parts")
        note("building_project")
        stems = [a for a in assets if a["role"] == "stem"]
        peak = sum_peak(project, stems)
        gain = min(1.0, 10 ** (-3 / 20) / peak) if peak else 1.0
        built = build_set(project, assets, parts, tempo, meter, gain, template_als_path)
        for row in built["tracks"]:
            if row["role"] == "midi" and not row["preview_instrument"]:
                issue("missing_instrument", "No installed Operator preview preset found", name=row["name"])
        manifest = {"schema_version": 1, "import_id": uuid.uuid4().hex,
                    "source_mode": mode, "sources": source_paths, "excluded": excluded,
                    "assets": assets, "tempo_map": tempo.points, "meter": meter,
                    "pre_roll_sec": pre_roll, "common_gain": gain, "sum_peak": peak,
                    "separation": provenance, **built}
        manifest["set_sha256"] = digest(project / built["set_path"])
        report = {"schema_version": 1, "status": "needs_review", "verification_level": "static",
                  "issues": issues, "grid": grid, "midi": midi_checks,
                  "checks": {"media_integrity": True, "six_stem_completeness":
                             len(stems) == 6 if mode == "separate_recording_6stems" else None,
                             "live_reopened": False, "portable_reopened": False,
                             "save_format_matches_live": False},
                  "pending": ["Open and verify playback, artifact checkpoint/reopen and relocated media in Live"]}
        write_json(project / "import_manifest.json", manifest)
        write_json(project / "validation_report.json", report)
        # Prove all media paths are relative, contained, and byte-identical after collection.
        for asset in assets:
            target = project / asset["path"]
            if not target.resolve().is_relative_to(project.resolve()) or digest(target) != asset["sha256"]:
                raise ImportError("Collected media failed static portability/integrity checks")
            if asset.get("original_path") and digest(project / asset["original_path"]) != asset["original_sha256"]:
                raise ImportError("Original compressed media failed integrity checks")
        (project / "Ableton Project Info").mkdir()
        if destination_path.exists():
            raise ImportError("Destination appeared during import; existing project was preserved")
        project.rename(destination_path)
        note("built")
        return {"status": report["status"], "project_path": str(destination_path),
                "set_path": str(destination_path / built["set_path"]),
                "tracks": [{k: v for k, v in row.items()
                            if k not in {"note_data", "controller_data", "pitch_bend_data"}}
                           for row in built["tracks"]],
                "report_path": str(destination_path / "validation_report.json"),
                "issues": issues, "verification_level": "static"}
    except BaseException as exc:
        if not inspect_only:
            write_json(work / "failure.json", {"error": str(exc), "sources": source_paths})
        raise
    finally:
        # Keep failed staging for diagnosis; completed/inspection staging has no useful media.
        if inspect_only or not project.exists():
            shutil.rmtree(work)
