# Round 2: Alternative approaches

*Companion to [RESEARCH_LEADSHEET_SECTION_CORRELATION.md](RESEARCH_LEADSHEET_SECTION_CORRELATION.md) (round 1). Synthesized 2026-07-27 from verified round-2 research. All facts below survived adversarial verification unless explicitly flagged **[PLAUSIBLE — unverified]** or **[extrapolation]**. Where verification refuted a research claim, the corrected fact is used.*

---

## 1. Executive summary

Three findings materially change the round-1 picture:

1. **Synced lyrics solve the exact failure mode round 1 could not.** LRCLIB (lrclib.net) is a free, no-key API with line-level `[mm:ss.xx]` synced lyrics, **verified live for every worship/CCM test song queried (3/3 and 8/8 across two independent passes)**, with separate entries per studio/live/radio master distinguishable by the `duration` field. Because SongSelect ChordPro charts carry full lyrics under each section label, matching each section's opening lines to LRC timestamps gives section start times by pure text alignment — **inherently transposition-immune, and it distinguishes Verse 1 from Verse 2 in a one-chord vamp because the lyrics differ even when the chords don't**. This is precisely the weak spot named in round 1. Caveat vs. the original research: LRCLIB *does* have rate limiting (429 + `Retry-After`, User-Agent required) — trivial to honor, but the client must.

2. **The chord-template JumpDTW aligner should be demoted from primary engine to instrumental-gap filler and cross-check.** Lyric anchoring (LRC happy path) plus ASR anchoring (WhisperX transcript + fuzzy matching of chart lyrics — the production-proven nomadkaraoke pattern, now in `karaoke-gen`; the original `python-lyrics-transcriber` is archived) covers all sung sections more robustly than chroma. Chroma/chord evidence remains essential only where lyrics are blind: intros, interludes, solos, tags. Supporting evidence: raw-audio LLMs are quantifiably bad at boundaries (best model ~42% within 0.5 s, ~32% on full-song segmentation at ±3 s), so no shortcut exists there either.

3. **The chart side gets cheaper and richer via Planning Center.** The PCO Services API (free with an account, PAT auth, 100 req/20 s) returns per-arrangement `sequence` (ordered section labels), per-section `{label, lyrics}`, ChordPro `chord_chart`, key, BPM, and meter — the entire chart model, machine-readable, no PDF/ChordPro scraping. SongSelect imports land there automatically for church users. No per-section *timing* exists anywhere in the worship ecosystem's exportable surface (MultiTracks/Loop Community hold it server-side, app-locked, ToS-fenced) — with one legitimate leak: the **Guide/"Click & Vocal Cues" stem** in purchased MultiTracks/PraiseCharts ZIPs speaks each upcoming section name one measure early; ASR on that stem yields near-ground-truth section times for owned songs.

A stale project memory also falls: **RTX 5090 no longer needs nightly torch** — PyTorch 2.7+ stable ships cu128/sm_120 wheels, and torchaudio's final release (2.11.0) explicitly claims forward compatibility with future torch. This removes most of the anticipated environment friction for the ASR stack.

---

## 2. Option-by-option assessment

### 2.1 Synced lyrics (LRCLIB) — **ADOPT**

- **What**: Fetch line-timestamped LRC lyrics; match each chart section's first lyric lines monotonically (rapidfuzz) against LRC lines; section start = matched line's timestamp snapped to the beat_this downbeat grid.
- **Verified capabilities**: Free, no API key/registration (official docs); `/api/get` exact-signature lookup (matches only within **±2 s of stored duration**) and `/api/search`; line-level LRC returned live for all test songs. Multiple duration variants per song (e.g., "Goodness of God" 270/283/296/303/316 s) enable picking the entry matching the local WAV. Community-synced quality varies — documented duration mismatches of 5–6 s exist, so duration-proximity selection + last-timestamp sanity checks are mandatory. Line-level only (no word-level) — sufficient for section marking.
- **Coverage (worship/CCM)**: Strong, verified twice independently: Firm Foundation 9/9 synced, What A Beautiful Name 10/10, Goodness of God 16 entries (≥9 synced confirmed), plus 8/8 in a second sweep (Elevation "Praise", "Jireh" live 599 s, "Gratitude" 785 s extended, etc.). Tail risk: brand-new releases, obscure arrangements, medleys.
- **Effort/cost**: ~150–200 lines on existing infrastructure; $0; no GPU.
- **ToS/licensing**: API openly free by design; must send a distinctive User-Agent (WAF drops some defaults) and honor 429/`Retry-After` with 200–500 ms inter-request delays. Lyrics-DB copyright is the usual community gray area; personal practice use is low-risk.
- **Verdict: ADOPT** — cheapest, transposition-immune, and the only option that directly disambiguates Verse 1 vs. Verse 2 on identical chord progressions.

