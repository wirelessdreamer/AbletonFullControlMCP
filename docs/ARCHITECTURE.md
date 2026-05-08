# AbletonMCP architecture

## The 30-second tour

```
   +-------------------+      stdio JSON-RPC       +-----------------------+
   |  MCP client       |  <--------------------->  |  AbletonMCP server    |
   |  Claude Desktop / |                           |  src/ableton_mcp/     |
   |  Claude Code /    |                           |  - server.py (FastMCP)|
   |  Cursor           |                           |  - tools/*.py (~162)  |
   +-------------------+                           |  - osc_client.py      |
                                                   +-----------+-----------+
                                                               |
                                                  UDP/11000  send  (request)
                                                  UDP/11001  recv  (reply)
                                                               |
                                                   +-----------v-----------+
                                                   | AbletonOSC remote     |
                                                   | script in Live 11     |
                                                   | (Python in Live's     |
                                                   |  control-surface host)|
                                                   +-----------+-----------+
                                                               |
                                                +--------------v---------------+
                                                | Live Object Model (LOM)      |
                                                | song / tracks / clips /      |
                                                | scenes / devices / view ...  |
                                                +------------------------------+
```

Two transports glued together:

1. **MCP client → server (stdio JSON-RPC).** FastMCP from the official MCP
   SDK handles framing. Tools are registered as decorated `async def`
   functions — see `src/ableton_mcp/tools/*.py`. The full surface is
   auto-documented in [TOOLS.md](TOOLS.md).

2. **Server → Ableton (OSC over UDP).** AbletonOSC is a community remote
   script that exposes the LOM. We send to UDP/11000, it replies on
   UDP/11001. There is no request-id field in the protocol; correlation is
   our problem to solve.

## The OSC client (`osc_client.py`)

This is the ~150-line core that everything else builds on.

### Responsibilities

- Open a UDP socket pair and run a `python-osc` async server on the
  receive side.
- Provide three primitives:
  - `send(addr, *args)` — fire-and-forget mutation
    (`/live/track/set/volume`, etc.)
  - `request(addr, *args, timeout=...)` — send and `await` the reply
  - `listen(addr) → asyncio.Queue` / `stop_listening(addr, q)` — passive
    fan-out of every message on `addr`, used by the listener tools to
    surface AbletonOSC's `start_listen` / `stop_listen` notifications.

### Reply correlation: FIFO + prefix matching

Replies have no id. Two pending `/live/track/get/name` calls — one for
track 0, one for track 1 — would otherwise be indistinguishable. The client
solves this with a **per-(address, args-prefix)** waiter map.

When you call `request("/live/track/get/name", 0)` we register a waiter
under the key `("/live/track/get/name", (0,))`. When AbletonOSC replies with
the message `("/live/track/get/name", 0, "Lead")` the dispatcher tries
matching the longest prefix first:

```
("/live/track/get/name", (0, "Lead"))   → no waiter
("/live/track/get/name", (0,))          → match! pop FIFO oldest
("/live/track/get/name", ())            → would also have matched
```

The first non-empty bucket wins; that bucket is itself a FIFO `deque`, so
duplicate calls with the *same* selectors still resolve in send order.

This costs O(len(args)) per reply (small — most LOM addresses take 0–3
selectors) and gives us:

- correct concurrent gets across different selectors,
- correct FIFO within identical selectors,
- backwards compatibility with the no-args `/live/test`, `/live/song/get/*`
  family.

A reply with no matching waiter is fanned out to listeners; if no listener
is registered either, it is silently dropped (with a debug log line).

### Listener fan-out

`listen(addr)` returns an `asyncio.Queue` and adds it to a per-address
list. Every received message on that address is `put_nowait()` into every
queue, **in addition to** resolving any pending request. This is how the
listener tools (`listen_song`, `listen_track`, …) work — they wrap the
queue in a `_Subscription` handle and the user polls it via `listen_poll`.

## The FastMCP server (`server.py`)

`build_server()` constructs a `FastMCP` instance, sets the system
instructions (the cheat sheet of category prefixes), and walks each
`tools/*.py` module's `register(mcp)` function. `main()` then calls
`mcp.run()` which serves stdio JSON-RPC.

`Config.from_env()` is read once at startup; every tool obtains the
process-wide singleton client through `await get_client()`, which lazily
calls `start()` the first time it's needed.

## Tool layout

`src/ableton_mcp/tools/` is one module per LOM concept. Each module exposes
a single `register(mcp)` function that decorates inner `async def` tools.
This keeps the public tool surface flat (good for LLMs) while letting us
split the implementation by concern.

Categories (and their counts as of 2026-05-07):

| Module                | Prefix         | Tools |
|-----------------------|----------------|-------|
| transport.py          | `live_*`       | 28    |
| tracks.py             | `track_*`      | 22    |
| clips.py              | `clip_*`       | 21    |
| clip_slots.py         | `clip_slot_*`  | 5     |
| scenes.py             | `scene_*`      | 11    |
| cue_points.py         | `cue_point_*`  | 6     |
| view.py               | `view_*`       | 5     |
| arrangement.py        | `arrangement_*`| 2     |
| routing.py            | `routing_*`    | 5     |
| devices.py            | `device_*`     | 5     |
| midi_mapping.py       | `midi_map_*`   | 1     |
| midi_files.py         | `midi_file_*`  | 6     |
| audio_analysis.py     | `audio_*`      | 2     |
| listeners.py          | `listen_*`     | 9     |
| high_level.py         | `op_*`         | 10    |
| browser/render/...    | (Phase 2+)     | stubs |

For the full list, see [TOOLS.md](TOOLS.md), which is regenerated by
`python -m ableton_mcp.scripts.generate_tools_doc`.

## The listener poll pattern

FastMCP tool results are one-shot JSON, not a long-lived stream, so we
can't naturally hand the client a `for event in stream:` loop. The
solution is a triplet:

1. `listen_*` opens a server-side subscription, allocates a queue, sends
   `start_listen` to AbletonOSC, returns a uuid handle.
2. The client periodically calls `listen_poll(handle)` to drain queued
   events (FastMCP returns the batch as an array).
3. `listen_stop(handle)` sends the matching `stop_listen` and frees the
   queue.

Each subscription has a bounded `max_buffer`; once full, oldest events are
dropped (the count is reported in `dropped_total`).

## Singletons & lifecycle

- The OSC client is a process-wide singleton. Tools call `await
  get_client()` instead of constructing one.
- The server starts listening on UDP the first time any tool runs, not at
  import time. That keeps test runs that don't touch the network from
  binding sockets.
- `stop()` is rarely called in production; the server is killed by the
  client closing stdin and the OS reclaims the UDP port.

## Why not TCP?

AbletonOSC predates the project and we'd rather build new layers
(sound modeling, RAG, Suno) than re-implement the bridge. A future M4L
companion (Phase 5) will add side channels for things OSC can't carry —
mostly audio capture and sample-accurate timing — but the LOM bridge stays
on UDP.
