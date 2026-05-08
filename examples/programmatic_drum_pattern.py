"""Build a 4-bar kick/snare/hat MIDI clip on a brand-new track.

This script bypasses the MCP layer entirely and drives ``AbletonOSCClient``
directly. It demonstrates what the higher-level tools (``track_create_midi``,
``clip_create_midi``, ``clip_add_notes``) reduce to under the hood.

Run::

    D:\\Code\\AbletonMCP\\.venv\\Scripts\\python.exe -m examples.programmatic_drum_pattern
    # or
    D:\\Code\\AbletonMCP\\.venv\\Scripts\\python.exe examples/programmatic_drum_pattern.py

Prerequisites: Ableton Live is open and AbletonOSC is the active control
surface. The script creates one new track at the end of the current set;
delete it after if you don't want it.
"""

from __future__ import annotations

import asyncio

# These imports work whether you run with `python -m examples.programmatic_drum_pattern`
# from the repo root, or invoke the script path directly (provided the venv
# has the package installed editable).
from ableton_mcp.config import Config
from ableton_mcp.osc_client import AbletonOSCClient


# General MIDI drum pitches.
KICK = 36   # C1
SNARE = 38  # D1
HAT = 42    # F#1


def _drum_pattern(bars: int = 4) -> list[tuple[int, float, float, int]]:
    """Build a flat list of (pitch, start_beats, duration_beats, velocity) tuples.

    - Kick: every quarter note.
    - Snare: on beats 2 and 4 of every bar (zero-indexed: 1, 3, 5, 7, ...).
    - Closed hat: every 8th note, with offbeats softer than on-beats.
    """
    notes: list[tuple[int, float, float, int]] = []
    total_beats = bars * 4
    for beat in range(total_beats):
        notes.append((KICK, float(beat), 0.25, 110))
        if beat % 2 == 1:  # 2 and 4 in 4/4
            notes.append((SNARE, float(beat), 0.25, 100))
    half = 0.0
    while half < total_beats:
        is_onbeat = (half % 1.0) == 0.0
        notes.append((HAT, half, 0.125, 95 if is_onbeat else 55))
        half += 0.5
    return notes


async def _wait_for_track(client: AbletonOSCClient, expected_n: int, timeout: float = 2.0) -> int:
    """Poll until ``num_tracks`` reaches ``expected_n``. Returns the new index."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        n = int((await client.request("/live/song/get/num_tracks"))[0])
        if n >= expected_n:
            return n - 1
        if asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(f"track count never reached {expected_n} (still {n})")
        await asyncio.sleep(0.05)


async def main() -> int:
    cfg = Config.from_env()
    client = AbletonOSCClient(cfg)
    await client.start()
    try:
        if not await client.ping():
            print("AbletonOSC did not reply on /live/test.")
            print("Is Live open with AbletonOSC enabled in Preferences?")
            return 1

        # Snapshot current state.
        n_before = int((await client.request("/live/song/get/num_tracks"))[0])
        print(f"Current set has {n_before} tracks.")

        # Create a MIDI track at the end and rename it.
        client.send("/live/song/create_midi_track", -1)
        new_index = await _wait_for_track(client, n_before + 1)
        client.send("/live/track/set/name", new_index, "Drums (programmatic)")
        print(f"Created MIDI track at index {new_index}.")

        # Empty 4-bar clip in slot 0.
        clip_index = 0
        client.send("/live/clip_slot/create_clip", new_index, clip_index, 16.0)
        client.send("/live/clip/set/name", new_index, clip_index, "Boom Bap")
        client.send("/live/clip/set/loop_start", new_index, clip_index, 0.0)
        client.send("/live/clip/set/loop_end", new_index, clip_index, 16.0)

        # Pack notes into AbletonOSC's "/live/clip/add/notes" wire format:
        # [track_id, clip_id, pitch, start, duration, velocity, mute, ...]
        notes = _drum_pattern(bars=4)
        flat: list = [new_index, clip_index]
        for pitch, start, duration, velocity in notes:
            flat.extend([pitch, start, duration, velocity, 0])
        client.send("/live/clip/add/notes", *flat)
        print(f"Added {len(notes)} notes (kick + snare + hats).")

        # Quick sanity read-back.
        result = await client.request(
            "/live/clip/get/notes", new_index, clip_index
        )
        readback = (len(result) - 2) // 5  # (track_id, clip_id, pitch, start, dur, vel, mute)*N
        print(f"Read-back: clip now contains {readback} notes.")

        # Fire it.
        client.send("/live/clip/fire", new_index, clip_index)
        client.send("/live/song/start_playing")
        client.send("/live/api/show_message", "AbletonMCP example: drum loop firing.")
        print("Loop fired. Stop transport in Live when you're done.")
        return 0
    finally:
        await client.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
