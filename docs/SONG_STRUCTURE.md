# Song structure — bar-counted section notation

The notation here is the **musician-facing dialect** AbletonFullControlMCP uses
when talking about song shape. It is deliberately concise — the kind of thing
you'd scribble on the back of a napkin while sketching an arrangement.

The seed example, drawn from `examples/jrock_composition.py`:

```
intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars
```

That's a complete song description. Six sections, total length 35 bars,
ordered left to right along the arrangement timeline.

---

## 1. Grammar

A `Structure` is a sequence of `Section`s separated by `/` (slashes) or
commas. Each `Section` is a **name** followed by a **bar count**. The name
is one or more words; the bar count is a positive integer.

### Canonical form

```
<name> <bars> / <name> <bars> / ... [= <total> bars]
```

Examples:

```
intro 4 / verse 8 / chorus 8
intro 4 / verse 8 / chorus 8 = 20 bars
intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8 = 35 bars
```

### Numbered alternate

You can also lead with the bar count:

```
4 bars intro, 8 bars verse, 8 bars chorus
```

The parser accepts `bar`, `bars`, or `b` as the unit synonym.

### Total assertion

If you append `= <N> bars`, the parser validates that the section bar counts
sum to `N`. A mismatch raises a `StructureParseError` with the actual sum and
the expected sum, so you catch arithmetic mistakes at parse time.

### Whitespace and punctuation

Tabs, multiple spaces, commas, and slashes all separate sections. Section
names are lowercased on parse. `verse 1`, `Verse 1`, and `verse 1` all yield
the same section name.

---

## 2. Section vocabulary

The role auto-detection table. The parser strips digits and trailing
qualifiers from the name (`verse 2`, `chorus_b`, `main groove A`), then
matches the resulting stem against the table below. The first keyword that
appears as a substring of the stem wins.

| Keyword stem(s) | Role | Semantic intent |
|---|---|---|
| `intro` | `intro` | Establishes mood, key, tempo. Often instrumental, sparse. |
| `pre-chorus`, `pre_chorus`, `prechorus`, `pre chorus` | `pre_chorus` | Lifts energy out of a verse into the chorus. |
| `post-chorus`, `post_chorus`, `postchorus` | `post_chorus` | Tag/release after a chorus. |
| `chorus`, `hook`, `refrain` | `chorus` | The hook. Maximum energy + memorability. (`refrain` keeps its own role.) |
| `refrain` | `refrain` | Repeating tag line, less hook-y than a chorus. |
| `verse` | `verse` | Storytelling section. Lower energy than the chorus. |
| `bridge`, `middle 8`, `middle eight` | `bridge` | Contrast section. New chords or melody, often only once. |
| `breakdown` | `breakdown` | Stripped-down section building tension; usually after a chorus. |
| `drop` | `drop` | The release point in EDM/electronic — heavy, full mix. |
| `build`, `buildup`, `build-up`, `riser` | `build` | Tension-builder leading into a drop or chorus. |
| `interlude`, `solo` | `interlude` / `solo` | Instrumental passage. `solo` = featured single instrument. |
| `outro`, `ending`, `coda` | `outro` | Wind-down or final tail. |
| `fill`, `fill-in` | `fill` | Short transitional flourish (1-2 bars). |
| `hit`, `stab` | `hit` | One-bar (or shorter) accent. |
| `tag` | `tag` | A short repeat of a phrase to extend a section. |
| `vamp`, `groove`, `loop`, `main`, `final` | `vamp` | A repeating instrumental groove with no chord changes (or just one). `groove`/`final`/`main` are jam-band shorthand for "the same vamp returns". |
| `half-time`, `halftime`, `half time` | `half_time` | Tempo-feel halved (kick on 1, snare on 3). |
| `double-time`, `doubletime`, `double time` | `double_time` | Tempo-feel doubled. |
| (anything else) | `other` | Free-form custom section name. |

Within a single song you can repeat a section name (`verse`, `chorus`, etc.).
The operations API addresses sections by **first occurrence by name**;
to disambiguate, rename to `verse 1`, `verse 2`, etc.

---

## 3. Bars → beats math

Live's transport speaks in **quarter-note beats**. A bar's beat-length is
a function of the time signature.

For a time signature `numerator / denominator`:

```
beats_per_bar = numerator * (4 / denominator)
```

Worked examples:

| Time sig | beats per bar | Why |
|---|---|---|
| 4/4 | 4 | 4 quarters |
| 3/4 | 3 | 3 quarters |
| 6/8 | 3 | 6 eighths = 3 quarters |
| 7/8 | 3.5 | 7 eighths = 3.5 quarters |
| 12/8 | 6 | 12 eighths = 6 quarters |
| 5/4 | 5 | 5 quarters |

So `intro 4 / groove 8 / breakdown 6 / interlude 6 / buildup 3 / final 8`
in **6/8** at 144 BPM = 35 bars × 3 beats = **105 beats**. At 144 BPM that
is `105 / 144 * 60 = 43.75 seconds`.

---

## 4. Mapping to Live constructs

A `Structure` is, by itself, just a plan. Translating to Live happens via
`live_bridge.py`:

- **Section ranges:** every section maps to a `(start_beat, length_beats)`
  on the arrangement timeline. `start_beat` is the cumulative beat-length
  of all preceding sections.
- **Loop a section:** sets Live's arrangement loop to the section's range
  via `/live/song/set/loop_start` + `/live/song/set/loop_length`, then
  toggles `loop` on.
- **Jump to a section:** sets `current_song_time` to the section's
  `start_beat`.
- **Place clips:** the bar-counted plan is meant to drive
  `clip.duplicate_to_arrangement` calls (or the existing `arrangement_*`
  tools) — one section, one clip at the corresponding `start_beat`.

The data model itself is decoupled from Live, so unit tests can roundtrip
parse and operations without a running Live instance.

---

## 5. Editing in dialect

The user-facing operations all take a section by name:

- `extend the breakdown by 4 bars` → `structure_extend(struct, "breakdown", 4)`
- `duplicate the final groove` → `structure_duplicate(struct, "final")`
- `insert a 2-bar fill before the buildup` → `structure_insert(struct,
  after_section_name="<the section before buildup>", new_section_name="fill",
  bars=2)` (use the section that precedes "buildup" as the anchor; the
  notation always inserts *after* a named anchor for unambiguous positioning).
- `remove the bridge` → `structure_remove(struct, "bridge")`
- `what's bars 12-18 again?` → introspect with `structure_section_range`.

All operations return a *new* `Structure`; nothing is mutated in place.
Round-tripping `to_text()` always yields the user's notation back, so the
chat-readable form is always ground truth.
