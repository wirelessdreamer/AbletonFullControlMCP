# `song_load_wav_to_arrangement` confirmed broken on Live 11.3.43

**Status:** CONFIRMED Live LOM limitation. `Track.create_audio_clip` on Live 11.3.43 cannot programmatically load a wav from an absolute path. Verified across: (a) original session, (b) Live restart, (c) full system reboot, (d) fresh empty Live Set on a fresh boot. Same probe errors every time.

**Resolution:** SKILL.md updated to require a manual drag step on Live 11.3.x. Auto-load remains in the codebase for future Live builds (12.x, or any 11.3.x point release that adds the missing C++ binding) — the bridge probe table will pick it up automatically when Live exposes a working signature.

## What we observed (2026-05-12 session)

Calling `song_load_wav_to_arrangement(wav_path=…, track_name=…)` on Live 11.3.43 with bridge 1.4.0 returns:

```
{
  "status": "not_supported",
  "loaded": false,
  "attempt_errors": [
    "create_audio_clip(file_path, position) -> returned None",
    "create_audio_clip(file_path) -> ArgumentError: Python argument types in\n    Track.create_audio_clip(Track, str)\ndid not match C++ signature:\n    create_audio_clip(class TTrackPyHandle, class TString, double)",
    "create_audio_clip(basename, position) + clip.file_path = fp -> RuntimeError: Please provide an absolute path"
  ]
}
```

Same exact error on:
- First attempt (Live just opened)
- After bridge reinstall via `python -m ableton_mcp.scripts.install_bridge --force`
- After full Live restart (File → Quit, reopen)
- Both with the user's original session AND with 4 existing tracks muted

## What we ruled OUT

- **Bridge not installed / wrong version** — direct `bridge.call("system.version")` returns `{"bridge_version":"1.4.0","handlers":[…40+ handlers including create_arrangement_audio_clip…],"live_version":"11.3.43"}`. Bridge IS 1.4.0. The `project_describe` display showing `bridge_version: 0.0.0` is a separate stale-cache bug — see follow-up task.
- **Stale module bytecode in Live** — confirmed by Live restart not changing the probe attempts (i.e. Live IS running the latest bridge code).
- **Hidden working API method** — ran `clip._probe_audio_clip_creation`. Live's only relevant method on `Track` is `create_audio_clip` itself; no secret variant exists.
- **Wav file format issue** — files are standard 44.1 kHz 16-bit WAV; same files presumably worked before.
- **Wrong track type** — `_create_audio_track` in `song_flow/load_to_arrangement.py` correctly sends `/live/song/create_audio_track`, and the new track has audio I/O.

## What we did NOT yet rule out

1. **System-level state** — the user wants to do a full system reboot and retest. Reasons this could matter:
   - Live's audio engine sometimes gets stuck in odd states that persist across Live restarts but clear on system reboot.
   - Windows file-handle / sample-cache state may be wedged.
   - Some background process (other DAWs, audio drivers, ASIO router) could be interfering with Live's sample-loading subsystem.
2. **Live version-point release nuance** — `create_audio_clip` semantics may differ between 11.3.43 and whatever the user had before. Worth confirming via `live_ping` reply that the version hasn't changed unexpectedly.
3. **Project state** — current session has 4 generic tracks (2 MIDI, 2 Audio) at 232 beats. Maybe an empty starter session behaves differently. Worth retesting with **File → New Live Set** before loading.

## Test plan after system reboot — EXECUTED 2026-05-12

