# AbletonFullControlTape

A Max for Live audio effect that captures its parent track's audio to disk on
demand, controlled over OSC. **Path A** of the Phase 2 audio-capture pipeline.

| Service | Direction | Port |
|---|---|---|
| AbletonFullControlTape | Python -> Max | UDP 11003 |
| AbletonFullControlTape | Max -> Python | UDP 11004 |

The full wire spec lives in [`PROTOCOL.md`](PROTOCOL.md).

## Install

### 1. Copy the device files into Live's User Library

```powershell
python -m ableton_mcp.scripts.install_tape
```

This copies the contents of this folder into

- Windows: `%USERPROFILE%\Documents\Ableton\User Library\Presets\Audio Effects\Max Audio Effect\AbletonFullControlTape\`
- macOS:   `~/Music/Ableton/User Library/Presets/Audio Effects/Max Audio Effect/AbletonFullControlTape/`

### 2. One-time: compile the .maxpat to .amxd

Live's browser only loads `.amxd`, not `.maxpat`. Open Live, find the patcher
in the browser at the path above, drag `AbletonFullControlTape.maxpat` onto a track —
Max for Live opens. Then in Max:

> **File -> Save As Device... -> AbletonFullControlTape.amxd** (in the same folder)

Once compiled, the `.amxd` shows up in Live's browser as a normal Max Audio
Effect.

### 3. Use it

Drop `AbletonFullControlTape.amxd` onto **the track whose output you want to capture**
(after the instrument + any FX you care about). The device's UI shows two
fields:

- `status` (read-only): `idle` / `recording` / `done`
- `port` (read-only): the receive port (default 11003)

From Python:

```python
from ableton_mcp.tape import TapeClient, CaptureConfig

client = TapeClient(CaptureConfig.from_env())
await client.start()
assert await client.ping()
await client.record("C:/tmp/take1.wav", duration_sec=2.0)
```

Or via the MCP tools:

```
tape_ping()                                              # liveness check
tape_record(track_index=2, output_path=".../take1.wav", duration_sec=2.0)
```

## Manual-build fallback

If the shipped `.maxpat` won't open in your Max version (the JSON format
shifts between major versions), build it manually. Drop these objects onto a
fresh **Max Audio Effect** patcher in Live and connect them as listed:

### Objects

- `[live.thisdevice]` — for live-context init bangs.
- `[udpreceive 11003]` — incoming OSC from Python.
- `[route /tape/ping /tape/record /tape/stop /tape/list]` — splits incoming
  OSC by address. **Use Max's stock `route` object, NOT `route`** —
  `route` is a CNMAT external that's not bundled with Max for Live.
  `udpreceive` already emits OSC addresses as Max message selectors, so
  plain `route` works directly.
- `[plugin~]` (left + right outlets) — the audio source: parent track input
  to the device.
- `[plugout~]` — pass-through so the device doesn't break the signal chain.
- `[sfrecord~ 2 16]` — stereo, 16-bit wav writer. Use `24` if you want 24-bit.
- `[unpack s 0.]` — splits the `/tape/record path duration` message into a
  symbol (path) + float (seconds).
- `[* 1000.]` — convert seconds -> milliseconds for `[delay]`.
- `[delay]` — fires after `duration` ms to stop recording.
- `[message: open $1, record 1]` — opens the file path and starts sfrecord~.
- `[message: record 0, stop]` — stops sfrecord~.
- `[message: /tape/pong]` — reply to ping.
- `[message: /tape/done $1 $2]` — ack with `(path, duration)` after stop.
- `[udpsend 127.0.0.1 11004]` — Python listens here.
- `[live.text]` showing status (parameter name `tape_status`).
- `[live.numbox]` showing the receive port (parameter name `tape_port`).

### Connections

```
udpreceive 11003 ---> route /tape/ping /tape/record /tape/stop /tape/list
  route out0 (ping)   ---> [/tape/pong] ---> [udpsend 127.0.0.1 11004]
  route out1 (record) ---> [unpack s 0.]
                                  out0 (path)     ---> [open $1, record 1] ---> [sfrecord~]
                                  out1 (duration) ---> [* 1000.] ---> [delay] ---> [record 0, stop]
                                                                                 ---> [sfrecord~]
                                                                                 ---> [/tape/done $1 $2] ---> [udpsend]
  route out2 (stop)   ---> [record 0, stop] ---> [sfrecord~]

[plugin~] left  signal ---> [sfrecord~] inlet 1
[plugin~] right signal ---> [sfrecord~] inlet 2
[plugin~] left  signal ---> [plugout~]   inlet 1
[plugin~] right signal ---> [plugout~]   inlet 2
```

(The done-message wiring needs the path + actual duration to be packed
together; in practice you `[pack s f]` the path + duration before formatting.
The shipped patch wires this for you.)

### Verify

1. Drop the device on a track. The status box should read `idle`.
2. From Python: `await TapeClient(...).ping()` returns True.
3. From Python: `await TapeClient(...).record("/tmp/x.wav", 1.0)` returns
   `{"path": "/tmp/x.wav", "duration_actual": ~1.0}` and a wav appears at
   that path.

## Caveats

- The shipped `.maxpat` is best-effort JSON — it has not been opened in a
  live Max install during this commit. If it errors on open, fall back to
  the manual build above.
- `sfrecord~`'s `open $1` argument is the absolute file path; **the user must
  pass an absolute path** to `tape_record(...)`. The Python client doesn't
  resolve relative paths because Max's working directory is unpredictable.
- The track's monitoring + arming setup is the user's responsibility — the
  tape device only sees what's already on its track.

## Credits

This device is original code, but the *Python -> OSC -> Max for Live ->
Live Object Model* shape draws on prior art. See
[`../../NOTICE.md`](../../NOTICE.md) for the full attribution.

- **[JIKASSA/terminaldaw](https://github.com/JIKASSA/terminaldaw)** —
  earlier project demonstrating that pattern (Python -> OSC -> M4L -> LOM)
  for parameter automation. The architectural shape of an OSC-controlled
  M4L device with disk side-effects is the contribution from that project.
- **[`sfrecord~`](https://docs.cycling74.com/legacy/max5/refpages/msp-ref/sfrecord~.html)**
  is a stock Cycling '74 Max object; we're not redistributing it.
