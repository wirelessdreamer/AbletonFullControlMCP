"""Locate the Ableton Live User Library, honouring a relocated library.

Live lets users move their User Library (Preferences -> Library -> Location of
User Library). Installing Remote Scripts into the stock
``~/Documents/Ableton/User Library`` when the library was relocated puts them
in a folder Live never scans, so they silently never appear in the Control
Surface dropdown.

The configured location is stored in ``Library.cfg``, an XML preferences file:

    Windows: %APPDATA%/Ableton/Live <version>/Preferences/Library.cfg
    macOS:   ~/Library/Preferences/Ableton/Live <version>/Library.cfg

Inside it, ``<UserLibrary><LibraryProject>`` holds a ``<ProjectPath>`` (base
folder) and usually a ``<ProjectName>`` (e.g. "User Library"); the actual
User Library is typically ``<ProjectPath>/<ProjectName>``. We verify
candidates by their canonical folder structure (Presets/Defaults/Clips) and
fall back to the stock Documents location when no cfg is found.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Folders Live creates inside every User Library; used to sanity-check that a
# candidate path really is one.
_CANONICAL_SUBDIRS = ("Presets", "Defaults", "Clips", "Remote Scripts", "Samples")

_LIVE_DIR_RE = re.compile(r"^Live (\d+(?:\.\d+)*)")


def _ableton_prefs_roots() -> list[Path]:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "Ableton"] if appdata else []
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Preferences" / "Ableton"]
    return []


def _library_cfg_candidates(root: Path) -> list[Path]:
    """Return Library.cfg paths under one prefs root, newest Live version first."""
    versions: list[tuple[tuple[int, ...], Path]] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        m = _LIVE_DIR_RE.match(entry.name)
        if not entry.is_dir() or m is None:
            continue
        key = tuple(int(part) for part in m.group(1).split("."))
        versions.append((key, entry))
    cfgs: list[Path] = []
    for _, version_dir in sorted(versions, reverse=True):
        for cfg in (
            version_dir / "Preferences" / "Library.cfg",  # Windows
            version_dir / "Library.cfg",  # macOS
        ):
            if cfg.is_file():
                cfgs.append(cfg)
    return cfgs


def _parse_library_cfg(cfg: Path) -> tuple[str, str] | None:
    """Return (project_path, project_name) from Library.cfg, or None."""
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    path_value: str | None = None
    name_value = ""
    try:
        project = ET.fromstring(text).find(".//UserLibrary/LibraryProject")
        if project is not None:
            path_el = project.find("ProjectPath")
            name_el = project.find("ProjectName")
            path_value = path_el.get("Value") if path_el is not None else None
            name_value = (name_el.get("Value") or "") if name_el is not None else ""
    except ET.ParseError:
        # The file is only mostly-XML in some Live versions; scrape the two
        # attributes we need instead.
        m = re.search(r"<ProjectPath\s+Value=\"([^\"]*)\"", text)
        path_value = m.group(1) if m else None
        m = re.search(r"<ProjectName\s+Value=\"([^\"]*)\"", text)
        name_value = m.group(1) if m else ""
    if not path_value:
        return None
    return path_value, name_value


def _looks_like_user_library(path: Path) -> bool:
    try:
        return path.is_dir() and any((path / sub).is_dir() for sub in _CANONICAL_SUBDIRS)
    except OSError:
        return False


def _user_library_from_cfg() -> Path | None:
    for root in _ableton_prefs_roots():
        for cfg in _library_cfg_candidates(root):
            parsed = _parse_library_cfg(cfg)
            if parsed is None:
                continue
            base_str, name = parsed
            base = Path(base_str)
            candidates: list[Path] = []
            for candidate in (
                base / name if name else None,  # e.g. D:/Ableton/Packs + "User Library"
                base,  # ProjectPath may already be the library itself
                base / "User Library",
            ):
                if candidate is not None and candidate not in candidates:
                    candidates.append(candidate)
            for candidate in candidates:
                if _looks_like_user_library(candidate):
                    return candidate
            # cfg points somewhere real but the canonical folders aren't there
            # yet (fresh relocation): still trust it over the Documents guess.
            for candidate in candidates:
                if candidate.is_dir():
                    return candidate
    return None


def _default_user_library() -> Path:
    if sys.platform == "win32":
        userprofile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        return userprofile / "Documents" / "Ableton" / "User Library"
    if sys.platform == "darwin":
        return Path.home() / "Music" / "Ableton" / "User Library"
    # Linux is unsupported by Ableton officially; assume the same layout under HOME.
    return Path.home() / "Ableton" / "User Library"


def user_library() -> Path:
    """Return the Live User Library, preferring the location in Library.cfg."""
    return _user_library_from_cfg() or _default_user_library()


def user_library_remote_scripts() -> Path:
    """Return the Remote Scripts directory inside the Live User Library."""
    return user_library() / "Remote Scripts"
