"""Discovery DP: segment a transcript into chart sections, in performed order.

The core "correlate lead sheet to recording" step. Given the chart's unique
section definitions (label + lyrics) and the free transcription's timed
words, a dynamic program tiles the transcript with section-lyric blocks —
WITHOUT consulting the chart's sequence — so the *performed* order falls out
naturally, including repeats the chart doesn't list (band sang the chorus a
third time) and sections the band skipped. The chart sequence is only used
afterwards, by :func:`diff_sequences`, to classify each performed block as
matched/extra and to report missing sections.

Pure module: rapidfuzz + stdlib only — importable and testable without
torch/stable_whisper installed.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Sequence

from .model import MatchedBlock, TranscriptWord

# Tuning constants — calibrated in M4's real-song verification; every one of
# these is a parameter of discover_blocks so experiments don't need edits here.
SKIP_PENALTY_PER_TOKEN = 0.5
"""Cost of leaving one transcript token unmatched (ad-libs, spontaneous
vamping). Cheaper than a bad match, dearer than a good one."""

ACCEPT_SCORE = 60.0
"""Minimum rapidfuzz ratio (0-100) for a block match to enter the DP."""

ACCEPT_SCORE_SHORT = 85.0
"""Stricter threshold for very short sections ("oh oh" tags) — a 3-token
block matches half the transcript at 60."""

SHORT_SECTION_TOKENS = 4
"""Sections with fewer normalized tokens than this use ACCEPT_SCORE_SHORT."""

WINDOW_MIN_RATIO = 0.6
WINDOW_MAX_RATIO = 1.4
"""Candidate transcript-window lengths as a fraction of the section's token
count — singers stretch and compress, but not by more than ~40%."""

MIN_WINDOW_TOKENS = 3
LABEL_TIEBREAK_MARGIN = 5.0
"""Two sections scoring within this margin on the same span are treated as
interchangeable; diff_sequences resolves the label via the chart sequence."""


# Normalization ------------------------------------------------------------

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_REPEAT_MARKER_RE = re.compile(r"\b[x×]\s*\d+\b|\b\d+\s*[x×]\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^a-z0-9\s]")


def norm_tokens(text: str) -> list[str]:
    """Normalize lyric text to comparable tokens.

    Lowercase; drop parentheticals ("(echo)"), repeat markers ("x2"),
    punctuation and apostrophes; collapse whitespace.
    """
    t = text.lower()
    t = _PARENTHETICAL_RE.sub(" ", t)
    t = _REPEAT_MARKER_RE.sub(" ", t)
    t = t.replace("'", "")
    t = _NON_WORD_RE.sub(" ", t)
    return t.split()


@dataclass(frozen=True)
class _SectionTokens:
    label: str
    tokens: tuple[str, ...]
    text: str  # pre-joined for rapidfuzz

    @property
    def accept_score(self) -> float:
        return ACCEPT_SCORE_SHORT if len(self.tokens) < SHORT_SECTION_TOKENS else ACCEPT_SCORE


def _prepare_sections(sections: Sequence[dict[str, Any] | Any]) -> list[_SectionTokens]:
    """Accepts ChartSection-likes (attrs or dict) with label + lyrics."""
    out: list[_SectionTokens] = []
    for s in sections:
        label = s["label"] if isinstance(s, dict) else s.label
        lyrics = s["lyrics"] if isinstance(s, dict) else s.lyrics
        tokens = tuple(norm_tokens(lyrics or ""))
        if tokens:
            out.append(_SectionTokens(label=str(label), tokens=tokens, text=" ".join(tokens)))
    return out


# Discovery DP --------------------------------------------------------------


def discover_blocks(
    words: Sequence[TranscriptWord],
    sections: Sequence[dict[str, Any] | Any],
    *,
    skip_penalty: float = SKIP_PENALTY_PER_TOKEN,
    window_min_ratio: float = WINDOW_MIN_RATIO,
    window_max_ratio: float = WINDOW_MAX_RATIO,
) -> list[MatchedBlock]:
    """Tile the transcript with section-lyric blocks via DP.

    ``dp[i]`` = best score covering transcript tokens ``[0, i)``. From each
    ``i`` either skip one token (cost ``skip_penalty``) or match any section
    whose lyrics fuzzy-fit a window starting at ``i``; a match's gain is the
    estimated matched-token count minus estimated errors (see inline note),
    so clean long matches anchor the segmentation while junk absorption and
    partial-split matches lose to skipping. Repeated sections are matched as
    many times as they occur; blocks come back in transcript-time order by
    construction.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:  # pragma: no cover - exercised via fake-module tests
        raise RuntimeError(
            "rapidfuzz not installed. Add via "
            "`pip install ableton-full-control-mcp[song_sections]` or "
            "`pip install rapidfuzz`."
        ) from exc

    prepared = _prepare_sections(sections)
    tokens: list[str] = []
    token_word_idx: list[int] = []
    for wi, w in enumerate(words):
        for tok in norm_tokens(w.text):
            tokens.append(tok)
            token_word_idx.append(wi)
    n = len(tokens)
    if n == 0 or not prepared:
        return []

    NEG = float("-inf")
    dp = [NEG] * (n + 1)
    dp[0] = 0.0
    # back[i] = (prev_i, matched _SectionTokens | None, score)
    back: list[tuple[int, _SectionTokens | None, float]] = [(-1, None, 0.0)] * (n + 1)

    for i in range(n):
        if dp[i] == NEG:
            continue
        # Skip one token.
        if dp[i] - skip_penalty > dp[i + 1]:
            dp[i + 1] = dp[i] - skip_penalty
            back[i + 1] = (i, None, 0.0)
        # Match a section block starting here.
        for sec in prepared:
            lo = max(MIN_WINDOW_TOKENS, int(len(sec.tokens) * window_min_ratio))
            hi = max(lo, int(round(len(sec.tokens) * window_max_ratio)))
            if i + lo > n:
                continue  # not enough transcript left for a valid window
            for j in range(i + lo, min(i + hi, n) + 1):
                score = fuzz.ratio(sec.text, " ".join(tokens[i:j]))
                if score < sec.accept_score:
                    continue
                # Gain = estimated matched tokens minus estimated errors.
                # ratio ≈ 2M/(len_a+len_b) → M ≈ score/100 × (sec+window)/2;
                # errors = window − M; gain = M − errors. This makes absorbing
                # a junk token into a window strictly worse than skipping it,
                # and splitting one long section into two partial matches
                # worse than matching it once cleanly.
                window = j - i
                gain = (score / 100.0) * (len(sec.tokens) + window) - float(window)
                if dp[i] + gain > dp[j]:
                    dp[j] = dp[i] + gain
                    back[j] = (i, sec, score)

    # Backtrack from full coverage. dp[n] is always reachable via skips, and
    # leading/trailing skip costs cancel out between alternatives — ending at
    # an interior argmax instead would drop blocks after early skips.
    blocks: list[MatchedBlock] = []
    k = n
    while k > 0:
        prev, sec, score = back[k]
        if sec is not None:
            w_start = token_word_idx[prev]
            w_end = token_word_idx[k - 1]
            blocks.append(
                MatchedBlock(
                    label=sec.label,
                    score=float(score),
                    word_start=w_start,
                    word_end=w_end + 1,
                    start_sec=float(words[w_start].start),
                    end_sec=float(words[w_end].end),
                )
            )
        k = prev
    blocks.reverse()
    return blocks


