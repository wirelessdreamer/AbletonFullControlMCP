# Ableton MCP demos

These are actual Ableton Live 11.3.43 window captures. Project preparation, model separation, transport, mixer changes and sound-design actions ran through the MCP server. Computer Use framed the views, captured the screenshots, and opened/saved demonstration copies. The screenshots contain no composited interface elements.

The two project-import demos are a **development preview**. They require a checkout that registers `song_prepare_project`, `song_import_job_status` and `song_verify_imported_project`; this documentation update does not ship that implementation. Instrument and transport tools are documented in the [tool reference](TOOLS.md).

The source material is an original 24-second, twelve-bar sketch at 120 BPM in 4/4. It is generated locally by [make_demo_sources.py](../examples/make_demo_sources.py): six synthetic audio parts, a stereo mix, and a 36-note companion MIDI file. The part labeled Vocals is a synthesized melody, not a human recording. No account or external media is needed to generate these sources. Demucs may fetch its model weights on first use.

## Suno stems and MIDI split and sync

> “Here are my Suno stem ZIP and MIDI exports. Split the MIDI into editable parts, pair each part with its audio, check the sync through the song, and prepare an Ableton project I can audition.”

This development-preview feature automates the handoff from a Suno export to Live: track creation, MIDI part splitting, audio/MIDI pairing, tempo conversion, collected media and sync checks. Suno performs stem separation and MIDI transcription; the importer works with the resulting local files and does not generate missing MIDI.

### 1. Export matching stems and MIDI from Suno

