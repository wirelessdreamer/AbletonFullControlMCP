"""End-to-end smoke test against a running Ableton Live with AbletonOSC enabled.

Run:
    python -m ableton_mcp.scripts.smoke_test
"""

from __future__ import annotations

import asyncio
import sys

from ..config import Config
from ..osc_client import AbletonOSCClient


async def main() -> int:
    cfg = Config.from_env()
    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        print(f"Pinging AbletonOSC at {cfg.osc_host}:{cfg.osc_send_port} ...")
        ok = await client.ping()
        if not ok:
            print("FAIL: AbletonOSC did not reply on /live/test.")
            print("- Confirm Ableton Live is open.")
            print("- Preferences > Link/Tempo/MIDI > Control Surface = AbletonOSC.")
            print("- Make sure no other process is bound to UDP port 11001.")
            return 1
        print("OK: AbletonOSC responded.")

        version = await client.request("/live/application/get/version")
        print(f"Live version: {'.'.join(str(v) for v in version)}")

        n_tracks = (await client.request("/live/song/get/num_tracks"))[0]
        tempo = (await client.request("/live/song/get/tempo"))[0]
        print(f"Set has {n_tracks} tracks at {tempo} BPM.")

        client.send("/live/api/show_message", "AbletonMCP smoke test: hello!")
        print("Sent a hello message to Live's status bar.")
        return 0
    finally:
        await client.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
