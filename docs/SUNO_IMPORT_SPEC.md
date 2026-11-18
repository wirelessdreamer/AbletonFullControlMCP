# Recording and stem import feature spec

Status: Draft proposal. Defaults below remain open to product discussion.

Implementation: both source paths now share `song_prepare_project`, with a prepared-Suno shortcut, durable jobs and a Live verification gate. See [the implementation guide](PROJECT_IMPORT.md) for supported formats, timing overrides and current review conditions. Live 11 has no native set-save API; the implementation writes and checkpoints importer-owned `.als` artifacts directly, then reopens them through MCP without screen control. Native saving of arbitrary user edits is unsupported. End-to-end certification requires a running Live instance past any recovery dialogs; static construction retains `needs_review`.

Point the assistant at an existing Suno stem download or one mixed recording and receive a saved Ableton project that opens at the beginning and plays the intended song. For a mixed recording, first create six stems with Demucs. Include supplied MIDI as editable parts, aligned with the corresponding audio. Both features share the same project setup and completion checks.

Example request: "Set up this Suno download in Ableton so I can open it and hit play."

Additional request: "Take this recording, split it into six stems with Demucs, and set up the Ableton project so I can open it and hit play."

## Proposed user experience

1. Accept either prepared stems in a local ZIP, folder or file list, or one mixed audio recording. Discover optional reference audio, MIDI and available metadata. For a mixed recording, run Demucs six-stem separation before proceeding.
2. Identify the song version, parts, source timing, tempo and meter. Resolve ambiguous versions or timing only when the files cannot establish a reliable choice.
3. Build a new project containing the media, audio tracks and any supplied MIDI. Preserve the source performance and organize related parts together.
4. Open, inspect, audition, save and reopen the generated set. Check timing through the song, routing, missing media, instrument availability and saved playback state.
5. Return the project path and a short result such as: "Ready: 8 audio stems, 3 paired MIDI parts, 108 BPM. Audio plays; MIDI is muted and ready to edit."

The proposed first playback uses the imported or newly separated audio stems. Paired MIDI sits beside its audio, muted, with an available stock instrument for auditioning. The reference mix is muted; for a separated recording, that reference is the original recording. This avoids doubling the same part. Instrument previews reproduce the MIDI notes; they do not promise the original sound.

Both features start with local files. Prepared stems bypass separation, and may contain any supported number of parts. The single-recording path explicitly uses six-stem separation. Acquiring an export from a Suno URL, generating music, and generating missing MIDI remain separate features.

