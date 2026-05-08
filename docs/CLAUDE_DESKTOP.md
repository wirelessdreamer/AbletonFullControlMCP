# Using AbletonMCP with Claude Desktop

The fastest path: run the unified installer, then restart Claude Desktop.
The manual path is below if you'd rather edit the JSON yourself.

## One-shot install (recommended)

```powershell
D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.install_clients
```

That:
- locates `%APPDATA%\Claude\claude_desktop_config.json` (creating it if it
  doesn't exist),
- merges in an `ableton` MCP server entry (preserving any existing servers),
- writes a `.bak.<timestamp>` next to the original,
- also updates `<repo>/.mcp.json` so Claude Code in this project sees the
  same server.

Add `--user` to also write to `~/.claude.json` (Claude Code user-scope, so
sessions started anywhere on the machine see the server).
Add `--dry-run` first to preview the diff. Add `--uninstall` to roll back.

After the script reports `Done.`:
1. **Fully quit Claude Desktop** — right-click the system-tray icon → Quit.
   Just closing the window leaves the daemon running.
2. **Relaunch.**
3. The hammer/tool icon in the chat composer should now show ableton tools
   (~196 of them across 25 categories — see [TOOLS.md](TOOLS.md)).

## Manual install

Press `Win+R` and paste `%APPDATA%\Claude\claude_desktop_config.json`. If
the file doesn't exist, create it. Merge in:

```json
{
  "mcpServers": {
    "ableton": {
      "command": "D:/Code/AbletonMCP/.venv/Scripts/python.exe",
      "args": ["-m", "ableton_mcp"],
      "env": {
        "ABLETON_OSC_HOST": "127.0.0.1",
        "ABLETON_OSC_SEND_PORT": "11000",
        "ABLETON_OSC_RECV_PORT": "11001",
        "ABLETON_OSC_TIMEOUT": "5",
        "ABLETON_MCP_LOG": "INFO"
      }
    }
  }
}
```

Notes:
- `command` is the **absolute** path to the venv's Python. Forward slashes
  are fine on Windows; backslashes need to be escaped (`\\`).
- `env` is optional; every variable has a default. Bump
  `ABLETON_MCP_LOG=DEBUG` to trace OSC traffic.
- For multiple Live machines on a LAN, set `ABLETON_OSC_HOST` to the remote
  Ableton machine's IP and forward UDP/11000 + UDP/11001 there.

## Smoke test from chat

```
Use ableton's live_ping tool and tell me what version of Live is running.
```

Claude should call `live_ping` and report your running Live version.
A good follow-up:

```
Run inventory_scan_browser with category="instruments" and dry_run=True.
List what you find.
```

## Troubleshooting

### "Server failed to start"
- Open `%APPDATA%\Claude\logs` and read `mcp-server-ableton.log`.
- The most common cause is the `command` path being wrong. Test it from
  PowerShell directly:
  ```powershell
  & "D:\Code\AbletonMCP\.venv\Scripts\python.exe" -m ableton_mcp
  ```
  It should print nothing and wait for stdin (Ctrl+C to exit).

### Tools appear but every call times out
- AbletonOSC isn't enabled or Live isn't running. Open Live and verify both
  AbletonOSC and AbletonFullControlBridge are picked in
  Preferences → Link/Tempo/MIDI → Control Surface. Then run:
  ```powershell
  D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.smoke_test
  ```

### Port 11001 is in use
- Another process (often a previous AbletonMCP instance, or another OSC
  tool) is bound to UDP/11001. Either kill it, or set the
  `ABLETON_OSC_RECV_PORT` env var **and** point AbletonOSC at the same port
  via its config. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for details.

### Logs

- **Claude Desktop logs:** `%APPDATA%\Claude\logs\mcp-server-ableton.log`
- **AbletonMCP server logs:** the server logs to stderr, which Claude Desktop
  captures into the file above. Bump `ABLETON_MCP_LOG=DEBUG` for OSC traces.
- **AbletonOSC logs:** Live's standard log file
  (`%USERPROFILE%\AppData\Roaming\Ableton\Live <version>\Preferences\Log.txt`).

### Common errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: ableton_mcp` | Wrong Python in `command` | Use the venv's `python.exe`, not the system one. |
| `OSC request ... timed out` | AbletonOSC not enabled | Preferences → Link/Tempo/MIDI → Control Surface = AbletonOSC. |
| `[WinError 10048] Only one usage of each socket address` | Port 11001 busy | Kill the other listener or change ports. |
| Tools list empty after restart | JSON syntax error in config | Validate at https://jsonlint.com/ — or rerun the unified installer; it backs up and rewrites cleanly. |

## Rolling back

```powershell
D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.install_clients --uninstall
```

Removes the `ableton` entry from each config (other servers are left alone)
and writes a fresh `.bak`. The original pre-install `.bak` files remain
untouched, so you can also restore those by hand.
