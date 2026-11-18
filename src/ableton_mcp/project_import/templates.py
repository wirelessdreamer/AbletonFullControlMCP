"""Local, content-addressed references saved by the user's running Live build."""

from __future__ import annotations

import json
from pathlib import Path

from ..local_automation.als_synth import _load_als_xml
from ..paths import data_dir
from .assets import ImportError, digest


def format_metadata(path: Path) -> dict:
    root = _load_als_xml(str(path))
    protection = root.find("LiveSet/OverwriteProtectionNumber")
    return {**root.attrib, "OverwriteProtectionNumber":
            protection.get("Value") if protection is not None else None}


def selected_template() -> dict | None:
    config = data_dir("project_import_templates", "selected.json")
    if not config.is_file():
        return None
    value = json.loads(config.read_text(encoding="utf-8"))
    path = Path(value["path"])
    if not path.is_file() or digest(path) != value["sha256"]:
        raise ImportError("Cached native template changed or disappeared; select a saved template again")
    return value


def remember_saved_template(path: Path, live_version: str) -> dict:
    """Cache disk scaffolding only; never save or change the active document."""
    path = path.expanduser().resolve()
    raw = path.read_bytes()
    metadata = format_metadata(path)
    if metadata.get("Creator") != f"Ableton Live {live_version}" or not live_version.startswith("11."):
        raise ImportError("Select a set saved by this exact running Live 11 build")
    root = _load_als_xml(str(path))
    if (root.find("LiveSet/Tracks/AudioTrack") is None or root.find("LiveSet/Tracks/MidiTrack") is None
            or root.find("LiveSet/MasterTrack") is None
            or metadata.get("OverwriteProtectionNumber") is None):
        raise ImportError("Saved template needs audio/MIDI/master scaffolding and compatibility metadata")
    folder = data_dir("project_import_templates")
    folder.mkdir(parents=True, exist_ok=True)
    sha = digest(path)
    cached = folder / f"{sha}.als"
    if not cached.exists():
        cached.write_bytes(raw)
    if digest(cached) != sha:
        raise ImportError("Native template cache failed its integrity check")
    value = {"path": str(cached), "source_path": str(path), "sha256": sha,
             "live_version": live_version, "format": metadata}
    temporary = folder / "selected.tmp"
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(folder / "selected.json")
    return value
