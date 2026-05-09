# Roadmap — AbletonFullControlMCP

Six phases. Each ships a usable surface; later phases stack on earlier ones.

> Project name: AbletonFullControlMCP. Python module: `ableton_mcp` (kept short — same pattern as scikit-learn → sklearn).

**Status as of 2026-05-08:** ~218 tools across 26 categories. Phases 1, 3, 4, 6, 7 real. **Sound-understanding stack** — schemas for 57 stock devices, 109-descriptor semantic vocabulary, NL shaping engine, 6-synth in-process bench, 44-preset library with cluster discovery — all ship and validate end-to-end. **Inventory tooling** — bulk browser scan + per-instrument schema introspection + manifest. **Bounce pipeline** — `bounce_song`, `bounce_tracks`, `bounce_enabled` capture wav (+ optional mp3) using Live's built-in Resampling input; one playback pass captures every requested track in parallel. The earlier Max-for-Live "tape" capture device was removed in favour of this path. **Conversational song-flow** — `song_analyze` / `song_transpose` (Complex Pro per-clip with snapshot-restore) / `song_make_variations` / `song_import_variations_to_live` chain end-to-end so a producer can say "transpose this to F# and create all track variations" and the LLM client orchestrates the rest.

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
- [x] **Render:** bounce master + per-track stems via Live's Resampling input (`bounce_song`, `bounce_tracks`, `bounce_enabled`). One playback pass; no Max for Live or loopback driver required.
- [ ] **Project Q&A:** "what's on track 3?", "what plugin is making this sound?" — composable from existing `track_list` + `device_list` but not packaged as a single tool yet
- [ ] **Arrangement:** insert at time, move clips, set scene length, follow actions (Live's Python LOM coverage here is sparse)

## Phase 3 — Sound understanding (OFFLINE PIPELINE + NL SHAPING DONE)

Goal: "play me a sound" → server proposes synth + params that match.

Math + planner + matcher + dataset + synth-stub all shipped and tested end-to-end against an in-process numpy synth. The only blocker for matching against real Live devices is rendering (Phase 2 / Phase 5).

- [x] **Probe:** `sound_probe_device` sweeps N params with grid / Latin-Hypercube / random strategies, renders each cell, persists features to sqlite
- [x] **Match:** `sound_match` loads reference wav → 32-dim feature vector (13 MFCC mean + 13 std + 6 spectral) → kNN cosine
- [x] **Refine:** scipy.optimize.minimize (Powell, bounded) refines top-k candidates with a render-in-the-loop callback
- [x] **Param explanation:** `sound_explain_parameter` sweeps one axis, ranks feature-dim deltas, reports which knob does what
- [x] **Renderer interface:** abstract `Renderer` with `SynthStubRenderer` (works today) and `LiveRenderer` (currently a stub raising `NotImplementedError`; needs a Resampling-track-based capture path to close the loop against real Live devices)
- [x] **Device schema library:** canonical Parameter + DeviceSchema for **57 Ableton stock devices** (12 instruments, 1 drum rack, 31 audio effects, 7 MIDI effects, 3 racks, 3 utilities). Each schema marks 5-15 params as `recommended_for_sweep`.
- [x] **Semantic vocabulary:** 109 sound descriptors across 10 categories (brightness, warmth, dynamics, space, character, envelope, harmonic, punch, air, body), each with feature-anchor predicates + opposites + aliases. `sound_describe(audio)` returns ranked descriptors.
- [x] **NL shaping engine:** parse "brighter and punchier" → structured intent → target features → kNN over probe dataset → push params via OSC. Tools: `shape_parse`, `shape_predict`, `shape_apply`, `shape_compare_apply`.
- [x] **Synth test bench:** 6 in-process synths (subtractive, fm_2op, fm_4op, wavetable, additive, granular) + composable FX chain (filter/delay/Schroeder reverb/saturator) — runs the entire pipeline without Live.
- [x] **Preset library:** 44 hand-curated presets (warm pad, plucky lead, fat bass, etc.) in sqlite + KMeans cluster discovery from probe datasets that auto-names + auto-tags discovered presets.
- [ ] **End-to-end against real Live devices:** needs a `LiveRenderer` capture path. The bounce pipeline already captures Live audio via Resampling — the per-render hook for `sound_probe_device` cells is the missing piece.

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

## Phase 5 — Max for Live companion (DEFERRED)

Goal: things even AbletonFullControlBridge can't do — sample-accurate timing, listen to MIDI input, LFO/macro proxies for params LOM doesn't expose.

The original Phase 5 audio-capture device (`AbletonFullControlTape`) was removed in favour of the Resampling-input bounce path (Phase 2 / `bounce_song` etc.), which covers the same audio-out-to-disk use case without requiring users to install Max for Live and Save-As-Device an `.amxd`. Future Phase 5 work would only be for things Resampling can't do.

- [ ] M4L MIDI device: forward played notes to MCP server (for "match what I just played")
- [ ] M4L sidechain probe: drives the device under test with a known MIDI pattern during sound-modeling sweeps (would also unblock real-device `sound_probe_device`)
- [ ] M4L LFO/macro proxy for params not in LOM

## Phase 6 — Generators + stems (DONE; final import step pending Phase 2)

Goal: generate full songs externally, slice into stems, integrate as Live clips at project tempo.

- [x] **Pluggable Generator interface** — `Generator` ABC + `GenResult`; `gen_list_providers` reports which are configured + ready
- [x] **Suno adapter** — `SUNO_API_KEY` env; httpx-based, mockable for tests
- [x] **MusicGen adapter** — local audiocraft via subprocess (optional `[musicgen]` extra)
- [x] **Stable Audio adapter** — Stability AI v2beta; `STABLE_AUDIO_API_KEY` env
- [x] **Demucs stem split** — `stems_split(audio_path, n_stems=4|6)` (base install — demucs is required because every conversational song-flow request depends on it). 6-stem `htdemucs_6s` adds guitar + piano channels.
- [x] **Stems → tracks** — `stems_import_to_live` creates one fresh audio track per stem at project tempo
- [x] **Final wav-into-clip import** — `song_import_variations_to_live` does this in a loop via `browser.load_sample` for any list of `{label, wav_path}` entries; covers the stem-import case as well as song-flow variations
- [ ] **Udio adapter** — no public API yet; symmetrical class will land when one ships
- [x] **Tempo / key auto-detect on import** — `song_analyze` reads tempo from Live + estimates key via librosa chroma on a 30 s slice bounce

## Phase 7 — Conversational song flow (DONE)

Goal: a producer can say "transpose this song to F# and create all track variations", and the chain happens end-to-end without naming individual tools.

- [x] **`song_analyze`** — tempo + length + librosa key estimate + Live Scale UI hint, in one call
- [x] **`song_transpose`** — per-clip in-place transpose (warp=on, warp_mode=Complex Pro, pitch_coarse += delta on every audio arrangement clip; note pitches += delta on every MIDI arrangement clip); snapshot/restore around a single bounce so the source session is bit-identical afterwards. New bridge handlers: `clip.get_arrangement_pitch_state`, `clip.set_arrangement_warp`, `clip.set_arrangement_warp_mode`, `clip.set_arrangement_pitch`, `clip.get_arrangement_notes`, `clip.set_arrangement_notes`.
- [x] **`song_make_variations`** — instrument-up remixes (focal stem +6, others -3) + instrumental + recombined original, offline math via extended `mix_stems_to_master(gains_db=...)`
- [x] **`song_import_variations_to_live`** — bulk-create one new audio track per variation, load the wav into clip slot 0
- [ ] **Session-view clip transposition** — current scope is arrangement-view only; songs that drive playback from session clips need the same bridge handlers extended to clip slots. Most arrangement bounces don't fire session clips, so this is a deferred follow-up.

---

## Cross-cutting (not phase-bound)

- [ ] **Streaming**: long-running ops (probe sweeps, renders) emit MCP progress notifications
- [ ] **Cancellation**: any tool call can be cancelled mid-flight
- [ ] **Per-tool dry-run mode** for destructive ops (delete, overwrite clip)
- [ ] **State diffing**: cache LOM snapshot, diff after each tool call, return only the delta
- [ ] **Multi-version support**: detect Live 11 vs 12 capabilities at startup
