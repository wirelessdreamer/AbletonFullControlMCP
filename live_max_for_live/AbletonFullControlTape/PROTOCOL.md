# AbletonFullControlTape — OSC wire protocol

Authoritative spec for the Python <-> Max for Live tape device messaging.

## Endpoints

| Direction | Default port | Override env var |
|---|---|---|
| Python -> Max (requests) | UDP 11003 | `ABLETON_MCP_TAPE_SEND_PORT` |
| Max -> Python (replies)  | UDP 11004 | `ABLETON_MCP_TAPE_RECV_PORT` |
| Host                     | 127.0.0.1 | `ABLETON_MCP_TAPE_HOST` |

Both sides run on the same machine. The Python client must bind a UDP
listener on the recv port BEFORE sending any request.

## Requests (Python -> Max)

### `/tape/ping`

No args. Liveness probe.

The Max device must reply with `/tape/pong` (no args) within ~1.5 s.

### `/tape/record <path> <duration_sec> [<track_index>]`

Begin capturing the track's audio to disk.

| Arg | Type | Meaning |
|---|---|---|
| `path` | string | **Absolute** filesystem path. The Max device passes this to `sfrecord~ open`. The directory must already exist. The file is overwritten if it exists. |
| `duration_sec` | float | How long to record. The device opens `sfrecord~`, waits this many seconds, then stops. |
| `track_index` | int (optional) | Informational. Lets multi-tape setups disambiguate which device should respond if multiple devices share the receive port (rare). The shipped Max device ignores this arg. |

While recording: device shows `recording` in its status field.

When the duration elapses (or `/tape/stop` arrives early): device closes the
file and replies with `/tape/done <path> <duration_actual>`.

### `/tape/stop`

No args. Bail out of an in-progress record. The device closes `sfrecord~`
and emits `/tape/done <path> <duration_actual>` (where `duration_actual` is
how long the partial recording was).

### `/tape/list`

Reserved. Future use: ask the device to enumerate alternate input sources.
The shipped device does not handle this — Python clients should not depend
on a reply.

## Replies (Max -> Python)

### `/tape/pong`

No args. Sent in response to `/tape/ping`.

### `/tape/done <path> <duration_actual>`

| Arg | Type | Meaning |
|---|---|---|
| `path` | string | The path the wav was actually written to (echoes the request `path`). |
| `duration_actual` | float | How long the recording ran in seconds. May be slightly larger than the requested duration due to Max's scheduler granularity, or shorter if `/tape/stop` arrived early. |

The Python client correlates this with the pending `/tape/record` request by
matching `path`.

### `/tape/error <message>`

| Arg | Type | Meaning |
|---|---|---|
| `message` | string | Human-readable error: bad path, file open failed, sfrecord already running, etc. |

The Python `TapeClient` raises `TapeError(message)` on the next pending
record/ping waiter.

## Correlation rules

- One record at a time per device. The device does not queue.
- Concurrent records to **different** paths against **different** tape
  devices on different tracks are correlated by `path` in the Python
  client's FIFO buckets.
- Pings are FIFO — if you issue two pings, the two pongs are matched in
  order.

## Error states

| Symptom | Likely cause | Fix |
|---|---|---|
| `tape_ping()` returns False, no error | Device not on any track, or Max isn't running | Drop `AbletonFullControlTape.amxd` on a track; check Live's Max console |
| `TapeTimeout` after `tape_record` | Path directory missing, or sfrecord~ failed silently | Ensure the directory exists; check Max console for sfrecord~ errors |
| `/tape/error: file open failed` | Path not writable | Check permissions / use an absolute path |
| `/tape/done` arrives but file is silent | Track not armed / monitoring off / instrument silent | Arm the track + set monitor to In; trigger a MIDI note before recording |

## Versioning

This is **v1** of the protocol. If breaking changes are needed, the device
will respond to a new `/tape/version` request with `(major, minor, patch)` —
clients can branch on that.

## Reference Python implementation

See `src/ableton_mcp/tape/client.py`. Key responsibilities:

1. Bind UDP listener on `tape_recv_port` BEFORE sending any request.
2. Send via `pythonosc.udp_client.SimpleUDPClient`.
3. Correlate replies on `(reply_address, args_prefix)` so concurrent
   requests don't cross.
4. Apply request timeout = `duration_sec + tape_timeout` for records;
   1.5 s for pings.
