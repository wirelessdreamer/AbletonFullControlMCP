"""Tests for Library.cfg-based User Library resolution (live_paths)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ableton_mcp.scripts import live_paths


def _write_cfg(version_dir: Path, project_path: str, project_name: str = "User Library") -> None:
    prefs = version_dir / "Preferences"
    prefs.mkdir(parents=True, exist_ok=True)
    (prefs / "Library.cfg").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_11300">
\t<ContentLibrary>
\t\t<UserLibrary>
\t\t\t<LibraryProject Id="1">
\t\t\t\t<ProjectLocation />
\t\t\t\t<ProjectName Value="{project_name}" />
\t\t\t\t<ProjectPath Value="{project_path}" />
\t\t\t</LibraryProject>
\t\t</UserLibrary>
\t</ContentLibrary>
</Ableton>
""",
        encoding="utf-8",
    )


def _make_user_library(path: Path) -> Path:
    for sub in ("Presets", "Defaults", "Clips"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def prefs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "Ableton"
    root.mkdir()
    monkeypatch.setattr(live_paths, "_ableton_prefs_roots", lambda: [root])
    return root


def test_relocated_library_under_project_path(prefs_root: Path, tmp_path: Path) -> None:
    # The on-disk shape this fixes: ProjectPath is the parent, the library is
    # a "User Library" subfolder (e.g. D:/Ableton/Packs + User Library).
    base = tmp_path / "Packs"
    lib = _make_user_library(base / "User Library")
    _write_cfg(prefs_root / "Live 11.3.43", base.as_posix())
    assert live_paths.user_library() == lib
    assert live_paths.user_library_remote_scripts() == lib / "Remote Scripts"


def test_project_path_is_the_library_itself(prefs_root: Path, tmp_path: Path) -> None:
    lib = _make_user_library(tmp_path / "MyLibrary")
    _write_cfg(prefs_root / "Live 11.3.43", lib.as_posix(), project_name="")
    assert live_paths.user_library() == lib


def test_newest_live_version_wins(prefs_root: Path, tmp_path: Path) -> None:
    old_lib = _make_user_library(tmp_path / "old" / "User Library")
    new_lib = _make_user_library(tmp_path / "new" / "User Library")
    _write_cfg(prefs_root / "Live 11.3.9", (tmp_path / "old").as_posix())
    _write_cfg(prefs_root / "Live 11.3.43", (tmp_path / "new").as_posix())
    # "Live Reports" and stray files must not break version scanning.
    (prefs_root / "Live Reports").mkdir()
    assert old_lib != new_lib
    assert live_paths.user_library() == new_lib


def test_malformed_cfg_falls_back_to_regex_scrape(prefs_root: Path, tmp_path: Path) -> None:
    lib = _make_user_library(tmp_path / "Packs" / "User Library")
    prefs = prefs_root / "Live 11.3.43" / "Preferences"
    prefs.mkdir(parents=True)
    (prefs / "Library.cfg").write_text(
        '<Ableton><UserLibrary><LibraryProject>\n'
        f'<ProjectName Value="User Library" />\n'
        f'<ProjectPath Value="{(tmp_path / "Packs").as_posix()}" />\n'
        "</LibraryProject></UserLibrary>\n"
        "&& this trailing junk is not XML <<",
        encoding="utf-8",
    )
    assert live_paths.user_library() == lib


def test_fresh_relocation_without_canonical_folders_still_trusted(
    prefs_root: Path, tmp_path: Path
) -> None:
    lib = tmp_path / "Packs" / "User Library"
    lib.mkdir(parents=True)  # exists, but Live hasn't populated it yet
    _write_cfg(prefs_root / "Live 11.3.43", (tmp_path / "Packs").as_posix())
    assert live_paths.user_library() == lib


def test_no_cfg_falls_back_to_default(prefs_root: Path) -> None:
    assert live_paths.user_library() == live_paths._default_user_library()


def test_cfg_pointing_at_missing_path_falls_back_to_default(
    prefs_root: Path, tmp_path: Path
) -> None:
    _write_cfg(prefs_root / "Live 11.3.43", (tmp_path / "unmounted-drive").as_posix())
    assert live_paths.user_library() == live_paths._default_user_library()


def test_installers_share_the_helper() -> None:
    from ableton_mcp.scripts import install_abletonosc, install_bridge

    assert install_abletonosc.user_library_remote_scripts is live_paths.user_library_remote_scripts
    assert install_bridge.user_library_remote_scripts is live_paths.user_library_remote_scripts
