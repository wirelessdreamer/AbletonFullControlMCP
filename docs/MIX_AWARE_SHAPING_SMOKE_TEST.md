# Mix-aware shaping — real-Live smoke test

> Goal: shake out OSC-side surprises that the mocked test suite (635 green) can't catch. Run this with Live actually open + AbletonOSC + the bridge connected, against a real session that has a buried lead during a solo region.

The 635 unit tests prove the math and the structured tool surface are right. They don't prove that:

- AbletonOSC's reply timing for EQ Eight band parameters matches our reads.
- The exact `"<N> Filter Type A"` parameter name lookup is case-stable across Live versions.
- The arrangement-record start time actually lands at `start_beats * 60 / tempo` seconds.
- `browser_load_device("audio_effects/EQ Eight", ...)` resolves to the right device on Live 11 + 12.
- A real bounced stem's spectrum lines up with our expected band table (sample rate, framing).

This checklist walks the full conversational loop end-to-end and flags the most likely failure modes.

---

## Pre-flight

- [ ] **Live open** with an active set that has at least 4 tracks, one of them clearly a "lead" (lead vocal / lead guitar / featured solo instrument).
- [ ] **AbletonOSC** installed and running (UDP 11000/11001).
- [ ] **AbletonFullControlBridge** ≥ 1.4.0 running (TCP 11002). Confirm with `mcp__ableton_full_control__live_ping`.
- [ ] **MCP server** running (this repo's `ableton_full_control`). Confirm the `mix_*` tools are listed.
- [ ] **A solo / featured section** somewhere in the arrangement, 8-32 beats long. Either named via a clip name containing "solo"/"lead"/"break" OR detectable via clip-overlap heuristic (focal has a clip in the range, ≥1 other track is silent).
- [ ] **Clean save** of the set before starting — every step writes to Live, and you'll want a known-good restore point.

---

## Step 1 — Discovery tools (no Live writes)

These exercise the L3 / KB layers without touching Live state. They should always work.

```text
> Call mix_list_intents
```
- [ ] **Expect**: 12 descriptors returned. Names include `cuts_through`, `buried`, `muddy`, `harsh`, `airy`, `boomy`, `sibilant`, etc.
- [ ] **Check**: each has `band_low_hz` < `band_high_hz`, both in (0, 22000].

```text
> Call mix_describe_intent intent="Cut Through"
```
- [ ] **Expect**: resolves to `cuts_through`, `band_low_hz=2000.0`, `band_high_hz=5000.0`, `action_class="cut_competitors"`. (Tests case + whitespace normalisation against the real registry.)

```text
> Call mix_classify_track_by_name on each real track name
```
- [ ] **Expect**: tracks named "Lead Vocal", "Bass", "Kick", "Snare", "Hi-Hat", "Lead Guitar", "Rhythm Gtr", "Piano", "Keys" classify correctly.
- [ ] **Likely failure**: track names with parens or version suffixes (e.g. `"Lead Gtr (Solo Take)"`). The whole-word matcher should still fire on `"Lead Gtr"`. If it doesn't, refine the regex or add aliases.

---

## Step 2 — Section detection (L1.2)

```text
> Call arrangement_find_sections track_index=<focal>
```
- [ ] **Expect**: at least one `Section` with `start_beats`, `end_beats`, `confidence`, `kind` ∈ {"clip_name", "clip_overlap"}.
- [ ] **Sanity check**: visually compare with Live's arrangement view — does the start_beats / end_beats actually point at where the solo lives?
- [ ] **Known limitation**: this is the clip-based heuristic only (L1.2). If your focal's solo doesn't sit on a separate-named clip AND doesn't have ≥1 other track silent during it, this returns nothing — L1.2b (audio RMS) is deferred. Workaround: manually supply the beats below.

Pick the strongest section and record:

- `START_BEATS = ___`
- `END_BEATS = ___`
- `FOCAL_TRACK = ___`

---

## Step 3 — Region bounce sanity (L1.3)

Before chaining into the L2 analysis, test the bounce in isolation. Surface OSC issues here, not later.

```text
> Call bounce_region_all_active output_dir="<tmp>" start_beats=<START> end_beats=<END> encode_mp3=false
```
- [ ] **Expect**: `kind="region_stems"`, one stem per un-muted audio-producing track, each with `copied=true`, `output_path` pointing at an existing wav.
- [ ] **Sanity check** each wav: open in Live or any audio tool. Duration should be `(END - START) * 60 / tempo` seconds. Content should match the *region* of the source track, not the start of the song.
- [ ] **Likely failure 1**: bounce starts at 0:00 of the song instead of `START`. The `_record_arrangement` setter for `current_song_time` might be reading beats vs seconds — check the conversion (`beats_to_seconds(start, tempo)`).
- [ ] **Likely failure 2**: a stem comes out silent. Try `warmup_sec=0.5` to prime samplers on the first run.
- [ ] **Likely failure 3**: orphan `_ tmp_bounce` tracks left after a crash. The pre-cleanup pass should delete them; if not, do it manually before retrying.

---

## Step 4 — Spectrum analysis (L2.1)

```text
> Call mix_spectrum_at_region start_beats=<START> end_beats=<END>
```
- [ ] **Expect**: one entry in `tracks` per non-muted audio track, each with `analyzed=true` and a `energy_db_per_band` array of 29 floats.
- [ ] **Sanity check** the focal track's `top_bands`. For a lead vocal these should cluster in 200-300 Hz (body) + 2-4 kHz (presence). For a bass: 60-100 Hz (body) + 700 Hz-2 kHz (attack).
- [ ] **Check**: `spectral_centroid_hz` is plausibly in-band (e.g. ~1-3 kHz for a vocal; <200 Hz for a kick).
- [ ] **Likely failure 1**: `wav_path` resolution mismatch — the bounce wrote to one location but the analyzer reads from another. Should be fixed by `Path.resolve()` everywhere; verify both paths in the log.
- [ ] **Likely failure 2**: dB values all at `-160` (DB_FLOOR) — the wav is empty or the STFT is choking on the sample rate. Check the wav exists and has non-zero size.

---

## Step 5 — Masking diagnosis (L2.2)

```text
> Call mix_masking_at_region focal_track_index=<FOCAL> start_beats=<START> end_beats=<END>
```
- [ ] **Expect**: `focal_money_bands` = 3-5 entries in the focal's strongest range, `competing_tracks` = ranked list with `masking_score` in [0, 1].
- [ ] **Sanity check**: does the top competitor make musical sense? If the focal is a vocal and the top competitor is the rhythm guitar in the 2-5 kHz range, that's the textbook case.
- [ ] **Red flag**: every competitor has `masking_score < 0.1`. Either the lead is genuinely isolated (no masking — the section isn't a "cut through" problem) OR the spectrum bounds aren't aligned with the focal's actual energy. Spot-check `focal_money_bands` — do they overlap the focal's loudest content in real life?

---

## Step 6 — Proposal (L4.1, dry-run-equivalent, no writes)

```text
> Call mix_propose_at_region focal_track_index=<FOCAL> intent="cuts_through" start_beats=<START> end_beats=<END>
```
- [ ] **Expect**: 1-4 actions, each with a non-empty `rationale` referencing real dB / Hz numbers from the masking analysis.
- [ ] **Sanity check the rationales**: do they read like things a mix engineer would actually say? Example good rationale: *"Rhythm Gtr masks the focal by +3.2 dB at 3150 Hz (masking score 0.78); cutting frees the cuts through band."*
- [ ] **Action sizing**: `gain_db` magnitudes should be small (-2 to -6 dB for cuts, +2 to +3 for boosts). If you see -12 dB anywhere, something miscaled.
- [ ] **Save this proposal** — you'll pass it to apply in step 8.

Try a few different intents on the same focal/region to spot-check vocabulary coverage:

- [ ] `intent="muddy"` → cuts on competitors in 200-400 Hz (only if any competitor has energy there)
- [ ] `intent="harsh"` → one cut on the **focal** in 2-5 kHz
- [ ] `intent="airy"` → one high-shelf boost on the **focal** above 10 kHz
- [ ] `intent="boomy"` → high-passes on non-bass competitors at 80-120 Hz

---

## Step 7 — Baseline snapshot

```text
> Call mix_snapshot_for_verification focal_track_index=<FOCAL> start_beats=<START> end_beats=<END>
```
- [ ] **Expect**: same shape as `mix_masking_at_region`. Save the full result — pass back as `baseline_snapshot` in step 9.
- [ ] **Quick sanity**: the focal_money_bands and competing_tracks should match what step 5 returned (give or take run-to-run drift in the bounce).

---

## Step 8 — Apply dry-run, then real (L4.2)

**Dry-run first** to confirm the plan looks right before any state change:

```text
> Call mix_apply_proposal proposal=<the dict from step 6> dry_run=true
```
- [ ] **Expect**: `dry_run=true`, `plan` = ordered list of `DeviceStep`s, `results=[]` (no execution).
- [ ] **Sanity check the plan**: for each action in the proposal, there should be a `set_band` step. If a track had no EQ Eight, an `insert_eq_eight` step precedes the `set_band`.
- [ ] **Check `band_index`**: should be `1` if no band-state info was readable (fallback), or a higher number (the first off band) if reads succeeded.

If the plan looks right, **apply for real**:

```text
> Call mix_apply_proposal proposal=<same> dry_run=false
```
- [ ] **Expect**: `results` array with one entry per step.
- [ ] **Visually verify in Live**: every target track now has an EQ Eight (or an existing one was reused) with the expected band on, at the expected frequency, with the expected gain.
- [ ] **Likely failure 1**: `_set_device_param` returns "parameter not found." Check the EQ Eight's parameter list with `device_get_parameters` — verify the exact format (`"1 Filter Type A"` vs `"1 Filter Type"` — the trailing ` A` is intentional, it's the upper-EQ-A bank).
- [ ] **Likely failure 2**: the inserted EQ Eight lands in an unexpected device-chain position. Verify the new `device_index` from the insert step matches where Live actually put the device.
- [ ] **Likely failure 3**: `browser_load_device("audio_effects/EQ Eight", ...)` returns "no such device" on Live 12. Live 12 may rename or move EQ Eight. Try `"audio_effects/EQ Eight (Legacy)"` or use the URI form.

---

## Step 9 — Verification (L5)

Round-trip the same intent + region against the baseline you saved in step 7.

```text
> Call mix_verify_intent focal_track_index=<FOCAL> intent="cuts_through" start_beats=<START> end_beats=<END> baseline_snapshot=<dict from step 7>
```
- [ ] **Expect**: `intent_achieved=true`, `summary` mentioning the focal-band Δ + competitor-band Δ in dB.
- [ ] **Check the per-competitor diffs**: the track(s) we cut should show `band_energy_delta_db ≈ -3` (or whatever the proposal asked for, give or take a fraction of a dB). The focal's band energy should be roughly unchanged.
- [ ] **Likely failure 1**: `intent_achieved=false` because the dB delta is < `MIN_DELTA_DB` (1.5). The cuts we made were too small to clear the threshold. Either bump the proposal magnitudes (edit `MIN_COMPETITOR_SCORE` / `_competitor_gain_db`) or accept that some intents need iteration.
- [ ] **Likely failure 2**: `regressed=true`. The change went the wrong way. Most likely cause: the apply landed on the wrong band or wrong filter type, and we boosted instead of cutting. Undo in Live (Ctrl+Z), inspect the proposal/plan from step 8.

**The good outcome**: a summary that reads like *"intent=cuts_through; focal band Δ +0.4 dB; avg competitor band Δ -3.1 dB; intent ACHIEVED."*

---

## Step 10 — Listening test

The numbers can lie. Always close the loop with ears.

- [ ] **Loop the region** in Live's arrangement view (set the locator to `[START, END]` and play).
- [ ] **A/B**: Live's undo (Ctrl+Z) reverts the EQ moves. Toggle and listen to "before" vs "after." Does the lead actually cut through more, with the kind of effect the user asked for?
- [ ] **Check for side effects**: any tracks sounding thin / nasal / dull from the cuts we made? If yes, the proposal magnitudes are too aggressive — tune `_competitor_gain_db` down.

---

## Failure capture

If any step fails or surprises you, capture:

1. The exact tool call + args.
2. The full response dict (including `status` / `error`).
3. The Live version (Help → About).
4. AbletonOSC version (look at `AbletonOSC.amxd` in the M4L folder).
5. Bridge version (check the `version` field in `ableton_explain` output).

File those as issues in this repo with the label `mix-aware-shaping`. The smoke test will produce a small backlog of "OSC reality vs our mocks" diffs — each becomes a tightly-scoped follow-up PR.

---

## Quick post-test cleanup

- [ ] Undo all EQ Eight inserts (`Ctrl+Z` repeatedly, or revert the saved set).
- [ ] Delete any orphan `_tmp_bounce` tracks left over.
- [ ] Delete bounced wavs from the output directory if you're not keeping them.

---

## What "feature complete in practice" looks like

After this smoke test passes on at least one real session, the following gaps remain in the build-out (tracked in [`MIX_AWARE_SHAPING.md`](MIX_AWARE_SHAPING.md)):

- **L1.2b** audio-based section detection (RMS-windowed) — for solos that don't sit on a uniquely-named clip.
- **L4.2 follow-up** de-esser apply path and compressor-attack apply path — currently in `skipped`.
- **L4.2 follow-up** sidechain compression apply path — requires bridge-side work; sidechain routing isn't OSC-exposed.
- **L4.3** region-bounded automation writes — lets a fix apply *only during* the solo region instead of the whole song.

The smoke test itself doesn't fix any of those — it confirms the **core conversational loop** works end-to-end on real audio.