### 2.2 ASR forced alignment on the vocal stem — **ADOPT** (as the lyric fallback + onset refiner)

- **What**: WhisperX transcript with word timestamps → fuzzy anchor matching of chart-section lyrics against the transcript in time order → optional CTC refinement of first-word onsets.
- **Verified capabilities**: WhisperX actively maintained (v3.8.6, May 2026), Windows + CUDA 12.8, ~70× realtime, <8 GB VRAM; alignment regression in v3.3.3–3.8.1 fixed in 3.8.2. Dedicated lyric aligners reach AAE 0.16–0.20 s / PCO@0.3s 93–94% on JamendoLyrics. `ctc-forced-aligner` aligns provided text to long audio (MMS-300m, HF-based, ~5× less memory than torchaudio, **no torchaudio dependency since Feb 2026**; default model CC-BY-NC 4.0 — fine for personal use). `jhuang448/LyricsAlignment-Multilingual` (MIT, pretrained, built for separated vocals) is the purpose-built alternative. Classic speech aligners (MFA, aeneas, Gentle) are explicitly wrong-domain or dead.
- **Critical detail (verified)**: **Run Whisper ASR on the full mix, not the stem** — Jam-ALT shows separated vocals *degrade* Whisper (35.5% → 47.9% WER, wrong-language outputs). Stems help the *known-text CTC refinement* step (accuracy rises with vocal quality), so: ASR on mix, refine on stem. Known failure mode: repeated vocables ("oh, whoa, la la") shift alignment — anchor on each section's first *distinctive* line.
- **Coverage**: Universal — needs no lyrics DB at all; the SongSelect chart *is* the reference transcript. Covers live versions, spontaneous repeats, and songs absent from LRCLIB.
- **Effort/cost**: ~1–2 days; <30 s GPU per song on the 5090. Environment friction is low (see torchaudio correction above); a stable-torch venv is an option, not a necessity.
- **Verdict: ADOPT** — the robust fallback and onset refiner; same disambiguation power as LRC (each chorus occurrence is a separate time-ordered hit) at higher compute cost.

### 2.3 Pre-existing annotations (YouTube chapters, SponsorBlock, Spotify, Hooktheory, research corpora)

- **YouTube chapters/description timestamps**: Verified empirically **0/8 hit rate** on single-song worship/pop videos (reproduced independently on 5/8). But the data is already in the `info.json` audio_downloader produces, is zero-cost, and is authoritative and offset-free when present (album/concert uploads). **Verdict: FAST-PATH** — check first, expect it to miss.
- **SponsorBlock**: `chapter` category exists (zero hits on all tested music videos), but **`music_offtopic` segments verifiably mark non-music video intros/outros** (Shape of You 0–6.05 s, locked outro 238–263 s). One yt-dlp flag merges them. **Verdict: ADOPT (narrow)** — as a where-does-the-music-start signal, not a structure source.
- **Spotify Audio Analysis (`sections`)**: Restricted since 2024-11-27 to grandfathered extended-quota apps; May 2025 policy requires ~250K MAU; new apps get 403; no official replacement; third-party "replacements" just run server-side DSP. **Verdict: REJECT** — unreachable for any new integration in 2026.
- **Hooktheory/TheoryTab**: Official API is chord-trends only; per-tab sync data is behind opaque hashes; worship coverage is **sparse but nonzero** (corrected: Elevation "RATTLE" exists; Hillsong content lives under hillsong-united / hillsong-young-and-free slugs) — still far too thin, site 403s bots, and the CC BY-NC-SA Sheet Sage dump (~5k songs, crude alignments, 2022 snapshot) skews pop/game/anime. **Verdict: REJECT** for this catalog; keep the Sheet Sage JSON as an optional offline bonus for mainstream pop.
- **Research corpora (Harmonix Set, SALAMI, McGill Billboard, Isophonics)**: Right annotation shape, ~zero worship overlap. **Verdict: REJECT.** Chordify: no API, no section export — **REJECT**.