1. `live_ping` — ✅ Live 11.3 reachable, `ok: true`.
2. `project_describe` — ✅ Now reports `bridge_version: "1.4.0"`, `compatible: true`. The stale-cache display bug appears to have been cleared by the reboot (or by the bridge having a clean first-handshake after the bridge reinstall). Cosmetic follow-up (`claude/fix-project-describe-stale-bridge-version`) may still be relevant for the next time someone hot-swaps a bridge mid-session.
3. **File → New Live Set** — ✅ confirmed via project_describe (back to default 2 MIDI + 2 Audio, no instruments).
4. `song_load_wav_to_arrangement(Psalm 10 - Why.wav)` — ❌ **Same exact probe errors as before reboot**:

   ```
   create_audio_clip(file_path, position) -> returned None
   create_audio_clip(file_path) -> ArgumentError [signature mismatch]
   create_audio_clip(basename, position) + clip.file_path = fp -> RuntimeError: Please provide an absolute path
   ```

5. **Conclusion:** real Live 11.3.43 LOM limitation. Not fixable from our side; the missing capability is `Track.create_audio_clip(TString, double)` with full-path acceptance.

## What changed in SKILL.md

`~/.claude/skills/practice-pack/SKILL.md` Pipeline A step 2 now includes the manual-drag fallback note for Live 11.3.x builds. Auto-load remains the documented happy path because (a) Live 12 may fix it, (b) point releases of 11.3.x may add the missing binding, (c) the bridge probe will auto-pick-up any working signature without code changes.

---

## Automation deep-dive — attempts to bypass manual drag

Three attempts to programmatically drop wavs onto Live, all blocked by different OS-level constraints. Captured here so the next person doesn't re-investigate from scratch.

### Attempt 1: `WM_DROPFILES` from inside Live's process (bridge handler)

Idea: the bridge Python remote script runs INSIDE Live's process. Posting `WM_DROPFILES` to Live's own window would be intra-process, no cross-process marshaling needed.

**Killed by:** Live's bundled Python (Remote Scripts) doesn't ship `ctypes`. Sandboxed. Can't call Win32 APIs from inside Live's Python.

Code: bridge handler `clip.drop_wav_via_wmdropfiles` in `live_remote_script/AbletonFullControlBridge/handlers/clips.py`. Returns `{ok: false, error: "ctypes/wintypes import failed"}` and is otherwise a no-op stub.

### Attempt 2: `WM_DROPFILES` from MCP server process (cross-process)

Idea: the MCP server's Python has full ctypes. Post `WM_DROPFILES` cross-process to Live's main window. Windows kernel marshals the DROPFILES payload to the target process.

Code: `src/ableton_mcp/local_automation/wm_dropfiles.py`.

**Killed by:** Live's main window does NOT have `WS_EX_ACCEPTFILES` set, and has **zero Win32 child windows** (UI is custom-drawn, not a tree of native controls). `WM_DROPFILES` is silently ignored.

Verified with `GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_ACCEPTFILES → False` and `EnumChildWindows → 0 children`.

### Attempt 3: Direct `IDropTarget` interface call (cross-process)

Idea: Live IS registered as an OLE drop target. The window property `OleDropTargetInterface` holds the `IDropTarget` pointer. Unmarshal it cross-process, build a CF_HDROP `IDataObject`, call `IDropTarget::DragEnter` + `Drop` directly. No mouse simulation needed.

**Status:** Confirmed Live exposes the property. Pointer value: e.g. `0x4186da0`. But `ReadProcessMemory(our_process, p_unk, ...)` returns `ERROR_PARTIAL_COPY (299)` — **the pointer is in Live's address space, not ours**. Direct dereference segfaults.

**Killed by:** No public Windows API exists to convert a remote `IDropTarget` pointer into a cross-process proxy without going through `DoDragDrop` first. The OLE drag-drop protocol is fundamentally designed around `DoDragDrop` + real mouse tracking; calling `IDropTarget` methods on a window you don't own was never a supported scenario. (Could be done via DLL injection into Live's process, but that's a non-starter.)

### Conclusion + open paths

The only viable automated paths require **real mouse simulation**:

1. **`DoDragDrop` + `SendInput`** — proper OLE protocol; the system mouse-tracks our synthetic clicks to Live's window; `IDropTarget` fires on mouse-up. Most "correct" technically. Estimated 3-4h to implement + harden. Downside: user can't touch the mouse during batch runs.

