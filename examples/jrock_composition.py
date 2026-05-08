"""J-rock instrumental in 6/8, 144 BPM, 35 bars (~44 sec), E minor.

Tracks: Drums / Bass / Rhythm Guitar / Lead Guitar / Piano.
Structure: intro (4) → main groove A (8) → breakdown (6) → piano interlude (6) → buildup (3) → final groove (8).

Used as both the working "automation test" demo and a worked example of
driving Live end-to-end without going through an MCP client.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from ableton_mcp.bridge_client import (
    AbletonBridgeError,
    AbletonBridgeUnavailable,
    get_bridge_client,
)
from ableton_mcp.osc_client import get_client


# --- GM drum kit ---
KICK = 36
SNARE = 38
HAT_C = 42
HAT_O = 46
CRASH = 49
RIDE = 51
TOM_LO = 41

# --- Chords (E minor key, common J-rock i-VI-III-VII) ---
CHORDS = ["Em", "C", "G", "D"]

# Bass roots (E2/C2/G2/D2 register — comfortable bass-guitar range)
BASS_ROOT = {"Em": 40, "C": 36, "G": 43, "D": 38}

# Power-chord-style guitar voicings, mostly E3-E4 register
RHY_VOICING = {
    "Em": [40, 47, 52],   # E2, B2, E3
    "C":  [48, 55, 60],   # C3, G3, C4
    "G":  [43, 50, 55],   # G2, D3, G3
    "D":  [50, 57, 62],   # D3, A3, D4
}

# Piano voicings — bass + mid + upper
PIANO_VOICING = {
    "Em": [40, 55, 59, 64],   # E2 G3 B3 E4
    "C":  [36, 52, 60, 64],   # C2 E3 C4 E4
    "G":  [43, 50, 55, 62],   # G2 D3 G3 D4
    "D":  [38, 50, 57, 62],   # D2 D3 A3 D4
    "Am": [33, 52, 57, 60],   # A1 E3 A3 C4
}


def n(pitch: int, start: float, dur: float, vel: int = 100, mute: bool = False) -> dict[str, Any]:
    return {
        "pitch": int(pitch),
        "start": float(start),
        "duration": float(dur),
        "velocity": int(max(1, min(127, vel))),
        "mute": bool(mute),
    }


def compose() -> tuple[list, list, list, list, list]:
    drums: list = []
    bass: list = []
    rhythm: list = []
    lead: list = []
    piano: list = []

    # --- INTRO bars 0-3 ---
    # Sparse drums, single sustained bass, clean guitar arpeggio, no lead/piano.
    for bar in range(4):
        b = bar * 3.0
        drums.append(n(KICK, b, 0.25, 95))
        drums.append(n(SNARE, b + 1.5, 0.25, 75))
        for e in range(6):
            drums.append(n(HAT_C, b + e * 0.5, 0.25, 55 + e * 2))
        if bar == 0:
            drums.append(n(CRASH, b, 0.5, 90))
        bass.append(n(40, b, 3.0, 65))  # E2 pedal
        # rolling clean arpeggio E - B - E - G - B - E
        arp = [40, 47, 52, 55, 59, 64]
        for i, p in enumerate(arp):
            rhythm.append(n(p, b + i * 0.5, 0.45, 60))

    # --- MAIN GROOVE A bars 4-11 (Em C G D, 2 bars each) ---
    for bar in range(4, 12):
        b = bar * 3.0
        rel = bar - 4
        chord = CHORDS[rel // 2]

        if rel == 0:
            drums.append(n(CRASH, b, 0.5, 115))
        drums.append(n(KICK, b, 0.25, 115))
        drums.append(n(KICK, b + 1.0, 0.25, 95))
        drums.append(n(SNARE, b + 1.5, 0.25, 110))
        drums.append(n(KICK, b + 2.0, 0.25, 90))
        for e in range(6):
            drums.append(n(HAT_C, b + e * 0.5, 0.25, 75))
        # accent open hat at end of every other bar
        if rel % 2 == 1:
            drums.append(n(HAT_O, b + 2.5, 0.25, 85))

        root = BASS_ROOT[chord]
        bass.append(n(root, b, 1.5, 105))
        bass.append(n(root, b + 1.5, 1.5, 100))

        for p in RHY_VOICING[chord]:
            rhythm.append(n(p, b, 1.5, 95))
            rhythm.append(n(p, b + 1.5, 1.5, 95))

        # Lead — anthemic pentatonic line (E minor pentatonic + some tension)
        lead_per_chord = {
            "Em": [(76, 0.0, 0.4), (74, 0.5, 0.4), (71, 1.0, 0.4),
                   (76, 1.5, 0.4), (79, 2.0, 0.5), (76, 2.5, 0.4)],
            "C":  [(72, 0.0, 0.4), (76, 0.5, 0.4), (79, 1.0, 0.5),
                   (76, 1.5, 0.4), (72, 2.0, 0.4), (74, 2.5, 0.4)],
            "G":  [(74, 0.0, 0.4), (71, 0.5, 0.4), (74, 1.0, 0.4),
                   (79, 1.5, 0.5), (78, 2.0, 0.4), (74, 2.5, 0.4)],
            "D":  [(74, 0.0, 0.4), (78, 0.5, 0.4), (81, 1.0, 0.5),
                   (78, 1.5, 0.4), (74, 2.0, 0.4), (76, 2.5, 0.5)],
        }
        for pitch, off, dur in lead_per_chord[chord]:
            lead.append(n(pitch, b + off, dur, 95))

        # Piano — chord stab on each felt-pulse
        for p in PIANO_VOICING[chord]:
            piano.append(n(p, b, 0.45, 75))
            piano.append(n(p, b + 1.5, 0.45, 70))

    # --- BREAKDOWN bars 12-17 (6 bars: Em Em C C G G) ---
    breakdown_chords = ["Em", "Em", "C", "C", "G", "G"]
    for bar in range(12, 18):
        b = bar * 3.0
        rel = bar - 12
        chord = breakdown_chords[rel]
        # Drums minimal — kick on 1, snare on 4, occasional open hat
        drums.append(n(KICK, b, 0.25, 90))
        drums.append(n(SNARE, b + 1.5, 0.25, 80))
        if rel % 2 == 1:
            drums.append(n(HAT_O, b + 2.5, 0.6, 75))
        # sparse hats
        drums.append(n(HAT_C, b, 0.2, 50))
        drums.append(n(HAT_C, b + 1.5, 0.2, 50))
        # Bass — sustained root (let it ring)
        root = BASS_ROOT[chord]
        bass.append(n(root, b, 3.0, 75))
        # Rhythm — palm-muted 8th note picking on root
        for e in range(6):
            rhythm.append(n(RHY_VOICING[chord][0], b + e * 0.5, 0.35, 55))
        # Piano — quiet, single bass note pad
        piano.append(n(PIANO_VOICING[chord][0], b, 3.0, 40))
        # Lead silent

    # --- PIANO INTERLUDE bars 18-23 (6 bars, piano only) ---
    interlude_chords = ["Em", "C", "G", "D", "Am", "Em"]
    for bar in range(18, 24):
        b = bar * 3.0
        rel = bar - 18
        chord = interlude_chords[rel]
        voicing = PIANO_VOICING[chord]
        # Bass note + sustained mid voicing
        piano.append(n(voicing[0], b, 3.0, 55))
        piano.append(n(voicing[1], b, 3.0, 50))
        # Right-hand melodic line — descending E minor pentatonic
        # bar offsets: E5 (76) → D5 (74) → B4 (71) → A4 (69) → G4 (67) → E5 (76 final lift)
        rh_melody = [76, 74, 71, 69, 67, 76]
        rh_pitch = rh_melody[rel]
        # phrase: pitch on 1, neighbour on 2.5, sustain
        piano.append(n(rh_pitch, b + 0.0, 1.3, 75))
        piano.append(n(rh_pitch + 2 if rel < 5 else rh_pitch, b + 1.5, 0.4, 65))
        piano.append(n(rh_pitch - 3, b + 2.0, 0.5, 60))
        piano.append(n(rh_pitch, b + 2.5, 0.5, 70))

    # --- BUILDUP bars 24-26 (3 bars on D — V chord, building tension) ---
    for bar in range(24, 27):
        b = bar * 3.0
        rel = bar - 24
        # Drums — coming back, intensifying
        drums.append(n(KICK, b, 0.25, 90 + rel * 8))
        if rel >= 1:
            drums.append(n(SNARE, b + 1.5, 0.25, 85 + rel * 8))
        for e in range(6):
            drums.append(n(HAT_C, b + e * 0.5, 0.25, 60 + rel * 10))
        # Bar 26 — sixteenth-note snare roll for the lift
        if rel == 2:
            for s in range(12):
                drums.append(n(SNARE, b + s * 0.25, 0.18, 65 + s * 4))
            drums.append(n(CRASH, b + 2.75, 0.5, 110))
        # Bass — returns gradually
        if rel >= 1:
            bass.append(n(BASS_ROOT["D"], b, 1.5, 90 + rel * 5))
            bass.append(n(BASS_ROOT["D"], b + 1.5, 1.5, 90 + rel * 5))
        # Rhythm — sustained D power chord
        if rel >= 1:
            for p in RHY_VOICING["D"]:
                rhythm.append(n(p, b, 3.0, 80 + rel * 8))
        # Piano — fading
        if rel < 2:
            for p in PIANO_VOICING["D"][:2]:
                piano.append(n(p, b, 3.0, 50 - rel * 15))
        # Lead — ascending pickup notes in bar 26 (last bar of buildup)
        if rel == 2:
            pickup = [69, 71, 74, 76, 78]
            for i, p in enumerate(pickup):
                lead.append(n(p, b + 1.5 + i * 0.3, 0.3, 85 + i * 6))

    # --- FINAL GROOVE bars 27-34 (8 bars Em C G D, climactic) ---
    for bar in range(27, 35):
        b = bar * 3.0
        rel = bar - 27
        chord = CHORDS[rel // 2]

        if rel == 0:
            drums.append(n(CRASH, b, 0.6, 125))
        drums.append(n(KICK, b, 0.25, 122))
        drums.append(n(KICK, b + 1.0, 0.25, 105))
        drums.append(n(SNARE, b + 1.5, 0.25, 120))
        drums.append(n(KICK, b + 2.0, 0.25, 105))
        drums.append(n(SNARE, b + 2.5, 0.25, 95))  # ghost snare
        for e in range(6):
            drums.append(n(HAT_C, b + e * 0.5, 0.25, 90))
        # Crash on bar 31 (4th bar) and bar 34 (final)
        if rel == 3 or rel == 7:
            drums.append(n(CRASH, b + 1.5, 0.6, 115))

        root = BASS_ROOT[chord]
        bass.append(n(root, b, 1.5, 115))
        bass.append(n(root, b + 1.5, 1.5, 110))

        for p in RHY_VOICING[chord]:
            rhythm.append(n(p, b, 3.0, 110))

        # Lead — anthemic, octave higher than main groove
        anthem = {
            "Em": [(76, 0.0, 0.5), (79, 0.5, 0.5), (83, 1.0, 0.5),
                   (79, 1.5, 0.5), (76, 2.0, 0.5), (74, 2.5, 0.4)],
            "C":  [(72, 0.0, 0.5), (76, 0.5, 0.5), (79, 1.0, 0.5),
                   (84, 1.5, 0.5), (79, 2.0, 0.5), (76, 2.5, 0.4)],
            "G":  [(74, 0.0, 0.5), (79, 0.5, 0.5), (83, 1.0, 0.5),
                   (86, 1.5, 0.5), (83, 2.0, 0.5), (79, 2.5, 0.4)],
            "D":  [(78, 0.0, 0.5), (81, 0.5, 0.5), (86, 1.0, 0.5),
                   (81, 1.5, 0.5), (78, 2.0, 0.5), (76, 2.5, 0.4)],
        }
        for pitch, off, dur in anthem[chord]:
            lead.append(n(pitch, b + off, dur, 108))

        for p in PIANO_VOICING[chord]:
            piano.append(n(p, b, 0.45, 95))
            piano.append(n(p, b + 1.5, 0.45, 90))

    # Final hit — let the Em chord ring
    final_b = 35 * 3.0 - 0.25
    drums.append(n(CRASH, 102.0, 1.0, 127))
    return drums, bass, rhythm, lead, piano


async def try_load_instruments(bridge, track_indices: dict[str, int]) -> dict[str, str]:
    """Best-effort load of stock instruments. Returns {track_name: status}.

    Bridge contract reminders:
      - Use op `browser.load_device` (NOT `browser.load`).
      - `browser.search` reply has shape {query, category, count, results: [...]}
        — the list lives under `results`, not `items`.
      - `category` is optional; when omitted, the query walks every category.
        We pick a sensible category per instrument so e.g. "707 Core Kit"
        resolves to the drum-rack preset under drums/, not a same-named
        clip somewhere else.
    """
    statuses: dict[str, str] = {}
    # (track_name, query, category, prefer_exact_name)
    plan = [
        ("Drums", "707 Core Kit", "drums", True),
        ("Bass", "808 Pure", "instruments", False),     # 808 Pure.adg is a Drift bass preset
        ("Rhythm Guitar", "Drift", "instruments", True),
        ("Lead Guitar", "Drift", "instruments", True),
        ("Piano", "Operator", "instruments", True),
    ]
    osc = await get_client()
    for track_name, query, category, prefer_exact in plan:
        track_idx = track_indices[track_name]
        # Skip if the track already has a device (re-run safety).
        try:
            n_devices = int(
                (await osc.request("/live/track/get/num_devices", track_idx))[1]
            )
        except Exception:
            n_devices = 0
        if n_devices > 0:
            statuses[track_name] = f"already has {n_devices} device(s); skipped"
            continue
        try:
            kwargs: dict[str, Any] = {"query": query}
            if category:
                kwargs["category"] = category
            reply = await bridge.call("browser.search", **kwargs)
            results = reply.get("results", []) if isinstance(reply, dict) else []
            if not results:
                statuses[track_name] = f"no browser hits for '{query}' in '{category or 'any'}'"
                continue
            # Pick exact name match if available, else first loadable.
            chosen = None
            if prefer_exact:
                chosen = next(
                    (it for it in results
                     if it.get("name", "").strip() == query and it.get("is_loadable")),
                    None,
                )
            if chosen is None:
                chosen = next((it for it in results if it.get("is_loadable")), None)
            if chosen is None:
                statuses[track_name] = f"no loadable hit for '{query}'"
                continue
            uri = chosen.get("uri")
            if not uri:
                statuses[track_name] = f"no URI on top hit for '{query}'"
                continue
            # Use load_drum_kit for drum-category items (semantically the same
            # under the hood; keeps Live's browser context tidy).
            op = "browser.load_drum_kit" if category == "drums" else "browser.load_device"
            await bridge.call(op, uri=uri, track_index=track_idx)
            statuses[track_name] = f"loaded {chosen.get('name', query)}"
        except (AbletonBridgeUnavailable, AbletonBridgeError, KeyError, TypeError) as e:
            statuses[track_name] = f"skip ({type(e).__name__}: {e})"
    return statuses


async def main() -> None:
    osc = await get_client()
    bridge = get_bridge_client()

    if not await osc.ping():
        print("ERROR: AbletonOSC not responding.", file=sys.stderr)
        sys.exit(1)

    # 1. Tempo + signature
    osc.send("/live/song/set/tempo", 144.0)
    await asyncio.sleep(0.1)
    osc.send("/live/song/set/signature_numerator", 6)
    osc.send("/live/song/set/signature_denominator", 8)
    await asyncio.sleep(0.2)
    print("Set tempo=144, signature=6/8")

    # 2. Resolve track indices — reuse existing tracks named the same, create new ones otherwise
    track_names = ["Drums", "Bass", "Rhythm Guitar", "Lead Guitar", "Piano"]
    existing_names = list(await osc.request("/live/song/get/track_names"))
    track_indices: dict[str, int] = {}
    for name in track_names:
        if name in existing_names:
            track_indices[name] = existing_names.index(name)
        else:
            osc.send("/live/song/create_midi_track", -1)
            await asyncio.sleep(0.2)
            n_total = int((await osc.request("/live/song/get/num_tracks"))[0])
            idx = n_total - 1
            track_indices[name] = idx
            existing_names.append(name)  # so subsequent ones see it
            osc.send("/live/track/set/name", idx, name)
            await asyncio.sleep(0.05)
    print(f"Tracks: {track_indices}")

    # 3. Try to load instruments (best-effort; continues on failure)
    print("Attempting to load stock instruments via bridge...")
    statuses = await try_load_instruments(bridge, track_indices)
    for k, v in statuses.items():
        print(f"  {k}: {v}")

    # 4. Compose
    drums, bass, rhythm, lead, piano = compose()
    print(
        f"Composed: drums={len(drums)} bass={len(bass)} rhythm={len(rhythm)} "
        f"lead={len(lead)} piano={len(piano)} "
        f"(total notes={len(drums)+len(bass)+len(rhythm)+len(lead)+len(piano)})"
    )

    # 5. For each track: create a 105-beat clip in slot 0 and add notes
    CLIP_LEN = 105.0  # 35 bars * 3 beats
    SLOT = 0
    note_sets = {
        "Drums": drums,
        "Bass": bass,
        "Rhythm Guitar": rhythm,
        "Lead Guitar": lead,
        "Piano": piano,
    }
    for name, notes in note_sets.items():
        track_idx = track_indices[name]
        # Idempotent: delete any existing clip in this slot before re-creating.
        osc.send("/live/clip_slot/delete_clip", track_idx, SLOT)
        await asyncio.sleep(0.1)
        osc.send("/live/clip_slot/create_clip", track_idx, SLOT, CLIP_LEN)
        await asyncio.sleep(0.25)
        # Send notes in chunks for safety
        chunk_size = 60
        for i in range(0, len(notes), chunk_size):
            chunk = notes[i:i + chunk_size]
            flat: list = [track_idx, SLOT]
            for nt in chunk:
                flat.extend([
                    int(nt["pitch"]),
                    float(nt["start"]),
                    float(nt["duration"]),
                    int(nt["velocity"]),
                    1 if nt["mute"] else 0,
                ])
            osc.send("/live/clip/add/notes", *flat)
            await asyncio.sleep(0.05)
        # Set clip name + a colour per track
        osc.send("/live/clip/set/name", track_idx, SLOT, f"{name} (jrock)")
        await asyncio.sleep(0.05)

    osc.send("/live/api/show_message", "AbletonMCP composed: 6/8 J-rock @ 144 BPM, 35 bars")
    print("Composition created in scene 1. Fire scene 1 to play.")


if __name__ == "__main__":
    asyncio.run(main())
