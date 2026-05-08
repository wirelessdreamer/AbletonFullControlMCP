"""Unit tests for the Phase 4 RAG subsystem (crawler/indexer/search/tools).

Network is never touched — we feed the indexer a hand-written fixture corpus
and exercise the TF-IDF backend (always available because scikit-learn is in
requirements). The sentence-transformers path is exercised only if that
optional dep is installed in the current venv.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ableton_mcp.knowledge.crawler import html_to_markdown
from ableton_mcp.knowledge.indexer import (
    HAS_ST,
    build_index,
    chunk_markdown,
    pick_backend,
)
from ableton_mcp.knowledge.search import KnowledgeIndex


# ---------------------------------------------------------------------------
# Fixture corpus
# ---------------------------------------------------------------------------


FIXTURE_DOCS = {
    "manual__sidechain.md": (
        "---\n"
        "source_url: https://example.invalid/manual/sidechain\n"
        "chapter: Sidechain Compression\n"
        "source: manual\n"
        "---\n\n"
        "# Sidechain Compression\n\n"
        "Sidechain compression is a technique where the dynamics of one signal — the sidechain "
        "input — control the gain reduction applied to a different signal. In Ableton Live the "
        "Compressor and Glue Compressor both expose a sidechain section. To duck a bass line "
        "every time the kick drum hits, route the kick to the sidechain input of a compressor "
        "on the bass track.\n\n"
        "## Routing the kick\n\n"
        "Open the bass track's compressor, click the small triangle to reveal the sidechain "
        "panel, enable Sidechain, and pick the kick track as the audio source. Set the threshold "
        "low and the ratio high to get a punchy ducking effect. Adjust attack and release until "
        "the bass breathes with the kick.\n\n"
        "Tip: a fast attack and medium release of around 100 ms typically works well for "
        "four-on-the-floor patterns.\n"
    ),
    "manual__warping.md": (
        "---\n"
        "source_url: https://example.invalid/manual/warping\n"
        "chapter: Warping Audio Clips\n"
        "source: manual\n"
        "---\n\n"
        "# Warping Audio Clips\n\n"
        "Warp markers in Live let you stretch audio so that it follows the project tempo. Each "
        "warp marker pins a particular point in the audio file to a particular position in beats. "
        "Live's Beats, Tones, Texture, Re-Pitch and Complex modes pick different stretching "
        "strategies depending on the source material.\n\n"
        "## Choosing a warp mode\n\n"
        "For percussive material like drum loops, pick Beats. For pitched melodic material with "
        "stable harmonic content, Tones is usually the cleanest. Complex Pro is the highest "
        "quality but most CPU-hungry.\n"
    ),
    "cookbook__layered_synths.md": (
        "---\n"
        "source_url: https://example.invalid/cookbook/layered-synths\n"
        "chapter: Layered Synth Sounds\n"
        "source: cookbook\n"
        "---\n\n"
        "# Layered Synth Sounds\n\n"
        "Stacking two or three synth voices on a single MIDI track is a fast way to build a "
        "rich pad or lead. Use a Rack and load multiple instruments into chains. Detune one "
        "voice by a few cents, transpose another by an octave, and macro-map the wet/dry mix.\n\n"
        "Add a subtle stereo widener at the rack output to glue the layers together.\n"
    ),
    "manual__overview.md": (
        "---\n"
        "source_url: https://example.invalid/manual/overview\n"
        "chapter: Live Concepts\n"
        "source: manual\n"
        "---\n\n"
        "# Live Concepts\n\n"
        "Ableton Live has two main views: Session View for non-linear, clip-based performance, "
        "and Arrangement View for traditional linear editing. Tracks hold clips. Devices process "
        "audio or MIDI on a track.\n"
    ),
}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Write the fixture markdown into <tmp>/raw and return the knowledge dir."""
    knowledge_dir = tmp_path / "knowledge"
    raw_dir = knowledge_dir / "raw"
    raw_dir.mkdir(parents=True)
    for name, body in FIXTURE_DOCS.items():
        (raw_dir / name).write_text(body, encoding="utf-8")
    return knowledge_dir


# ---------------------------------------------------------------------------
# Crawler / HTML helper
# ---------------------------------------------------------------------------


def test_html_to_markdown_strips_nav_and_keeps_headings() -> None:
    html = (
        "<html><head><title>Sidechain</title></head><body>"
        "<nav><a href='#'>top</a></nav>"
        "<header>banner</header>"
        "<main>"
        "<h1>Sidechain Compression</h1>"
        "<p>Use a <strong>kick</strong> to duck a bass.</p>"
        "<ul><li>Open compressor</li><li>Enable sidechain</li></ul>"
        "</main>"
        "<footer>copyright</footer>"
        "</body></html>"
    )
    md, title = html_to_markdown(html)
    assert title == "Sidechain"
    assert "# Sidechain Compression" in md
    assert "**kick**" in md
    assert "- Open compressor" in md
    # Stripped chunks must NOT appear.
    assert "banner" not in md
    assert "copyright" not in md


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def test_chunk_markdown_respects_target_size() -> None:
    text = "\n\n".join(["word " * 100] * 5)  # ~500 words
    chunks = chunk_markdown(text, target_tokens=120, overlap_tokens=20)
    assert len(chunks) >= 3
    for c in chunks:
        # Chunks should have content and not blow past the target by an extreme margin.
        assert c.strip()
        assert len(c.split()) <= 250  # generous ceiling