2. **`pyautogui` drag from Explorer** — Explorer initiates the drag (it already works with Live); we just simulate the click+drag motion. Estimated 1-2h. Same mouse-takeover constraint.

3. **Manual drag** — current workaround. Reliable, slow per-song.

4. **Live 12 LOM upgrade** — Live 12 may have added a working `create_audio_clip(file_path, position)` C++ binding. Free if the user upgrades and tests; the existing bridge probe table will auto-pick-up any signature that works.

### Attempt 4: `DoDragDrop` + `SendInput` (real mouse simulation)

Idea: implement the canonical Windows OLE drag-drop protocol from our process. CoInitialize STA → build `IDataObject` via clipboard (CF_HDROP) → build minimal `IDropSource` via ctypes vtable → press mouse via SendInput → call `DoDragDrop` (modal loop) → drive cursor to Live's window from a background thread → release mouse → `IDropTarget::Drop` fires in Live.

Code: `src/ableton_mcp/local_automation/ole_dragdrop.py`. Implementation complete (~370 lines) — IDropSource vtable, GUID helpers, CF_HDROP byte layout, mouse-driver thread, OleInitialize/OleUninitialize lifecycle. Code looks technically correct and follows the documented protocol exactly.

**Killed by:** `DoDragDrop` hung in its modal loop indefinitely (~3 min CPU before kill) — the mouse-release from `SendInput` was never observed by the OLE modal loop's mouse-state polling. Track count stayed at 4 throughout; Live's `IDropTarget` was never invoked.

Most likely cause: synthetic mouse events generated by `SendInput` carry the `LLMHF_INJECTED` flag in the input event header. Windows' low-level mouse polling (`GetAsyncKeyState` / the `DoDragDrop` internal hook) may ignore injected events for security (UIPI / SmartScreen-style filtering, especially for desktop apps not running with elevated privileges). Live itself may also be running at a different integrity level than our MCP server process.

Could potentially be fixed by:
- Running the MCP server elevated (admin privileges may bypass UIPI for injected input)
- Using a different mouse-event mechanism (`PostMessage WM_LBUTTONUP` to a specific window instead of `SendInput`)
- Implementing a real `IDropSource` that DOESN'T wait for mouse state — has its `QueryContinueDrag` return `DRAGDROP_S_DROP` immediately based on a timer/flag instead of polling MK_LBUTTON

But these are diagnostic guesses, and at this point we've exhausted the time-box. Result: **the codebase has a complete OLE drag-drop scaffold that doesn't quite work**; future investigation can pick it up.

## Files referenced

- `src/ableton_mcp/song_flow/load_to_arrangement.py` — wrapper that calls the bridge
- `live_remote_script/AbletonFullControlBridge/handlers/clips.py` — bridge handler with the 3 probe variants (lines 595-705)
- `live_remote_script/AbletonFullControlBridge/handlers/clips.py` `_probe_audio_clip_creation` (lines 754+) — diagnostic
- `src/ableton_mcp/scripts/install_bridge.py` — bridge installer (has the unicode-arrow crash on cp1252 consoles — separate follow-up)

## Pending follow-ups already spawned

- `claude/fix-install-bridge-unicode` — fix `→` in install_bridge.py print statements
- `claude/fix-project-describe-stale-bridge-version` — make project_describe always re-call system.version instead of using a cached value

## Practice-pack request that was blocked

User wanted practice packs for these 3 songs, transposed down 2 semitones:

- `C:\Users\dreamer\Downloads\TestSet\Psalm 10 - Why.wav`
- `C:\Users\dreamer\Downloads\TestSet\Psalm 11 - The Lord still rules from heaven.wav`
- `C:\Users\dreamer\Downloads\TestSet\Psalm 13 - Long Enough.wav`

Target output: `<input_dir>/practice_pack/down_2/<song>/` as 44.1 kHz 16-bit WAV per `~/.claude/skills/practice-pack/SKILL.md`.
