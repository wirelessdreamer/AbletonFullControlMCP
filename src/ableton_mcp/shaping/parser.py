"""Parse a free-text shaping request into structured intent.

This is intentionally a small token/regex-based parser, not a real NL
parser. It looks for descriptor words in a known vocabulary, infers an
intensity modifier from a small set of qualifiers ("much", "slightly",
"a bit", "less", ...), and pulls out a couple of optional clauses:

- ``compare_to``: target after "like a ..." / "sound like ...".
- ``device_hint``: rough target hint after "on the ..." / "for the ...".
- ``targets_specific``: explicit feature/value targets like
  "centroid above 3000Hz" or "rms < 0.1".

The intensity modifier is an integer in ``{-2, -1, 0, +1, +2}`` corresponding
to "much less", "less", neutral, "more", "much more". The integer is what
the planner multiplies the descriptor's nominal feature delta by.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Known descriptor vocabulary. We deliberately keep this list a *superset* of
# the fallback vocab — words like "vintage" or "dirty" are recognised here
# even when the fallback can't translate them to feature deltas, so the
# parser can faithfully echo what the user wrote.
# ---------------------------------------------------------------------------
KNOWN_DESCRIPTORS: tuple[str, ...] = (
    "bright",
    "brighter",
    "dark",
    "darker",
    "warm",
    "warmer",
    "cold",
    "cool",
    "thin",
    "thinner",
    "thick",
    "thicker",
    "punchy",
    "punchier",
    "soft",
    "softer",
    "harsh",
    "harsher",
    "smooth",
    "smoother",
    "muddy",
    "clear",
    "clearer",
    "crisp",
    "crispier",
    "fat",
    "fatter",
    "lean",
    "leaner",
    "boomy",
    "boomier",
    "airy",
    "air",
    "tight",
    "tighter",
    "loose",
    "looser",
    "vintage",
    "modern",
    "dirty",
    "clean",
    "noisy",
    "rich",
    "richer",
    "hollow",
    "full",
    "fuller",
    "open",
    "closed",
    "wide",
    "narrow",
    "aggressive",
    "mellow",
    "edgy",
    "round",
    "sharp",
    "sharper",
    "dull",
    "duller",
    "shimmery",
    "shimmer",
    "lo-fi",
    "lofi",
    "hi-fi",
    "hifi",
)


# Synonyms / hyphenations folded to a canonical label.
_DESCRIPTOR_CANONICAL: dict[str, str] = {
    "brighter": "bright",
    "darker": "dark",
    "warmer": "warm",
    "cool": "cold",
    "thinner": "thin",
    "thicker": "thick",
    "punchier": "punchy",
    "softer": "soft",
    "harsher": "harsh",
    "smoother": "smooth",
    "clearer": "clear",
    "crispier": "crisp",
    "fatter": "fat",
    "leaner": "lean",
    "boomier": "boomy",
    "air": "airy",
    "tighter": "tight",
    "looser": "loose",
    "richer": "rich",
    "fuller": "full",
    "sharper": "sharp",
    "duller": "dull",
    "shimmer": "shimmery",
    "lo-fi": "lofi",
    "hi-fi": "hifi",
}


_INTENSIFIERS_STRONG = {"much", "way", "very", "really", "lots", "tons"}
_INTENSIFIERS_WEAK = {"slightly", "little", "bit", "tad", "touch", "somewhat", "kinda", "kind"}
_LESS_WORDS = {"less", "reduce", "reduced", "fewer", "drop", "remove", "without", "no", "kill"}
_MORE_WORDS = {"more", "increase", "boost", "extra", "add", "enhance", "with"}


def _canonicalise(label: str) -> str:
    label = label.strip().lower()
    return _DESCRIPTOR_CANONICAL.get(label, label)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class ShapeRequest:
    """Structured intent extracted from a natural-language shaping request."""

    text: str
    descriptors: list[tuple[str, int]] = field(default_factory=list)
    targets_specific: list[dict] = field(default_factory=list)
    compare_to: str | None = None
    device_hint: str | None = None
    unknown_words: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "descriptors": [
                {"label": label, "intensity_modifier": intensity}
                for label, intensity in self.descriptors
            ],
            "targets_specific": list(self.targets_specific),
            "compare_to": self.compare_to,
            "device_hint": self.device_hint,
            "unknown_words": list(self.unknown_words),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SPECIFIC_TARGET_RE = re.compile(
    r"\b(centroid|bandwidth|rolloff|rms|zcr|flatness|brightness)\b\s*"
    r"(above|below|under|over|<|>|=|~|near|around|less than|more than|greater than)\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(hz|hertz|khz|db)?",
    re.IGNORECASE,
)

_COMPARE_RE = re.compile(
    r"(?:sound\s+)?(?:like|as if it were|similar to|reminiscent of)\s+(?:an?\s+)?([\w' \-]+?)(?:\s+(?:on|for|with|but|and)\b|[.,!?]|$)",
    re.IGNORECASE,
)

_DEVICE_HINT_RE = re.compile(
    r"\b(?:on|for|make|to)\s+(?:the\s+)?(lead|bass|pad|drum|drums|kick|snare|hat|hi-hat|hihat|vocal|vox|guitar|keys|synth|piano|fx|sub)\b",
    re.IGNORECASE,
)


def _classify_intensity(window: list[str]) -> int:
    """Map a small window of preceding/surrounding words to {-2, -1, 0, +1, +2}.

    Polarity (less/more) and strength (much/slightly) are independent.
    """
    polarity = +1  # default: an unmodified descriptor reads as "more X".
    strong = False
    weak = False
    for w in window:
        wl = w.lower()
        if wl in _LESS_WORDS:
            polarity = -1
        elif wl in _MORE_WORDS:
            polarity = +1
        elif wl in _INTENSIFIERS_STRONG:
            strong = True
        elif wl in _INTENSIFIERS_WEAK:
            weak = True
    if strong:
        return polarity * 2
    if weak:
        # Weak qualifier still means "a bit X" — keep magnitude 1, just with
        # the right polarity. (We don't have an "0.5" rung.)
        return polarity
    return polarity


def _tokens(text: str) -> list[str]:
    # Keep hyphens for "lo-fi" / "hi-fi"; strip other punctuation.
    return re.findall(r"[A-Za-z][A-Za-z\-]*", text.lower())


def _extract_specific_targets(text: str) -> list[dict]:
    out: list[dict] = []
    for m in _SPECIFIC_TARGET_RE.finditer(text):
        feature, op, value, unit = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            num = float(value)
        except ValueError:
            continue
        if unit and unit.lower() == "khz":
            num *= 1000.0
        op_l = op.lower().strip()
        if op_l in ("above", "over", ">", "more than", "greater than"):
            comparator = ">"
        elif op_l in ("below", "under", "<", "less than"):
            comparator = "<"
        elif op_l in ("near", "around", "~"):
            comparator = "~"
        else:
            comparator = "="
        out.append(
            {
                "feature": feature.lower(),
                "comparator": comparator,
                "value": num,
                "unit": unit.lower() if unit else None,
            }
        )
    return out


def _extract_compare_to(text: str) -> str | None:
    m = _COMPARE_RE.search(text)
    if not m:
        return None
    candidate = m.group(1).strip().lower()
    # Trim trailing common stopwords that snuck through.
    candidate = re.sub(r"\s+(it|that|this)$", "", candidate).strip()
    if not candidate or len(candidate.split()) > 5:
        return None
    # Strip any leading descriptor adjectives ("vintage rhodes" -> "rhodes").
    parts = candidate.split()
    while parts and _canonicalise(parts[0]) in {*KNOWN_DESCRIPTORS, *(_DESCRIPTOR_CANONICAL.values())}:
        parts = parts[1:]
    if not parts:
        return None
    return " ".join(parts)


def _extract_device_hint(text: str) -> str | None:
    m = _DEVICE_HINT_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().lower()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_shape_request(text: str) -> ShapeRequest:
    """Parse free text into a :class:`ShapeRequest`.

    Examples
    --------

    >>> parse_shape_request("brighter").descriptors
    [('bright', 1)]

    >>> parse_shape_request("much warmer with less air").descriptors
    [('warm', 2), ('airy', -1)]

    >>> r = parse_shape_request("make the lead sound like a vintage Rhodes")
    >>> r.descriptors, r.compare_to, r.device_hint
    ([('vintage', 1)], 'rhodes', 'lead')
    """
    text = (text or "").strip()
    if not text:
        return ShapeRequest(text=text)

    # Pull structured slices first so the descriptor scan can operate on a
    # cleaner residue.
    targets_specific = _extract_specific_targets(text)
    compare_to = _extract_compare_to(text)
    device_hint = _extract_device_hint(text)

    tokens = _tokens(text)
    descriptors: list[tuple[str, int]] = []
    seen_canonicals: set[str] = set()
    unknown_descriptor_candidates: list[str] = []

    # We do a left-to-right scan, looking for known descriptors. Window of
    # the *preceding* 3 tokens drives the intensity classification — that
    # captures "much warmer", "a bit warmer", "less air".
    for i, tok in enumerate(tokens):
        canonical = _canonicalise(tok)
        if canonical not in {*KNOWN_DESCRIPTORS, *(_DESCRIPTOR_CANONICAL.values())}:
            continue
        if canonical in seen_canonicals:
            continue
        # "make it sound like X" — when the descriptor sits inside the compare
        # clause (e.g. "vintage Rhodes"), we still want the descriptor itself
        # but skip any bare-noun continuation.
        window = tokens[max(0, i - 3) : i]
        # For trailing-clause descriptors like "less air", "less" is to the
        # left so the window already includes it.
        intensity = _classify_intensity(list(window))
        descriptors.append((canonical, intensity))
        seen_canonicals.add(canonical)

    # Gather words the user used that look like descriptors but aren't in our
    # vocab — useful for debugging / "we didn't understand X".
    for tok in tokens:
        canonical = _canonicalise(tok)
        if (
            canonical not in seen_canonicals
            and tok.endswith(("er", "ier"))
            and len(tok) > 4
        ):
            unknown_descriptor_candidates.append(tok)

    return ShapeRequest(
        text=text,
        descriptors=descriptors,
        targets_specific=targets_specific,
        compare_to=compare_to,
        device_hint=device_hint,
        unknown_words=unknown_descriptor_candidates,
    )


def known_descriptors() -> Iterable[str]:
    """Return every canonical descriptor label this parser recognises."""
    seen: set[str] = set()
    for label in KNOWN_DESCRIPTORS:
        canonical = _canonicalise(label)
        if canonical not in seen:
            seen.add(canonical)
            yield canonical