### 2.4 Worship ecosystem (MultiTracks, Loop Community, Planning Center, PraiseCharts, WBB)

- **Planning Center Services API**: Verified: arrangement `sequence`/`sequence_full`, read-only sections endpoint returning `{label, lyrics, breaks_at}` derived from the chord chart, ChordPro `chord_chart`, `bpm`, `meter`, `length`; PAT auth; 100 req/20 s. **No per-section timing** — feeds the chart model only. **Verdict: ADOPT** as an alternative chart source alongside (not replacing) the round-1 ChordPro parser — it eliminates parsing for anything already in the user's PCO account.
- **MultiTracks Guide-stem ASR**: Verified: purchased multitracks include a Click stem ("perfectly in sync with the tempo … including tempo changes") and a Guide stem that "informs the band which song section is coming up, 1 measure before each section." Whisper on that stem + downbeat snap = named, bar-exact sections for the original master — legitimate personal use of purchased audio (the ZIP-packaging detail specifically is plausible but unverified). ToS forbids reverse-engineering the *apps* and scraping; this touches neither. **Verdict: FAST-PATH** — highest-accuracy source when the user owns the multitrack; skip when they don't.
- **MultiTracks Playback/Cloud section markers**: Real, downbeat-anchored, server-side — no data export (a new PDF chart export exists; visual only), no API, ToS-fenced. **Verdict: REJECT** as a data source.
- **PraiseCharts developer API**: Real public API (OAuth 1.0), ChordPro download for owned arrangements per key; same Guide-stem opportunity in their multitrack ZIPs. **Verdict: DEFER** — redundant with PCO/SongSelect for now.
- **Worship Backing Band / Musicademy**: The only vendor shipping per-song **.als files with section locators pre-placed** (~$10–17/song, 14 stems, parseable gzipped-XML locators) — but for their own re-recorded covers, so timings don't transfer to a YouTube master. **Verdict: DEFER** — useful only if the user adopts WBB audio.
- **Loop Community Prime / Loop Connect**: Section markers exist in-app; downloads are plain WAV ZIPs, no marker export, no public SDK. **Verdict: REJECT** as data source. **RehearsalMix**: streaming-only commercial twin of the practice-pack output — buy-vs-build context only.

### 2.5 Audio LLMs — **CROSS-CHECK** (raw audio) / **ADOPT** (symbolic reasoning)

