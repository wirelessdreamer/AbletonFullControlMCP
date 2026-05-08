"""Download AbletonOSC and copy it into the Ableton User Library Remote Scripts folder.

AbletonOSC is a third-party Live Remote Script:

  - Project: https://github.com/ideoforms/AbletonOSC
  - Author:  Daniel Jones (@ideoforms)
  - Licence: BSD-3-Clause
  - Paper:   "AbletonOSC: A unified control API for Ableton Live", NIME 2023

We download it from upstream at install time and write it unmodified into
the user's Live User Library. We do not vendor or fork the source. See
NOTICE.md at the repo root for the full third-party attribution.

Usage:
    python -m ableton_mcp.scripts.install_abletonosc           # latest master
    python -m ableton_mcp.scripts.install_abletonosc --ref 1.2 # tag/branch/commit

Manual fallback (if you'd rather):
    git clone https://github.com/ideoforms/AbletonOSC.git \
        "%USERPROFILE%/Documents/Ableton/User Library/Remote Scripts/AbletonOSC"
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen


def user_library_remote_scripts() -> Path:
    """Return the OS-correct Ableton User Library Remote Scripts directory."""
    if sys.platform == "win32":
        userprofile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        return userprofile / "Documents" / "Ableton" / "User Library" / "Remote Scripts"
    if sys.platform == "darwin":
        return Path.home() / "Music" / "Ableton" / "User Library" / "Remote Scripts"
    # Linux is unsupported by Ableton officially; assume the same layout under HOME.
    return Path.home() / "Ableton" / "User Library" / "Remote Scripts"


def download_zip(ref: str = "master") -> bytes:
    url = f"https://github.com/ideoforms/AbletonOSC/archive/refs/heads/{ref}.zip"
    print(f"Downloading {url} ...")
    req = Request(url, headers={"User-Agent": "ableton-mcp-installer/0.1"})
    with urlopen(req) as resp:
        return resp.read()


def install(ref: str = "master", force: bool = False) -> Path:
    rs_dir = user_library_remote_scripts()
    target = rs_dir / "AbletonOSC"
    if target.exists():
        if not force:
            print(f"AbletonOSC already exists at {target}. Re-run with --force to overwrite.")
            return target
        print(f"Removing existing {target}")
        shutil.rmtree(target)
    rs_dir.mkdir(parents=True, exist_ok=True)
    data = download_zip(ref)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Top-level dir in archive is "AbletonOSC-<ref>". Extract to a temp,
        # then move to AbletonOSC/.
        tmp = rs_dir / "_abletonosc_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        zf.extractall(tmp)
        # Find the single subdir.
        subdirs = [p for p in tmp.iterdir() if p.is_dir()]
        if len(subdirs) != 1:
            raise RuntimeError(f"Unexpected archive layout: {[p.name for p in subdirs]}")
        shutil.move(str(subdirs[0]), str(target))
        shutil.rmtree(tmp)
    print(f"Installed AbletonOSC to {target}")
    print()
    print("AbletonOSC is a third-party project by Daniel Jones (BSD-3-Clause).")
    print("We install it unmodified from its upstream GitHub. AbletonFullControlMCP")
    print("uses it as the OSC transport for ~70% of our tools, alongside our own")
    print("AbletonFullControlBridge Remote Script which fills the gaps.")
    print()
    print("Next:")
    print("  1. Open Ableton Live -> Preferences -> Link/Tempo/MIDI ->")
    print("     Control Surface dropdown -> choose AbletonOSC.")
    print("  2. Run install_bridge to install AbletonFullControlBridge alongside.")
    print("  3. Run `python -m ableton_mcp.scripts.smoke_test` to verify.")
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description="Install AbletonOSC into the Live User Library.")
    ap.add_argument("--ref", default="master", help="git ref (branch/tag) to download")
    ap.add_argument("--force", action="store_true", help="overwrite existing install")
    args = ap.parse_args()
    install(ref=args.ref, force=args.force)


if __name__ == "__main__":
    main()
