# Using AbletonMCP with Claude Code

Claude Code reads MCP servers from two places:
- **Project scope** — `<repo>/.mcp.json` at the repo root (only sessions
  started in that directory see it).
- **User scope** — `~/.claude.json` (every session sees it).

The unified installer writes both for you.

## One-shot install (recommended)

```powershell
D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.install_clients --user
```

That writes:
- `<repo>/.mcp.json` — project scope
- `~/.claude.json` — user scope (the `--user` flag)
- `%APPDATA%\Claude\claude_desktop_config.json` — Claude Desktop, in the
  same pass

Skip Desktop with `--no-desktop`. Skip the project file with `--no-project`.
Preview with `--dry-run`. Roll back with `--uninstall`.

After the installer finishes:

1. `/exit` your current Claude Code session.
2. Relaunch `claude` from any directory.
3. Run `/mcp` — `ableton` should show `connected` with ~196 tools.

## Manual install

If you'd rather not run the script, drop this into `<repo>/.mcp.json`:

```json
{
  "mcpServers": {
    "ableton": {
      "type": "stdio",
      "command": "D:/Code/AbletonMCP/.venv/Scripts/python.exe",
      "args": ["-m", "ableton_mcp"],
      "env": {
        "ABLETON_OSC_HOST": "127.0.0.1",
        "ABLETON_OSC_SEND_PORT": "11000",
        "ABLETON_OSC_RECV_PORT": "11001",
        "ABLETON_MCP_LOG": "INFO"
      }
    }
  }
}
```

Or use Claude Code's CLI:

```powershell
claude mcp add ableton --scope user -- D:/Code/AbletonMCP/.venv/Scripts/python.exe -m ableton_mcp
```

`--scope user` puts it in `~/.claude.json` (every project sees it).
`--scope local` keeps it tied to the current project.

## Smoke test from chat

```
Use the ableton tool live_ping and tell me what version of Live is running.
```

A good follow-up to confirm the bridge is wired:

```
Use inventory_scan_browser with category="instruments" and dry_run=True.
List my installed instruments.
```

## Tips

- **Per-project config wins.** A `.mcp.json` in the working directory
  overrides anything in `~/.claude.json` for the same server name.
- **Tool permissions:** by default Claude Code prompts before each new tool.
  To allow ableton tools without prompting, add this to
  `<repo>/.claude/settings.local.json`:
  ```json
  { "permissions": { "allow": ["mcp__ableton__*"] } }
  ```
  Or set it user-wide in `~/.claude/settings.json`.
- **Logging:** Claude Code captures the server's stderr. Bump
  `ABLETON_MCP_LOG=DEBUG` (in the `env` block) for OSC frame traces.
- **Multiple Lives:** copy the `ableton` block under a different name
  (`ableton_studio`, `ableton_laptop`) and change `ABLETON_OSC_HOST` per
  copy.

## Troubleshooting

### `/mcp` shows the server but every call hangs
Run the smoke test from the same shell to confirm Ableton is reachable:

```powershell
D:\Code\AbletonMCP\.venv\Scripts\python.exe -m ableton_mcp.scripts.smoke_test
```

If that hangs, AbletonOSC isn't actually enabled (Preferences →
Link/Tempo/MIDI → Control Surface = AbletonOSC).

### `Failed to spawn server: ENOENT`
The `command` path is wrong. Use forward slashes on Windows or double-escape
backslashes in JSON. The unified installer always uses forward slashes.

### Tools succeed but mutations don't appear in Live
Confirm AbletonOSC AND AbletonFullControlBridge are both **active** control
surfaces (the dropdowns in Preferences → Link/Tempo/MIDI must be set, not
just installed).

### Tools list is empty after restart
JSON syntax error somewhere in `.mcp.json` or `~/.claude.json`. Validate
both. Or rerun the unified installer — it parses then rewrites, surfacing
errors clearly.

For more, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
