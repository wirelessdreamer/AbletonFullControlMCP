# Research Report: Automatic Song-Section Annotation in Ableton via Lead-Sheet / Recording Correlation

*(Synthesized 2026-07-26 from verified, adversarially cross-checked research. Facts marked "unverified" carried only a PLAUSIBLE verdict.)*

---

## 1. MuScriptor: what we found

**MuScriptor is real, and it is exactly the name you used — no typo.** It is an open-weight **multi-instrument audio-to-MIDI transcription model** released publicly on **July 10, 2026** by **Kyutai** (the French open-science lab) and **Mirelo** (a music-AI startup). Code is MIT on GitHub; weights are gated on Hugging Face under **CC BY-NC 4.0** (fine for personal practice packs, blocks commercialization); `pip install muscriptor` (v0.2.2, 2026-07-21) works today.

**The critical finding: MuScriptor is not a song annotator.** The Hugging Face model card states directly that the open model outputs **only note events** — pitch, onset/offset in seconds, and instrument (36-class vocabulary including bass, guitar, piano, drums, and **Voice**). It does **not** detect chords, key, tempo, sections, lyrics, or velocity. The "detects chords, key, and tempo" language in launch coverage refers to **Mirelo Studio**, Mirelo's proprietary web app running an improved closed version of the model — free to try, but with **no documented API or batch mode today** (their api-docs page only documents a Text-to-SFX endpoint; the product page advertises "Access via API," so this may change, but it is not automatable now).

**So "song annotation with MuScriptor crosschecked against a lead sheet" needs reframing:** MuScriptor cannot annotate sections, but it *can* serve as a high-quality symbolic front-end — converting the recording into per-instrument note events that you then reduce to per-bar chord estimates and align against the lead sheet. Notably:

- It fits your machine exactly: Windows GPU is documented via **cu128 wheels** (`--torch-backend=cu128`), matching your nightly cu128 torch for the RTX 5090. Python 3.10–3.12 per README (3.13 claimed only in PyPI classifiers). Automation surfaces are clean: CLI with `--format jsonl` streaming to stdout, a streaming Python API (`NoteStartEvent`/`NoteEndEvent`), and a FastAPI serve mode with SSE.
- Accuracy is state-of-the-art for full-mix AMT but noisy in absolute terms: **Onset F1 60.4 / Frame F1 72.4 / Offset F1 48.6** (large model, ~1.3B params, held-out 372-track test set). Any chord inference built on it must aggregate per bar (e.g., bass-weighted pitch-class histograms), not trust individual notes.
- The **Voice class** (21.09% of training tracks) is a genuinely useful side signal: vocal-activity spans distinguish sung sections from instrumental intros/solos absent from the chart. (That application is our inference, not a source claim.)
- Known quirk: 5-second chunking can flicker instrument labels at boundaries; mitigate with `--instruments voice,drums,acoustic_bass,acoustic_guitar,acoustic_piano`-style conditioning.

**Honest assessment:** for *this* task, MuScriptor is optional. What the lead sheet gives you is chords, and chroma-based features (librosa, NNLS-Chroma) or a dedicated chord-recognition model get you to chord-comparable features more directly. MuScriptor earns its place if you want the bass line (root motion) and vocal-activity signals from one model — or for future per-instrument practice features.

---

## 2. The landscape

The task decomposes into three sub-problems:

**(a) Get structure from the audio.**
- **`allin1` (All-In-One, WASPAA 2023)** — joint beats/downbeats/BPM + functional segments `{start, end, label}` with the 10-label Harmonix vocabulary (`intro, outro, break, bridge, inst, solo, verse, chorus, start, end`). Original repo is dead (last commit Oct 2023) and needs NATTEN, which has no official Windows wheels. **The maintained fork `all-in-one-infer` (PyPI v3.1.0, released 2026-07-12) removes every blocker**: pure-PyTorch attention (self-reported numerically identical to NATTEN), `madmom-infer` and `demucs-infer` replacements (no Cython builds, no conflict with your existing `demucs`), explicit Windows support, `torch>=2.0` unbounded, MIT, same original checkpoints, and a pre-computed-stems API.
- **SongFormer (Oct 2025, CC-BY-4.0)** — current SOTA: on SongFormBench-Harmonix, SongFormer variants score ACC up to 0.807 and HR.5F up to 0.703 vs allin1's 0.740/0.596 (note: those two bests come from *different* SongFormer configs; every config still beats allin1 on both metrics). But it pins `torch==2.4.0` + Linux-only `triton==3.0.0`, is tested only on Ubuntu 22.04, and torch 2.4 has no sm_120 kernels — running it on your 5090 means an unpinned separate venv, which is unverified. MuQ weights it depends on are CC-BY-NC.
- **Classical/unsupervised** (librosa Laplacian segmentation — already in your venv; MSAF from git) — boundaries + repetition-group IDs only, no functional labels. Acceptable here because the lead sheet supplies the names.
- Commercial: **Music.ai API** (sections as `{start, end, section}` JSON, official Python SDK; pay-as-you-go per minute — **no free tier**, contrary to first-pass research) and **zplane deCoda** (~$58 Windows app; the only consumer tool that exports sections as SMF markers + tempo map — manual GUI export only).

