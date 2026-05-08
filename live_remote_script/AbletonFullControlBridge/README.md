# AbletonFullControlBridge

A Live Remote Script that fills the gaps left by [AbletonOSC](https://github.com/ideoforms/AbletonOSC):

- Walking and searching Live's **browser** (instruments, audio effects, samples, drums, plugins, packs).
- Loading browser items onto specific tracks (`load_item`).
- **Track ops** that AbletonOSC doesn't wrap: `group`, `ungroup`, `freeze`, `flatten`.
- **Clip ops**: `consolidate`, `crop`, and best-effort `reverse`.
- **Project**: `save` (and `info`).

It runs alongside AbletonOSC — they don't share ports.

| Service | Port | Protocol |
|---|---|---|
| AbletonOSC | UDP 11000 (recv) / 11001 (send) | OSC |
| **AbletonFullControlBridge** | **TCP 11002** | **JSON lines** |

## Install

```powershell
python -m ableton_mcp.scripts.install_bridge
```

This copies `AbletonFullControlBridge/` into your Ableton User Library:

- Windows: `%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\AbletonFullControlBridge\`
- macOS:   `~/Music/Ableton/User Library/Remote Scripts/AbletonFullControlBridge/`

Then in Ableton: **Preferences → Link/Tempo/MIDI → Control Surface → AbletonFullControlBridge**.

You should see a status-bar message: `AbletonFullControlBridge listening on 11002`. Live's Log.txt will also have an `[AbletonFullControlBridge] listening on 127.0.0.1:11002 ...` line.

## Wire format

One TCP connection per request. Each side sends a single line of JSON terminated by `\n`, then the server closes the connection.

**Request**
```json
{"id": 7, "op": "browser.search", "args": {"query": "operator", "category": "instruments"}}
```

**Response (success)**
```json
{"id": 7, "ok": true, "result": {"count": 3, "results": [{"name": "Operator", "path": "instruments/Operator", "is_loadable": true, "uri": "..."}]}}
```

**Response (error)**
```json
{"id": 7, "ok": false, "error": "ValueError: track_index 9 out of range (0..2)"}
```

## Handlers

| Op | Args | Notes |
|---|---|---|
| `system.ping` | — | Health check |
| `system.version` | — | Reports Live version |
| `browser.tree` | `depth=2` | Top-level categories with N levels of children |
| `browser.list_at_path` | `path` | Lists immediate children at e.g. `"instruments/Operator"` |
| `browser.search` | `query`, `category?`, `limit=200` | Substring search, case-insensitive |
| `browser.load_device` | `path` or `uri`, `track_index` | Loads onto selected track |
| `browser.load_drum_kit` | same | Drum-rack alias |
| `browser.load_sample` | `path` or `uri`, `track_index`, `clip_index?` | |
| `track.group` | `track_indices=[…]` | Range must be contiguous |
| `track.ungroup` | `group_track_index` | |
| `track.freeze` | `track_index` | |
| `track.flatten` | `track_index` | |
| `clip.consolidate` | `track_index`, `clip_index` | |
| `clip.crop` | `track_index`, `clip_index` | |
| `clip.reverse` | `track_index`, `clip_index` | Returns `{supported: false}` if Live doesn't expose it from Python |
| `clip.duplicate_to_arrangement` | `track_index`, `slot_index`, `time` | Place a Session clip on the Arrangement timeline at `time` (beats). Internally calls `Track.duplicate_clip_to_arrangement(clip, time)` (see verified-signatures section below). |
| `project.save` | — | Equivalent to Cmd-S; Save-As needs the UI dialog |
| `project.info` | — | Lightweight project snapshot |
| `system.reload` | — | `importlib.reload` every handler module + rebuild dispatch table. Lets us hot-swap handler edits without restarting Live or toggling the Control Surface. Does NOT reload `bridge_server.py` itself — restart Live for that. |
| `system.ping` / `system.version` | — | Liveness + Live version. |

## Verified LOM signatures (Live 11.3.43)

These were verified against running Live during debug sessions, NOT inferred
from documentation. See `docs/LIVE_API_GOTCHAS.md` at the repo root for the
complete reference. The handlers above already encode these; document them
here so future hand-rolled ops don't have to guess.

| Method | Signature | Notes |
|---|---|---|
| `Track.duplicate_clip_to_arrangement` | `(clip, time)` — `Clip` + float beats | First arg is `slot.clip`, NOT the `ClipSlot` and NOT the slot index. Returns new arrangement `Clip`. Other shapes raise `TypeError` silently. |
| `Track.create_midi_clip` | **does not exist** | Don't use as a fallback. |
| `Track.arrangement_clips` | property → iterable[Clip] | |
| `Track.create_audio_clip(name, position)` | exists | Audio tracks only. |
| `ClipSlot.duplicate_clip_to_arrangement` | **does not exist** | Only on `Track`. |
| `ClipSlot.create_clip(length)` | exists | Session view, MIDI tracks. |
| `ClipSlot.duplicate_clip_to(target)` | exists | Session→Session. |
| `Clip.consolidate()` / `Clip.crop()` | exist | |
| `Clip.reverse()` | **does not exist** | UI-only. Bridge handler returns `{supported: false}`. |
| `Clip.add_new_notes(((p, t, d, v, m), ...))` | exists | Bulk note add. |
| `Clip.is_arrangement_clip` / `Clip.is_midi_clip` | properties, bool | |

## Threading

All handlers run on Live's main thread inside `update_display()` (~60 Hz). Sockets are non-blocking, so a slow request never freezes Live's UI. Each request is short-lived (one round-trip per connection), which keeps the state machine trivial.

## Logs

Live writes Remote Script stdout to `Log.txt`:

- Windows: `%USERPROFILE%\AppData\Roaming\Ableton\Live <version>\Preferences\Log.txt`
- macOS:   `~/Library/Preferences/Ableton/Live <version>/Log.txt`

Look for lines prefixed `[AbletonFullControlBridge]`.

## Credits

This bridge is original code, but it owes design debts to two earlier
projects. See [`../../NOTICE.md`](../../NOTICE.md) for the full credits.

- **[AbletonOSC](https://github.com/ideoforms/AbletonOSC)** by Daniel Jones
  (`ideoforms`) is the sister Remote Script that handles ~80% of the LOM
  surface (transport / tracks / clips / scenes / devices). This bridge
  fills only the gaps AbletonOSC doesn't cover, and runs alongside it.
- **[ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)** is
  the original "MCP-talks-to-Live" project; it uses a different transport
  (a Python-pickle socket protocol with its own bespoke Remote Script) and
  a smaller surface, but the architectural shape — *one Live Remote Script
  listening for external commands and dispatching to LOM* — is its
  contribution to the prior art.
