"""Copy the AbletonFullControlBridge Live Remote Script into the user's Live User Library.

Usage:
    python -m ableton_mcp.scripts.install_bridge          # install
    python -m ableton_mcp.scripts.install_bridge --force  # overwrite existing
    python -m ableton_mcp.scripts.install_bridge --dry-run

Mirror of `install_abletonosc.py` but copies a local directory shipped with this
package (no network round-trip) into:

    Windows:   %USERPROFILE%\\Documents\\Ableton\\User Library\\Remote Scripts\\AbletonFullControlBridge
    macOS:     ~/Music/Ableton/User Library/Remote Scripts/AbletonFullControlBridge
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .install_abletonosc import user_library_remote_scripts


def _bridge_source_dir() -> Path:
    """Locate the AbletonFullControlBridge source dir shipped with this repo."""
    # repo layout: <root>/live_remote_script/AbletonFullControlBridge
    here = Path(__file__).resolve()
    # src/ableton_mcp/scripts/install_bridge.py → repo root is parents[3]
    repo_root = here.parents[3]
    src = repo_root / "live_remote_script" / "AbletonFullControlBridge"
    if not src.is_dir():
        raise FileNotFoundError(
            f"Could not find AbletonFullControlBridge source at {src}. "
            "If you installed from a wheel, clone the repo and re-run from there."
        )
    return src


def install(force: bool = False, dry_run: bool = False) -> Path:
    rs_dir = user_library_remote_scripts()
    target = rs_dir / "AbletonFullControlBridge"
    src = _bridge_source_dir()

    # v0.3.0 hard rename: clean up the legacy AbletonMCPBridge folder if present.
    legacy = rs_dir / "AbletonMCPBridge"
    if legacy.exists():
        if dry_run:
            print(f"DRY RUN: would remove legacy {legacy}")
        else:
            print(f"Removing legacy AbletonMCPBridge at {legacy}")
            shutil.rmtree(legacy)
            print("  Note: open Live -> Preferences -> Link/Tempo/MIDI and replace")
            print("        AbletonMCPBridge with AbletonFullControlBridge in the dropdown.")

    if target.exists():
        if not force:
            print(f"AbletonFullControlBridge already at {target}. Re-run with --force to overwrite.")
            return target
        if not dry_run:
            print(f"Removing existing {target}")
            shutil.rmtree(target)

    if dry_run:
        print(f"DRY RUN: would copy {src} -> {target}")
        return target

    rs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Installed AbletonFullControlBridge to {target}")
    print()
    print("Next:")
    print("  1. Open Ableton Live → Preferences → Link/Tempo/MIDI →")
    print("     Control Surface dropdown → choose AbletonFullControlBridge.")
    print("  2. You should see a status-bar message confirming it's listening on TCP/11002.")
    print("  3. AbletonFullControlBridge runs ALONGSIDE AbletonOSC — keep both control surfaces enabled.")
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description="Install AbletonFullControlBridge Remote Script.")
    ap.add_argument("--force", action="store_true", help="overwrite existing install")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen")
    args = ap.parse_args()
    try:
        install(force=args.force, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