In Suno Studio, extract the stems, select the desired stem and choose **Get MIDI**. Repeat for the parts you want to edit. Use **Export → Multitrack** for the audio tracks. Get MIDI uses credits; see [Suno's export instructions](https://help.suno.com/en/articles/13925249). Suno currently lists Studio MIDI downloads for Premier subscribers, with availability depending on the song and account: [supported download formats](https://help.suno.com/en/articles/13926081).

Keep audio and MIDI from the same song version and export range, and retain file beginnings, silence and tails. Keep a selected-range or individual-clip export's original timeline position with the files; those files may not begin at the same origin as a full-song export. Put companion MIDI in the stem folder or supply its separate folder alongside the ZIP.

For an intentionally fixed-tempo export, Suno documents selecting **Manual BPM** before exporting the stems and using that same BPM in the destination DAW. Export matching MIDI from that project state too. See [Suno's tempo-drift guidance](https://help.suno.com/en/articles/8363457). If preserving a variable-tempo performance, let the importer inspect the MIDI tempo map and review any grid uncertainty.

### 2. Point the assistant at both local exports

```json
{
  "tool": "suno_prepare_project",
  "arguments": {
    "source_paths": [
      "D:/Music/Suno/Song Stems.zip",
      "D:/Music/Suno/Song MIDI"
    ],
    "destination": "D:/Music/Song - Ableton Project"
  }
}
```

Use a new destination folder. If the ZIP already contains all companion MIDI files, supply only that ZIP in `source_paths`. The shortcut imports prepared stems without running Demucs again. Poll the returned job ID with `song_import_job_status`. Supply BPM, meter or a source downbeat when known; omitting an unknown value is preferable to guessing. The native-template and Live-verification setup is described in the fixture walkthrough below.

### 3. Split, pair and check the parts in Live

The importer creates an audio track per stem and a separate MIDI part for each supported source track/channel containing notes. It preserves note timing, duration, velocity and supported controller data, and places paired MIDI beside its audio. With no tempo override, matching companion MIDI tempo maps supply the grid; conflicting maps remain reviewable. Audio keeps its native speed with Warp off.

Timing checks compare MIDI onset patterns against the paired audio at multiple points. A clear mismatch is reported; repeating or sparse parts can remain ambiguous. The report can require review even when the files open and play. No automatic quantization, independent stem stretching or speculative offset correction is applied.

When filenames do not establish a pairing, use `song_prepare_project` with explicit `midi_pairings`. The same tool accepts known placements through `offsets_seconds`. For example, **only after confirming that Piano.mid is consistently 160 ms early**, a new import can delay that MIDI by 0.16 seconds:

```json
{
  "tool": "song_prepare_project",
  "arguments": {
    "source_paths": ["D:/Music/Suno/Matching Export"],
    "destination": "D:/Music/Song - Corrected Project",
    "source_mode": "prepared_stems",
    "midi_pairings": {"Piano.mid": "Piano.wav"},
    "offsets_seconds": {"Piano.mid": 0.16}
  }
}
```

That value is an illustration, not a standard Suno correction. Offsets are nonnegative delays from the source origin. A constant offset does not solve changing drift; review tempo-map or export mismatches instead of nudging every stem independently.

### 4. Audition audio, then the MIDI alternative

First playback uses the audio stems. MIDI starts muted with an available preview instrument, so the same musical part is not doubled. To audition a MIDI part, mute its recorded audio counterpart and unmute the MIDI track. A preview instrument plays the exported notes; it does not recreate the original Suno sound or guarantee transcription accuracy.

The project includes `import_manifest.json` and `validation_report.json`. Read `needs_review` items before treating sync as finished; `song_verify_imported_project` can recheck Live playback, note readback and portable reopening. Once timing is confirmed, the paired MIDI is ready to edit or assign to your preferred instruments.

## Prepared stems and companion MIDI: reproducible fixture

> “Prepare this folder of stems and MIDI as a Live project at 120 BPM in 4/4. The source downbeat is at zero. Keep the MIDI muted until I audition it.”

![Prepared audio stems playing with companion MIDI and the piano roll visible](images/prepared-stems-midi.jpg)

The import creates six audio tracks and a paired MIDI track with a stock Operator preview instrument. All audio starts at the shared source origin, without warping. The screenshot shows playback and the imported notes, with the MIDI track muted. Track colors and the twelve-bar playback loop were applied for the demonstration after verification.

The same prepared-stem workflow accepts a local Suno export ZIP or folder; it does not run separation again. This screenshot uses generated WAV/MIDI fixtures rather than a Suno download.

Generate the original fixtures from the repository root after installing the project dependencies:

```powershell
python examples/make_demo_sources.py data/readme_demo_sources
```

The destination must be new. Call this MCP tool with absolute paths appropriate to your machine:

```json
{
  "tool": "song_prepare_project",
  "arguments": {
    "source_paths": ["D:/Music/demo_sources/prepared_stems"],
    "destination": "D:/Music/Prepared Stems Project",
    "source_mode": "prepared_stems",
    "bpm": 120,
    "time_signature": [4, 4],
    "downbeat_sec": 0
  }
}
```

The default response returns a job ID. Poll `song_import_job_status`; background preparation can include Live verification. Before a verification run, stop transport and preserve any work in the active set. The importer needs Live 11 track scaffolding: in the development checkout, `song_use_import_template(template_als_path)` caches the currently loaded set after it has been saved by the running Live build. The demo used that native reference for its schema and compatibility metadata.

In this capture, `song_verify_imported_project` confirmed playback, all 36 MIDI notes, the MIDI instrument preview, and reopening both the original and a relocated project. The result remained `needs_review`: repeated chord onsets could not establish a unique audio/MIDI offset. Successful playback does not silently certify uncertain musical timing.

## One recording to six stem tracks

> “Take Studio Sketch.wav, separate it into six stems, and prepare a project with the original mix muted for reference.”

![Six real Demucs outputs playing in Live with the original mix muted](images/recording-six-stems.jpg)

```json
{
  "tool": "song_prepare_project",
  "arguments": {
    "source_paths": ["D:/Music/demo_sources/Studio Sketch.wav"],
    "destination": "D:/Music/Six Stem Separation Project",
    "source_mode": "separate_recording_6stems",
    "bpm": 120,
    "time_signature": [4, 4],
    "downbeat_sec": 0
  }
}
```

This example ran the actual `htdemucs_6s` model. The six outputs are Drums, Bass, Other, Vocals, Guitar and Piano; a seventh track contains the muted original mix. Playback, source duration, relative media paths, compatible template metadata and both original/relocated reopening passed verification as `ready_audio_only`. Separation results depend on the source: the model's categories do not guarantee perfect isolation or recovery of the original synthetic parts.

## Change an instrument while auditioning MIDI

> “Mute the recorded piano, audition its MIDI part, and make Operator warmer.”

![Operator during playback after a device-aware MCP sound-design action](images/sound-design-operator.jpg)

The demonstrated calls were `track_set_mute`, `view_select_device`, and:

```json
{
  "tool": "sound_apply_descriptor",
  "arguments": {
    "track_index": 5,
    "device_index": 0,
    "descriptor": "warm",
    "intensity": 0.25,
    "dry_run": false
  }
}
```

Readback through `device_get_parameters` confirmed Tone changed from about 77% to 69%, and normalized Filter Freq from 0.438 to 0.394. The preset's filter was off, so the cutoff edit was stored but not active in the sound. The tool returned `partial`, reporting that its `B Level` rule did not match the installed Operator parameter name. The screenshot shows the actual result, not a claim that every rule applied.

The playback examples also used `live_set_loop`, `live_jump_to_beat`, `live_play`, `live_stop`, `view_select_track`, `track_set_name`, and `track_set_color_index`. Their effects are visible in the transport, track headers, meters and selected device.

## Capture notes

The screenshot assets are native JPEG window captures. The source audio, model outputs, Live projects and detailed MCP logs remain generated local data; they are not embedded in this repository. The fixtures are available through the generator above. UI saving of a demonstration copy does not imply that Live 11 exposes a native MCP save API.