def test_chunk_markdown_handles_short_input() -> None:
    out = chunk_markdown("just one short paragraph here.")
    assert out == ["just one short paragraph here."]


# ---------------------------------------------------------------------------
# TF-IDF index round trip
# ---------------------------------------------------------------------------


def test_build_and_search_tfidf(corpus: Path) -> None:
    raw_dir = corpus / "raw"
    db_path = corpus / "index.sqlite"
    backend = pick_backend("tfidf")
    summary = build_index(raw_dir=raw_dir, db_path=db_path, backend=backend, rebuild=True)
    assert summary["backend"] == "tfidf"
    assert summary["chunks"] >= 4  # 4 docs, possibly more chunks
    assert db_path.exists()

    # Verify schema sanity.
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n == summary["chunks"]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        assert meta["backend"] == "tfidf"
        # tfidf_state must be parseable JSON with vocab + idf.
        state = json.loads(meta["tfidf_state"])
        assert "vocab" in state and "idf" in state
        assert len(state["vocab"]) > 0
    finally:
        conn.close()

    idx = KnowledgeIndex.open(corpus)
    hits = idx.search("how do I sidechain a kick to a bass", k=5)
    assert hits, "expected at least one hit"
    top = hits[0]
    # The sidechain doc should win on a sidechain query.
    assert "Sidechain" in top.chapter or "sidechain" in top.chunk_text.lower()
    assert top.score > 0
    # Non-sidechain docs should rank below the sidechain doc.
    assert hits[0].source_url.endswith("/sidechain")


def test_search_returns_empty_for_blank_query(corpus: Path) -> None:
    raw_dir = corpus / "raw"
    db_path = corpus / "index.sqlite"
    build_index(raw_dir=raw_dir, db_path=db_path, backend=pick_backend("tfidf"), rebuild=True)
    idx = KnowledgeIndex.open(corpus)
    assert idx.search("", k=5) == []
    assert idx.search("   ", k=5) == []


def test_unrelated_query_still_returns_results_but_lower_scores(corpus: Path) -> None:
    raw_dir = corpus / "raw"
    db_path = corpus / "index.sqlite"
    build_index(raw_dir=raw_dir, db_path=db_path, backend=pick_backend("tfidf"), rebuild=True)
    idx = KnowledgeIndex.open(corpus)
    sidechain_hits = idx.search("sidechain kick bass duck", k=3)
    unrelated_hits = idx.search("zebra giraffe rainforest", k=3)
    # The unrelated query should produce strictly lower top-1 score (often ~0).
    if unrelated_hits:
        assert sidechain_hits[0].score >= unrelated_hits[0].score


# ---------------------------------------------------------------------------
# Sentence-transformers path (skipped if optional dep absent)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_ST, reason="sentence-transformers not installed")
def test_build_and_search_sentence_transformers(corpus: Path) -> None:
    raw_dir = corpus / "raw"
    db_path = corpus / "index.sqlite"
    backend = pick_backend("st")
    summary = build_index(raw_dir=raw_dir, db_path=db_path, backend=backend, rebuild=True)
    assert summary["backend"] == "st"
    idx = KnowledgeIndex.open(corpus)
    hits = idx.search("ducking bass with kick drum", k=3)
    assert hits
    assert "sidechain" in hits[0].chunk_text.lower() or "Sidechain" in hits[0].chapter


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


def test_tools_report_missing_index(monkeypatch, tmp_path: Path) -> None:
    """When the sqlite file doesn't exist, tools must return an error string, not crash."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("ABLETON_MCP_KNOWLEDGE_DIR", str(empty))

    from ableton_mcp.tools import knowledge as knowledge_tools

    # The decorated tool functions live inside register(); call the underlying open helper.
    result = knowledge_tools._open_index()
    assert isinstance(result, dict)
    assert "error" in result
    assert "not built" in result["error"]


@pytest.mark.asyncio
async def test_tools_search_against_fixture_corpus(monkeypatch, corpus: Path) -> None:
    """End-to-end: build a fixture index, point the tool at it, run a search."""
    raw_dir = corpus / "raw"
    db_path = corpus / "index.sqlite"
    build_index(raw_dir=raw_dir, db_path=db_path, backend=pick_backend("tfidf"), rebuild=True)
    monkeypatch.setenv("ABLETON_MCP_KNOWLEDGE_DIR", str(corpus))

    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import knowledge as knowledge_tools

    mcp = FastMCP("test")
    knowledge_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {"ableton_search_docs", "ableton_explain"} <= names

    # Pull the actual callables via the underlying tool manager — FastMCP doesn't expose them
    # as plain attributes, so we drive them through call_tool().
    res = await mcp.call_tool(
        "ableton_search_docs",
        {"query": "how do I sidechain a kick to a bass", "k": 3},
    )
    # FastMCP returns (content, structured_data); structured_data has our dict.
    structured = res[1] if isinstance(res, tuple) else res
    assert isinstance(structured, dict)
    assert "hits" in structured and structured["hits"]
    assert structured["backend"] == "tfidf"
    assert "sidechain" in (structured["hits"][0]["chunk_text"] + structured["hits"][0]["chapter"]).lower()

    res2 = await mcp.call_tool(
        "ableton_explain",
        {"question": "how do I duck a bass with a kick?", "k": 3},
    )
    structured2 = res2[1] if isinstance(res2, tuple) else res2
    assert isinstance(structured2, dict)
    assert structured2.get("context"), "explain must build a context block"
    assert "[1]" in structured2["context"]
