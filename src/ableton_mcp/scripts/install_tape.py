"""Copy the AbletonFullControlTape Max for Live device into Live's User Library.

Usage:
    python -m ableton_mcp.scripts.install_tape          # install
    python -m ableton_mcp.scripts.install_tape --force  # overwrite existing
    python -m ableton_mcp.scripts.install_tape --dry-run

Mirror of `install_bridge.py` but writes the M4L tape device into the User
Library Max Audio Effects folder so Live's browser shows it under
User Library -> Presets -> Audio Effects -> Max Audio Effect.

    Windows: %USERPROFILE%\\Documents\\Ableton\\User Library\\Presets\\Audio Effects\\Max Audio Effect\\AbletonFullControlTape
    macOS:   ~/Music/Ableton/User Library/Presets/Audio Effects/Max Audio Effect/AbletonFullControlTape

Important: this ships the .maxpat (a Max patcher JSON) plus README + PROTOCOL.
The user must open it in Max for Live ONCE — File -> Save As Device... -> save
as AbletonFullControlTape.amxd in the same folder — to compile the .amxd. Live's
browser only loads .amxd, not .maxpat.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .install_abletonosc import user_library_remote_scripts


def user_library_max_audio_effects() -> Path:
    """Return the OS-correct User Library Max Audio Effect folder."""
    # user_library_remote_scripts() returns ".../User Library/Remote Scripts";
    # walk up one level to reach "User Library".
    rs = user_library_remote_scripts()
    user_library = rs.parent
    return user_library / "Presets" / "Audio Effects" / "Max Audio Effect"


def _tape_source_dir() -> Path:
    """Locate the AbletonFullControlTape source dir shipped with this repo."""
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    src = repo_root / "live_max_for_live" / "AbletonFullControlTape"
    if not src.is_dir():
        raise FileNotFoundError(
            f"Could not find AbletonFullControlTape source at {src}. "
            "If you installed from a wheel, clone the repo and re-run from there."
        )
    return src


def install(force: bool = False, dry_run: bool = False) -> Path:
    dst_root = user_library_max_audio_effects()
    target = dst_root / "AbletonFullControlTape"
    src = _tape_source_dir()

    # v0.3.0 hard rename: clean up the legacy AbletonMCPTape folder if present.
    legacy = dst_root / "AbletonMCPTape"
    if legacy.exists():
        if dry_run:
            print(f"DRY RUN: would remove legacy {legacy}")
        else:
            print(f"Removing legacy AbletonMCPTape at {legacy}")
            shutil.rmtree(legacy)
            print("  Note: any saved AbletonMCPTape.amxd in old projects will need")
            print("        to be replaced with the new AbletonFullControlTape.amxd.")

    if target.exists():
        if not force:
            print(f"AbletonFullControlTape already at {target}. Re-run with --force to overwrite.")
            return target
        if not dry_run:
            print(f"Removing existing {target}")
            shutil.rmtree(target)

    if dry_run:
        print(f"DRY RUN: would copy {src} -> {target}")
        return target

    dst_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target)
    print(f"Installed AbletonFullControlTape to {target}")
    print()
    print("Next steps:")
    print("  1. Open Ableton Live. In the browser, navigate to:")
    print("     User Library -> Presets -> Audio Effects -> Max Audio Effect -> AbletonFullControlTape")
    print("  2. Drag AbletonFullControlTape.maxpat onto a track. Max for Live opens.")
    print("  3. In Max: File -> Save As Device... -> save as AbletonFullControlTape.amxd")
    print("     in the same folder. This compiles the patcher to a Live device.")
    print("  4. From Python: tape_ping() should now return True.")
    print()
    print("If anything breaks, see PROTOCOL.md for the OSC wire format and the")
    print("manual-build fallback in README.md (drop ~10 objects onto a fresh")
    print("Max Audio Effect and connect them as documented).")
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description="Install AbletonFullControlTape M4L device.")
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
