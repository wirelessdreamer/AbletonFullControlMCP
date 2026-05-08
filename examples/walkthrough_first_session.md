# Your first AbletonMCP session

A narrative walkthrough — install through your first generated drum loop.
About 15 minutes start to finish.

---

## 0. What you should already have

- Ableton Live 11 installed.
- Python 3.10+ on your PATH.
- An MCP-capable client (Claude Desktop, Claude Code, or Cursor).
- This repo cloned to e.g. `D:\Code\AbletonMCP`.

## 1. Install AbletonOSC

AbletonOSC is the bridge that exposes Live's Object Model over UDP. The
helper script downloads the latest release and drops it into your User
Library.

```powershell
cd D:\Code\AbletonMCP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m ableton_mcp.scripts.install_abletonosc
```

You should see a line like `Installed AbletonOSC to ...\Remote Scripts\AbletonOSC`.

## 2. Enable AbletonOSC in Live

Open Ableton Live → Preferences → Link, Tempo & MIDI →
**Control Surface** dropdown → choose **AbletonOSC**. Leave Input/Output
set to **None** (AbletonOSC doesn't use them). Live's status bar should
flash *"AbletonOSC: Listening for OSC on port 11000"*.

## 3. Smoke test

Without going through the LLM yet, confirm the bridge works:

```powershell
python -m ableton_mcp.scripts.smoke_test
```

Expected output:

```
Pinging AbletonOSC at 127.0.0.1:11000 ...
OK: AbletonOSC responded.
Live version: 11.x.y
Set has 4 tracks at 120.0 BPM.
Sent a hello message to Live's status bar.
```

If anything fails, see [../docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)
before going further.

## 4. Wire up your MCP client

Pick your client and follow the matching guide:

- [Claude Desktop](../docs/CLAUDE_DESKTOP.md)
- [Claude Code](../docs/CLAUDE_CODE.md)
- [Cursor](../docs/CURSOR.md)

Restart the client after editing config. Open a fresh chat.

## 5. First conversation: introspect the set

Open a blank Live set (`File → New Live Set`). Then in chat:

> **You:** Are you connected to Ableton? What's currently in my set?

Claude should call:
- `live_ping` → confirms connection and reports the Live version.
- `live_get_state` → tempo, time signature, transport.
- `track_list` → 4 default tracks (2 MIDI, 2 audio, 2 returns) on a fresh set.
- `scene_list` → 8 default scenes.

Expected response (paraphrased):

> Connected to Ableton Live 11.x.y. Empty set: 120 BPM, 4/4, 4 tracks
> (2 MIDI, 2 audio), 8 empty scenes, transport stopped.

## 6. Generate a 4-bar drum loop

> **You:** Create a 4-bar drum loop in 4/4 at 100 BPM. Use a kick on every
> beat, a snare on 2 and 4, and closed hats on every 8th. Drop it on a
> new MIDI track called "Drums" in the first scene.

Claude is expected to make this sequence of tool calls:

1. `live_set_tempo` with `bpm=100`.
2. `track_create_midi` with `name="Drums"` → returns `{index: 4}` (4
   pre-existing tracks; this becomes track 4).
3. `clip_create_midi` with `track_index=4, clip_index=0, length_beats=16`
   (4 bars × 4 beats).
4. `clip_set_name` with `name="Drum loop"`.
5. `clip_add_notes` with `notes=[...]` — typically:
   - Kick (C1, MIDI 36) on beats 0, 1, 2, …, 15 — every quarter.
   - Snare (D1, MIDI 38) on beats 1, 3, 5, 7, 9, 11, 13, 15 — every other beat.
   - Closed hat (F#1, MIDI 42) on every half-beat from 0 to 15.5.
   Each note has `duration=0.25, velocity=100`.
6. `clip_fire` with `track_index=4, clip_index=0` to start playback.
7. `live_play` to make sure transport is running.

You should now hear the loop in Live, and see the clip with all its notes
in the session view.

## 7. Iterate

> **You:** Drop the velocity of the offbeat hats by half so they sit
> behind the on-beat hats.

Claude calls `clip_get_notes(track_index=4, clip_index=0, start_pitch=42, pitch_span=1)`,
filters to the notes where `start` is `0.5, 1.5, 2.5, ...`, sends
`clip_remove_notes` to drop them, then `clip_add_notes` with
`velocity=50` for the offbeats. The pattern updates live without
stopping playback.

## 8. Save your work

> **You:** Save the set to D:/sketches/first_loop.als.

The `op_save_set` tool is currently a stub (Phase 2). Until it lands,
Claude will fall back to suggesting `Ctrl+S` and giving you a path to
type. Don't be surprised by that.

## 9. Where to next

- Browse [TOOLS.md](../docs/TOOLS.md) — 162 tools and counting.
- Try `examples/programmatic_drum_pattern.py` for the same loop without
  the LLM in the loop.
- Try `examples/live_set_introspection.py` for a bare-bridge view of your
  current set.
- Read [ARCHITECTURE.md](../docs/ARCHITECTURE.md) if you want to add
  tools.