# Sequence diff -------------------------------------------------------------


def diff_sequences(
    performed_labels: Sequence[str], chart_sequence: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Classify performed blocks against the chart sequence.

    Returns ``(entries, warnings)`` where each entry is
    ``{"label", "status": "matched"|"extra", "chart_index": int|None}``
    (one per performed block, in order) and warnings note extra repeats and
    chart sections never performed. Comparison is case-insensitive.
    """
    perf = [p.strip().lower() for p in performed_labels]
    chart = [c.strip().lower() for c in chart_sequence]
    sm = difflib.SequenceMatcher(a=chart, b=perf, autojunk=False)

    entries: list[dict[str, Any]] = [
        {"label": performed_labels[i], "status": "extra", "chart_index": None}
        for i in range(len(perf))
    ]
    matched_chart: set[int] = set()
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            entries[b + k] = {
                "label": performed_labels[b + k],
                "status": "matched",
                "chart_index": a + k,
            }
            matched_chart.add(a + k)

    warnings: list[str] = []
    # Extra repeats: count per label.
    from collections import Counter

    extra_counts = Counter(
        e["label"] for e in entries if e["status"] == "extra"
    )
    for label, count in extra_counts.items():
        performed_total = sum(1 for p in perf if p == label.strip().lower())
        chart_total = sum(1 for c in chart if c == label.strip().lower())
        warnings.append(
            f"extra_repeat: {label} performed {performed_total}x, "
            f"chart sequence has {chart_total}x (+{count} extra)"
        )
    # Missing sections.
    for idx, label in enumerate(chart_sequence):
        if idx not in matched_chart and label.strip().lower() not in perf:
            warnings.append(f"missing_section: {label} in chart sequence but not detected")
    return entries, warnings


def assign_display_names(labels: Sequence[str]) -> list[str]:
    """Suffix repeated labels in time order: Chorus, Chorus 2, Chorus 3.

    Labels that already end in a number ("Verse 1") are left alone unless
    the exact same label repeats.
    """
    from collections import Counter

    totals = Counter(label.strip().lower() for label in labels)
    seen: Counter[str] = Counter()
    out: list[str] = []
    for label in labels:
        key = label.strip().lower()
        seen[key] += 1
        if totals[key] > 1 and seen[key] > 1:
            out.append(f"{label} {seen[key]}")
        else:
            out.append(label)
    return out


def block_confidence(score: float) -> float:
    """Map a rapidfuzz score to a coarse confidence bucket."""
    if score >= 90.0:
        return 0.9
    if score >= 75.0:
        return 0.75
    return 0.5
