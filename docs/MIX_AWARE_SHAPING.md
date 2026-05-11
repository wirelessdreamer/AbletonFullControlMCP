# Mix-aware sound shaping — build-out plan

> **Goal.** Let a user say something like *"the lead guitar doesn't cut through the mix during the solo"* and have the MCP server (a) locate the solo, (b) figure out **why** the lead isn't cutting (which tracks are masking it in which bands), (c) propose changes (EQ cuts on competing tracks, EQ boosts on the focal, sidechain ducking, etc.), (d) apply them, and (e) verify the fix worked.
>
> This is the conversational mix-engineering surface. It builds on the existing per-device shaping stack (`sound_shaping`, 109-descriptor semantic vocab, device schemas, LiveRenderer) and adds the **cross-track / time-region / perceptual-masking** layer that single-device shaping doesn't cover today.

## Living tracker

| Status | Layer | Item | PR / Branch |
|---|---|---|---|
| ✅ | L1.1 | Region-bounded bounce primitive | [#21](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/21) |
| ✅ | L1.2 | Section detection (cheap — clip-name + overlap) | [#22](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/22) |
| ⬜ | L1.2b | Section detection (audio-based, RMS) — follow-up | |
| ✅ | L1.3 | `region_bounce_all_active` wrapper | [#23](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/23) |
| ✅ | L2.1 | `mix_spectrum_at_region` — per-track band energy | [#24](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/24) |
| ✅ | L2.2 | `mix_masking_analyze` — which tracks mask the focal | [#25](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/25) |
| ✅ | L3 | Mix-vocabulary descriptors (cuts_through, buried, muddy, …) | [#26](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/26) |
| ✅ | L4.1 | `mix_propose` — structured proposal for intent | [#27](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/27) |
| ✅ | L4.2 | `mix_apply` — push to Live (auto-insert EQ Eight) | [#28](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/28) |
| ⬜ | L4.3 | Region-bounded automation writes (LOM-limited; v2) | |
| ✅ | L5 | `mix_verify_intent` — A/B before/after | [#29](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/29) |
| ✅ | KB | Money-bands + masking rules data file | [#30](https://github.com/wirelessdreamer/AbletonFullControlMCP/pull/30) |

Legend: ⬜ not started · 🟡 in progress · ✅ merged

**Status: Core build-out (L1–L5 + KB) complete.** A user can now say *"the lead guitar doesn't cut through during the solo"* and the MCP can locate the section, diagnose masking, propose EQ moves with rationales, apply them (auto-inserting EQ Eight when needed), and verify the intent was achieved by diffing before/after spectra. The two follow-ups (L1.2b audio-based section detection, L4.3 region-bounded automation) are unblocking the next round of improvements but aren't on the critical path for the core conversational loop.

---

## Why it has to be a stack, not one tool

The example *"make the lead cut through the solo"* fans out into:

1. **Identify the lead track.** Existing (`track_list`, `project_describe`).
2. **Find "the solo."** A time region on the timeline. **Missing** — most users don't pre-label this, and the existing `cue_points` / `structure_*` tools require the user to have set them up.
3. **Analyze where everything sits frequency-wise during the solo.** **Missing** — `audio_analyze` works per-clip; nothing gives a *multi-track × frequency-band × time-region* matrix.
4. **Figure out what's masking the lead.** **Missing** — needs a perceptual masking computation across the multi-track spectrum.
5. **Map "cut through" to specific changes.** Partly present — 109-descriptor semantic vocab handles *per-device* tone words, but mix words like "cut through" / "buried" / "muddy" / "harsh" are cross-track and currently absent.
6. **Apply the changes.** Partly present — device parameter setters exist; the missing piece is *cross-track mix actions* (EQ cuts on competing tracks, sidechain compression, region-bounded gain rides).
7. **Verify the fix.** Partly present — `audio_compare` exists; needs orchestration around it framed in mix terms.

Each layer below addresses one of these gaps. Build bottom-up — every layer needs Layer 1's region-bounded analysis as a building block.

---

## Layer 1 — Region-aware analysis (foundation)

Everything above operates on **time regions**. Today the bounce + analyze tools operate on whole songs or whole clips. Layer 1 gives us per-region versions.

### L1.1 — Region-bounded bounce primitive

**Goal.** Bounce N tracks (or the master) for a specific `[start_beats, end_beats]` range only, not the whole song.

**Approach.** Two paths matching the existing bounce modes:
- **Resampling.** Set song time to `start_beats`, bounce for `(end − start) * 60 / tempo` seconds. Existing `bounce_song_via_resampling` already does the whole bounce shape; just needs `start_beat` parameterized (currently hardcoded to 0).
- **Freeze.** Already produces a whole-track wav. Slice to `[start_seconds, end_seconds]` post-freeze via `librosa` / `soundfile`. Cheaper than resampling for offline workflows.

**Public surface.**
```python
async def bounce_region_via_resampling(
    track_indices: Sequence[int] | None,  # None → master
    output_dir: str,
    start_beats: float,
    end_beats: float,
    *,
    warmup_sec: float = 0.0,
    settle_sec: float = 0.4,
) -> dict
```

MCP tool: `bounce_region(track_indices, output_dir, start_beats, end_beats, mode="resampling" | "freeze")`.

**Implementation deltas vs PR #19's bounce:**
- `_record_arrangement_with_progress` already takes the duration; just need to also accept `start_beats`.
- Progress phase labels: keep "recording N/T s" but the meaning is relative to the region, not the song.
- Output wav covers `[0, duration]` in wav-time and `[start, end]` in song-time. Returned result includes `start_beats` / `end_beats` metadata.

**Tests.**
- region bounce calls `set/current_song_time` to `start_beats * 60/tempo` seconds before recording
- duration computed correctly from beats + tempo
- bounce_region without track_indices captures the master
- with track_indices, captures per-track stems
- mode="freeze" slices each track's frozen wav to the region

### L1.2 — Section detection

**Goal.** Auto-find time regions where a focal track is featured (e.g. "the solo" = where lead guitar is loud and other tracks are quieter).

**Approach.** Two-tier:
1. **Cheap path.** Use `arrangement_clips_list` per track. A "lead-prominent section" is where the focal track has clips but at least N other tracks don't, OR where the focal has clips with `name` matching common solo keywords ("solo", "lead", etc.). Zero DSP.
2. **Robust path.** Bounce each active track at low resolution (e.g. 22 kHz mono, full song), compute windowed RMS per second per track. A "focal-prominent" window is `RMS(focal) > threshold` AND `mean(RMS others) < threshold * k`. Returns contiguous ranges.

**Public surface.**
```python
section_find_lead_features(track_index: int,
                            min_duration_beats: float = 16.0,
                            method: str = "auto") -> list[Section]
```

`method="auto"` tries clip-based first, falls back to audio-based if the clip pattern is ambiguous. Returns sections with `start_beats`, `end_beats`, `confidence`, `kind` (`"audio_rms"`, `"clip_pattern"`, `"clip_name"`, `"cue_point"`).

**Tests.** Synthesized mocks of RMS over time → expected section boundaries.

### L1.3 — `region_bounce_all_active`

Convenience wrapper: bounce every un-muted, audio-producing track for a region. Mirrors `bounce_enabled_via_resampling` but region-scoped. Building block for L2.

---

## Layer 2 — Multi-track spectral analysis

### L2.1 — `mix_spectrum_at_region(start_beats, end_beats, *, bands="third_octave_32")`

For every active track, compute spectral energy per band over the region. Output shape:

```json
{
  "region": {"start_beats": 128, "end_beats": 160},
  "bands": [{"center_hz": 31.5, "edges_hz": [22.4, 44.7]}, ...],
  "tracks": [
    {"track_index": 0, "name": "Drums",
     "energy_db_per_band": [-12.3, -14.1, ...],
     "peak_db": -3.2, "rms_db": -14.1, "lufs": -18.4},
    ...
  ]
}
```

Pure DSP: bounce each track for region (via L1.1), compute STFT, sum into third-octave bins, normalise. ~30 lines of librosa.

### L2.2 — `mix_masking_analyze(focal_track, start_beats, end_beats)`

For each band where the focal has significant energy, score how much non-focal tracks contribute there. Returns:

```json
{
  "focal_track": 4,
  "focal_money_bands": [{"center_hz": 2500, "energy_db": -8.4}, ...],
  "competing_tracks": [
    {"track_index": 2, "name": "Rhythm Gtr",
     "masking_score": 0.78,
     "per_band": [{"center_hz": 2500, "energy_db": -6.2, "overlap_with_focal_db": 2.2}, ...]
    },
    ...
  ]
}
```

`masking_score` ∈ [0, 1] is a weighted sum of per-band overlap, where weights come from perceptual band importance (presence band 2-5 kHz weighted higher for "cut through" intent).

---

## Layer 3 — Mix-vocabulary descriptors

Extend the existing 109-descriptor semantic vocab with cross-track mix terms. Each descriptor encodes:

1. **Bands it lives in** (frequency range)
2. **Sign** (more / less of)
3. **Action class** (boost focal, cut competitors, compress, etc.)

| Descriptor | Bands | Action class |
|---|---|---|
| `cuts_through` / `present` | 2-5 kHz (presence) | Boost focal there + cut competitors there |
| `buried` / `lost_in_mix` | (inverse of cuts_through) | Same direction, weighted toward cutting competitors |
| `muddy` | 200-400 Hz | Cut low-mids on offending tracks |
| `boomy` | 60-120 Hz | High-pass non-bass tracks |
| `honky` / `boxy` | 400-800 Hz | Cut 500 Hz on offenders |
| `harsh` | 2-5 kHz peak | Cut presence on offenders |
| `sibilant` | 6-9 kHz | De-ess focal |
| `airy` / `open` | 10-16 kHz | High shelf on focal |
| `wide` / `narrow` | (stereo image) | Adjust Utility width |
| `punchy` | (transient prominence) | Compression attack tuning |
| `thick` / `thin` | 80-300 Hz | Low shelf |

Lives in `src/ableton_mcp/semantics/mix_descriptors.py` next to the existing 109-descriptor catalogue.

---

## Layer 4 — Mix shaping engine

### L4.1 — `mix_propose(focal_track, intent, region_start, region_end)`

Given a parsed mix intent ("cut through", "fit in mix", "less muddy", etc.), returns a structured proposal — does NOT apply.

```json
{
  "focal_track": 4, "intent": "cut_through",
  "region": [128, 160],
  "actions": [
    {"track": 4, "kind": "eq_boost", "device": "EQ Eight",
     "freq_hz": 3000, "q": 1.5, "gain_db": 2.0,
     "band_hint": "presence"},
    {"track": 2, "kind": "eq_cut", "device": "EQ Eight",
     "freq_hz": 2800, "q": 2.0, "gain_db": -3.0,
     "rationale": "rhythm gtr masking focal's presence band by 4.1 dB"},
    {"track": 5, "kind": "sidechain_compress",
     "source": 4, "ratio": 4.0, "threshold_db": -18, "ducking_db": -4,
     "rationale": "keys obscuring lead during solo region"}
  ],
  "expected_changes": {
    "focal_presence_band_energy_delta_db": 5.2,
    "estimated_competing_masking_reduction_db": 6.8
  }
}
```

### L4.2 — `mix_apply(proposal, *, dry_run=False)`

Executes the proposal. For each action:
- Locate or insert the target device (auto-insert EQ Eight via `browser_load_device` if no EQ exists on the track).
- Set device parameters via existing `device_set_parameter_by_name`.
- For sidechain compression: load Compressor, configure sidechain input via the bridge.

`dry_run=True` returns what would be done without acting — same pattern as PR #15.

### L4.3 — Region-bounded automation writes (deferred to v2)

LOM coverage for clip envelopes is uneven. The static-EQ proposal already solves a huge fraction of real mix problems; region-bounded automation is an upgrade, not a prerequisite. Defer until a specific user need surfaces.

---

## Layer 5 — A/B verification

`mix_verify_intent(focal_track, intent, region, baseline_snapshot=None)`:

1. Re-bounce the region (after `mix_apply`).
2. Compute the same intent-relevant metrics as `mix_masking_analyze`.
3. Diff against the baseline (taken before `mix_apply` and passed in via `baseline_snapshot`).
4. Return a structured *"intent achieved by X dB"* answer.

The LLM uses this to tell the user *"the lead's energy in 2-5 kHz increased by 4.6 dB while the rhythm guitar's energy there dropped by 3.1 dB — measurable cut-through improvement."*

---

## Mix engineering knowledge (data file, not tools)

`src/ableton_mcp/mix_knowledge.py` codifies:

**Money bands per instrument class:**

| Instrument | Body | Presence/Attack |
|---|---|---|
| Lead vocal | 200-300 Hz | 2-4 kHz |
| Lead guitar | 150-400 Hz | 1-4 kHz |
| Rhythm guitar | 100-400 Hz | 2-5 kHz |
| Bass | 60-100 Hz | 700 Hz-2 kHz |
| Kick | 60-80 Hz | 2-5 kHz |
| Snare | 200 Hz | 5 kHz |
| Hi-hat | (no body) | 8-12 kHz |
| Piano | 80-1 kHz spread | 2-5 kHz |

**Standard fixes:**

- High-pass non-bass instruments at 80-120 Hz
- Cut focal's presence band on every other track by 2-4 dB
- Sidechain bass/keys/pads to kick

**Masking-overlap weights:** 1/3-octave masking spread; weights from ISO 532 / Bark scale loosely approximated.

---

## Open questions / risks

1. **Section detection accuracy.** Audio-based detection works but tuning per song style is real work. Mitigation: cheap path first (clip presence + names), audio path as fallback, LLM asks user to confirm ranges if confidence is low.
2. **Region-bounded automation.** LOM is sparse. Mitigation: ship static-EQ proposals first; defer automation.
3. **EQ Eight auto-insertion.** Conservative default — only insert if no EQ exists, never replace an existing chain. Surface what was inserted in the result so the user can see.
4. **Trust.** Mid-mix parameter changes need user-visible diffs. Use the state-diff infrastructure from PR #18 to show before/after of all parameters changed.

---

## Sequencing recommendation

Fastest path to a working *"make the lead cut through during the solo"* loop:

1. L1.1 (region-bounded bounce) — closes data-gathering gap
2. L1.2 minimal (clip-pattern detection only) — covers most common case
3. L2.1 (mix_spectrum_at_region) + L2.2 (mix_masking_analyze)
4. L3 minimal (just `cuts_through`, `buried`, `muddy` descriptors)
5. L4.1 + L4.2 minimal (static EQ proposals + apply, no automation)
6. L5 (verification)

~10-12 days of focused work. Region-bounded automation, sidechain wiring, and the full descriptor set come later.
