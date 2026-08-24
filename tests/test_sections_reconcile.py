"""Tests for song_sections.reconcile — the discovery DP + sequence diff.

Pure-module tests: rapidfuzz + stdlib only, fast, no models.
"""

from __future__ import annotations

import random

from ableton_mcp.song_sections.model import TranscriptWord
from ableton_mcp.song_sections.reconcile import (
    assign_display_names,
    block_confidence,
    diff_sequences,
    discover_blocks,
    norm_tokens,
)

# ---------------------------------------------------------------------------
# Helpers — build synthetic transcripts from lyric text
# ---------------------------------------------------------------------------

VERSE_1 = "In the quiet in the stillness I know that You are God"
VERSE_2 = "In the chaos in confusion I know You're sovereign still"
CHORUS = "I'm caught up in Your presence I just want to sit here at Your feet"
BRIDGE = "I'm so in love I'm so in love with You"
TAG = "oh oh oh"


def _words_from(text: str, start: float, wps: float = 2.5) -> list[TranscriptWord]:
    """Turn lyric text into evenly spaced TranscriptWords (wps words/sec)."""
    words = []
    t = start
    for tok in text.split():
        words.append(TranscriptWord(text=tok, start=t, end=t + 1.0 / wps, prob=0.9))
        t += 1.0 / wps
    return words


def _transcript(*parts: tuple[str, float]) -> list[TranscriptWord]:
    out: list[TranscriptWord] = []
    for text, start in parts:
        out.extend(_words_from(text, start))
    return out


def _sections() -> list[dict[str, str]]:
    return [
        {"label": "Verse 1", "lyrics": VERSE_1},
        {"label": "Verse 2", "lyrics": VERSE_2},
        {"label": "Chorus", "lyrics": CHORUS},
        {"label": "Bridge", "lyrics": BRIDGE},
    ]


def _corrupt(text: str, fraction: float, seed: int = 42) -> str:
    """Replace ~fraction of words with junk (simulates ASR errors)."""
    rng = random.Random(seed)
    toks = text.split()
    n_bad = max(1, int(len(toks) * fraction))
    for idx in rng.sample(range(len(toks)), n_bad):
        toks[idx] = "blah"
    return " ".join(toks)


# ---------------------------------------------------------------------------
# norm_tokens
# ---------------------------------------------------------------------------


def test_norm_tokens_strips_noise() -> None:
    assert norm_tokens("I'm caught, up! (echo) x2") == ["im", "caught", "up"]


def test_norm_tokens_empty() -> None:
    assert norm_tokens("(x3)") == []


# ---------------------------------------------------------------------------
# discover_blocks
# ---------------------------------------------------------------------------


def test_exact_match_song_in_order() -> None:
    words = _transcript((VERSE_1, 10.0), (CHORUS, 25.0), (VERSE_2, 45.0), (CHORUS, 60.0))
    blocks = discover_blocks(words, _sections())
    assert [b.label for b in blocks] == ["Verse 1", "Chorus", "Verse 2", "Chorus"]
    assert blocks[0].start_sec == 10.0
    assert all(b.score >= 90 for b in blocks)
    # Blocks are in time order by construction.
    starts = [b.start_sec for b in blocks]
    assert starts == sorted(starts)


def test_extra_chorus_discovered() -> None:
    # Performed: V1 C C C — more choruses than any chart sequence would say.
    words = _transcript((VERSE_1, 5.0), (CHORUS, 20.0), (CHORUS, 40.0), (CHORUS, 60.0))
    blocks = discover_blocks(words, _sections())
    assert [b.label for b in blocks] == ["Verse 1", "Chorus", "Chorus", "Chorus"]


def test_missing_section_not_forced() -> None:
    # Bridge never sung — nothing should claim its lyrics.
    words = _transcript((VERSE_1, 5.0), (CHORUS, 20.0))
    blocks = discover_blocks(words, _sections())
    assert [b.label for b in blocks] == ["Verse 1", "Chorus"]


def test_corrupted_words_still_match() -> None:
    words = _transcript((_corrupt(VERSE_1, 0.2), 5.0), (CHORUS, 20.0))
    blocks = discover_blocks(words, _sections())
    assert [b.label for b in blocks] == ["Verse 1", "Chorus"]
    assert blocks[0].score < 95  # degraded but accepted


