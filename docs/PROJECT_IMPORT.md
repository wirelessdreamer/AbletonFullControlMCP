# Recording and stem project import

Use `song_prepare_project` to turn prepared audio stems or one mixed recording into a portable Ableton Live 11 Arrangement project. A ZIP or folder uses the prepared-stem path. One audio file uses Demucs `htdemucs_6s`, producing drums, bass, other, vocals, guitar and piano. `suno_prepare_project` always selects prepared stems and never separates them again.

Both paths copy media into a new project, retain silence and tails, disable warping and clip loops, configure routing and headroom, and write `Song.als`, `import_manifest.json` and `validation_report.json`. MIDI parts are imported with notes, velocities, sustain, pitch bends and controller envelopes. They are muted initially; an installed stock Operator preset provides a melodic preview. The original recording or supplied reference mix is muted alongside the active stems.

## Handoff from official Suno MCP

Connect the [official Suno MCP](https://suno.com/mcp) and this Ableton MCP in the **same client on the machine running Live**. The client handles Suno sign-in. In Claude Code, from the project where you use Ableton:

```powershell
claude mcp add --scope local --transport http suno https://api.suno.com/mcp
claude mcp get suno
```

Use `/mcp` in Claude Code to authorize Suno if needed. This is separate from the legacy `suno_generate` tool and does not use `SUNO_API_KEY`.

> Use the Suno MCP for the completed stems and companion MIDI of this song version. If it returns actual export file links, hand the ZIP or the named stem and MIDI links to `suno_import_mcp_export`, then poll the Ableton job and report any sync issues. If the connector cannot provide the exports, tell me which ones to export in Studio. Ask before starting any credit-spending Suno action.

`suno_import_mcp_export` accepts `export_files` as `[{"filename": "Stems.zip", "url": "<direct URL from Suno MCP>"}]` or one entry per WAV/MP3/MIDI file, and `destination` as a new folder. Use the actual filenames when possible; names are used for pairing. MIDI is required by default, so an export with no playable MIDI does not quietly become an audio-only project. Set `require_midi=false` if audio only is intended. Pairing, delay and tempo options work as for local exports.

The Ableton server downloads actual HTTPS file links on Suno-owned domains and passes the local copies to the same prepared-stem job. It never sends account credentials with these requests. Songs, share pages and links that require an authenticated browser are not downloadable file links. Downloads happen before the job ID is returned; signed URLs are not retained in Ableton's job records, and staged files can be reused on job retry. If the host already saved the files locally, call `suno_prepare_project` with those paths and `require_midi=true` when MIDI is expected. For exports the Suno connector does not expose, follow the [Studio walkthrough](DEMOS.md#suno-stems-and-midi-split-and-sync) and import the downloaded files.

## Calling the tools

Prepare an existing Suno stem download:

```json
{
  "tool": "suno_prepare_project",
  "source_paths": ["C:/Users/me/Downloads/Song.zip"],
  "destination": "D:/Music/Song Project",
  "bpm": 108,
  "time_signature": [4, 4],
  "downbeat_sec": 0
}
```

Separate a single recording and build the same project layout:

```json
{
  "tool": "song_prepare_project",
  "source_paths": ["D:/Music/recording.wav"],
  "destination": "D:/Music/Recording Project",
  "source_mode": "separate_recording_6stems",
  "bpm": 120,
  "time_signature": [4, 4],
  "downbeat_sec": 0
}
```

Omit BPM or downbeat when unknown. The importer estimates beat timing and reports unresolved grid or bar alignment. `downbeat_sec` is a known bar downbeat in the source, including any leading silence. All files move together if pre-roll is needed to place that downbeat on a bar.

The default response contains `job_id`. Poll `song_import_job_status(job_id)` until `state` is `done`, `failed` or `cancelled`; the final project result is under `result`. Use `song_import_job_cancel` to stop the worker, including model inference. `song_import_job_resume` retries failed or interrupted work and reuses validated separation outputs. Existing project folders are never overwritten; choose a new destination when a project already exists. Job records and progress persist under the configured data directory across MCP reconnects. A host that terminates its entire process tree can also stop a worker; heartbeat expiry identifies it as interrupted and resume reuses validated separation outputs.

Set `inspect_only=true` on `song_prepare_project` to list input files and valid filename selectors without separating audio or opening Live. A single stem should use `source_mode="prepared_stems"` explicitly. Choose a destination outside an input folder to avoid recursively importing previous output.

Additional `song_prepare_project` options:

| Option | Meaning |
| --- | --- |
| `reference_path` | Reference audio path, or unique filename inside a ZIP. |
| `offsets_seconds` | Map source filenames to nonnegative placements in seconds, preserving the full files. MIDI offsets use the same source timebase. |
| `midi_pairings` | Map MIDI filenames to corresponding audio filenames or track labels. |
| `audio_roles` | Map audio filenames to `stem` or `reference` to resolve ambiguous names. Unclassified audio alongside named stems is muted pending selection. |
| `tempo_map` | Explicit shared tempo changes as `[[beat, bpm], ...]`, beginning at beat zero. Audio remains at native speed. |
| `template_als_path` | Compatible Live 11 set supplying track scaffolding. Defaults to `ABLETON_IMPORT_TEMPLATE`, then the cached native template, then the newest complete schema among installed factory/Core Library templates. |
| `verify_live` | Defaults to true. False builds on disk and leaves readiness at `needs_review`. |
| `background` | Defaults to true. False waits for the worker result. |

WAV, FLAC, AIFF and MP3 mono/stereo audio are supported. MP3 decoding requires FFmpeg and ffprobe on PATH, creates float WAVs at the original rate, honors gapless skip/padding metadata, and keeps byte-identical MP3 originals under Sources/Original. AAC and other unsupported media are named in the report. Invalid Suno key annotations are retained in the original MIDI and ignored for playback with a warning; notes, velocities and timing remain intact. A common companion MIDI tempo map supplies the project grid when no tempo was supplied. MIDI uses standard PPQ SMF0/SMF1 files. SMPTE timing, SMF2 independent sequences, dynamic program changes and unsupported expressive events require review or conversion; original MIDI remains inside the project. Meter changes are reported for review rather than silently flattened as a certified import.

## Verification and readiness

Install the current companion bridge with `python -m ableton_mcp.scripts.install_bridge --force`, then restart Live. Bridge 1.7 adds direct import state inspection, playback probes and preview restoration. An older bridge cannot certify an imported project.

For the save-format compatibility check, save a set with at least one audio and one MIDI track in the running Live 11 build, then call `song_use_import_template` with the path to that open set. The tool caches a reference in the local MCP data directory; neither the source set nor its edits are changed.

All Live operations run through the bridge; no screen capture, mouse input or keyboard input is used. `op_project_capabilities` reports the running API. `op_reload_bridge` applies installed lifecycle fixes without restarting Live or closing its listening socket. Live 11 does not expose native set save; an unsaved set can also omit file-path properties: `op_save_set` reports `saved=false, supported=false` instead of claiming a save. The importer writes the portable `.als` directly. Track and master faders stay at unity (XML amplitude 1.0, API fader value about 0.85); shared linear headroom attenuation is stored in each stem clip’s SampleVolume. `project.import_checkpoint` checks its recorded SHA-256; the MCP host validates the gzip/XML schema, since Live's embedded Python may omit expat; this is an artifact checkpoint, **not a native save of arbitrary Live edits**.

`op_open_set` uses the OS file association through the bridge and refuses to replace a set with undoable edits, active playback/recording or a dialog. The verifier identifies an imported set by all track names and absolute collected-media paths, explicitly reporting that evidence instead of inventing a native document filename. It checks actual Arrangement clips, media paths, tempo, markers, warp settings, MIDI notes and devices, auditions several positions and MIDI previews, validates the generated artifact, and reopens both a relocated copy and the original. Preview changes are grouped and undone before switching sets. If Live still asks to save those transient audition changes, the verifier uses Live's native dialog API only after checking a short-lived certificate for the source artifact hash, restored track/device controls, no undoable edits and the exact Save Changes dialog. Other dialogs or changed controls stop verification. A pending import-open request can be cancelled through `op_cancel_import_open` without saving or discarding the active document. Verification never saves or discards arbitrary user edits. The explicit `op_open_set(..., launch_if_unavailable=true)` option can send a normal OS file-open request from the MCP host if the bridge is offline; it reports an unconfirmed request and leaves Live’s save/error dialogs untouched. A global import-verification lock prevents workers from sharing the Live transport.

Run `song_verify_imported_project(project_path)` to retry Live checks after resolving a pending item. A process interrupted during Live verification can leave `data/project_import_jobs/live-verification.lock`; after confirming no verification worker is active, remove that stale lock before retrying.

Playback and reopening do not prove save compatibility. The report also compares the exported template's compatibility release with the running Live release. Older or unknown formats remain `needs_review`, even after successful playback. The exporter preserves genuine template schema and overwrite-protection metadata; it does not relabel an older file as a newer version. The installed `Builtin/Templates/DefaultLiveSet.als` can be older than Core Library templates. `save_compatibility.native_save_tested` stays false because the observed Live API cannot perform a native save. If an already open older-format import has edits, use Live's **Save As…** to a new filename to preserve those edits and convert the file. See [Ableton's compatibility guidance](https://help.ableton.com/hc/en-us/articles/360000841004-Backward-Compatibility).

| Result | Meaning |
| --- | --- |
| `ready` | All required checks passed, including supplied MIDI, artifact checkpoint, Live reopen/portability and matching template release. Native save remains untested. |
| `ready_audio_only` | The same checks passed for audio, and MIDI was absent. |
| `needs_review` | A usable project exists, but timing, media roles, MIDI, instruments or Live verification needs attention. |
| `failed` | The job could not build a usable project; its log and retained staging explain the failure. |

Static construction alone never returns ready. Tempo estimates do not establish a downbeat. MIDI checks compare distributed onset patterns and competing offsets; sparse, sustained or repeating patterns remain reviewable when they cannot establish a unique match. These checks do not certify transcription accuracy or perfect source isolation. No automatic quantization, independent stem stretching, four-stem substitution or MIDI generation occurs.

Separation writes float WAVs with shared timing and validates all six categories. Its cache includes source content, model identity, software versions and inference settings, and checks output hashes. CPU execution remains available when CUDA is absent or runs out of memory. Quiet or absent instruments retain their tracks; warnings about silence do not alone block readiness.

## Command line

The same implementation is available without an MCP host:

```powershell
python -m ableton_mcp.project_import inspect "C:\Downloads\Song.zip" --destination "D:\Music\Song Project"
python -m ableton_mcp.project_import prepare "C:\Downloads\Song.zip" --mode prepared_stems --destination "D:\Music\Song Project" --bpm 108 --meter 4/4 --downbeat 0
python -m ableton_mcp.project_import prepare "D:\Music\recording.wav" --destination "D:\Music\Recording Project" --bpm 120 --meter 4/4 --downbeat 0
python -m ableton_mcp.project_import status JOB_ID
python -m ableton_mcp.project_import cancel JOB_ID
python -m ableton_mcp.project_import resume JOB_ID --destination "D:\Music\Song Retry Project"
python -m ableton_mcp.project_import verify "D:\Music\Song Project"
```

Use `--no-live` for construction only, `--template` for an explicit template, and `--settings settings.json` for an object containing `offsets_seconds`, `midi_pairings`, `audio_roles` or `tempo_map`.


## Live acceptance checks

Validated through MCP on Windows with Live 11.3.43:

- A prepared archive with seven MP3 stems and seven MIDI files loaded as seven audio tracks and 38 MIDI parts. All 8,022 notes survived readback, all MIDI previews produced audio, and the original and relocated project passed media/path checks. Source meter and musical timing uncertainty remained `needs_review`.
- A 12-second recording ran the real `htdemucs_6s` model, produced all six float stems and a muted reference, passed three playback probes, and reopened from a relocated folder. The original acceptance run did not check save compatibility; its older template subsequently triggered Live's Save As conversion warning.
- Native Save Changes handling was verified without rewriting the checked source artifact. The active original project was left stopped at its beginning.
