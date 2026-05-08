"""Auto-configure Claude Desktop and Claude Code to use AbletonFullControlMCP.

This script does an idempotent merge into both clients' config files:

1. **Claude Desktop** — `%APPDATA%\\Claude\\claude_desktop_config.json` on
   Windows, `~/Library/Application Support/Claude/claude_desktop_config.json`
   on macOS, `~/.config/Claude/claude_desktop_config.json` on Linux.
2. **Claude Code (project)** — `<repo_root>/.mcp.json` so any session opened
   in the repo sees the server.
3. **Claude Code (user)** — `%USERPROFILE%\\.claude.json` (or `~/.claude.json`)
   so any session anywhere sees the server, when invoked with `--user`.

The MCP server is registered under the name **`ableton_full_control`**. This
is intentionally distinct from the generic `ableton` name used by other
projects (e.g. ahujasid/ableton-mcp) so users with both installed don't get
confused. As of v0.3.0, this script also automatically migrates older
`"ableton"` entries from previous versions of this project to the new name
(it removes the old entry and writes the new). Pass `--no-migrate` to keep
both entries (only useful if you're testing both projects side-by-side).

Existing entries from OTHER projects are preserved. A `.bak.<timestamp>` is
written next to each file before edits. `--dry-run` shows the diff without
writing.

Usage:
    python -m ableton_mcp.scripts.install_clients          # Desktop + project
    python -m ableton_mcp.scripts.install_clients --user   # also user-level
    python -m ableton_mcp.scripts.install_clients --dry-run
    python -m ableton_mcp.scripts.install_clients --uninstall
    python -m ableton_mcp.scripts.install_clients --no-migrate
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

SERVER_NAME = "ableton_full_control"
LEGACY_SERVER_NAMES = ("ableton",)  # cleaned up by --migrate (default on)


def repo_root() -> Path:
    """Return the repo root (the directory two levels above this file)."""
    return Path(__file__).resolve().parents[3]


def venv_python() -> Path:
    """Return the absolute path to the venv's python.exe (Windows) or python."""
    if sys.platform == "win32":
        return repo_root() / ".venv" / "Scripts" / "python.exe"
    return repo_root() / ".venv" / "bin" / "python"