**(b) Get structure from the lead sheet.**
- **SongSelect ChordPro** (Premium, not every song): rigidly regular — blank-line blocks, `{title:/key:/tempo:/time:}` header, sections as `{comment: Verse 1}` directives (not `{start_of_verse}` environments), `[E]`-bracket chords, CCLI footer block. **Contains no bar counts, barlines, or rhythm.** Verified from three independent codebases that parse it.
- **SongSelect PDFs** (chord sheets, lead/vocal sheets): born-digital vector PDFs — extract the text layer (pdfplumber/pypdf, already installed) rather than OMR. OMR (Audiveris, oemer) is inapplicable to staff-less charts and drops chord symbols even on real lead sheets.
- **MusicXML (MuseScore)** via **music21 v10.5.0** (June 2026 — *not* v9.7.1 as first researched; requires Python ≥3.11, compatible with your 3.11.9 venv): the **only** input with exact bar counts (measures + RehearsalMarks + ChordSymbol + `repeat.Expander`).
- Python ChordPro parsers on PyPI are stale/minimal (python-chordpro is v0.0.2 from 2021, self-described subset). **Write a ~60-line custom parser.**
- Acquisition: CCLI retired the SongSelect API partner program. Community tools use the user's logged-in session against undocumented endpoints (`GetSongChordPro`, `GetSongLeadPdf`, ...). Safest design: user downloads the file; pipeline watches a folder.

**(c) Align the two.** No off-the-shelf tool does "lead sheet with sections/repeats vs. structurally different recording." But every ingredient has strong prior art:
- **JumpDTW** (ISMIR 2010): score partitioned into blocks; DP adds transitions from any block-end to any block-start; >99% bar-level accuracy on Beethoven. Blocks map directly to lead-sheet sections.
- **Hierarchical DTW** (Shan & Tsai, ISMIR 2020): subsequence-match each section independently, then a segment-level DP with a tempo weight and jump penalty; beats JumpDTW by ~10–13 points when jump locations are unknown.
- **Forced alignment of known chord sequences** goes back to Sheh & Ellis (ISMIR 2003); Mauch et al. (SMC 2010) aligned internet-style chord sheets where chords are written once per section — your exact situation.
- **Transposition invariance**: score all 12 chroma rotations (Optimal Transposition Index from cover-song research; canonical OTI picks the rotation via 12 cheap dot products of global chroma profiles before aligning).
- **ChordSync** (SMC 2024, CTC forced alignment of a chord annotation) exists but presumes the annotation spans the audio — structural mismatch (extra intros/solos) is precisely its blind spot.

