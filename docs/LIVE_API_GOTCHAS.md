# Live API gotchas — verified facts

Hard-won knowledge about Live's Python LOM, AbletonOSC's wire format, and our
own AbletonFullControlBridge's JSON contracts. Every entry here was verified against
**Ableton Live 11.3.43** during this project's debugging sessions, NOT
guessed from documentation. Read this before reaching for "I think Live's API
has..." — it probably has something subtly different.

If you discover a fact that isn't here, add it. Future-you will thank you.

---

## 1. Live Object Model — verified method signatures

### `Track`

| Member | Verified? | Notes |
|---|---|---|
| `Track.arrangement_clips` | **yes** | Property, iterable of `Clip` objects on the arrangement timeline. Length = `len(list(track.arrangement_clips))`. |
| `Track.clip_slots` | **yes** | Property, iterable of `ClipSlot` objects (Session view rows). |
| `Track.create_audio_clip(name, position)` | **yes** | Audio tracks only. Used for placing audio onto the arrangement. |
| `Track.create_midi_clip(start, end)` | **DOES NOT EXIST** | Live 11.3 does not expose this. Don't use it as a fallback for arrangement-clip creation. Discovered the hard way. |
| `Track.delete_clip(clip)` | yes | |
| `Track.duplicate_clip_slot(slot_index)` | yes | Session-to-session duplication. |
| `Track.duplicate_clip_to_arrangement(clip, time)` | **yes — and signature matters** | The first arg is the `Clip` object (`slot.clip`), NOT the `ClipSlot`, NOT the integer slot index. The `time` is a beat position. Returns the new arrangement `Clip`. Other shapes raise `TypeError` silently. |
| `Track.delete_device(int)` | **yes** | Deletes the device at the given chain index. Index validity is the caller's job — it does NOT raise on OOB cleanly in all builds. The bridge handler `track.delete_device` validates first. |
| `Track.devices` | yes | Property, iterable of `Device`. Use `len(list(track.devices))` to count. |
| `Track.freeze` / `Track.freeze_track` | **NO** | Not exposed in Live 11.3.43's LOM despite the UI command. Probed `dir(track)` — no freeze-related methods. There's no programmatic way to render a track to audio via LOM in this build. Workarounds: Live's UI Export Audio menu, or capture via M4L device. |
| `Track.flatten_track()` | unverified | Listed previously but not actually probed; given freeze is missing, flatten likely is too. |
| `Track.jump_in_running_session_clip(time)` | yes | |
| `Track.stop_all_clips()` | yes | |

### `ClipSlot`

| Member | Verified? | Notes |
|---|---|---|
| `ClipSlot.has_clip` | yes | Property, bool. |
| `ClipSlot.clip` | yes | Property → `Clip` (or `None`). |
| `ClipSlot.create_clip(length)` | yes | Session view, MIDI tracks. |
| `ClipSlot.create_audio_clip(...)` | yes | Audio tracks. |
| `ClipSlot.delete_clip()` | yes | |
| `ClipSlot.duplicate_clip_to(target_clip_slot)` | yes | Session → Session. |
| `ClipSlot.duplicate_clip_to_arrangement(...)` | **DOES NOT EXIST** | Only on `Track`. |
| `ClipSlot.controls_other_clips` | yes | Stop-button state. |

### `Clip`