Suno offers multitrack, selected-range and individual-clip exports, and derives MIDI by analyzing stems. These export scopes may have different origins. MIDI availability also varies by song and account. Inspect the actual files and treat missing MIDI as a valid audio-only input. See [Exporting from Studio](https://help.suno.com/en/articles/8128193) and [download formats](https://help.suno.com/en/articles/13926081).

## Input contract

Required input is either prepared stems in one source package/file list or one mixed audio file, plus a destination project folder. Optional inputs are song title, reference mix, companion MIDI, expected BPM and meter, audio-to-MIDI pairings, export range, source origin and known timing offsets. Record whether each timing value was supplied, embedded, inferred or estimated. Use an explicit source mode when one file could be either a finished stem or a recording to separate.

Discover files recursively without relying on one Suno archive layout. Support WAV and standard MIDI first, including files exported separately into a companion folder. Validate actual file contents. MP3 support requires a tested decoder and encoder-delay handling; otherwise report it as unsupported and request the equivalent WAV export. Preserve source bit depth and sample rate when copying WAV files.

Create a manifest with content hashes, original names and paths, role, version, audio frame count, sample rate, channel count and duration. For MIDI, record file type, tick resolution, tracks/channels, tempo and meter events, notes and expressive events. Record excluded files and why they were excluded. Do not guess that a full mix is another stem, or combine alternate takes and duplicate export variants.

Combining individual clips or selected-range exports with other ranges requires a known shared origin or explicit placement. A bare clip that starts at its own zero does not establish where it belongs in the full song. A complete set exported from one selected range may use that range's start as its shared origin. Preserve ambiguous material for review instead of placing it at an invented position.

Extract archives into bounded staging storage with path containment checks. Copy media into the new project before generating the set. Originals remain unchanged; derived files retain their relationship to the originals in the manifest.

## Separation from a single recording

Use Demucs `htdemucs_6s` to produce vocals, drums, bass, guitar, piano and other. Import these as six separate Ableton audio tracks, preserving stereo within each output. "Six stems" describes musical parts; the requested deliverable is a multitrack project. The model adds guitar and piano to its four basic sources: [Demucs model documentation](https://github.com/facebookresearch/demucs#separating-tracks).

Accept mono and stereo recordings in the first version. Convert mono to the model's required channel layout and resample once to the model's sample rate. Record all conversions and preserve real-time duration and the source origin. Reject unsupported channel layouts explicitly until a defined downmix policy exists; do not discard channels. Retain the unchanged original recording inside the project as a muted reference.

Validate exactly six uniquely named, readable, finite audio outputs. They must share a sample rate, frame count and origin, with the expected duration after resampling. Verify separation and import introduced no unexplained delay, truncation or segment-boundary shifts. Use the original recording and an unscaled sum of the stems for timing and reconstruction diagnostics; exact cancellation is not a readiness requirement. Any model padding or delay correction must be measured, shared across stems and recorded.

Write derived stems as float32 WAV by default to preserve model output without integer clipping. Preserve relative output levels, with any required playback attenuation applied equally across the stems. Do not normalize outputs independently. Check for clipping before file conversion and during Live playback. Existing downloaded WAV stems continue to be copied at their original format.

All six categories remain visible even when an instrument is absent and its output is near silent. Report silence and separation artifacts separately from timing. Separation does not guarantee isolated original instrument recordings; the upstream documentation specifically notes limitations in piano separation. Treat a structurally valid but obviously unusable result as `needs_review` rather than silently omit a part. See [Demucs model documentation](https://github.com/facebookresearch/demucs#separating-tracks).

Use GPU when available, with CPU execution supported for the same six-stem model. Recover from resource failures with bounded retries and recorded settings. A model download or execution failure must never silently substitute a four-stem model. Surface the failing stage and retain validated intermediate work for retry.

Run separation as a cancellable job. Cache completed outputs by source content hash, model/checkpoint identity, software version and separation settings. Include stochastic settings in provenance; do not promise byte-identical regeneration. Two recordings with the same filename must not share output accidentally. Incomplete outputs cannot be reused as completed results, and cancellation must stop processing or isolate its writes before cleanup.

Feed the validated stems into the shared alignment, project assembly and verification pipeline below. Detect tempo using the original recording and suitable separated material. Demucs does not supply MIDI in this workflow; companion MIDI, when supplied, follows the same pairing and timing checks as the prepared-stem path. Audio-only completion remains valid when MIDI is absent.

## Alignment rules

Three separate checks determine correctness:

| Check | Required behavior |
| --- | --- |
| Audio against audio | Preserve the shared export origin and all source offsets, leading silence and tails. Confirm the import added no shift, stretch or truncation. |
| Audio against the musical grid | Establish BPM, meter, first downbeat and any tempo changes, then check beat alignment across the song. |
| MIDI against audio | Establish the matching take and timing basis, preserve event timing and expression, and check offset and drift against the paired audio. |

### Audio timing

Default to native audio timing with Warp off. Disable looping, reset inherited sample-region offsets, and avoid automatic fades that alter initial attacks. Never trim each stem to its first audible event or stretch each stem independently. Ableton explicitly recommends preserving file beginnings and disabling automatic warping when importing stems: [Ableton stem import guide](https://help.ableton.com/hc/en-us/articles/360000843404-Importing-and-exporting-stems).

Different durations can reflect tails or partial exports. Inspect the common time range and provenance; do not require equal frame counts, truncate longer stems, or pad shorter ones to conceal a mismatch. Matching lengths alone do not prove musical synchronization. Weak correlation between different instruments is also insufficient evidence to move a stem.

For a fixed-tempo export, set the actual saved set tempo and meter to the validated values. Resolve half-time or double-time interpretations using downbeats and musical context. Preserve pickups and count-ins with a shared pre-roll or timeline offset; the first audible sound need not be beat 1.1.1.

For variable tempo, preserve audio at native speed and build one shared tempo map where the evidence supports it. Suno documents tempo drift and a Manual BPM export workflow: [Fixing Tempo Drift](https://help.suno.com/en/articles/8363457). A single average BPM is insufficient to certify a drifting performance. Unresolved maps produce `needs_review`; they do not silently trigger quantization or stretching. A later option may intentionally conform the whole song to one tempo using a shared timing transformation.

### MIDI timing

Parse all tempo events and source ticks. For a shared tempo map, preserve source musical positions. For a different destination map, convert source ticks through the complete source tempo map into seconds, then through the destination map into beats. Never multiply all event seconds by the first BPM. Distinguish an implicit default MIDI tempo from a tempo explicitly supplied by the export.

Import every supported musical part, preserving note starts, durations, velocities, channel identity, sustain, pitch bends and relevant controllers. Keep the original MIDI file. If a supplied event type, time division or independent sequence cannot be represented, name the limitation and require review when it changes playback or editing. Do not silently drop expression or collapse multiple parts into one instrument.

Pair MIDI using source metadata, explicit mappings, names and musical evidence. Check whether it belongs to the original audio or a tempo-locked export. Compare several passages in each available part, including early and late material; sparse parts need different checkpoints from drums.

Classify a mismatch as constant offset, tempo-scale mismatch, or changing drift. Apply known offsets from reliable metadata or explicit user input. Estimated corrections require a calibrated confidence threshold and a consistent match across checkpoints; otherwise present the proposed correction for review. Derive corrected material without overwriting originals. Do not quantize or transpose by default.

MIDI transcription quality is separate from timing. An aligned transcription can still contain incorrect or missing notes. Instrument attack latency must also be distinguished from event placement when auditioning a MIDI preview.

## Saved project behavior

Create one audio track per stem in Arrangement View, with related MIDI adjacent and consistently named and colored. Preserve source names in the manifest even when track labels are simplified. Include an optional muted reference track. Session clips must not override Arrangement playback.

Use a clean, version-compatible template. Explicitly configure routing, sends, devices, clip gain, pan, mute, solo, record arm, monitoring, clip offsets, fades, loops and automation. Template mixer or effect settings must not accidentally alter the song.

Keep relative stem levels and panning. Measure the summed audio and apply one documented common gain reduction if necessary to provide at least 3 dB of sample-peak headroom. Do not normalize each stem separately or add mastering effects. Check clipping in actual Live playback as well as the offline sum. The stem sum need not null against a mastered reference mix.

Assign only verified, installed instruments to MIDI previews. Live requires an instrument to sound MIDI and does not automatically assign General MIDI sounds: [Understanding MIDI files](https://help.ableton.com/hc/en-us/articles/209068169-Understanding-MIDI-files). Missing preview instruments must be reported rather than leave unexplained silent tracks.

Save with Arrangement View visible, transport stopped at the intended playback start, recording and overdub off, loop and metronome off, and no unintended solo state. Retain the complete ending and tails.

Store audio and original MIDI inside the project. Resolve media from within the project after relocation. Include `import_manifest.json` and `validation_report.json` with versioned schemas. Create the output through staging and publish it after validation; retries must not duplicate tracks or overwrite a previously edited set. Changes to source content create a new revision unless an explicit replacement policy applies.

Building the project must not discard work in the active Live set. Verification needs a supported way to protect that set before opening another. If that cannot be established, retain the generated artifact and report the pending verification action.

## Validation and completion

Record expected and observed values for each check, with evidence and any correction applied. Track readiness for stem timing, grid timing, each supplied MIDI part, media portability and playback separately.

Proposed import-fidelity tolerances are at most one source audio frame for audio placement and duration, and at most the greater of one source MIDI tick or 1 ms for representable MIDI event timing. For generated stems, use their output sample rate for frame tolerances and validate resampling against the original real-time duration separately. These measure changes introduced by the importer. They do not certify separation quality, source transcription or musical performance.

Grid and inferred MIDI alignment need instrument-aware tolerances calibrated on representative exports before automatic correction ships. Tests must cover the beginning, middle, end and tempo transitions, plus each sparse part's available passages. Beat detection confidence and the number and spread of usable checkpoints must accompany any inferred result. An unchecked region or low-confidence match cannot receive a passing result merely because the other files passed.

The completion gate requires:

1. Static checks of the generated set, unique references, all source media and expected event counts.
2. Opening in the supported Live build and reading actual Arrangement clips, paths, markers, warp settings, tempo, meter, devices and routing.
3. Playback checks at distributed checkpoints and analysis of the full active audio span for clipping and unintended silence. Audition MIDI previews separately so intentional muting does not mask a broken instrument.
4. Confirming that the intended set was saved, reopening it, and repeating essential state and timing checks.
5. Opening a relocated copy with original download paths unavailable, proving that the project contains its required media.

| Result | Meaning |
| --- | --- |
| `ready` | All required checks passed for the audio and every included MIDI part. |
| `ready_audio_only` | All audio, grid, playback, save/reopen and portability checks passed, and MIDI was absent or explicitly excluded by the user. |
| `needs_review` | A usable artifact may exist, but timing, MIDI, instruments, portability or Live verification remains unresolved. |
| `failed` | No usable project could be built. |

Supplied MIDI that fails validation cannot silently turn into `ready_audio_only`. Record `verification_level` separately, such as `static` or `live_reopened`; a generated set checked only on disk remains `needs_review`. The user result names the specific remaining action and affected part.

## Implementation plan

Expose a proposed shared `song_prepare_project` tool with source paths, destination, source mode (`prepared_stems` or `separate_recording_6stems`), optional timing/pairing overrides and playback preference. A `suno_prepare_project` convenience wrapper selects prepared stems. Use a durable job for discovery, optional separation, analysis, building and verification, with progress, cancellation and resume. An inspection mode returns the source inventory and planned processing without running separation or changing Live.

Implement reusable logic under a proposed `project_import/` package: discovery, manifest, separation adapter, timing model, MIDI conversion, project assembly and validation. Keep the existing generation and low-level stem tools available. An already separated download bypasses Demucs entirely.

| Existing component | Reuse and required change |
| --- | --- |
| `tools/suno.py` | Existing `suno_import_stems` splits a single file; `stems_import_to_live` creates empty tracks. Add a separate prepared-download workflow. |
| `stems/demucs.py` | Reuse the six-stem Python API and device selection. Add output validation, safe float output, shared timing checks and collision-free job storage. The current wrapper writes PCM16 under model/basename and truncates inputs with more than two channels; those behaviors do not meet this contract. |
| `local_automation/als_synth.py` | Reuse audio set scaffolding. Add project media references, explicit master tempo/meter and tempo maps, clean inherited state, MIDI support and version checks. Its current `tempo` argument only calculates clip lengths. |
| `tools/midi_files.py` | Replace first-tempo-only conversion for this workflow with a complete timing model and preserve supported expressive events. |
| Bridge Arrangement handlers | Reuse clip creation, note insertion and direct read-back where supported. Extend missing event and validation coverage. |
| Project and transport tools | Reuse inspection and playback controls. Add verified open/save/reconnect/reopen orchestration. A save request alone is insufficient proof of a saved file. |

The existing ALS builder documents use with Live 11.3.43. Direct audio loading through the Live API is unreliable on that build, so the implementation must prove a supported template-based route with no manual stem dragging. Template and Live compatibility are explicit capabilities; an unknown version receives an actionable result rather than a success claim.

Deliver in four increments:

1. Prove project creation and open/save/reopen on the target Live build using a real Suno export and a recording separated with Demucs. Establish a clean template and media portability.
2. Ship both source paths, six-stem separation jobs, fixed-tempo audio import, routing, native playback, headroom and validation.
3. Add paired MIDI, complete tempo conversion, expression preservation, preview instruments and reliable variable-tempo maps. Unsupported cases remain visible in the report.
4. Harden mixed export variants, interruption recovery and the acceptance matrix. A later feature may add Suno link acquisition and intentional tempo conformance.

## Acceptance cases

| Input or condition | Expected outcome |
| --- | --- |
| Fixed-tempo WAV stems with leading silence and optional reference mix | One shared origin, full tails, correct saved BPM, stems audible and reference muted. |
| One mono or stereo recording | Demucs produces six aligned tracks; the unchanged original is a muted reference; shared playback and portability gates pass. |
| Recording at a different sample rate from the model | Resampling preserves duration, pitch and origin; all six outputs share the validated timebase. |
| Missing instrument or nearly silent separated part | Keep all six named categories and report the near-silent part without inventing musical content. |
| Missing/corrupt separated output or accidental four-stem model | Prevent a ready result and identify the separation failure; no silent model substitution. |
| GPU resource failure, interruption or two recordings with the same basename | Retry with recorded settings or resume safely; outputs do not collide and incomplete work is not reused. |
| Separation output peaks above integer full scale | Float files retain output; equal playback attenuation prevents clipping without changing relative stem levels. |
| Audio plus matching MIDI | Every part imported at the correct position; previews work; initial playback has no doubling. |
| No MIDI | Verified audio project returns `ready_audio_only`. |
| MIDI with tempo changes, pickup and sustain | Event times and expression survive import and save/reopen; the first-tempo-only regression fails the test. |
| Known offset or nonlinear drift in supplied MIDI | Known offset is preserved or corrected as specified; uncertain drift returns `needs_review`. |
| Original audio with changing tempo | Native audio is preserved and a validated shared grid follows it, or grid validation remains unresolved. |
| A selected-range stem mixed with full-song stems | Require a known range origin; do not silently place all files at zero. |
| Different sample rates or tail lengths | Duration and relative timing stay correct; no pitch change or truncation. |
| Duplicate takes, ambiguous names or unpaired MIDI | Resolve the take or pairing explicitly; report unresolved parts. |
| Empty/corrupt audio, unsupported MIDI events or missing instruments | Identify the affected file or feature and prevent a false ready result. |
| Relocated project with Downloads unavailable | Set reopens and plays without missing media. |
| Template containing effects, loop state or muted tracks | Output uses the explicit import configuration, with no inherited playback changes. |
| Interrupted run or repeat import | Resume or create a clean revision, preserving originals, existing edits and unrelated Live work. |

Use synthetic fixtures for exact timing and drift regressions, real Suno exports for package and transcription behavior, real mono/stereo recordings for separation behavior, and Live integration fixtures for playback, schema compatibility and saved state. Include at least one audio-only export, one audio/MIDI pair, a tempo-locked export, an original-timing export and a recording requiring resampling. A successful XML test alone cannot satisfy the playback requirement.

## Product choices still open

The proposed defaults are local files, a new project, six-stem Demucs separation for a mixed recording, audio playback and muted MIDI previews. The two immediate choices are whether Suno link acquisition belongs in the first release and whether initial playback should instead use MIDI instruments for some parts. A real export is needed during implementation to establish archive layout, metadata and MIDI timing behavior for the supported export types.