- **Raw audio**: Verified numbers are damning for boundary placement: Gemini 2.5 Pro on SongFormBench-HarmonixSet — label accuracy 0.748 but HR@0.5s only 0.423 (vs. 0.813 @3s; dedicated SongFormer: 0.703 @0.5s); BASS benchmark full-song structural segmentation at ±3 s: best models ~32%, degrading toward bridges/outros — exactly where worship arrangements diverge from charts. An entire 2025–26 subliterature (TimeAudio, SpotSound, etc.) exists because LALMs can't do fine temporal localization. Cost is trivial (4-min song ≈ 7,680 Gemini audio tokens, sub-cent on Flash), and *labels* are decent — so a Gemini pass is a cheap third opinion, never a boundary source. OpenAI audio models are voice-chat-shaped with no timestamp contract ($32/1M audio tokens); **Claude's API has no audio input at all**, so Claude participates only symbolically. Local models: Music Flamingo is the only one engineered for musical time but is NVIDIA-noncommercial, demonstrates no boundary-list task, and caps at 10 min effective processing; Qwen3-Omni needs 69–79 GB BF16 (won't fit the 5090; the ~15 GB Omni quant is unverified). **Verdict: CROSS-CHECK** only.
- **Symbolic LLM over DSP outputs**: Verified precedent — GPT-4o CoT over text-serialized Beat This! beats + Harte chords + key improves chord recognition 1–2.77% MIREX. Feeding Claude the downbeat grid, per-bar Roman-numeral chords, the chart, and timestamped lyric lines makes it a labeler/arbiter whose timing precision comes entirely from DSP. **Verdict: ADOPT** as the reconciliation/arbitration layer (it's also free — Claude is already the MCP orchestrator).

### 2.6 Synthetic-reference DTW (render chart → audio → DTW) — **REJECT**

Well-precedented in MIR (Turetsky & Ellis 2003; Lakh MIDI's 45k alignments — note: against MSD *preview clips*), but every precedent uses note-level MIDI. A chord chart carries only pitch-class sets; synthesis reproduces the chord-template chroma noisily while adding timbre mismatch, and it inherits the identical structural-mismatch problem (synctoolbox/MrMsDTW documents no jump/repeat handling; subsequence DTW absorbs only flanking material) — so you'd need the same JumpDTW block machinery with a strictly worse front-end. **Salvage**: synctoolbox's tuned chroma/DLNCO features and MrMsDTW as a within-section refinement pass; rendered charts as audible debug artifacts.

### 2.7 Chart-prior constrained segmentation (SSM/novelty DP) — **ADOPT** (as fusion term)

No off-the-shelf implementation, but every ingredient is standard and verified: Foote novelty, librosa recurrence/Laplacian segmentation, and CBM's (Correlation Block-Matching, TISMIR 2023) DP-over-SSM skeleton with novelty + regularity criteria. A Viterbi over (chart sections × beat_this downbeats) with novelty-at-boundary, within-section homogeneity, and same-label-blocks-must-be-similar terms is milliseconds of CPU, needs no chord recognition, and is transposition-invariant. Weakness: assumes chart order — needs wildcard insertion states for unlisted intros/solos/tags (cheap at ~15 states) and cannot *name* an inserted solo. **Verdict: ADOPT** as a boundary-snapping/disagreement-flagging term fused with chord JumpDTW, and as the fallback when chroma is unreliable.

### 2.8 Fingerprint / offset re-anchoring — **ADOPT** (utility)

- `bbc/audio-offset-finder` (0.5.5, Apache-2.0): MFCC cross-correlation, ~0.01 s accuracy, z-score confidence (>10 trust, <5 reject); README warns about similar repeated sections (use long/full-file queries); pins `numpy<2` — isolate in its own venv or vendor the ~200-line core. Acceptance gate: score ≥10 AND duration-delta sanity check.
- `audalign` (1.3.1, MIT, Windows-documented): fingerprint recognizer robust for stem-vs-mix, correlation fallback, `fine_align` refinement.
- Chromaprint/fpcalc: identification only (~0.12 s offset granularity **[memory-based figure]**) — use as a cheap "same recording?" gate, not the anchor.
- The ffsubsync-style VAD × LRC-line-activity cross-correlation for constant-offset recovery is mechanism-verified for subtitles, but **its application to sung vocals on a Demucs stem is an [extrapolation] — validate empirically before trusting** (speech VAD on melisma is untested; an energy gate on the stem may work better).
- **Verdict: ADOPT** as a shared utility all lookup layers use to validate third-party timelines. All tools assume same-master-plus-constant-offset; live/re-recorded/pitch-shifted versions must fail the gate and fall through to ASR.

### 2.9 Human-in-the-loop tap UI — **ADOPT** (ship early; it's also the confirm layer)

Verified: SALAMI double-annotation shows humans are bar-accurate but not sub-second in one pass (F≈0.76 @±3s, ≈0.67 @±0.5s), and taps lag boundaries — so **snap each tap to the nearest preceding downbeat** to convert ±1–2 s taps into effectively exact locators. Sonic Visualiser (tap-to-instant) and Songle (auto-propose, human-correct at scale) are the established UX patterns. The practice_app web player already exists: add a key listener recording `audio.currentTime`, snap server-side, prefill labels in chart order, write locators via existing cue_point tools. One listen-through (~5 min/song), zero ML risk. **Verdict: ADOPT** — both as the reliability backstop and as the review UI every automatic layer needs anyway.

---

## 3. The updated recommended pipeline

Round 1's chord-template JumpDTW survives, but as **one evidence stream among several**, primary only for instrumental material. The system becomes a layered cascade with a shared downbeat grid and a confidence-gated fallthrough:

```
INPUTS (already available): WAV (yt-dlp) · info.json · htdemucs_6s stems
                            beat_this beats/downbeats · SongSelect ChordPro chart

L0  FAST-PATH LOOKUPS (free, definitive when they hit)
    ├─ YouTube chapters in info.json (+ SponsorBlock chapter merge)  → rare, authoritative
    ├─ SponsorBlock music_offtopic → trim/offset hint (music start)
    └─ Guide-stem ASR (owned MultiTracks/PraiseCharts ZIP)           → near-ground-truth
         │  any full hit → skip to L5
         ▼
L1  CHART MODEL
    ChordPro parse (round 1)  ──or──  PCO Services API
    → ordered sections, per-section lyrics, chords, key, BPM, meter

L2  LYRIC ANCHORS — happy path (no GPU)
    LRCLIB duration-matched entry → offset gate (audio-offset-finder if a
    reference exists; VAD/energy × LRC-activity correlation [unvalidated on
    singing — verify empirically]) → monotonic rapidfuzz match of each
    section's first DISTINCTIVE line → sung-section starts
         │  low confidence / no entry / offset gate fails
         ▼
L3  ASR ANCHORS — robust path (GPU, ~30 s/song)
    WhisperX large-v3 on the FULL MIX (not the stem) → time-ordered fuzzy
    anchor match of chart lyrics (karaoke-gen pattern) → discovers performed
    order incl. extra repeats → optional first-word CTC refinement on the
    VOCAL STEM (ctc-forced-aligner / LyricsAlignment-Multilingual)

L4  INSTRUMENTAL & RECONCILIATION LAYER (round-1 machinery, refocused)
    chord-template JumpDTW + 12-rotation OTI  ⊕  SSM/novelty chart-prior DP
    → labels the lyric-free gaps (Intro/Interlude/Solo/Tag), cross-checks
    L2/L3 boundaries, flags disagreements
    [optional: Gemini Flash raw-audio pass as cheap third opinion — labels
    only, never boundary times; symbolic-LLM (Claude) arbitration over the
    serialized downbeats + Roman-numeral chords + lyric timestamps]

L5  SNAP + WRITE
    every boundary → nearest beat_this downbeat (pickup-aware, ~1-beat bias
    window) → AbletonOSC locators (stop → seek → toggle → diff-by-time → rename)

L6  HUMAN CONFIRM (always available, mandatory below confidence threshold)
    practice_app waveform UI: provisional locators shown, tap-to-add with
    downbeat snapping, drag/delete, labels prefilled in chart order
```

**Which layer resolves which failure mode:**

| Failure mode | Round-1 status | Resolved by |
|---|---|---|
| Verse 1 vs. Verse 2 on identical progressions | **unsolved** | L2/L3 — lyrics differ; time-ordered matching assigns each occurrence |
| One-chord vamps (chroma featureless) | **unsolved** | L2/L3 — lyric anchors don't use pitch at all |
| Chart-vs-recording transposition | 12-rotation OTI hack | L2/L3 are natively pitch-free; OTI still needed only in L4 |
| Performed order ≠ chart order (extra chorus, tags) | filler blocks | L3 — transcript is in performed order; unmatched sung regions flagged |
| Instrumental intro/interlude/solo/outro | primary strength | L4 (chord JumpDTW + SSM DP); L2/L3 see them only as gaps |
| Wrong master / live version / different edit | unaddressed | L2 duration matching + L2.5 offset gate; ASR (L3) needs no reference timeline |
| Vocable-opening sections ("oh, whoa") | n/a | L2/L3 anchor on first *distinctive* line (documented aligner failure mode) |
| Everything residual | — | L5 downbeat snapping (absorbs 0.2–2 s error at 2–3 s bar spacing) + L6 human |

---

## 4. Changes to the round-1 integration plan (delta only)

**New MCP tools / components:**

1. `lyrics_fetch_synced(artist, title, wav_duration)` — LRCLIB client: custom User-Agent, 429/`Retry-After` handling, 200–500 ms throttling, duration-proximity entry selection (remember `/api/get` matches only ±2 s; use `/api/search` + local ranking), last-timestamp ≤ WAV-length sanity check. Returns parsed (t, line) list + chosen-entry metadata.
2. `lyrics_anchor_sections(chart_sections, lrc_lines_or_transcript)` — monotonic rapidfuzz matcher (shared by L2 and L3): first-distinctive-line selection, sequential consumption of repeated choruses, gap inference for instrumental sections, per-section confidence scores.
3. `song_transcribe_lyrics(wav)` — WhisperX wrapper (large-v3, VAD, **mix input**), word-timestamped transcript; optional `align_known_text(stem, text, window)` via ctc-forced-aligner for onset refinement.
4. `audio_offset_check(file_a, file_b)` — audio-offset-finder wrapper (isolated venv or vendored core due to `numpy<2` pin), returns offset + z-score; audalign fingerprint fallback for stem-vs-mix.
5. `structure_snap_confidence` extension to the existing snap step: per-boundary provenance (which layer produced it) + confidence, exposed to the UI.
6. practice_app: **tap-to-mark + locator-review mode** (not an MCP tool; a web-UI feature) — records taps, server-side downbeat snapping, chart-order label pick-list, writes through existing cue_point tools.
7. `pco_get_arrangement(song)` — optional Planning Center Services client (PAT auth) as an alternative chart source feeding the same chart model as the ChordPro parser.
8. `guide_stem_asr(stems_dir)` — opportunistic: detect a "Click & Vocal Cues"/Guide stem in user-provided multitrack folders, Whisper it, emit section names + times (shift by one measure using the tempo map/downbeats).

**Modified:**

- The round-1 correlation tool becomes an **orchestrator** implementing the L0→L6 cascade with confidence gating, rather than calling chord-JumpDTW directly.
- Chord-template JumpDTW gains a narrower contract: label/verify instrumental gaps handed to it by the lyric layers; emit disagreement flags instead of being sole source of truth.
- The SSM/novelty chart-prior DP is added *inside* L4 as a fusion term (boundary snapping + repeat-consistency), not as a separate pipeline.
- Environment note: drop the "nightly torch required" assumption (stale — stable 2.7+ cu128 supports the 5090); ctc-forced-aligner avoids torchaudio entirely, so no separate venv is strictly required.

**Dropped from round-1 iteration 2:** all-in-one-infer segment labels as *the* prior — superseded by lyric anchors; keep it only as an optional extra vote in L4 if L2/L3 both fail.

---

## 5. Revised first prototype

**Yes — round 2 changes what to build first.** Round 1's first slice was the chord-template JumpDTW aligner. The new smallest slice is the **LRCLIB lyric-anchor happy path**, because it is smaller (~150–200 lines, no new ML deps, no GPU), directly attacks the known-unsolved failure modes (Verse-1-vs-2, vamps, transposition), and its confidence gate defines exactly where the chord aligner is still needed — making the chord work better-scoped, not wasted.

**Slice:** `chart ChordPro parse (existing) → lyrics_fetch_synced → duration-matched entry → lyrics_anchor_sections → downbeat snap → locators via existing cue_point tools`, plus a per-song JSON report (chosen entry, per-section match scores, unmatched regions, inferred instrumental gaps). Defer the offset gate (the ffsubsync-on-singing question) to slice 2 unless duration matching alone proves insufficient.

**Success criteria** (on ~10 repertoire songs with hand-marked ground truth via one tap-through each — which conveniently also prototypes the L6 UI):

1. **Sung-section starts**: ≥90% of Verse/Chorus/Bridge boundaries within ±1 bar of ground truth on songs where an LRC entry within ±3 s of WAV duration exists.
2. **Disambiguation**: Verse 1 and Verse 2 (and repeated choruses) assigned to the correct occurrences on 100% of matched songs — this is the round-1 killer case and must be perfect, not approximate.
3. **Honest abstention**: on songs with no suitable LRC entry or match confidence below threshold, the tool reports "no answer" rather than wrong locators (zero silent failures) — that abstention set is the measured requirement spec for the L3 ASR slice and the L4 chord aligner.
4. **Throughput/cost**: <5 s per song, zero GPU, LRCLIB rate limits honored.

---

## 6. Sources

**Synced lyrics / LRCLIB**
- https://lrclib.net/docs · https://github.com/tranxuanthang/lrclib · https://github.com/tranxuanthang/lrcget/issues/51 · https://github.com/tranxuanthang/lrcget/releases
- https://news.ycombinator.com/item?id=39480390 · https://lrclibapi.readthedocs.io/en/stable/lrclib.html · https://github.com/iiPythonx/lrcup · https://github.com/Dr-Blank/lrclibapi · https://pypi.org/project/syncedlyrics/ · https://github.com/moehmeni/syncedlyrics
- https://github.com/jellyfin/jellyfin-plugin-lrclib/issues/37 · https://github.com/slonopot/LRCLIBee · https://github.com/mierak/rmpc/issues/620
- https://github.com/simonroedig/ChordSync · https://github.com/smacke/ffsubsync · https://github.com/kaegi/alass
- https://freeapihub.com/apis/musixmatch · https://github.com/Strvm/musicxmatch-api · https://github.com/akashrchandran/spotify-lyrics-api · https://pypi.org/project/syrics/ · https://github.com/rryam/MusanovaKit
- https://archives.ismir.net/ismir2020/paper/000088.pdf · https://aclanthology.org/C18-1174/ · https://hal.science/hal-03295581v1
- https://www.quicklrc.com/subtitle-formats/enhanced-lrc · https://skipthewatch.com/blog/yt-dlp-youtube-subtitles · https://github.com/yt-dlp/yt-dlp/issues/9371

**ASR / forced alignment**
- https://github.com/m-bain/whisperX · https://pypi.org/project/whisperx/ · https://github.com/m-bain/whisperX/issues/1220 · https://github.com/m-bain/whisperX/issues/1247 · https://github.com/m-bain/whisperX/issues/1111 · https://arxiv.org/abs/2303.00747
- https://github.com/jianfch/stable-ts · https://github.com/linto-ai/whisper-timestamped
- https://github.com/nomadkaraoke/python-lyrics-transcriber · https://github.com/nomadkaraoke/karaoke-gen
- https://github.com/MahmoudAshraf97/ctc-forced-aligner · https://docs.pytorch.org/audio/stable/tutorials/ctc_forced_alignment_api_tutorial.html · https://github.com/pytorch/audio/issues/3902 · https://pytorch.org/blog/pytorch-2-7/
- https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html · https://github.com/readbeyond/aeneas · https://github.com/lowerquality/gentle · https://github.com/qiuqiao/SOFA · https://github.com/NVIDIA/NeMo/tree/main/tools/nemo_forced_aligner
- https://arxiv.org/abs/2306.07744 · https://ieeexplore.ieee.org/document/10888807/ · https://github.com/tikick/LyricsAlignment · https://ismir2025program.ismir.net/lbd_412.html · https://github.com/jhuang448/LyricsAlignment-Multilingual/ · https://github.com/jhuang448/LyricsAlignment-MTL
- https://arxiv.org/html/2311.13987 · https://arxiv.org/abs/2108.02625 · https://github.com/zhuole1025/LyricWhiz · https://github.com/Berkeley-Speech-Group/sylber · https://arxiv.org/abs/2507.06670
- https://www.audioshake.ai/products/lyric-transcription-alignment · https://developer.audioshake.ai/api/lyrics-transcription/alignment
- https://github.com/rakuri255/UltraSinger · https://github.com/retotito/UltrastarCreatorTool · https://github.com/rzru/nightingale

**Pre-existing annotations**
- https://github.com/yt-dlp/yt-dlp/issues/3339 · https://github.com/yt-dlp/yt-dlp/releases/tag/2026.03.17 · https://wiki.sponsor.ajay.app/w/Types · https://github.com/ajayyy/SponsorBlock/issues/409 · https://www.mintlify.com/yt-dlp/yt-dlp/cli/sponsorblock-options
- https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api · https://developer.spotify.com/documentation/web-api/concepts/quota-modes · https://reccobeats.com/docs/apis/extract-audio-features
- https://github.com/chrisdonahue/sheetsage · https://www.hooktheory.com/theorytab/view/chris-tomlin/our-god · https://www.hooktheory.com/theorytab/view/elevation-worship/rattle · https://github.com/rkthrasher/HookTheory
- https://github.com/urinieto/harmonixset · https://github.com/jblsmith/matching-salami · https://github.com/DDMAL/salami-data-public · https://ddmal.ca/research/The_McGill_Billboard_Project_(Chord_Analysis_Dataset)/
- https://support.chordify.net/hc/en-us/community/posts/360005503397 · https://blog.metabrainz.org/2022/02/16/acousticbrainz-making-a-hard-decision-to-end-the-project/ · https://pypi.org/project/pyacoustid/

**Worship ecosystem**
- https://api.planningcenteronline.com/services/v2/documentation/2018-11-01/vertices/arrangement · https://api.planningcenteronline.com/services/v2/documentation/2018-11-01/vertices/arrangement_sections · https://api.planningcenteronline.com/docs/overview/rate-limiting · https://github.com/planningcenter/developers/issues/520
- https://helpcenter.multitracks.com/en/articles/5317629-get-started-intro-to-tracks · https://helpcenter.multitracks.com/en/articles/4620755-which-track-product-is-right-for-me · https://helpcenter.multitracks.com/en/articles/5077698-how-to-create-edit-cloud-song-sections-in-playback · https://helpcenter.multitracks.com/en/articles/7436863-how-to-create-cloud-song-charts · https://helpcenter.multitracks.com/en/articles/12302102-new-export-print-cloud-song-charts · https://helpcenter.multitracks.com/en/articles/4745490-get-started-rehearsalmix · https://www.multitracks.com/terms/ · https://www.multitracks.com/blog/advanced-ableton-templates-now-available
- https://developer.praisecharts.com/api · https://www.praisecharts.com/company/praisecharts-api-usage-agreement/ · https://www.praisecharts.com/products/multi-tracks
- https://loopcommunity.com/prime-multitrack-app · https://loopcommunity.com/blog/2020/11/purchasing-tracks-on-loop-community/ · https://loopcommunity.com/en-US/loop-connect · https://www.worshiptools.com/en-us/docs/156-loop-connect
- https://www.worshipbackingband.com/multitrack-stems · https://www.musicademy.com/blog/multitrack-ableton-wav-stems-worship-backing-tracks/ · https://www.worshipfuel.com/equip/songselects-new-number-format-heard/ · https://worshiptutorials.com/product/clicks-and-cues/ · https://www.theworshipinitiative.com/the-worship-initiative · https://worshiponline.com/

**Audio LLMs**
- https://ai.google.dev/gemini-api/docs/audio · https://ai.google.dev/gemini-api/docs/pricing · https://github.com/google-gemini/cookbook/issues/733
- https://arxiv.org/html/2510.02797 · https://arxiv.org/html/2602.04085v1 · https://arxiv.org/abs/2510.24693 · https://arxiv.org/abs/2511.11039 · https://arxiv.org/abs/2408.01337 · https://arxiv.org/abs/2410.19168
- https://developers.openai.com/api/docs/models/gpt-audio-1.5 · https://platform.claude.com/docs/en/build-with-claude/overview · https://github.com/anthropics/anthropic-sdk-python/issues/1198
- https://research.nvidia.com/labs/adlr/MF/ · https://huggingface.co/nvidia/music-flamingo-2601-hf · https://huggingface.co/nvidia/audio-flamingo-3 · https://github.com/QwenLM/Qwen3-Omni · https://github.com/MoonshotAI/Kimi-Audio
- https://arxiv.org/html/2509.18700v1 · https://arxiv.org/abs/2501.13261 · https://arxiv.org/abs/2402.16153 · https://music.ai/workflows/transcription-and-alignment/lyric-transcription-and-alignment/

**Synthetic reference, chart-prior segmentation, offset tools, human-in-the-loop**
- https://ismir2003.ismir.net/presentations/Turetsky.pdf · https://colinraffel.com/projects/lmd/ · https://dl.acm.org/doi/10.1109/ICASSP.2016.7471641 · https://github.com/meinardmueller/synctoolbox · https://librosa.org/doc/main/generated/librosa.sequence.dtw.html · https://www.audiolabs-erlangen.de/resources/MIR/FMP/C7/C7S2_SubsequenceDTW.html
- https://www.audiolabs-erlangen.de/content/05_fau/professor/00_mueller/03_publications/2010_FremereyMuellerClausen_PartialSync_ISMIR.pdf · https://archives.ismir.net/ismir2020/paper/000139.pdf
- https://transactions.ismir.net/articles/10.5334/tismir.167 · https://librosa.org/doc/main/auto_examples/plot_segmentation.html · https://arxiv.org/abs/2509.16566
- https://github.com/bbc/audio-offset-finder · https://github.com/benfmiller/audalign · https://github.com/acoustid/chromaprint · https://github.com/kdave/audio-compare · https://github.com/acoustid/notebooks/blob/master/fingerprint-matching.ipynb
- https://grrrr.org/pub/ullrich_schlueter_grill-2014-ismir.pdf · https://staff.aist.go.jp/m.goto/PAPER/ISMIR2011goto.pdf · https://www.sonicvisualiser.org/doc/reference/1.9/en/ · https://tempobeatdownbeat.github.io/tutorial/ch2_basics/annotation.html · https://code.soundsoftware.ac.uk/projects/segmenter-vamp-plugin · https://github.com/craffel/pretty-midi/blob/main/Tutorial.ipynb