| Member | Verified? | Notes |
|---|---|---|
| `Clip.is_arrangement_clip` | yes | Property, bool. |
| `Clip.is_midi_clip` | yes | Property, bool. |
| `Clip.length` | yes | Property, float beats. |
| `Clip.position` | yes | Property; for arrangement clips this is the timeline position. |
| `Clip.start_time` / `Clip.end_time` | yes | Arrangement clips only. |
| `Clip.consolidate()` | yes | Existing. |
| `Clip.crop()` | yes | Existing. |
| `Clip.duplicate_loop()` | yes | Extends clip by repeating its loop region. |
| `Clip.duplicate_region(...)` | yes | |
| `Clip.duplicate_notes_by_id(...)` | yes | |
| `Clip.reverse()` | **DOES NOT EXIST** | Reverse is UI-only in Live 11. The bridge tries `reverse`/`reverse_audio`/`reverse_warp` and falls back to `{supported: false}`. |
| `Clip.get_notes_extended(start_pitch, pitch_span, start_time, time_span)` | yes | Newer note-read API. Returns iterable of MidiNoteSpec-shaped objects with `.pitch .start_time .duration .velocity .mute`. |
| `Clip.get_notes(start, pitch_start, time_span, pitch_span)` | yes | Older API; deprecated but functional. |
| `Clip.add_new_notes(((pitch, time, dur, vel, mute), ...))` | yes | Bulk note add. |
| `Clip.set_notes(...)` | sometimes | Older API; not always present on freshly-created clips. |
| `Clip.remove_notes_extended(...)` | yes | |

### `Song` / `Application`

| Member | Verified? | Notes |
|---|---|---|
| `Live.Application.get_application().get_document()` | yes | Returns the `Song`. |
| `Application.browser` | yes | Has `instruments`, `audio_effects`, `midi_effects`, `drums`, `sounds`, `samples`, `plugins`, `user_library`, `current_project`, `packs`. |
| `Application.browser.load_item(item)` | yes | Loads onto whichever track is currently selected — so always set `song.view.selected_track = track` first. |
| `Song.tracks` | yes | Property, iterable. |
| `Song.scenes` | yes | Property, iterable. |
| `Song.view.selected_track` / `.selected_clip` / `.selected_scene` / `.selected_device` | yes | Setters + getters. |
| `Song.create_midi_track(index)` | yes | `index=-1` appends. |
| `Song.create_audio_track(index)` | yes | |
| `Song.create_return_track()` | yes | |
| `Song.create_scene(index)` | yes | |
| `Song.duplicate_track(index)` | yes | |

---

## 2. AbletonOSC — verified reply shapes

`/live/test` → `("ok",)` (single-element).

`/live/song/get/<scalar>` → `(value,)` — single element.

`/live/song/get/track_names` → `(name_0, name_1, ..., name_N)` — flat list, no leading id.

`/live/track/get/<scalar>` (e.g. mute, name, volume) → `(track_id, value)` — id first, then scalar.

`/live/track/get/clips/name` → `(track_id, slot_idx_0, name_0, slot_idx_1, name_1, ...)` — first arg is the track id, then alternating `(slot_index, value)` pairs for slots that contain a clip. Empty slots are simply absent. Same shape for `/live/track/get/clips/length` and `/live/track/get/clips/color`.

`/live/track/get/arrangement_clips/name` → similar pattern: `(track_id, arr_idx_0, name_0, ...)`. Beware: my first naive parser assumed the leading element was *not* the track_id; that was wrong.

`/live/track/get/devices/name` → `(track_id, name_0, name_1, ...)` — first arg is track id, **NO** indices interleaved (devices are positional, just dump in order).

`/live/clip/get/notes` (req: `track_id, clip_id [, start_pitch, pitch_span, start_time, time_span]`) → `(track_id, clip_id, pitch_0, start_0, dur_0, vel_0, mute_0, pitch_1, ...)` — flat 5-tuples after the leading two id fields. Skip the first 2 args, then chunk by 5.

`/live/clip/add/notes` (request shape mirrors): `(track_id, clip_id, pitch, start, dur, vel, mute, ...)`. Repeat the 5-tuple to add multiple notes in one call. Mute is `0` or `1`.

`/live/device/get/parameters/<field>` → `(track_id, device_id, value_0, value_1, ...)` — strip first 2 args.

`/live/device/get/parameter/value` (req: `track_id, device_id, parameter_id`) → `(track_id, device_id, parameter_id, value)` — strip first 3 args.

`/live/clip_slot/get/has_clip` (req: `track_id, slot_idx`) → `(track_id, slot_idx, value)` — strip first 2.

