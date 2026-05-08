# Troubleshooting

A quick triage chart followed by detailed fixes.

## Triage

```
Tool calls hang or time out
        |
        v
Run smoke_test ────► OK ───► problem is in the MCP client config; see CLAUDE_DESKTOP.md / CLAUDE_CODE.md / CURSOR.md
        |
        v fails
Live not running ──── start Live, retry
        |
        v Live is running
AbletonOSC not the active control surface ── enable it in Preferences, retry
        |
        v active
Port 11000 / 11001 conflict ── see "Port conflict" below
        |
        v ports look OK
Firewall blocking 127.0.0.1 ── see "Firewall" below
```

```powershell
D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.smoke_test
```

## "Control Surface dropdown is empty"

Live can't find AbletonOSC. Causes, in order of likelihood:

1. **Not installed.** Run:
   ```powershell
   D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.install_abletonosc
   ```
   It downloads the release zip and unpacks it under
   `%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\AbletonOSC`.
2. **Wrong User Library location.** Live's User Library is configurable in
   Preferences → Library. If it's somewhere other than `Documents\Ableton`,
   move the `AbletonOSC` folder under the configured path's
   `Remote Scripts\` directory.
3. **Live needs a restart.** Live only scans Remote Scripts at startup.
4. **Permissions.** Live, running unprivileged, can't read AbletonOSC if
   you copied it as Administrator. Re-copy without elevation.

## Port conflict

AbletonOSC binds **UDP/11000** (recv from us) and **UDP/11001** (send to
us). If something else owns those ports the server starts but every
request times out.

`live_ping` reports this directly with `cause: "reply_port_in_use"` —
that is the structured equivalent of the cryptic `WinError 10048` /
`OSError: [Errno 98] Address already in use` you would otherwise see on
the first request.

```powershell
# See who owns 11001 (Windows)
Get-NetUDPEndpoint -LocalPort 11001 | Select-Object OwningProcess
Get-Process -Id <pid>
```

```bash
# macOS / Linux
lsof -i UDP:11001
```

Common culprits:

- **Two MCP hosts running this server.** Each MCP host (Claude Code CLI,
  Claude Code desktop app, Claude Desktop, Cursor, Continue, ...) that
  has `ableton-mcp` configured spawns its **own** subprocess — only one
  can bind UDP/11001. The losing process(es) fail to bind and every
  request times out, *and* the reply traffic from Live goes to the
  winner regardless of which client issued the request.

  Quickest fix: configure the MCP only in the host you're actively using,
  or close the other host before starting work. If you genuinely need
  multiple hosts at the same time, give each a distinct port pair via
  `ABLETON_OSC_RECV_PORT` / `ABLETON_OSC_SEND_PORT` and update
  AbletonOSC's `consts.py` for whichever instance gets the non-default
  pair.
- A previous AbletonMCP server still alive (kill it).
- Another OSC tool — TouchOSC bridge, Open Stage Control, etc.
- A second Live instance.

If you genuinely can't free the ports, change them on **both sides**:

1. Edit AbletonOSC's `consts.py` (under your User Library) to use new
   ports, restart Live.
2. Set `ABLETON_OSC_SEND_PORT` / `ABLETON_OSC_RECV_PORT` env vars in your
   MCP client config to match.

## Firewall

Even though traffic is loopback-only, some endpoint-protection products
block UDP between processes by default.

- **Windows Defender Firewall:** add an inbound *allow* rule for the
  Python interpreter at `D:\Code\AbletonMCP\.venv\Scripts\python.exe`.
- **Norton / McAfee / corporate EDRs:** whitelist the Python EXE.

A quick way to confirm firewall is the issue: `netcat` test.

```powershell
# in one terminal — listener
.\.venv\Scripts\python.exe -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(('127.0.0.1', 11001)); print(s.recvfrom(2048))"
```

If the listener never returns when AbletonOSC sends a `/live/test` reply,
the firewall is dropping the packet.

## Tools time out under load

Symptoms:
- Smoke test passes.
- Bulk operations (e.g. `track_list` on a 30-track set, `clip_list` on a
  scene with many clips) intermittently raise `AbletonOSCTimeout`.

Why: AbletonOSC processes calls on Live's main audio thread. While the
song is playing, the budget per call is small.

Fixes:

- Bump `ABLETON_OSC_TIMEOUT=10` in the MCP client config.
- Stop transport before bulk reads if you don't need them live.
- Call `live_get_state` to confirm `is_playing=False` before fan-outs.

## "AbletonOSC did not reply on /live/test"

The smoke test prints exactly this. Walk down:

1. Live is open and AbletonOSC is the active control surface.
2. Live's status bar showed "AbletonOSC v… loaded" at startup. If not,
   Live failed to import the script — check Live's Log.txt for a stack
   trace.
3. No port conflict (above).
4. The host you're sending to actually has Live running. If you set
   `ABLETON_OSC_HOST` to a remote IP, double-check both ports are
   forwarded.

## Listener handles overflow

`listen_poll` returns `dropped_total > 0` when AbletonOSC is firing
events faster than you're polling. Either poll more often or pass a
larger `max_buffer` to the `listen_*` tool.

For very chatty channels (`song.beat`, `song.current_song_time`,
`clip.playing_position`) consider only subscribing while you actively
need the data.

## Log file paths

| Component | Path |
|-----------|------|
| Claude Desktop server logs | `%APPDATA%\Claude\logs\mcp-server-ableton.log` |
| Claude Code | `~/.claude/logs/` |
| Cursor | open MCP settings → click the server's status icon |
| Ableton Live | `%USERPROFILE%\AppData\Roaming\Ableton\Live <ver>\Preferences\Log.txt` |
| AbletonMCP server | logs to stderr (captured by whichever client launched it) |

Bump verbosity with `ABLETON_MCP_LOG=DEBUG` to trace every OSC frame.

## When all else fails

Open an issue with:
- The output of `live_ping` from `live_get_state`.
- The last 50 lines of the relevant log.
- Live version and OS version.
- `pip list | findstr -i osc` so we know which `python-osc` you have.