**Ableton write path (fully verified, including on your machine):** AbletonOSC exposes `/live/song/cue_point/add_or_delete` (LOM `set_or_delete_cue` — a *toggle* at the current Arrangement position), `/live/song/cue_point/set/name`, and `/live/song/get/cue_points` (flat `(name, time-in-beats)` pairs). `CuePoint.time` is read-only — moving a locator means delete + recreate. `cue_points` is in **creation order, not time order** (per AbletonOSC's author). Your `cue_points.py` MCP wrappers already cover all of it. Alternative: writing `<Locators><Locators><Locator>` XML (LomId/Time/Name/Annotation/IsSongStart, Time in beats) directly into the .als — schema verified against your own Live 11.3.43 sets and written programmatically by DawVert. Importing marker-bearing MIDI does **not** create locators (community-forum-sourced, unverified against official docs — but plan around it).

---

## 3. Recommended architecture

### Option A (recommended): audio segmentation with `all-in-one-infer` + chord-template cross-check against the lead sheet

```
WAV ──> beat_this ────────────────────────────┐ (beat/downbeat grid, BPM)
  │                                           │
  ├─> htdemucs_6s stems (existing) ──> map to 4 stems (guitar+piano→other)
  │                                           │
  ├─> all-in-one-infer (--stems-from-dir) ──> segments {start,end,label} + 100FPS label activations
  │                                           │
  └─> per-bar chroma (librosa, harmonic submix: bass+guitar+piano+other) ─┐
                                                                          │
lead sheet (.cho/PDF/MusicXML) ──> parser ──> ordered sections + chords ──┤
                                                                          v
                     section-sequence alignment (DP over allin1 segments,
                     validated/refined by chord templates, 12-rotation OTI)
                                                                          │
                                                                          v
                snap boundaries to nearest beat_this downbeat ──> cue points in Live
```

- **Libraries/install (this machine):** `pip install all-in-one-infer` into the main venv — pure PyTorch, `torch>=2.0` unbounded so pip should not touch your nightly cu128 build; **do not** install the `[natten]` extra. `demucs-infer` imports as `demucs_infer`, no clash with existing `demucs`. Parser side: ~60-line custom ChordPro parser + `pypdf`/pdfplumber (present) + `pip install music21` (v10.5.0) for MusicXML.
- **Accuracy/failure modes:** allin1 boundary HR.5F ≈ 0.60–0.66, label pairwise-F ≈ 0.74 — good enough as a scaffold, wrong labels ~1 in 4 segments. That is why the lead sheet cross-check matters: the DP re-labels and re-orders segments using the chart's known section sequence; allin1's `inst`/`solo` labels absorb recording-only material. Failure modes: merged pre-chorus/chorus boundaries; half-time feel confusing bar snapping; worship-specific vamps (one chord for 16 bars) giving chroma nothing to grab — mitigate with bass chroma from the Demucs bass stem and allin1's boundary activations as a novelty bonus.
- **Effort:** ~2–4 days. Lowest-risk path; everything Windows-verified.

### Option B: pure chord-template forced alignment (no segmentation model)

```
lead sheet ──> sections ──> per-bar 12-d chord templates (repeat-expanded, capo/key applied)
WAV ──> beat_this downbeats ──> per-bar chroma (harmonic submix)
        └──> cost matrix (sheet-bars × recording-bars, cosine)
             ──> JumpDTW-style DP: blocks = sections; jumps block-end→block-start;
                 wildcard/filler block for non-sheet material; relaxed start/end;
                 best of 12 chroma rotations (OTI)
             ──> section start bars ──> downbeats ──> cue points
```

- **Libraries:** nothing new — numpy/scipy/librosa/beat_this already installed. Optionally `pip install vamphost` (v1.3.2 ships win_amd64 wheels for CPython 3.10–3.13) + the NNLS-Chroma plugin DLL for tuned treble+bass chroma. Optional GPU cross-check: **BTC-ISMIR19** chord recognizer (pretrained 12 MB weights committed in-repo, plain PyTorch, ~83 Root WCSR) as an independent recognized-chord sequence to validate alignments.
- **Caveat:** SongSelect ChordPro has **no bar counts**, so per-bar templates require a harmonic-rhythm assumption (default 1 chord ≈ 1 bar, snapped to 2/4/8-bar phrases) — treat as a soft prior; the DP's tempo flexibility absorbs moderate error. MusicXML inputs give exact bar counts as hard priors.
- **Accuracy/failure modes:** no published number for this exact combination (honest unknown). JumpDTW achieved >99% bar accuracy in classical score-following where the score is complete; your setting is harder (incomplete sheets, vamps, one-chord stretches). Expect strong performance on harmonically active songs, weak on static vamps — same mitigations as Option A.
- **Effort:** ~1–2 days for the core; it is also the *engine* Option A's cross-check needs, so A and B share most code. **Build B's DP either way.**
- **Where MuScriptor fits:** as an alternative/additional feature extractor for this option — JSONL note events → per-bar bass-note histogram (root motion) + voice-activity mask. Adds a model download + GPU inference per song for signals chroma mostly already provides; recommend deferring to a later iteration.

### Option C: commercial/hosted import

- **Music.ai API**: sections as `{start, end, section}` in seconds, `pip install musicai-sdk`. Clean, but **pay-as-you-go per audio-minute (no free tier)**, cloud upload of your audio, and it solves only the audio side — you still need the alignment layer. Good as a cross-check oracle during development.
- **zplane deCoda** (~$58, Windows): detects structure, exports MIDI with **sections as SMF markers + tempo map** (full version, manual GUI export) and ChordPro lead sheets. Live will not import those markers as locators — parse with `mido`/`pretty_midi` and feed your cue-point writer. A reasonable manual-assist path, not an automated pipeline.
- **Effort:** ~1 day each, but neither eliminates the alignment work. Rank last.

---

## 4. Integration plan for AbletonFullControlMCP

**New MCP tools (data flows top to bottom):**

1. **`song_detect_sections(wav_path, stems_dir=None)`** — wraps `all-in-one-infer` (pass existing htdemucs_6s stems down-mapped to 4; else let it separate). Returns `{segments: [{start_s, end_s, label, confidence}], activations_path}`. Cache per song hash.
2. **`leadsheet_parse(path)`** — dispatch on extension: `.cho/.chordpro` → custom SongSelect-aware parser (blank-line blocks, `{comment: X}` headers, `[chord]` regex, stop at CCLI footer; also accept generic `{start_of_*}` environments, `{chorus}` recall expansion, and grid environments); `.pdf` → text layer + LLM structuring with `bars` nullable; `.musicxml/.mxl` → music21 (exact bars, `repeat.Expander` first). Output one IR: `{key, capo, time_sig, tempo, sections: [{label, chords[], bars|null, lyrics[]}]}`.
3. **`sections_align(sections_ir, wav_path)`** — the Option B DP: expand IR to per-bar chord templates in sounding pitch (key + capo); per-bar chroma on the beat_this grid from the harmonic submix; JumpDTW-with-filler DP; 12-rotation OTI (which also *reports the detected transposition delta* — feeds `song_transpose` sanity checks); fuse `song_detect_sections` labels/activations when available. Returns `{sections: [{label, start_bar, end_bar, start_beat, start_s, confidence}], transposition_semitones, rotation_score_margin}`.
4. **`cue_points_write(sections, mode="osc")`** — OSC mode (transport stopped): for each section, `live_jump_to_beat`/set `current_song_time` → check `cue_points_list` for an existing cue at that beat (the toggle would *delete* it) → `cue_point_add_or_delete` → re-fetch and **diff by time** (list is creation-ordered) → `cue_point_set_name` by index. `.als` mode: rewrite the inner `<Locators>` list of a Live-saved set (gzip round-trip, set closed in Live) for batch preparation.

**Downbeat snapping:** all detector output is in seconds; convert via the beat_this beat map (nearest downbeat, tie-break toward the allin1/novelty boundary activation peak), then beats = downbeat index × beats-per-bar for locator placement. Working on the bar grid also absorbs tempo drift in live recordings.

**Feeding existing tools:** the aligned section list is exactly a `structure_*` model — named sections with bar counts — so `structure_parse`'s data model gets a second producer, and `structure_loop_section` / `structure_jump_to_section` / `arrangement_find_sections` work unchanged. For practice packs, pass sections into `song_make_variations` so bounced outputs carry section metadata (and optionally per-section bounce ranges: "chorus-only loop, bass boost"). Section numbering (Verse 1 vs Verse 2) comes from the lead sheet's order, which the alignment preserves — neither audio model numbers sections.

---

## 5. Key risks & open questions

- **Transposition/capo mismatch** — handled by OTI over the alignment (whole progression votes, more robust than standalone key detection, which your memory notes confuses keys a 4th/5th apart). Apply `{capo}` before building templates. Risk: capo charts where the band *also* transposed; the 12-rotation search still finds it, but report `rotation_score_margin` and flag when the margin is thin.
- **ChordPro bar-count ambiguity** — chords-over-lyrics charts structurally do not encode bars (this is PLAUSIBLE-verified: true by construction of the format, but no fetched source states it outright). Never trust inferred counts; keep them soft priors. MusicXML is the only ground-truth-bars input.
- **One-chord vamps / ambient pads** — chroma is uninformative there; the DP can slide. Mitigations in order: bass chroma channel, structure-model boundary bonus, and (heaviest) lyric anchors via wav2vec2/WhisperX forced alignment against the ChordPro lyrics — the strongest Verse-1-vs-Verse-2 disambiguator, at degraded-but-usable accuracy on singing.
- **Dependency conflicts** — the main venv (Python 3.11.9, numpy 2.4.4, nightly cu128 torch) rules out: madmom from PyPI (2018, needs numpy<2; open numpy-2 issues as of May 2026), original allin1/NATTEN, autochord (no Windows), SongFormer as-pinned (torch 2.4, Linux triton). `all-in-one-infer` is the exception designed for exactly this situation — but verify at install that pip leaves the nightly torch untouched (unbounded `>=2.0` *should* be satisfied by the nightly version string; unverified until tried). MuScriptor likewise claims no hard torch pin — confirm at install.
- **Licensing/ToS** — SongSelect: API partner program retired; internal endpoints undocumented and session-authenticated; downloads count against the annual unique-song allowance; terms limit content to personal use. Use a watch-folder flow, keep any endpoint automation strictly user-initiated per song, and do not redistribute charts. MuScriptor weights and MuQ weights: CC BY-NC (fine personally; blocks productization). allin1/all-in-one-infer: MIT. SongFormer: CC-BY-4.0.
- **Open questions** — (1) Does the toggle-based cue-point recipe behave deterministically when Live's insert marker quantization is active? (Author-endorsed recipe, but test with transport stopped.) (2) all-in-one-infer's "numerically identical to NATTEN" is self-reported (golden-fixture tests), not independently benchmarked. (3) Real-world alignment accuracy on worship arrangements with heavy tags/vamps is unmeasured — the prototype exists to answer this. (4) Whether Mirelo ships an audio-to-MIDI API later (product page hints at it) — would change MuScriptor's role.

---

## 6. Suggested first prototype

**Smallest end-to-end slice (target: ~1–2 days):**

1. Pick 3 songs you already have as WAVs *with* SongSelect ChordPro charts — one harmonically busy, one with a long intro/solo not on the chart, one with a vamp/tag.
2. Write the ChordPro parser (sections + chords + key/capo; no bar counts). ~60 lines; unit-test against the 3 charts.
3. Compute per-bar chroma: beat_this downbeats + librosa CQT chroma on a harmonic submix summed from your existing htdemucs_6s stems (drop drums, attenuate vocals).
4. Implement the JumpDTW-with-filler DP (sections as blocks, 1-chord-per-bar templates, 12-rotation OTI, relaxed start/end). Pure numpy; the FMP notebooks provide reference DP code.
5. Write cue points over the existing `cue_point_*` MCP tools using the stop → seek → toggle → diff-by-time → rename recipe.
6. **Defer** for the prototype: allin1 (add in iteration 2 as label prior), MuScriptor, PDFs, MusicXML, lyric anchors.

**Success criteria:**
- Every chart section is placed, in chart order, with each boundary within **±1 bar** of a manually annotated ground truth on at least 2 of 3 songs.
- The intro/solo song: recording-only material lands in the filler block (no chart section stretched over it).
- Detected transposition matches the known chart-vs-recording delta on all 3 songs, with a clear rotation-score margin.
- Locators appear correctly named in Live 11 with no duplicate/deleted cues (idempotent re-run: second run makes zero changes).

If the vamp song fails at step 4, that is the expected failure — it triggers iteration 2 (bass chroma + allin1 boundary prior), not a redesign.

---

## 7. Sources

**MuScriptor / Mirelo**
- https://github.com/muscriptor/muscriptor
- https://raw.githubusercontent.com/muscriptor/muscriptor/main/README.md
- https://huggingface.co/MuScriptor/muscriptor-large
- https://arxiv.org/abs/2607.08168 / https://arxiv.org/html/2607.08168v1
- https://pypi.org/project/muscriptor/
- https://kyutai.org/blog/2026-07-10-muscriptor/
- https://mirelo.ai/blog/turning-audio-to-midi
- https://muscriptor.github.io/ ; https://muscriptor.kyutai.org

**Structure analysis**
- https://github.com/mir-aidj/all-in-one ; https://arxiv.org/abs/2307.16425 ; https://ar5iv.labs.arxiv.org/html/2307.16425
- https://pypi.org/project/allin1/ ; https://pypi.org/pypi/all-in-one-infer/json ; https://github.com/openmirlab/all-in-one-infer
- https://natten.org/install/ ; https://github.com/SHI-Labs/NATTEN/issues/147
- https://github.com/ASLP-lab/SongFormer/ ; https://arxiv.org/abs/2510.02797 ; https://huggingface.co/ASLP-lab/SongFormer
- https://github.com/urinieto/msaf ; https://librosa.org/doc/latest/auto_examples/plot_segmentation.html
- https://github.com/morgan76/LinkSeg ; https://github.com/tencent-ailab/MuQ ; https://huggingface.co/OpenMuQ/MuQ-large-msd-iter
- https://music-ir.org/mirex/wiki/2025:Music_Structure_Analysis

**Lead-sheet parsing**
- https://www.worshipfuel.com/equip/songselect-chordpro-format-and-expanded-search-more-new-features/
- https://github.com/De-Fontein/ccli-songselect-to-planning-center-extension
- https://documenter.getpostman.com/view/604633/TzseGkmA ; https://ccli.com/us/en/terms-of-use/
- https://www.chordpro.org/chordpro/directives-env/ ; https://www.chordpro.org/chordpro/directives-env_grid/ ; https://www.chordpro.org/chordpro/chordpro-directives/
- https://pypi.org/project/python-chordpro/ ; https://github.com/martijnversluis/ChordSheetJS
- https://github.com/cuthbertLab/music21 ; https://pypi.org/pypi/music21/json ; https://music21.org/music21docs/moduleReference/moduleRepeat.html
- https://github.com/Audiveris/audiveris ; https://github.com/Audiveris/audiveris/issues/243 ; https://github.com/BreezeWhite/oemer
- https://www.learnchordal.com/how-to-read-charts-1 ; https://manual.openlp.org/songs.html
- https://gitlab.com/openlp/openlp/-/raw/master/openlp/plugins/songs/lib/songselect.py

**Alignment / chord recognition**
- https://www.audiolabs-erlangen.de/content/05_fau/professor/00_mueller/03_publications/2010_FremereyMuellerClausen_PartialSync_ISMIR.pdf (JumpDTW)
- https://arxiv.org/abs/2007.14580 (Hierarchical DTW)
- https://jscholarship.library.jhu.edu/bitstream/handle/1774.2/26/paper.pdf (Sheh & Ellis 2003)
- https://hal.science/hal-00525172v2 (Papadopoulos & Peeters)
- https://www.researchgate.net/publication/228803313 (Mauch et al., SMC 2010)
- https://github.com/andreamust/ChordSync ; https://arxiv.org/abs/2408.00674
- https://github.com/jayg996/BTC-ISMIR19 ; https://archives.ismir.net/ismir2019/paper/000075.pdf
- https://pypi.org/pypi/vamphost/json ; https://github.com/c4dm/nnls-chroma ; https://www.vamp-plugins.org/download.html
- https://pypi.org/project/madmom/ ; https://github.com/CPJKU/madmom
- https://mtg.github.io/essentia-labs/news/2019/09/05/cover-song-similarity/ (OTI)
- https://librosa.org/doc/0.11.0/generated/librosa.sequence.dtw.html ; https://github.com/meinardmueller/synctoolbox
- https://www.audiolabs-erlangen.de/resources/MIR/FMP/C7/C7S2_SubsequenceDTW.html ; https://www.audiolabs-erlangen.de/resources/MIR/FMP/C5/C5S3_ChordRec_HMM.html
- https://www.robots.ox.ac.uk/~vgg/publications/2023/Bain23/bain23.pdf (WhisperX)

**Products / Ableton**
- https://products.zplane.de/uploads/Installers-Manuals/DECODA/deCoda_manual.pdf ; https://products.zplane.de/products/decoda
- https://music.ai/docs/api/file-formats/ ; https://music.ai/platform/api/ ; https://pypi.org/project/musicai-sdk/
- https://moises.ai/features/song-parts/ ; https://extensions.moises.ai/
- https://docs.aurallysound.com/docs/song-master-pro/exporting-songs ; https://chordify.net/pages/download-chords-as-midi-or-pdf/
- https://docs.cycling74.com/apiref/lom/song/ ; https://docs.cycling74.com/apiref/lom/cuepoint/
- https://github.com/ideoforms/AbletonOSC ; https://github.com/ideoforms/AbletonOSC/issues/6 ; https://github.com/ideoforms/AbletonOSC/issues/164
- https://github.com/DawVert/DawVert ; https://github.com/luizen/als-tools ; https://github.com/danielbayley/Ableton-Live-tools
- https://forum.ableton.com/viewtopic.php?t=227929 (community source; MIDI-marker import claim unverified against official docs)
