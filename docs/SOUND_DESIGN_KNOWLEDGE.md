# Sound Design Knowledge — Live 11 stock devices

A musician-facing reference for the descriptors AbletonFullControlMCP
maps to specific knobs on Live's stock devices. Use this when a user
says "make the lead more aggressive" or "soften the piano" and you need
to know what the LLM will actually twist.

This file is the human-readable companion to
`src/ableton_mcp/sound_design/device_rules.py` (the canonical machine
catalogue) and to the per-device schemas under
`src/ableton_mcp/device_schemas/`. Every parameter name quoted here is
the canonical schema name — it must match the LOM string Live exposes
via `/live/device/get/parameters/name`.

The 13 descriptors covered for every supported device:

> bright, dark, warm, aggressive, soft, punchy, sustained, plucky,
> distorted, clean, wide, tight, dense

Direction markers:
- `↑` push the param up (toward its max)
- `↓` push the param down (toward its min)

Each rule has a weight in `0..1` — used by the applier to blend rules
when several descriptors touch the same parameter (e.g. "bright +
aggressive" both move filter cutoff up).

---

## Coverage at a glance

Devices with curated rules:

| Class name (LOM) | Display name | Type |
|---|---|---|
| `Drift` | Drift | Instrument (compact monosynth) |
| `Operator` | Operator | Instrument (4-op FM hybrid) |
| `InstrumentVector` | Wavetable | Instrument (wavetable synth) |
| `Tension` | Tension | Instrument (string PM) |
| `AnalogDevice` | Analog | Instrument (virtual analog) |
| `Compressor2` | Compressor | Effect (dynamics) |
| `Reverb` | Reverb | Effect (algorithmic verb) |
| `AutoFilter` | Auto Filter | Effect (LFO/env filter) |
| `Saturator` | Saturator | Effect (waveshaping) |
| `Amp` | Amp | Effect (guitar amp emulator) |
| `Cabinet` | Cabinet | Effect (cab IR + mic) |
| `Echo` | Echo | Effect (delay) |

Devices the user mentioned but where this catalogue is intentionally
silent (rule sets too sketchy to ship honestly): `Sampler`,
`OriginalSimpler` (Simpler), `DrumGroupDevice` (Drum Rack), `Impulse`,
`Electric`, `Bass`, `Collision`, `Eq8` (EQ Eight), `DrumBuss`,
`GlueCompressor`, `PhaserFlanger`, `ChorusEnsemble`. Use the
heavy-but-precise `shape_apply` (probe-dataset) workflow for those.

---

## Drift (`Drift`)

**Architecture.** Compact MPE-ready monosynth: two oscillators (Sine /
Triangle / Saw / Square / Pulse), one multimode filter (LP/HP/BP/Notch),
one amp envelope, one filter LFO. Designed for fast tweaking, every knob
is on the front panel.

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Filter Frequency ↑ (0.9), Filter Resonance ↑ (0.3), Osc 1 Shape ↑ (0.2) | Cutoff is the single biggest move. Resonance accents harmonics around cutoff. |
| warmth | Filter Frequency ↓ (0.5), Filter Resonance ↓ (0.2), Env 1 Attack ↑ (0.3) | Warmth = dark-but-not-dull. Slow attack reduces transient bite. |
| aggression | Filter Resonance ↑ (0.7), Filter Frequency ↑ (0.4), Osc Mix ↑ (0.2) | Resonance is the main edge driver on Drift. |
| sustain | Env 1 Sustain ↑ (0.8), Env 1 Release ↑ (0.5), Env 1 Decay ↑ (0.3) | Hold the body, long release. |
| attack/punch | Env 1 Attack ↓ (0.7), Env 1 Decay ↓ (0.4), Env 1 Sustain ↓ (0.3) | Fast attack + short decay leaves the transient prominent. |
| body | Osc Mix ↓ (0.3), Noise Level ↑ (0.3) | Centre the oscillators (both audible) + a touch of noise. |
| air | (use Filter Frequency ↑) | Drift has no dedicated air band; cutoff opens the perceived air. |
| distortion-amount | Filter Resonance ↑ (0.4), Noise Level ↑ (0.3) | Drift has no built-in drive — closest we get is Q + noise. |
| space | (use a Reverb after Drift) | Drift has no built-in space. |
| movement | LFO 1 Amount ↑ (0.4) | LFO movement creates pseudo-stereo when used to mod pitch. |

**Pitfalls.** Resonance above ~0.85 self-oscillates (useful or not
depending on track). Pulling Env 1 Decay to zero with non-zero Sustain
pins the level high; expected, but easy to confuse with "no envelope".

---

## Operator (`Operator`)

**Architecture.** 4-operator FM / additive / wavetable hybrid synth.
Each operator (A/B/C/D) has its own oscillator + envelope; the global
Algorithm selects the FM routing topology (11 algorithms; 0 is parallel,
higher indices stack operators). Every operator can be either a
modulator or a carrier depending on the algorithm.

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Filter Freq ↑ (0.7), B Level ↑ (0.5), Tone ↑ (0.6) | More modulator level = more sidebands. Global Tone biases toward HF. |
| warmth | Tone ↓, Filter Freq ↓, B Level ↓ | Less modulation = simpler, warmer tone. |
| aggression | B Level ↑ (0.7), C Level ↑ (0.4), Filter Res ↑ (0.4) | Aggressive FM voices push modulator levels high; **above 0.7 turns harsh**. |
| punch | A Attack ↓ (0.7), A Decay ↓ (0.4), Pitch Env Amount ↑ (0.3) | Small positive pitch envelope adds attack 'click'. |
| sustain | A Sustain ↑ (0.8), A Release ↑ (0.5) | Hold the body. |
| distortion-amount | Filter Drive ↑ (0.7), B Level ↑ (0.5) | Excess FM is "distortion" in FM terms; above 0.7 turns harsh. |
| body | C Level ↑, D Level ↑ | Stack later operators. |
| movement | (LFO Amount on filter) | Use the LFO Amount + LFO Rate; rule set is light here. |

**Pitfalls.** Algorithm choice fundamentally changes which operator is a
modulator vs. carrier — the rule for "B Level ↑ adds brightness" assumes
Algorithm leaves B as a modulator into A. **Honest gap:** the rule set
does not cover the per-operator wave-folder, the full LFO destination
matrix, glide, or the per-operator Initial / Peak / End envelope
breakpoints. If those are critical for the sound, fall back to
`shape_apply` against a probe dataset.

---

## Wavetable (`InstrumentVector`)

**Architecture.** Two wavetable oscillators with sub osc, modulation
matrix, dual filters, three envelopes / two LFOs / MPE-style modulators.
The "Position" knob morphs through the wavetable, "Effect Mode" selects
how the wave is processed (None / FM / Classic / Modern / Sub).

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Filter 1 Frequency ↑ (0.8), Filter 1 Resonance ↑ (0.2), Oscillator 1 Wavetable Position ↑ (0.3) | Later positions are usually brighter, depends on the table. |
| warmth | Filter 1 Frequency ↓ (0.5), Sub Oscillator Gain ↑ (0.5) | Sub fills the bottom — warmth is low-mid weight. |
| aggression | Filter 1 Drive ↑ (0.7), Filter 1 Resonance ↑ (0.5), Oscillator 1 Effect 1 ↑ (0.4) | Effect 1 typically increases harmonic energy (FM/PD/etc). |
| punch | Envelope 1 Attack Time ↓ (0.7), Envelope 1 Decay Time ↓ (0.4), Envelope 1 Sustain Level ↓ (0.3) | Standard ADSR moves. |
| sustain | Envelope 1 Sustain Level ↑ (0.8), Envelope 1 Release Time ↑ (0.5) | |
| distortion-amount | Filter 1 Drive ↑ (0.8), Filter 1 Resonance ↑ (0.3) | Resonance + drive = nasty in a good way. |
| width | Oscillator 2 On ↑ (0.5), LFO 1 Amount ↑ (0.3) | Engaging Osc 2 enables stereo detune by default. |
| body | Sub Oscillator On ↑, Oscillator 2 On ↑ | Two oscillators audible. |

**Pitfalls.** The "Effect Mode" selector changes what `Effect 1` and
`Effect 2` mean — the rule treats Effect 1 ↑ as "more harmonic energy",
which is right in FM and Classic mode but less obvious in Sub mode.
**Honest gap:** Filter 2, Envelopes 2 and 3, LFO 2, the MPE/mod-matrix
routing, and the per-osc Unison parameters are not in the rule set.

---

## Tension (`Tension`)

**Architecture.** Physical-modelled string. The signal flow is
Excitator (Bow / Hammer / Hammer Bouncing / Plectrum / Plectrum Soft) →
String → Body. The model can be pushed into noisy / unstable regimes;
this is part of its charm but means "more force" doesn't always mean
"louder note".

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Position ↑ (0.5), String Damping ↓ (0.5) | Plucking nearer the bridge brightens; less damping leaves more upper partials. |
| warmth | String Damping ↑ (0.4), Position ↓ (0.4) | |
| aggression | Force ↑ (0.7), Velocity ↑ (0.5), Position ↑ (0.3) | **Bow Force at max produces pure noise** — physical-model honesty, not a bug. |
| sustain | String Decay ↑ (0.8), String Damping ↓ (0.5) | Bow excitator keeps the string indefinite. |
| pluck | String Decay ↓, String Damping ↑, Excitator Type ↓ | Plectrum is more pluck-shaped than bow. |
| distortion-amount | Force ↑ | Excessive force enters the noisy regime; this isn't 'clean' distortion. |

**Pitfalls.** "Bow Force at max produces pure noise" — real, by design.
**Honest gap:** the rule set doesn't cover the full damper, termination,
or body-radiation parameters, and "wide" has no rule because Tension's
body Type is mono.

---

## Analog (`AnalogDevice`)

**Architecture.** Two-oscillator virtual-analog subtractive synth modelled
on vintage polysynths. Two filters, two LFOs, two amp envelopes. The
rule set here covers Filter 1 / Amp 1 / LFO 1 / OSC1+OSC2.

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Filter1 Freq ↑ (0.9), Filter1 Res ↑ (0.3), Filter1 Env ↑ (0.3) | Filter envelope opens with notes. |
| warmth | Filter1 Freq ↓ (0.5), OSC1 Shape ↓ (0.3) | Sine/saw waves are warmer than rectangle/noise. |
| aggression | Filter1 Res ↑ (0.6), Filter1 Env ↑ (0.5), OSC2 Detune ↑ (0.4) | Wide detune sounds aggressive. |
| punch | Amp1 Attack ↓ (0.7), Amp1 Decay ↓ (0.4), Amp1 Sustain ↓ (0.3) | Standard ADSR. |
| width | OSC2 Detune ↑ (0.6) | |

**Pitfalls.** Amp1 Release at the maximum (10 sec) can leave notes
hanging if you didn't intend a pad.

---

## Compressor (`Compressor2`)

**Architecture.** Feed-forward / feedback compressor with peak/RMS
detection and sidechain. The Compressor2 class name is the modern Live
compressor (Live 9+).

| Concept | Knobs | Notes |
|---|---|---|
| punch | Attack ↑ (0.6), Release ↓ (0.5), Ratio ↑ (0.4) | Slow attack (10–30 ms) lets the transient through, body gets compressed. |
| aggression | Ratio ↑ (0.7), Threshold ↓ (0.6), Attack ↓ (0.4) | Heavy compression with fast attack flattens transients — sounds slammed. |
| warm | Attack ↑ (0.5), Ratio ↓ (0.3) | Gentler compression on full mixes. |
| dense | Threshold ↓ (0.5), Ratio ↑ (0.4), Output Gain ↑ (0.3) | Heavier compression = denser apparent loudness. |
| tight | Release ↓ (0.6), Attack ↓ (0.4), Ratio ↑ (0.4) | |

**Pitfalls.** Compressor isn't an EQ — `bright` and `dark` rule lists are
intentionally empty, since pulling threshold down only nudges perceived
brightness via make-up gain. **No "wide" rule** because the compressor
sums L/R for detection.

---

## Reverb (`Reverb`)

**Architecture.** Algorithmic reverb with early reflections + diffusion
network. Predelay, room size, decay time, plus a tail HiShelf and an
input bandpass filter.

| Concept | Knobs | Notes |
|---|---|---|
| brightness (of the tail) | HiShelf Gain ↑ (0.6), HiShelf Freq ↑ (0.3), In Filter Freq ↑ (0.4) | |
| darkness (of the tail) | HiShelf Gain ↓ (0.7), HiShelf Freq ↓ (0.4), In Filter Freq ↓ (0.3) | "Darker reverb on the rhythm guitar" — this is the rule. |
| width | Stereo Image ↑ (0.7), Room Size ↑ (0.3) | Bigger rooms feel wider. |
| sustain | Decay Time ↑ (0.8), Diffuse Level ↑ (0.4) | |
| tight | Decay Time ↓ (0.6), Room Size ↓ (0.4), Predelay ↓ (0.3) | |
| dense | Diffuse Level ↑ (0.6), Decay Time ↑ (0.4) | |

**Pitfalls.** Decay Time = 60 sec at the max — useful as a freeze /
infinite verb but wears out fast in a normal mix. **No "distortion"
rule** because Reverb has no drive stage.

---

## Auto Filter (`AutoFilter`)

**Architecture.** Multimode filter with envelope follower and LFO
modulation. Drive, resonance, type, mode (LP/HP/BP/Notch/Morph), plus
LFO and envelope follower sections.

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Frequency ↑ (0.9), Resonance ↑ (0.2) | |
| aggression | Resonance ↑ (0.7), Drive ↑ (0.6) | Drive into clipping. |
| punch | Envelope Modulation ↑ (0.5), Envelope Attack ↓ (0.4) | Envelope follower opens cutoff with the transient. |
| pluck | Envelope Modulation ↑ (0.6), Envelope Release ↓ (0.5) | Big sweep + fast release = pluck shape. |
| distortion-amount | Drive ↑ (0.8), Resonance ↑ (0.4) | Auto Filter has a dedicated drive stage. |
| width | LFO Amount ↑ (0.5) | Stereo LFO offsets the filter per channel. |

---

## Saturator (`Saturator`)

**Architecture.** Waveshaping saturator with multiple curves (Analog
Clip / Soft Sine / Medium / Hard / Sinoid Fold / Digital Clip) and a
built-in HP/LP/EQ section.

| Concept | Knobs | Notes |
|---|---|---|
| distortion-amount | Drive ↑ (0.9), Type ↑ (0.5) | The whole point of this device. |
| warmth | Drive ↑ (0.4), Type ↓ (0.3) | Light drive on Analog Clip / Soft Sine is classic warmth. |
| aggression | Drive ↑ (0.8), Type ↑ (0.5) | Higher-index curves are nastier. |
| brightness | Drive ↑ (0.5) | Saturation generates upper harmonics. |
| dense | Drive ↑ (0.5) | Drive thickens the spectrum. |

**Pitfalls.** "Type" is an enum and curves are very different — the rule
treats lower indices as warmer, higher as harsher, which is right for
the default ordering but a user who has chosen a specific curve manually
may be surprised when "warmer" knocks Type down.

---

## Amp (`Amp`)

**Architecture.** Seven-model guitar amp emulator (Suite). Amp Type:
Clean / Boost / Blues / Rock / Lead / Heavy / Bass.

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Treble ↑ (0.7), Presence ↑ (0.6) | |
| warmth | Bass ↑ (0.5), Treble ↓ (0.4), Amp Type ↓ (0.3) | Clean / Boost / Blues are warmer than Heavy / Lead. |
| aggression | Gain ↑ (0.8), Amp Type ↑ (0.5), Presence ↑ (0.4) | Higher-index amps (Lead / Heavy) are aggressive by design. |
| distortion-amount | Gain ↑ (0.9), Amp Type ↑ (0.5) | |
| width | Dual Mono ↑ (0.7) | Dual Mono runs separate L/R amps — instant width. |
| tight | Bass ↓ (0.4), Middle ↑ (0.3) | Less low end = tighter; mids define the note. |

**Pitfalls.** Gain at max on the Heavy amp is **very** distorted — the
rule trusts the user's `intensity` to keep it sane.

---

## Cabinet (`Cabinet`)

**Architecture.** Speaker cabinet IR + dual mic emulation. Designed to
follow Amp on a guitar / bass chain.

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Microphone Type ↑ (0.5), Microphone Position ↑ (0.3) | Condenser mic is brighter than dynamic; far position picks up more room HF. |
| warmth | Microphone Type ↓ (0.5), Microphone Position ↓ (0.3) | Dynamic mic, near and on-axis. |
| punch | Microphone Position ↓ (0.5) | Near mic is more direct/punchy. |
| width | Dual Mono ↑ (0.7) | Stereo cabinet image. |

**Pitfalls.** **No "aggression" / "distortion-amount" / "sustain" /
"plucky" rules** — Cabinet sits *after* the distortion stage and has no
envelope. Don't expect Cabinet to make a clean sound aggressive on its
own.

---

## Echo (`Echo`)

**Architecture.** Character delay with built-in reverb tail, modulation,
ducking, and a tape-character section (noise, wobble).

| Concept | Knobs | Notes |
|---|---|---|
| brightness | Filter Freq Hi ↑ (0.6) | |
| darkness | Filter Freq Hi ↓ (0.7) | |
| warm | Filter Freq Hi ↓ (0.5), Character Wobble ↑ (0.3) | Tape wobble = warm character. |
| aggression | Feedback ↑ (0.6), Dry/Wet ↑ (0.4) | **Heavy feedback (>0.8) self-oscillates** — careful. |
| sustain | Feedback ↑ (0.6), Reverb Amount ↑ (0.4) | More repeats + built-in reverb tail. |
| dense | Feedback ↑ (0.5), Reverb Amount ↑ (0.3) | |

**Pitfalls.** Feedback can exceed 1 in this device — it's the
self-oscillation territory and gets loud. The intensity knob keeps the
applier from blasting it on its own, but a curious user can still get
themselves into trouble.

---

## Honest gaps

These devices were on the user's wishlist but the catalogue does NOT
yet ship rules for them, because the maintainer did not have a high
enough confidence model of the right knob → descriptor mapping to
publish honestly:

- **Sampler / Simpler / Drum Rack / Impulse** — sample-playback
  instruments where the relevant control is "which sample you loaded"
  rather than knobs. Filter cutoff / amp envelope rules would technically
  apply but it's almost always more useful to swap the sample. Use the
  inventory + browser tools for that path.
- **Electric / Bass / Collision** — physical-model instruments with
  small, idiosyncratic surfaces. Easy to write *something*, hard to
  write rules that hold across presets. The maintainer marked these
  TODO until a verified mapping exists.
- **EQ Eight (`Eq8`)** — the eight-band parametric EQ. Each band is
  individually programmable; "brighter" could mean "lift the high
  shelf" or "lift band 7 by 3 dB" depending on the band assignment.
  Defer to per-band controls.
- **Drum Buss / Glue Compressor / Phaser-Flanger / Chorus-Ensemble** —
  schemas exist, rule sets do not. These are good first-card candidates
  for someone wanting to extend the catalogue.

For the unsupported devices: prefer `shape_apply` (the probe-dataset
route in `tools/sound_shaping.py`) or a direct
`device_set_parameter_by_name` if the user knows exactly what they want.