def claude_desktop_config_path() -> Path:
    """Locate Claude Desktop's config file per OS."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("%APPDATA% not set; can't locate Claude Desktop config")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def project_mcp_json() -> Path:
    return repo_root() / ".mcp.json"


def user_claude_json() -> Path:
    return Path.home() / ".claude.json"


def make_server_block() -> dict:
    """The MCP server entry we install into each client."""
    py = str(venv_python()).replace("\\", "/")
    block: dict = {
        "command": py,
        "args": ["-m", "ableton_mcp"],
    }
    # Claude Code's .mcp.json schema accepts an optional "type" field;
    # Claude Desktop ignores it but doesn't reject it. Keeping it explicit
    # makes both clients route through stdio.
    return block


def with_type(block: dict) -> dict:
    """For Claude Code project config — annotate as stdio."""
    return {"type": "stdio", **block}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} contains invalid JSON ({exc}). Fix or delete the file and rerun."
        ) from exc


def backup(path: Path) -> Path | None:
    """Make a timestamped backup of `path` if it exists. Returns the backup path."""
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{stamp}")
    shutil.copy2(path, bak)
    return bak


def write_json(path: Path, data: dict, dry_run: bool = False) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if dry_run:
        print(f"[dry-run] would write {path}:\n{text}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  wrote {path}")


def merge_into_mcp_servers(
    cfg: dict, server_name: str, server_block: dict, *, remove: bool = False
) -> tuple[dict, str]:
    """Return (new_cfg, action_str) where action_str ∈ {'added','updated','removed','unchanged','skipped'}."""
    cfg = dict(cfg)  # shallow copy
    servers = dict(cfg.get("mcpServers") or {})
    existing = servers.get(server_name)
    if remove:
        if existing is None:
            return cfg, "skipped"
        servers.pop(server_name)
        cfg["mcpServers"] = servers
        return cfg, "removed"
    if existing == server_block:
        return cfg, "unchanged"
    action = "updated" if existing is not None else "added"
    servers[server_name] = server_block
    cfg["mcpServers"] = servers
    return cfg, action


def install_into(
    path: Path,
    server_block: dict,
    *,
    label: str,
    create_if_missing: bool,
    dry_run: bool,
    uninstall: bool,
    migrate_legacy: bool = True,
) -> None:
    print(f"\n{label}: {path}")
    if not path.exists() and not create_if_missing and not uninstall:
        print(f"  client not detected here; skipping")
        return
    cfg = load_json(path)
    actions: list[str] = []
    # Migration: drop legacy server names from older versions of this project.
    if migrate_legacy and not uninstall:
        for legacy in LEGACY_SERVER_NAMES:
            cfg, act = merge_into_mcp_servers(cfg, legacy, {}, remove=True)
            if act == "removed":
                actions.append(f"migrated-out {legacy!r}")
    cfg, act = merge_into_mcp_servers(
        cfg, SERVER_NAME, server_block, remove=uninstall
    )
    actions.append(f"{SERVER_NAME!r}: {act}")
    print(f"  actions: {' | '.join(actions)}")
    # Skip writing only if NOTHING happened (no migration, no install change).
    if act in ("unchanged", "skipped") and not any(a.startswith("migrated") for a in actions):
        return
    bak = backup(path)
    if bak:
        print(f"  backup: {bak.name}")
    write_json(path, cfg, dry_run=dry_run)


def verify_venv() -> None:
    py = venv_python()
    if not py.exists():
        raise SystemExit(
            f"venv python not found at {py}. Run `python -m venv .venv && "
            f".venv\\Scripts\\python.exe -m pip install -e .` from the repo root first."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Configure Claude Desktop + Claude Code for AbletonMCP.")
    ap.add_argument("--dry-run", action="store_true", help="show diffs but don't write")
    ap.add_argument("--user", action="store_true", help="also write to user-level ~/.claude.json")
    ap.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the 'ableton' entry from each config (keeps backups)",
    )
    ap.add_argument(
        "--no-desktop", action="store_true", help="skip Claude Desktop"
    )
    ap.add_argument(
        "--no-project", action="store_true", help="skip the project .mcp.json"
    )
    args = ap.parse_args()

    if not args.uninstall:
        verify_venv()
    block = make_server_block()
    project_block = with_type(block)

    print(f"AbletonMCP repo root: {repo_root()}")
    print(f"venv Python:          {venv_python()}")
    print(f"server name:          {SERVER_NAME!r}")
    if args.dry_run:
        print("(dry-run — no files will be modified)")

    if not args.no_desktop:
        install_into(
            claude_desktop_config_path(),
            block,
            label="Claude Desktop config",
            create_if_missing=True,  # write fresh if missing
            dry_run=args.dry_run,
            uninstall=args.uninstall,
        )

    if not args.no_project:
        install_into(
            project_mcp_json(),
            project_block,
            label="Claude Code project (.mcp.json)",
            create_if_missing=True,
            dry_run=args.dry_run,
            uninstall=args.uninstall,
        )

    if args.user:
        install_into(
            user_claude_json(),
            project_block,
            label="Claude Code user (~/.claude.json)",
            create_if_missing=True,
            dry_run=args.dry_run,
            uninstall=args.uninstall,
        )

    if args.uninstall:
        print("\nDone. Restart Claude Desktop / Claude Code to pick up the change.")
        return

    print("\nDone.")
    print("Next:")
    print("  1) Fully quit and relaunch Claude Desktop (right-click tray -> Quit) AND/OR")
    print("     restart your Claude Code session (/exit and relaunch).")
    print("  2) Ask the assistant: 'use the ableton tool live_ping'.")


if __name__ == "__main__":
    main()
