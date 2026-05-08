# Roadmap — AbletonFullControlMCP

Six phases. Each ships a usable surface; later phases stack on earlier ones.

> Project name: AbletonFullControlMCP. Python module: `ableton_mcp` (kept short — same pattern as scikit-learn → sklearn).

**Status as of 2026-05-07:** 196 tools across 25 categories. Phases 1, 3, 4, 6 real. **Sound-understanding stack** — schemas for 57 stock devices, 109-descriptor semantic vocabulary, NL shaping engine, 6-synth in-process bench, 44-preset library with cluster discovery — all ship and validate end-to-end. **Inventory tooling** — bulk browser scan + per-instrument schema introspection + manifest. **Phase 5 audio capture** — Max for Live tape device + Python `sounddevice` loopback fallback both ship; `LiveRenderer.render()` is implemented; the full sound-modeling loop now closes against real Live instruments.

---

## Phase 1 — Full LOM bridge (DONE)

Goal: every Live property and method that AbletonOSC exposes is reachable from an LLM.
Status as of 2026-05-07: **154 tools across 18 categories.**

- [x] Project scaffolding, packaging, config
- [x] AbletonOSC installer for Windows User Library
- [x] Async OSC client with FIFO reply tracking + listener fan-out
- [x] MCP server entry (stdio, FastMCP)
- [x] **Transport (28):** play/continue/stop, tempo, tap_tempo, time-signature, metronome, loop, jump, undo/redo, capture_midi, session_record, trigger_session_record, arrangement_overdub, back_to_arranger, punch in/out, record_mode, nudge up/down, clip-trigger / MIDI-recording quantization, groove amount, show_message
- [x] **Tracks (22):** list/get + meters, create midi/audio/return, delete (incl. return), duplicate, name, vol/pan/mute/solo/arm, sends (get + set), color (palette + RGB), fold_state, monitoring, stop_all_clips
- [x] **Clips (31):** list/get, create, delete, duplicate_loop, fire/stop, name, color (palette + RGB), muted, gain, pitch (semitones + cents), warp on/off, warp_mode, ram_mode, launch_mode, launch_quantization, legato, velocity_amount, loop start/end, start/end markers, position, get/add/remove notes (with pitch+time filters)
- [x] **Clip slots (5):** has_clip, has_stop_button get/set, fire, duplicate_clip_to
- [x] **Scenes (11):** list, create/delete/duplicate, fire (selected / index / as_selected), name, color, per-scene tempo + time-signature override
- [x] **Cue points (6):** list, add_or_delete at playhead, jump by index, jump next/prev, rename
- [x] **View (5):** get full selection, select scene/track/clip/device
- [x] **Arrangement (2):** list arrangement-view clips per track, summary (length, tempo, signature, position)
- [x] **Routing (5):** list available input/output types & channels, get current, set input/output by name
- [x] **Devices (5):** list, list all parameters, set by index, set by name, get value_string
- [x] **MIDI mapping (1):** map_cc → device parameter
- [x] **MIDI files (6):** summary, load_into_clip, export_from_clip, quantize, transpose, humanize
- [x] **Audio analysis (2):** analyze (tempo/key/MFCC/spectral), compare (cosine MFCC similarity)
- [x] **Listeners (9):** subscribe to song / track / scene / view / clip-position / device-parameter changes; poll-based event drain; list active subs
- [x] **High-level ops (10) — STUBS:** group/ungroup, freeze/flatten, consolidate, crop, reverse, slice-to-MIDI, save_set, new_set (need AbletonOSC extension or Max for Live)

## Phase 2 — Browser, presets, render (PARTIAL)

Goal: load any device/sample by name, save presets, bounce audio.

Browser + project ops shipped via the new **AbletonFullControlBridge** Remote Script (TCP/11002, runs alongside AbletonOSC). Render is the outstanding piece.

