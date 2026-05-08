"""Local knowledge base over the Ableton Live 11 manual + Cookbook (Phase 4 RAG).

Layout under `data/knowledge/`:
    raw/        one markdown file per crawled page
    index.sqlite    chunked + embedded corpus

Public surface:
    KnowledgeIndex.open(...)    load an existing index
    KnowledgeIndex.search(...)  top-k hits
    Hit                         dataclass returned by search()

The crawler/indexer modules are runnable via:
    python -m ableton_mcp.scripts.build_knowledge_index
"""

from __future__ import annotations

from .search import Hit, KnowledgeIndex, default_index_dir

__all__ = ["Hit", "KnowledgeIndex", "default_index_dir"]
