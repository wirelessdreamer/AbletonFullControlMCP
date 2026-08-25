"""Absolute, writable locations for the server's output data.

Why this exists: MCP hosts spawn the server with arbitrary working
directories — observed: a protected application directory where
``mkdir("data")`` raises ``PermissionError``, which broke every tool
whose default output was a relative ``data/...`` path (stems_split,
song transpose bounces, generators, preset DB, ...). All defaults now
resolve through :func:`data_dir`, which never depends on the cwd.

Resolution order:

1. ``ABLETON_MCP_DATA_DIR`` environment variable (use this to relocate
   all output in one move — set it in the MCP client config's ``env``).
2. ``<repo>/data`` when the package runs from a source checkout
   (src layout: this file lives at ``<repo>/src/ableton_mcp/paths.py``).
3. ``~/.ableton_mcp/data`` for wheel installs.
"""

from __future__ import annotations

import os
from pathlib import Path


def _base() -> Path:
    env = os.environ.get("ABLETON_MCP_DATA_DIR")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").is_file():
        return repo / "data"
    return Path.home() / ".ableton_mcp" / "data"


def data_dir(*subdirs: str) -> Path:
    """Absolute path under the data base; does NOT create directories.

    Callers keep their existing ``mkdir(parents=True, exist_ok=True)``
    behavior — this function only makes the location cwd-independent.
    """
    p = _base()
    for sub in subdirs:
        p = p / sub
    return p
