# Third-party credits and attribution

AbletonFullControlMCP (project) / `ableton_mcp` (Python module) stands on
a stack of open-source work. This file documents who built what we depend
on, what licence it ships under, and what specifically we use it for.

We **do not fork or vendor** any of these projects — they all enter the
system either as runtime dependencies (installed via pip) or, in the case
of AbletonOSC, downloaded at install time directly from upstream and placed
unmodified into Live's User Library. So this is a credits document, not a
licence-redistribution document. Where a project's licence requires
acknowledgement (BSD/MIT/ISC), surfacing it here satisfies the notice
clause for our usage.

## Live-side dependencies

### AbletonOSC — primary control bridge

- **Project:** https://github.com/ideoforms/AbletonOSC
- **Author:** Daniel Jones ([@ideoforms](https://github.com/ideoforms))
- **Licence:** BSD-3-Clause
- **Reference paper:** *AbletonOSC: A unified control API for Ableton Live*,
  NIME 2023 — https://nime.org/proceedings/2023/nime2023_60.pdf

AbletonOSC is the OSC-over-UDP Live Remote Script that exposes the Live
Object Model. Almost every transport / track / clip / device tool in this
repo (~140 of the ~200 MCP tools) routes through it.

**How we use it:** `python -m ableton_mcp.scripts.install_abletonosc`
downloads the upstream zip into your Live User Library. We never patch or
re-package it.

**How we don't use it:** we do not redistribute the AbletonOSC source.

### ahujasid/ableton-mcp — prior art for MCP-Live integration

- **Project:** https://github.com/ahujasid/ableton-mcp
- **Author:** Siddharth Ahuja ([@ahujasid](https://github.com/ahujasid))
- **Licence:** MIT

The first widely-shared MCP server for Ableton Live, using a custom
socket-based Remote Script bridge. We **deliberately diverged** by
building on AbletonOSC's larger LOM surface, but his project taught us:
- the value of a single Remote Script as the in-Live integration point,
- how to surface the LOM through a flat command/response protocol,
- the ergonomics gap between "raw LOM" and "what an LLM wants to call".

`live_remote_script/AbletonFullControlBridge/` is a different bridge (TCP/JSON
versus his Python-pickle-over-socket) addressing the gaps AbletonOSC
leaves (browser, group/freeze/flatten, save, session→arrangement copy),
but the architectural shape — a Live Remote Script that listens for
external commands — comes from his project.

## Python-side runtime dependencies

These are pinned in `pyproject.toml` and pulled in by `pip install -e .`.

| Package | Author / Org | Licence | Used for |
|---|---|---|---|
| [`mcp`](https://github.com/modelcontextprotocol/python-sdk) | Anthropic | MIT | Official MCP Python SDK; we use `mcp.server.fastmcp.FastMCP` to register all 200 tools. |
| [`python-osc`](https://github.com/attwad/python-osc) | Antoine Beauquesne (`attwad`) | "Unlicense" / BSD-style | UDP/OSC client + dispatcher used by `osc_client.py` to talk to AbletonOSC. |
| [`mido`](https://github.com/mido/mido) | Ole Martin Bjørndalen | MIT | MIDI message + file primitives, mostly read paths in `tools/midi_files.py`. |
| [`pretty_midi`](https://github.com/craffel/pretty-midi) | Colin Raffel | MIT | High-level MIDI file editing (quantise / transpose / humanise). |
| [`librosa`](https://librosa.org/) | LibROSA developers | ISC | All audio feature extraction in `sound/features.py` and `tools/audio_analysis.py`. |
| [`soundfile`](https://github.com/bastibe/python-soundfile) | Bastian Bechtold | BSD-3-Clause | wav read/write for the bounce pipeline and audio analysis. |
| [`numpy`](https://numpy.org/) | NumFOCUS | BSD-3-Clause | Numerical arrays everywhere. |
| [`scipy`](https://scipy.org/) | NumFOCUS | BSD-3-Clause | `scipy.signal` for the synth bench filters; `scipy.optimize.minimize` for `sound_match` refinement; `scipy.stats.qmc` for Latin-Hypercube sweep planning. |
| [`scikit-learn`](https://scikit-learn.org/) | scikit-learn developers | BSD-3-Clause | KMeans for `preset_discover`; TF-IDF fallback for the knowledge index. |
| [`httpx`](https://www.python-httpx.org/) | Tom Christie & contributors | BSD-3-Clause | Async HTTP for the Suno/Stable Audio adapters and the manual crawler. |
| [`pydantic`](https://docs.pydantic.dev/) | Samuel Colvin & contributors | MIT | Type validation for tool arg schemas (via FastMCP). |
| [`platformdirs`](https://github.com/platformdirs/platformdirs) | platformdirs maintainers | MIT | OS-correct user-library path resolution in `install_*` scripts. |
| [`anyio`](https://github.com/agronholm/anyio) | Alex Grönholm | MIT | Async runtime glue (transitive via `mcp`). |

## Optional dependencies (heavy extras)

These are NOT installed by default. Install only via `pip install -e ".[<extra>]"`.

| Package | Author / Org | Licence | Extra | Used for |
|---|---|---|---|---|
| [`sentence-transformers`](https://www.sbert.net/) | Nils Reimers / UKPLab | Apache-2.0 | `[knowledge]` | Embedding the Ableton manual + Cookbook for RAG. Pulls torch (~2 GB). |
| [`sqlite-vec`](https://github.com/asg017/sqlite-vec) | Alex Garcia | Apache-2.0 / MIT | `[knowledge]` | Optional sqlite extension for vector search; we fall back to brute-force cosine if not present. |
| [`demucs`](https://github.com/facebookresearch/demucs) | Meta AI Research | MIT | `[stems]` | Stem splitting (vocals/drums/bass/other) for the Suno-import pipeline. Pulls torch + torchaudio (~2 GB). |
| [`audiocraft`](https://github.com/facebookresearch/audiocraft) | Meta AI Research | MIT | `[musicgen]` | Local MusicGen generation. Pulls torch + transformers + xformers (~4 GB). |

## External services (configured via API key, never bundled)

These are integrations triggered by user intent; we never package or
redistribute their models or APIs.

- **Suno** (https://suno.ai) — text-to-music generation. `SUNO_API_KEY`.
- **Stability AI Stable Audio v2beta** (https://stability.ai/) — text-to-audio. `STABLE_AUDIO_API_KEY`.

## Reference material we drew on (not bundled)

- The **Ableton Live 11 Reference Manual** (https://www.ableton.com/en/manual/welcome-to-live/)
  and **Ableton Cookbook** (https://www.ableton.com/en/cookbook/) — primary
  source for the 57 `device_schemas/` entries (parameter names, ranges,
  semantic groupings). Crawled for the Phase 4 RAG index, with politeness:
  1 req/s, robots.txt respected.
- **Cycling '74 Live Object Model docs**
  (https://docs.cycling74.com/apiref/lom/) — primary source for the
  AbletonFullControlBridge handler implementations.

## License of this project

AbletonMCP itself is MIT-licensed. See `LICENSE` (or the `license` field in
`pyproject.toml`).

If you redistribute AbletonMCP — including by forking or shipping it as
part of a larger product — keep this NOTICE.md alongside the source so the
upstream credits travel with the code.
