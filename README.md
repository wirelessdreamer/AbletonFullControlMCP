# AbletonFullControlMCP

> Distribution name (PyPI / pip): `ableton-full-control-mcp`. Python module: `ableton_mcp` (kept short for `import` ergonomics + back-compat — same pattern as `scikit-learn` → `sklearn`). Wherever this README says "AbletonFullControlMCP", you're reading the project. Wherever code says `ableton_mcp`, you're reading the module.

A Model Context Protocol (MCP) server that lets an LLM client (Claude Desktop, Claude Code, Cursor, etc.) drive Ableton Live 11 with **full-surface natural-language control** — clip and MIDI editing, instrument and effect loading, sound design ("more aggressive", "warmer attack"), song-structure manipulation in bar counts ("extend the breakdown by 4 bars"), full bounce-to-wav-and-mp3 (mix + stems), audio capture, knowledge RAG over the official manual, and reverse-engineering sounds from a reference clip.

> Status: **200+ tools across 25+ categories.** Phases 1, 3, 4, and 6 (generators + stems) are real. Sound-understanding stack — schemas for 57 stock devices, 109-descriptor semantic vocabulary, NL shaping engine, 6-synth in-process test bench, 44-preset library — ships end-to-end. Inventory tooling bulk-scans every installed instrument. The bounce pipeline produces wav + mp3 (full mix and per-track stems) using Live's built-in Resampling input — no Max for Live or loopback driver required.

## Comparison vs the projects we built on

This is an MCP server in the same niche as [ableton-mcp][a] and [live-mcp-server][b]. It also sits **on top of** — not against — the lower-level [AbletonOSC][c] Remote Script and its [pylive][d] Python wrapper.

### vs other Ableton MCP servers

| Capability | **AbletonFullControlMCP** | [ableton-mcp][a] | [live-mcp-server][b] |
|---|---|---|---|
| Project type | MCP server (this repo) | MCP server | MCP server |
| Tool count exposed to LLM | **200+** | ~16 | ~20 |
| Transport | **OSC + JSON/TCP bridge** | Custom TCP + Remote Script | OSC via AbletonOSC |
| LOM coverage | **full LOM + browser + groups/freeze/flatten + save + S→A** | partial (tracks, clips, basic devices) | most of LOM (via AbletonOSC) |
| Browser / preset loading | **yes** (companion bridge) | yes (custom RS) | no |
| Group / freeze / flatten | **yes** | no | no |
| Session → Arrangement copy | **yes** (`clip.duplicate_to_arrangement`) | no | no |
| Listeners / event subscriptions | **9 poll-based subscription tools** | no | partial |
| MIDI file I/O on disk | **6 tools** (load/export/quantize/transpose/humanize/summary) | no | no |
| Audio analysis (librosa) | **MFCC, key, tempo, spectral, similarity** | no | no |
| Sound modeling (probe → match → describe) | **yes** (offline synth_stub pipeline; real-device capture pending) | no | no |
| Semantic vocabulary (109 descriptors → feature deltas) | **yes** | no | no |
| NL sound shaping ("brighter and punchier") | **yes** (`shape_predict` / `shape_apply`) | no | no |
| Per-device sound rules ("aggressive on Drift") | **yes** (curated per-device) | no | no |
| Song structure in bar counts | **yes** (`structure_*` tools) | no | no |
| Inventory of installed instruments | **yes** (`inventory_scan_all`) | no | no |
| Stock device schemas | **57 devices** | no | no |
| Bounce to wav / stems / mp3 | **yes** (Live Resampling track + libmp3lame, full mix + per-track stems in one pass) | no | no |
| Knowledge RAG over Live manual + Cookbook | **yes** (sentence-transformers / TF-IDF fallback) | no | no |
| AI generators (Suno / MusicGen / Stable Audio) | **pluggable Generator interface** | no | no |
| Stem splitting (Demucs) | **yes** (default 6-stem `htdemucs_6s`, fallback 4-stem `htdemucs`; auto GPU when CUDA torch installed) | no | no |
| Hot-reload of bridge handlers | **yes** (`system.reload`) | n/a | no |
| Reply correlation under concurrent calls | **per-(address, args-prefix) FIFO** | n/a | n/a |
| Auto-installer for clients (Claude Desktop + Code) | **yes** (`install_clients` script) | no | no |

### vs the lower-level layer we build on

