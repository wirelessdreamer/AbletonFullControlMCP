"""Read-side of the knowledge index.

Usage:
    idx = KnowledgeIndex.open()         # uses default location
    hits = idx.search("how to sidechain", k=5)
    for h in hits:
        print(h.score, h.source_url, h.chapter, h.chunk_text[:200])
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default index location resolution
# ---------------------------------------------------------------------------


def default_index_dir() -> Path:
    """Resolve the on-disk knowledge directory.

    Priority:
      1. ABLETON_MCP_KNOWLEDGE_DIR env var
      2. <repo-root>/data/knowledge   (3 levels above this file's package)
    """
    env = os.environ.get("ABLETON_MCP_KNOWLEDGE_DIR")
    if env:
        return Path(env)
    # search.py -> knowledge/ -> ableton_mcp/ -> src/ -> repo root
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    return repo_root / "data" / "knowledge"


# ---------------------------------------------------------------------------
# Hit model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    score: float
    source_url: str
    chapter: str
    chunk_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score), 6),
            "source_url": self.source_url,
            "chapter": self.chapter,
            "chunk_text": self.chunk_text,
        }


# ---------------------------------------------------------------------------
# Tokenisation matching scikit-learn's TfidfVectorizer default analyzer
# ---------------------------------------------------------------------------

# scikit-learn's default token_pattern is r"(?u)\b\w\w+\b"
_TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _ngrams(tokens: list[str], n_min: int = 1, n_max: int = 2) -> list[str]:
    out: list[str] = []
    for n in range(n_min, n_max + 1):
        if n == 1:
            out.extend(tokens)
        else:
            out.extend(" ".join(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1))
    return out


# ---------------------------------------------------------------------------
# KnowledgeIndex
# ---------------------------------------------------------------------------


class KnowledgeIndex:
    """Brute-force cosine search over the sqlite index.

    Suitable for the corpus size we have (manual + cookbook ~ 1-3k chunks).
    """

    def __init__(
        self,
        db_path: Path,
        backend: str,
        embedding_dim: int,
        model_name: str,
        tfidf_state: dict | None = None,
        st_model: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.backend = backend
        self.embedding_dim = int(embedding_dim)
        self.model_name = model_name
        self._tfidf_state = tfidf_state
        self._st_model = st_model
        self._cache: tuple[np.ndarray, list[tuple[str, str, str]]] | None = None

    # ---------------- factory ----------------

    @classmethod
    def open(cls, knowledge_dir: Path | None = None) -> "KnowledgeIndex":
        knowledge_dir = Path(knowledge_dir) if knowledge_dir else default_index_dir()
        db_path = knowledge_dir / "index.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"index not found at {db_path}")
        conn = sqlite3.connect(db_path)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
        backend = meta.get("backend", "tfidf")
        dim = int(meta.get("embedding_dim", "0"))
        model = meta.get("model_name", "")
        tfidf_state = None
        if "tfidf_state" in meta:
            try:
                tfidf_state = json.loads(meta["tfidf_state"])
            except Exception:
                tfidf_state = None

        st_model = None
        if backend == "st":
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                st_model = SentenceTransformer(model or "sentence-transformers/all-MiniLM-L6-v2")
            except Exception as e:  # pragma: no cover
                log.warning("ST model load failed: %s — search will fail.", e)

        return cls(
            db_path=db_path,
            backend=backend,
            embedding_dim=dim,
            model_name=model,
            tfidf_state=tfidf_state,
            st_model=st_model,
        )

    # ---------------- loading ----------------

    def _load_all(self) -> tuple[np.ndarray, list[tuple[str, str, str]]]:
        if self._cache is not None:
            return self._cache
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT source_url, chapter, chunk_text, embedding FROM chunks"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            self._cache = (np.zeros((0, self.embedding_dim), dtype=np.float32), [])
            return self._cache
        meta = [(r[0] or "", r[1] or "", r[2] or "") for r in rows]
        dim = self.embedding_dim
        # If dim wasn't recorded, infer from the first row.
        if dim == 0 and rows[0][3]:
            dim = len(rows[0][3]) // 4
            self.embedding_dim = dim
        mat = np.zeros((len(rows), dim), dtype=np.float32)
        for i, r in enumerate(rows):
            blob = r[3]
            if blob is None:
                continue
            n = len(blob) // 4
            vec = struct.unpack(f"<{n}f", blob)
            mat[i, : len(vec)] = vec
        self._cache = (mat, meta)
        return self._cache

    # ---------------- query encoding ----------------

    def _encode_query(self, query: str) -> np.ndarray:
        if self.backend == "st":
            if self._st_model is None:
                raise RuntimeError(
                    "Index was built with sentence-transformers but the model is not available "
                    "in this process. Install sentence-transformers or rebuild the index with TF-IDF."
                )
            v = self._st_model.encode(
                [query], normalize_embeddings=True, show_progress_bar=False
            )
            return np.asarray(v[0], dtype=np.float32)

        # TF-IDF fallback: rebuild the same TfidfVectorizer.transform output by hand.
        if not self._tfidf_state:
            raise RuntimeError("TF-IDF state missing from index meta.")
        vocab: dict[str, int] = self._tfidf_state["vocab"]
        idf: list[float] = self._tfidf_state["idf"]
        n_min, n_max = self._tfidf_state.get("ngram", [1, 2])
        toks = _tokenize(query)
        # Drop stop words (rough match to sklearn's English list — we intersect with vocab anyway).
        terms = _ngrams(toks, n_min, n_max)
        tf: dict[int, float] = {}
        for t in terms:
            j = vocab.get(t)
            if j is None:
                continue
            tf[j] = tf.get(j, 0.0) + 1.0
        vec = np.zeros(len(vocab), dtype=np.float32)
        for j, c in tf.items():
            vec[j] = c * idf[j]
        n = float(np.linalg.norm(vec))
        if n > 0:
            vec /= n
        return vec

    # ---------------- search ----------------

    def search(self, query: str, k: int = 5) -> list[Hit]:
        if not query.strip():
            return []
        mat, meta = self._load_all()
        if mat.shape[0] == 0:
            return []
        q = self._encode_query(query)
        if q.shape[0] != mat.shape[1]:
            # Defensive: pad/truncate so we never crash on a dim mismatch.
            target = mat.shape[1]
            if q.shape[0] < target:
                pad = np.zeros(target - q.shape[0], dtype=np.float32)
                q = np.concatenate([q, pad])
            else:
                q = q[:target]
        # Rows are L2-normalised at index time; q is L2-normalised above.
        sims = mat @ q
        if k >= sims.shape[0]:
            order = np.argsort(-sims)
        else:
            top = np.argpartition(-sims, k)[:k]
            order = top[np.argsort(-sims[top])]
        out: list[Hit] = []
        for idx in order[:k]:
            score = float(sims[idx])
            if math.isnan(score):
                continue
            src, chap, txt = meta[idx]
            out.append(Hit(score=score, source_url=src, chapter=chap, chunk_text=txt))
        return out