**Convention:** AbletonOSC echoes the addressing arguments back in every reply, in the order they were sent. The actual payload starts AFTER those echoed args.

---

## 3. AbletonFullControlBridge — verified request/response shapes

### `browser.search`

Request:
```json
{"op": "browser.search", "args": {"query": "Drift", "category": "instruments", "limit": 200}}
```

Response **`result` field**:
```json
{
  "query": "Drift",
  "category": "instruments",
  "count": 8,
  "results": [
    {"name": "Drift", "path": "instruments/Drift", "is_loadable": true, "uri": "query:Synths#Drift"},
    ...
  ]
}
```

**Gotcha:** the list of hits is under `results`, **NOT** `items` and **NOT** `hits`. Reaching for either of the wrong key gives 0 hits silently.

**Gotcha:** when `query=""` (empty string), no results are returned even if `category` is set. The search filter requires a query string. If you want "everything in this category", use `browser.list_at_path` instead.

### `browser.load_device` (the actual op name)

Request:
```json
{"op": "browser.load_device", "args": {"uri": "query:Synths#Drift", "track_index": 5}}
```

Response: `{"loaded": "Drift", "track_index": 5}`.

**Gotcha:** the op is `browser.load_device`, **NOT** `browser.load`. The latter returns `{"error": "unknown op: browser.load"}`. The siblings are `browser.load_drum_kit` (for items under the `drums` category — same effect but documents intent) and `browser.load_sample` (for `.wav`-style samples).

### `browser.list_at_path`

Request:
```json
{"op": "browser.list_at_path", "args": {"path": "instruments/Drift"}}
```

Response: `{"path": "instruments/Drift", "self": {...}, "children": [{...}, ...]}`. Empty `path` lists the top-level categories.

### `clip.duplicate_to_arrangement`

Request:
```json
{"op": "clip.duplicate_to_arrangement", "args": {"track_index": 4, "slot_index": 0, "time": 0.0}}
```

Response: `{"track_index": 4, "slot_index": 0, "time": 0.0, "method": "Track.duplicate_clip_to_arrangement(clip, time)", "lom_returned": "<Clip.Clip object at 0x...>"}`.

The handler internally calls `track.duplicate_clip_to_arrangement(slot.clip, time)`. See LOM section above for why this signature.

### `system.reload`

Request: `{"op": "system.reload", "args": {}}`.

Response: `{"ok": true, "reloaded": ["AbletonFullControlBridge.handlers.browser", ...], "errors": {}, "handler_count": N}`.

Reloads `handlers/*.py` from disk and rebuilds the dispatch table. Does **NOT** reload `bridge_server.py` itself — for that, restart Live.

---

## 4. Browser URIs — observed format

URIs returned by `browser.search` follow these patterns (Live 11.3.43):

| Pattern | Example |
|---|---|
| Stock instrument | `query:Synths#Drift` |
| Stock instrument preset | `query:Synths#Drift:Bass:FileId_4063` |
| Stock drum rack preset | `query:Drums#FileId_4196` |
| Stock audio effect | `query:AudioFx#Reverb...` |
| Pack content | `query:LivePacks#www.ableton.com/0:Devices:...` |

Don't construct these yourself — search and pick from `results`. The `FileId_*` integers are runtime-allocated and not stable across Live versions.

---

## 5. Browser categories — what's where

Live's browser tree exposes these top-level categories. **Drum-rack presets are NOT under `instruments`** — they live under `drums`. This trips up category-filtered searches.

| Category | Contains |
|---|---|
| `instruments` | All synths and instrument racks: Operator, Wavetable, Analog, Drift, Sampler, Simpler, Drum Rack (the empty rack template itself), Electric, Tension, Bass, Collision, Impulse, Drum Synth, Emit, Meld, Poli, plus VST/VST3/AU plugins under sub-folders. |
| `drums` | All drum-rack PRESETS: 505/606/707/808/909 Core Kit, Foley & Real Drum Kit, etc. (~38 in a stock install.) |
| `audio_effects` | EQ Eight, Reverb, Compressor, Echo, Saturator, etc. (~30+ stock.) |
| `midi_effects` | Arpeggiator, Chord, Scale, Velocity, etc. (7 stock.) |
| `samples` | Raw .wav files. |
| `sounds` | Tagged sound presets across categories. |
| `plugins` | VST/VST3/AU. |
| `user_library` | User's saved presets. |
| `current_project` | Files in the .als project folder. |
| `packs` | Live Packs and content libraries. |