- [x] **Browser:** search Live's browser (instruments, audio effects, samples, presets, drums) by query/tag
- [x] **Browser:** load selected item onto a track (`browser_load_device`, `browser_load_drum_kit`, `browser_load_sample`)
- [x] **Browser:** tree navigation (`browser_get_tree`, `browser_list_at_path`)
- [x] **High-level ops:** group/ungroup, freeze/flatten, consolidate/crop tracks/clips, save_set
- [ ] **Browser:** save current device chain as preset (LOM does not expose preset save — needs M4L hotswap trick)
- [ ] **Render:** bounce master, single track, single clip, or arrangement region to wav (needs M4L tape device or OS-level loopback)
- [ ] **Project Q&A:** "what's on track 3?", "what plugin is making this sound?" — composable from existing `track_list` + `device_list` but not packaged as a single tool yet
- [ ] **Arrangement:** insert at time, move clips, set scene length, follow actions (Live's Python LOM coverage here is sparse)

## Phase 3 — Sound understanding (OFFLINE PIPELINE + NL SHAPING DONE)

Goal: "play me a sound" → server proposes synth + params that match.

Math + planner + matcher + dataset + synth-stub all shipped and tested end-to-end against an in-process numpy synth. The only blocker for matching against real Live devices is rendering (Phase 2 / Phase 5).

- [x] **Probe:** `sound_probe_device` sweeps N params with grid / Latin-Hypercube / random strategies, renders each cell, persists features to sqlite
- [x] **Match:** `sound_match` loads reference wav → 32-dim feature vector (13 MFCC mean + 13 std + 6 spectral) → kNN cosine
- [x] **Refine:** scipy.optimize.minimize (Powell, bounded) refines top-k candidates with a render-in-the-loop callback
- [x] **Param explanation:** `sound_explain_parameter` sweeps one axis, ranks feature-dim deltas, reports which knob does what
- [x] **Renderer interface:** abstract `Renderer` with `SynthStubRenderer` (works today) and `LiveRenderer` (raises `NotImplementedError("Phase 2 render pipeline required")`) — clean integration point
- [x] **Device schema library:** canonical Parameter + DeviceSchema for **57 Ableton stock devices** (12 instruments, 1 drum rack, 31 audio effects, 7 MIDI effects, 3 racks, 3 utilities). Each schema marks 5-15 params as `recommended_for_sweep`.
- [x] **Semantic vocabulary:** 109 sound descriptors across 10 categories (brightness, warmth, dynamics, space, character, envelope, harmonic, punch, air, body), each with feature-anchor predicates + opposites + aliases. `sound_describe(audio)` returns ranked descriptors.
- [x] **NL shaping engine:** parse "brighter and punchier" → structured intent → target features → kNN over probe dataset → push params via OSC. Tools: `shape_parse`, `shape_predict`, `shape_apply`, `shape_compare_apply`.
- [x] **Synth test bench:** 6 in-process synths (subtractive, fm_2op, fm_4op, wavetable, additive, granular) + composable FX chain (filter/delay/Schroeder reverb/saturator) — runs the entire pipeline without Live.
- [x] **Preset library:** 44 hand-curated presets (warm pad, plucky lead, fat bass, etc.) in sqlite + KMeans cluster discovery from probe datasets that auto-names + auto-tags discovered presets.
- [ ] **End-to-end against real Live devices:** unblocked the moment `LiveRenderer` is implemented (Phase 2 / Phase 5).

## Phase 4 — Knowledge / RAG (DONE)

Goal: ask Ableton questions in plain English.

- [x] Polite httpx crawler with robots.txt + 1 req/sec; HTML→markdown via stdlib (no extra deps)
- [x] Chunker (~500 tokens, 50-token overlap)
- [x] Pluggable embedding backend: sentence-transformers (`all-MiniLM-L6-v2`) when available, TF-IDF fallback otherwise
- [x] sqlite store with optional sqlite-vec extension; portable index format
- [x] `ableton_search_docs(query, k)` → top-k snippets with citations + source URLs
- [x] `ableton_explain(question, k)` → snippets + a stitched citation-numbered context block; client-side LLM synthesises the answer
- [x] CLI: `python -m ableton_mcp.scripts.build_knowledge_index --source {manual,cookbook,both}`
- [ ] Keyboard-shortcut and routing diagrams indexed with images for visual answers (deferred)

## Phase 5 — Max for Live companion (AUDIO CAPTURE DONE)

Goal: things even AbletonFullControlBridge can't do — capture audio in-process, listen to MIDI input, sample-accurate timing.

- [x] **M4L audio capture device** (`live_max_for_live/AbletonFullControlTape/`) — records the host track's output to a wav on OSC trigger; replies on completion. Plus `sounddevice` loopback fallback for users without Max.
- [x] **`LiveRenderer.render()` implemented** — pushes params via OSC, triggers tape capture, returns numpy array. Closes the Phase 3 loop against real Live instruments.
- [x] **`tape_*` tool surface** — ping, record, list loopback devices, get/set config (5 tools).
- [ ] M4L MIDI device: forward played notes to MCP server (for "match what I just played")
- [ ] M4L sidechain probe: drives the device under test with a known MIDI pattern during sweeps
- [ ] M4L LFO/macro proxy for params not in LOM
- [ ] Verify the shipped `.maxpat` actually opens cleanly in Max (currently best-effort JSON; user may need to rebuild from PROTOCOL.md)

## Phase 6 — Generators + stems (DONE; final import step pending Phase 2)

Goal: generate full songs externally, slice into stems, integrate as Live clips at project tempo.

- [x] **Pluggable Generator interface** — `Generator` ABC + `GenResult`; `gen_list_providers` reports which are configured + ready
- [x] **Suno adapter** — `SUNO_API_KEY` env; httpx-based, mockable for tests
- [x] **MusicGen adapter** — local audiocraft via subprocess (optional `[musicgen]` extra)
- [x] **Stable Audio adapter** — Stability AI v2beta; `STABLE_AUDIO_API_KEY` env
- [x] **Demucs stem split** — `stems_split(audio_path, model="htdemucs")` (optional `[stems]` extra)
- [x] **Stems → tracks** — `stems_import_to_live` creates one fresh audio track per stem at project tempo
- [ ] **Final wav-into-clip import** — calls `browser_load_sample` (already shipped via Phase 2 bridge); loop wiring is `for stem in result.tracks: browser_load_sample(stem.path, stem.track_index, 0)` — small follow-up to wrap as one tool
- [ ] **Udio adapter** — no public API yet; symmetrical class will land when one ships
- [ ] **Tempo / key auto-detect on import** — wire `audio_analyze` into the import path

---

## Cross-cutting (not phase-bound)

- [ ] **Streaming**: long-running ops (probe sweeps, renders) emit MCP progress notifications
- [ ] **Cancellation**: any tool call can be cancelled mid-flight
- [ ] **Per-tool dry-run mode** for destructive ops (delete, overwrite clip)
- [ ] **State diffing**: cache LOM snapshot, diff after each tool call, return only the delta
- [ ] **Multi-version support**: detect Live 11 vs 12 capabilities at startup
