# Using AbletonMCP with Cursor

Cursor's agent mode supports MCP servers natively. AbletonMCP plugs in via
the same stdio transport it uses for Claude Desktop and Claude Code.

## Prerequisites

- Cursor 0.45+ (older builds had a different MCP config layout).
- Ableton Live 11 with **AbletonOSC** enabled.
- AbletonMCP virtualenv at `D:\Code\AbletonMCP\.venv` (`pip install -e .`).

## Step 1: Open Cursor settings

`Cmd/Ctrl+,` → search **MCP** → click **Add new global MCP server**. This
opens `~/.cursor/mcp.json` (Windows: `%USERPROFILE%\.cursor\mcp.json`).

For a per-project setup, create `.cursor/mcp.json` at the repo root instead.

## Step 2: Paste the server block

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
        "ABLETON_MCP_LOG": "INFO"
      }
    }
  }
}
```

Save the file. Cursor watches it and reloads the server automatically; if it
doesn't, click the **Refresh** button next to the entry in MCP settings.

## Step 3: Verify

Open Composer (`Ctrl+I`), switch to **Agent** mode, and type:

```
Run ableton.live_ping and tell me Ableton's version.
```

Cursor should propose the tool call; approve it. The first call after a
Cursor restart can take a couple of seconds while the venv warms up.

## Tips

- Cursor lets you **disable** individual tools. If you only want the
  read-only ones (`*_get`, `*_list`, `live_get_state`), turn off the
  `*_set_*`, `*_create_*`, `*_delete_*` entries in MCP settings.
- Add a project rule so the agent always pings before issuing work:
  ```
  Always call ableton.live_ping at the start of an Ableton task. If it
  returns ok=false, tell the user to enable AbletonOSC instead of
  proceeding.
  ```

## Troubleshooting

### Server icon shows red / "Failed to start"
Click the red dot for the stderr log. The most common causes are the same
as for Claude Desktop — wrong Python path, port already taken, venv missing.
See [CLAUDE_DESKTOP.md](CLAUDE_DESKTOP.md#troubleshooting) and the global
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### Tools time out only when the song is playing
That's usually fine — Live's main thread is busier during playback. Bump
`ABLETON_OSC_TIMEOUT=10` in the `env` block.