def test_adlib_run_is_skipped() -> None:
    adlib = "yeah come on lift it up sing it out everybody now"
    words = _transcript((VERSE_1, 5.0), (adlib, 22.0), (CHORUS, 30.0))
    blocks = discover_blocks(words, _sections())
    assert [b.label for b in blocks] == ["Verse 1", "Chorus"]
    # The ad-lib gap sits between the two blocks.
    assert blocks[0].end_sec < 22.0 + 0.01
    assert blocks[1].start_sec >= 30.0 - 0.01


def test_garbage_transcript_yields_nothing() -> None:
    garbage = "la la doo doo hmm hmm yeah yeah woo woo"
    words = _transcript((garbage, 0.0))
    assert discover_blocks(words, _sections()) == []


def test_short_section_needs_high_score() -> None:
    # TAG ("oh oh oh", 3 tokens) must not match random short spans.
    sections = _sections() + [{"label": "Tag", "lyrics": TAG}]
    words = _transcript(("no not that at all", 0.0), (VERSE_1, 10.0))
    blocks = discover_blocks(words, sections)
    assert [b.label for b in blocks] == ["Verse 1"]


def test_short_section_matches_when_exact() -> None:
    sections = [{"label": "Tag", "lyrics": TAG}]
    words = _transcript((VERSE_1, 0.0), (TAG, 20.0))
    blocks = discover_blocks(words, sections)
    assert [b.label for b in blocks] == ["Tag"]
    assert blocks[0].start_sec == 20.0


def test_identical_choruses_consumed_in_time_order() -> None:
    words = _transcript((CHORUS, 10.0), (CHORUS, 40.0))
    blocks = discover_blocks(words, _sections())
    assert [b.label for b in blocks] == ["Chorus", "Chorus"]
    assert blocks[0].end_sec <= blocks[1].start_sec


def test_word_spans_index_into_words() -> None:
    words = _transcript((VERSE_1, 5.0))
    blocks = discover_blocks(words, _sections())
    assert len(blocks) == 1
    b = blocks[0]
    assert b.word_start == 0
    assert b.word_end == len(words)
    assert words[b.word_start].start == b.start_sec


def test_empty_inputs() -> None:
    assert discover_blocks([], _sections()) == []
    assert discover_blocks(_transcript((VERSE_1, 0.0)), []) == []
    # Instrumental-only chart sections (no lyrics) are ignored.
    assert (
        discover_blocks(_transcript((VERSE_1, 0.0)), [{"label": "Intro", "lyrics": ""}])
        == []
    )


# ---------------------------------------------------------------------------
# diff_sequences
# ---------------------------------------------------------------------------


def test_diff_all_matched() -> None:
    entries, warnings = diff_sequences(
        ["Verse 1", "Chorus", "Verse 2", "Chorus"],
        ["Verse 1", "Chorus", "Verse 2", "Chorus"],
    )
    assert all(e["status"] == "matched" for e in entries)
    assert [e["chart_index"] for e in entries] == [0, 1, 2, 3]
    assert warnings == []


def test_diff_extra_chorus() -> None:
    entries, warnings = diff_sequences(
        ["Verse 1", "Chorus", "Chorus", "Chorus"],
        ["Verse 1", "Chorus", "Chorus"],
    )
    statuses = [e["status"] for e in entries]
    assert statuses.count("extra") == 1
    assert any("extra_repeat: Chorus performed 3x" in w for w in warnings)


def test_diff_missing_bridge() -> None:
    entries, warnings = diff_sequences(
        ["Verse 1", "Chorus"],
        ["Verse 1", "Chorus", "Bridge"],
    )
    assert all(e["status"] == "matched" for e in entries)
    assert any("missing_section: Bridge" in w for w in warnings)


def test_diff_case_insensitive() -> None:
    entries, warnings = diff_sequences(["verse 1"], ["Verse 1"])
    assert entries[0]["status"] == "matched"
    assert warnings == []


# ---------------------------------------------------------------------------
# display names + confidence
# ---------------------------------------------------------------------------


def test_display_names_suffix_repeats() -> None:
    assert assign_display_names(["Verse 1", "Chorus", "Verse 2", "Chorus", "Chorus"]) == [
        "Verse 1", "Chorus", "Verse 2", "Chorus 2", "Chorus 3",
    ]


def test_display_names_unique_untouched() -> None:
    assert assign_display_names(["Intro", "Verse 1", "Bridge"]) == [
        "Intro", "Verse 1", "Bridge",
    ]


def test_block_confidence_buckets() -> None:
    assert block_confidence(95.0) == 0.9
    assert block_confidence(80.0) == 0.75
    assert block_confidence(61.0) == 0.5