[AbletonOSC][c] is a Live Remote Script that exposes the LOM over OSC. [pylive][d] is a Python wrapper around AbletonOSC. Neither exposes tools to an LLM — they're transports. We use AbletonOSC as the backbone for ~70% of our tools (full LOM, raw event listeners with FIFO reply correlation, `/live/api/reload` hot-reload) and add the MCP layer, the companion bridge for things AbletonOSC doesn't expose (browser, groups, freeze, save, S→A), the schema / semantic / sound-design libraries, the structure model, the bounce pipeline, and the knowledge RAG on top.

[a]: https://github.com/ahujasid/ableton-mcp
[b]: https://github.com/Simon-Kansara/ableton-live-mcp-server
[c]: https://github.com/ideoforms/AbletonOSC
[d]: https://github.com/ideoforms/pylive

## Why we built this in our context

The user is a working musician with Live 11.3 + Max for Live. The goal isn't "drive Live from the command line" — it's **"talk to an LLM the way you'd talk to a session engineer"**:

- *"Make the lead guitar more aggressive in bars 28–32."*
- *"Soften the piano in the breakdown."*
- *"Extend the breakdown by 4 bars."*
- *"Bounce stems and mp3 to `D:\exports\jrock\`."*
- *"Build a J-rock instrumental in 6/8 at 144 BPM, 35 bars."*

Existing tools couldn't carry that conversation:

- **[AbletonOSC](https://github.com/ideoforms/AbletonOSC)** is excellent at exposing the LOM, but it's a transport — there's no MCP layer, no semantic vocabulary, no song-structure model. You hand-write OSC commands in beats. We use it as our backbone for ~70% of our tools.
- **[ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)** was the inspiration. It validated the "MCP server fronting Ableton" idea, but the surface is small (~16 tools, mostly track/clip CRUD), it rolls its own Remote Script in parallel to AbletonOSC instead of building on it, and it stops at the level "create a clip"—not "make this clip warmer".
- **[Simon-Kansara/ableton-live-mcp-server](https://github.com/Simon-Kansara/ableton-live-mcp-server)** wraps AbletonOSC into MCP but stays at LOM-thin coverage, no sound-design layer, no song-structure model, no bounce.
- **[pylive](https://github.com/ideoforms/pylive)** is a Python wrapper around AbletonOSC — useful for scripting but not exposing tools to an LLM.

So we built the layers a musician actually needs above all of those:

1. **Full LOM** via AbletonOSC (transport / tracks / clips / scenes / devices / cue points / view / arrangement / routing / MIDI mapping).
2. **A complementary bridge** (`AbletonFullControlBridge`, our own JSON-over-TCP Remote Script on port 11002) for the things AbletonOSC doesn't expose: browser, group/freeze/flatten, save, session→arrangement copy, device deletion, hot reload.
3. **A canonical schema library** (`device_schemas/`) for 57 stock devices with parameter names, ranges, and "recommended for sweep" hints.
4. **A semantic vocabulary** (`semantics/`) — 109 descriptors mapping musician language ("bright", "warm", "punchy") to quantitative audio-feature deltas.
5. **Per-device sound-design rules** (`sound_design/`) — curated mappings from descriptors to specific knobs on Drift/Operator/Wavetable/Tension/Reverb/Compressor/etc., so "more aggressive" actually moves the right faders on whatever instrument is on the track.
6. **A song-structure model** (`structure/`) that talks in bar counts and section names — the dialect this README's user uses.
7. **An NL shaping engine** (`shaping/`) and a probe-based sound-modeling pipeline (`sound/`) for matching reference audio.
8. **An inventory tool** (`inventory/`) that walks the user's installed-instrument library and writes a manifest.
9. **A bounce pipeline** (`bounce/`) — wav + mp3 (libmp3lame via ffmpeg), full mix and per-track stems, via Live's built-in Resampling input. One playback pass captures every requested track in parallel; no Max for Live or loopback driver required.
10. **A conversational song-flow** (`song_flow/`) — one-shot analyze/transpose/stem/variations over the active arrangement. *"transpose to F# and create all track variations"* chains `song_analyze` → `song_transpose` (per-clip Complex Pro warp + pitch shift, snapshot/restore) → `stems_split(n_stems=6)` → `song_make_variations` (instrument-up remixes + instrumental) → `song_import_variations_to_live`. The LLM client orchestrates the chain — no monolithic pipeline tool.
11. **A knowledge RAG layer** (`knowledge/`) over the Ableton manual + Cookbook for grounded how-to answers.

Net result: 200+ tools that the LLM picks across as the conversation moves between "what's loaded?", "tweak this knob", "extend this section", and "render the result". You can have the entire conversation in producer's language; we translate to OSC + LOM behind the scenes.

## Architecture

```
+--------------------+      stdio JSON-RPC       +--------------------+
|  MCP Client        |  <-------------------->   | ableton-full-      |
|  (Claude Desktop / |                           | control-mcp        |
|  Claude Code)      |                           | (Python server,    |
+--------------------+                           |  module ableton_mcp)|
                                                 +-----+--------+-----+
                                                       |        |
                                  OSC (UDP 11000/11001) JSON (TCP 11002)
                                                       |        |
                                       +---------------v--+  +--v--------------------+
                                       | AbletonOSC       |  | AbletonFullControlBridge      |
                                       | (full LOM)       |  | (browser, group,      |
                                       |  by Daniel Jones |  |  freeze/flatten,      |
                                       +------------------+  |  save, ses→arr,       |
                                                             |  delete_device,       |
                                                             |  system.reload)       |
                                                             +-----------------------+
                                              (both run as Live Remote Scripts)
```

## One-time install

### 1. Install the Python server

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
# First install pulls demucs + torch (~2 GB) because the song-flow path
# (stem split, instrumental, instrument-up variations) depends on it.

# Optional extras (each is additional heavy; install only what you need):
#   pip install -e ".[knowledge]"   # sentence-transformers + sqlite-vec for the manual RAG
#   pip install -e ".[musicgen]"    # audiocraft for local MusicGen (~4 GB torch+xformers)
#   pip install -e ".[all-heavy]"   # everything heavy
```

#### Optional: GPU acceleration for stem splitting

The default `pip install -e .` pulls **CPU-only** torch (cross-platform, no CUDA toolkit needed). The stem-split path (`stems_split`, `song_make_variations`) **auto-detects GPU at runtime** — if a CUDA torch build is installed, Demucs runs on the GPU; otherwise it falls back to CPU. Typical speedup is 5-10× on a modern NVIDIA card (a 5-min song goes from ~90s on CPU to ~15s on GPU).

To switch to a CUDA torch build:

```powershell
# CUDA 12.6 (stable) — most NVIDIA GPUs released before 2025
.\.venv\Scripts\python.exe -m pip uninstall -y torch torchaudio
.\.venv\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126

# CUDA 12.8 nightly — required for sm_120 / Blackwell (RTX 50-series)
.\.venv\Scripts\python.exe -m pip uninstall -y torch torchaudio
.\.venv\Scripts\python.exe -m pip install --pre torch torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128

# Verify
.\.venv\Scripts\python.exe -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

Both downloads are ~2-3 GB. No code changes needed after install — `stems_split` will pick up the GPU automatically. To go back to CPU torch, uninstall and reinstall without the `--index-url`.

### 2. Install the Live Remote Scripts

#### 2a. Copy the script files into Live's User Library

```powershell
python -m ableton_mcp.scripts.install_abletonosc   # transport, tracks, clips, devices, scenes, ...
python -m ableton_mcp.scripts.install_bridge       # browser, group/freeze/flatten, save, ...
```

This drops two folders under `Documents\Ableton\User Library\Remote Scripts\` (Windows) or `~/Music/Ableton/User Library/Remote Scripts/` (macOS). Live only scans this directory at startup, so **restart Ableton Live now** if it's open.

#### 2b. Enable BOTH scripts as Control Surfaces in Ableton

This step is required — without it, the MCP server can't talk to Live. After restart:

1. Open Ableton → **Preferences** → **Link, Tempo & MIDI**
2. Find the **Control Surface** column (six dropdown rows)
3. In one free row, pick **AbletonOSC** from the dropdown. Leave its Input and Output set to **None**.
4. In another free row, pick **AbletonFullControlBridge**. Leave Input and Output as **None**.
5. Close Preferences. Both run side-by-side: AbletonOSC owns UDP 11000/11001, the bridge owns TCP 11002.

If either entry isn't in the Control Surface dropdown, step 2a didn't land — re-run the install script and restart Live. Both must be enabled; the server will time out on roughly 70% of its tools if AbletonOSC is missing, and on browser / group / freeze / save / hot-reload calls if the bridge is missing.

> **Why two scripts, different ownership?**
>
> - **AbletonOSC** — third-party project by Daniel Jones (BSD-3-Clause). We
>   download it unmodified from [its upstream GitHub](https://github.com/ideoforms/AbletonOSC)
>   at install time and never touch the source. ~70% of our 219 tools route
>   through it. We don't fork or rebrand it because the licence requires
>   preserving the original name on unmodified distributions, and renaming
>   would falsely claim authorship of someone else's work.
> - **AbletonFullControlBridge** — ours. Fills the gaps AbletonOSC doesn't
>   cover (browser, group/freeze/flatten, save, session→arrangement, hot
>   reload).

The bounce pipeline (`bounce_song`, `bounce_tracks`, `bounce_enabled`) uses Live's built-in **Resampling** input — no extra setup beyond the two Control Surfaces above; install ffmpeg if you want mp3 alongside the wav.

### 3. (Optional) Build the knowledge index

```powershell
python -m ableton_mcp.scripts.build_knowledge_index --source both
```

Populates `data/knowledge/index.sqlite` so `ableton_search_docs` and `ableton_explain` return grounded citations.

### 4. Wire up your MCP client (one command)

```powershell
python -m ableton_mcp.scripts.install_clients --user
```

Merges an `ableton` MCP server entry into:
- `%APPDATA%\Claude\claude_desktop_config.json` (Claude Desktop)
- `<repo>/.mcp.json` (Claude Code, project scope)
- `~/.claude.json` (Claude Code, user scope — drop the `--user` flag to skip)

Existing servers are preserved. Each file is backed up before edits. Add `--dry-run` to preview, `--uninstall` to roll back. Then **fully quit and relaunch** whichever client you're using.

See `docs/CLAUDE_DESKTOP.md`, `docs/CLAUDE_CODE.md`, `docs/CURSOR.md` for full per-client walkthroughs.

### 5. Smoke test

```powershell
python -m ableton_mcp.scripts.smoke_test
```

### 6. Regenerate the auto-doc (after pulling)

```powershell
python -m ableton_mcp.scripts.generate_tools_doc
# rewrites docs/TOOLS.md from the live FastMCP registration (~2,700 lines)
```

## Roadmap & status

See [ROADMAP.md](ROADMAP.md) for the six phases and current state.

## Talking to it like a musician

Examples that "just work" once everything is installed and wired:

- *"What's the state of my set?"* → `live_get_state` + `track_list`.
- *"List every instrument I have installed in Live."* → `inventory_scan_browser`.
- *"Build a 6/8 J-rock instrumental at 144 BPM, 35 bars: intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8. Drums, bass, rhythm + lead guitar, piano. Use Rock Kit, Electric Bass Palm, Power Chords Guitar through a Crunch amp, Hard Picked Guitar through a Lead amp, Grand Piano Lost Ship."* → orchestrated through `live_set_tempo`, `live_set_time_signature`, `track_create_midi`, `browser.search` + `browser.load_device`/`load_drum_kit`, `clip_create_midi` + `clip_add_notes`, `clip.duplicate_to_arrangement`.
- *"Make the lead guitar more aggressive."* → `sound_describe_track(7)` + `sound_apply_descriptor(7, "aggressive")` (powered by per-device rules in `sound_design/`).
- *"Extend the breakdown by 4 bars."* → `structure_extend(structure, "breakdown", 4)` then re-render.
- *"Bounce wav + mp3 stems and full mix to `D:\exports\jrock\`."* → `bounce_full_pipeline`.

## Credits

AbletonFullControlMCP stands on a stack of open-source work. **[NOTICE.md](NOTICE.md)** has the full third-party attribution with licences and exactly what we use each project for.

- **[AbletonOSC](https://github.com/ideoforms/AbletonOSC)** by Daniel Jones (`ideoforms`, BSD-3-Clause) — the OSC Remote Script that exposes Live's Object Model. We install it unmodified into your Live User Library; it's the transport for ~70% of our 200+ tools. Without it, this project is impossible.
- **[ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)** by Siddharth Ahuja (MIT) — the original "MCP-talks-to-Live" project. The architectural shape — *one Live Remote Script listening for external commands and dispatching to the LOM* — is theirs. We diverged on transport (we ride AbletonOSC) and on scope.
- **[Simon-Kansara/ableton-live-mcp-server](https://github.com/Simon-Kansara/ableton-live-mcp-server)** — also sits on AbletonOSC; useful sibling project. Their handlers' shape informed ours where they overlap.
- **[ideoforms/pylive](https://github.com/ideoforms/pylive)** — Python wrapper for AbletonOSC; informs how we structured the async OSC client (FIFO correlation, listener queues).
- **[mcp](https://github.com/modelcontextprotocol/python-sdk)** (Anthropic, MIT) — the official MCP Python SDK; FastMCP registers all our tools.
- Plus librosa, scipy, scikit-learn, mido, pretty_midi, python-osc, httpx, pydantic, soundfile, numpy, demucs (+ torch, for the song-flow stem-split path) — and optionally sentence-transformers, audiocraft. Full list in [NOTICE.md](NOTICE.md).

If you fork or redistribute this repo, keep `NOTICE.md` alongside the source so the upstream credits travel with the code.
