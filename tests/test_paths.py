"""Tests for paths.data_dir — cwd-independent output locations."""

from __future__ import annotations

from pathlib import Path

import pytest

from ableton_mcp import paths


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ABLETON_MCP_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path / "elsewhere"
    assert paths.data_dir("stems") == tmp_path / "elsewhere" / "stems"


def test_repo_checkout_resolves_to_repo_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABLETON_MCP_DATA_DIR", raising=False)
    d = paths.data_dir()
    assert d.is_absolute()
    # Running from a source checkout: <repo>/data, next to pyproject.toml.
    assert d.name == "data"
    assert (d.parent / "pyproject.toml").is_file()


def test_cwd_independent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ABLETON_MCP_DATA_DIR", raising=False)
    before = paths.data_dir("stems")
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir("stems") == before


def test_no_directory_creation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ABLETON_MCP_DATA_DIR", str(tmp_path / "never_created"))
    _ = paths.data_dir("sub", "deeper")
    assert not (tmp_path / "never_created").exists()