When loading an instrument by name, search **without** a category filter and pick the exact-name `is_loadable` hit. That avoids missing items that live in a category you didn't expect (Drum Rack presets, plugins under `instruments/VST3 Plugins/...`, etc.).

---

## 6. Tempo and time signature — Live's quarter-note convention

`/live/song/get/tempo` and `/live/song/set/tempo` always speak **quarter-note BPM**, regardless of the time signature.

In 6/8 at "144 BPM-felt-as-dotted-quarter", Live's tempo field would actually need to read `216` (since 1 dotted quarter = 1.5 quarter notes, and 144 × 1.5 = 216). For "144 BPM-as-shown-in-Live" in 6/8, each bar is `3/144 min = 1.25 sec`.

When a user says "144 BPM in 6/8", confirm whether they mean Live's literal tempo field or the felt dotted-quarter pulse. They are not the same value.

---

## 7. Live's Python module cache and how to reload

Live caches Remote Script modules in `sys.modules`. Toggling the Control
Surface dropdown calls `disconnect()` on the existing instance, but the
imported handler modules stay cached. As a result, **toggling the Control
Surface does NOT pick up changes to `handlers/*.py`**.

Three reload paths, in order of cost:

1. **Cheapest:** call `bridge.call("system.reload")`. This `importlib.reload`s
   every handler module and rebuilds the dispatch table. Does NOT pick up
   changes to `bridge_server.py` itself (that's the loader and gets imported
   only once at script start).
2. **Medium:** restart Live. Always works for any code change.
3. **Expensive (don't):** delete the `__pycache__` and uninstall/reinstall
   the script. Almost never necessary.

---

## 8. Common installed instruments observed in the test set

For reference when writing example scripts. The user's Live had these stock
items (and most production setups will too):

- **Synths:** Operator, Wavetable, Analog, Drift, Bass, Collision, Electric,
  Tension, Impulse, Sampler, Simpler.
- **Drum Synth pack:** DS Clang, DS Clap, DS Cymbal, DS FM, DS HH, DS Kick,
  DS Sampler, DS Snare, DS Tom.
- **Live 12 additions** that may show up: Emit, Meld.
- **Drum kits** (under `drums` category): 505/606/707/808/909 Core Kit and
  variants — search "Core Kit" to enumerate.
- **Drift bass presets** (under `instruments/Drift/Bass/`): "808 Pure.adg",
  "808 Drifter.adg", and ~10 others.

When writing demos that need a bass tone, prefer one of the Drift/Bass
presets over loading bare Drift — the latter has a bright lead default.

---

## 9. AbletonOSC operations that are **getters only** (no setter equivalent)

These properties can be read via OSC but cannot be set. To modify them you
need either AbletonFullControlBridge or a Max for Live device.

- `/live/track/get/arrangement_clips/*` — arrangement clip listing is read-
  only via OSC. To place a clip on the arrangement, use the bridge's
  `clip.duplicate_to_arrangement` op.
- Browser tree, search, load — entirely the bridge's job.
- Group/freeze/flatten/consolidate/crop — bridge's job.
- Save set — bridge's job (`project.save`).

---

## Appendix — how to ground new claims in this file

Before committing a new entry:

1. Verify against running Live via `bridge.call("clip._dir_track", track_index=0)`
   or a similar one-off introspection handler.
2. State the Live version you tested against (currently 11.3.43).
3. If the API surface is known to differ across versions, say so explicitly.
4. Prefer "verified yes/no" cells in tables over prose-only claims.
