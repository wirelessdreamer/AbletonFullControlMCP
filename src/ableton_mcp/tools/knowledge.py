"""Ableton how-to knowledge tools (Phase 4 RAG over local manual + cookbook).

These tools query a sqlite index built from crawled markdown. Build it with:

    python -m ableton_mcp.scripts.build_knowledge_index --source both

The MCP client's LLM is responsible for the final natural-language synthesis;
`ableton_explain` returns a stitched-together context block plus the raw hits
so the LLM can ground its answer in citations rather than its training data.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..knowledge import KnowledgeIndex, default_index_dir

log = logging.getLogger(__name__)


_INDEX_NOT_BUILT = {
    "error": "index not built — run python -m ableton_mcp.scripts.build_knowledge_index"
}


def _open_index() -> KnowledgeIndex | dict[str, Any]:
    """Try to open the on-disk index; return an error dict on failure."""
    try:
        return KnowledgeIndex.open()
    except FileNotFoundError:
        return _INDEX_NOT_BUILT
    except Exception as e:
        log.exception("failed to open knowledge index")
        return {"error": f"failed to open index: {e}"}


def _summarise(hits: list, max_chars_per_hit: int = 600) -> str:
    """Concatenate top-k snippets with citations into a single context string.

    NOTE: this deliberately does not call an LLM — the MCP client's LLM does
    the synthesis from this material.
    """
    if not hits:
        return ""
    parts: list[str] = []
    for i, h in enumerate(hits, 1):
        snippet = h.chunk_text.strip()
        if len(snippet) > max_chars_per_hit:
            snippet = snippet[:max_chars_per_hit].rstrip() + "..."
        cite = f"[{i}] {h.chapter}" + (f" — {h.source_url}" if h.source_url else "")
        parts.append(f"{cite}\n{snippet}")
    return "\n\n".join(parts)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def ableton_search_docs(query: str, k: int = 5) -> dict[str, Any]:
        """Search the local Ableton Live 11 manual + Cookbook index for snippets relevant to `query`.

        Returns the top `k` matching chunks with their score, chapter, and source URL.
        """
        idx = _open_index()
        if isinstance(idx, dict):
            return idx
        try:
            hits = idx.search(query, k=k)
        except Exception as e:
            log.exception("search failed")
            return {"error": f"search failed: {e}"}
        return {
            "query": query,
            "k": k,
            "backend": idx.backend,
            "model": idx.model_name,
            "knowledge_dir": str(default_index_dir()),
            "hits": [h.to_dict() for h in hits],
        }

    @mcp.tool()
    async def ableton_explain(question: str, k: int = 8) -> dict[str, Any]:
        """Retrieve grounded context for an Ableton how-to question.

        Returns the top `k` relevant snippets PLUS a `context` string that
        concatenates them with citations. The MCP client's LLM should use the
        `context` string as grounding when answering — this tool intentionally
        does not call an LLM itself.
        """
        idx = _open_index()
        if isinstance(idx, dict):
            return idx
        try:
            hits = idx.search(question, k=k)
        except Exception as e:
            log.exception("explain search failed")
            return {"error": f"search failed: {e}"}
        context = _summarise(hits)
        return {
            "question": question,
            "k": k,
            "backend": idx.backend,
            "model": idx.model_name,
            "knowledge_dir": str(default_index_dir()),
            "hits": [h.to_dict() for h in hits],
            "context": context,
            "synthesis_note": (
                "The MCP client's LLM should answer the user's question using ONLY the "
                "snippets in `context`, citing them by [number]. This tool does not call "
                "an LLM — synthesis happens client-side."
            ),
        }
