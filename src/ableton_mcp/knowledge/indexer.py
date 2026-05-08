"""Chunk + embed crawled markdown into a sqlite index.

Embedding strategy:
  - prefer `sentence-transformers/all-MiniLM-L6-v2` (384-d float32)
  - fall back to TF-IDF (scikit-learn) when sentence-transformers is missing.
  - if sqlite-vec is available we *could* use vec0 virtual tables, but to keep
    schema portable we always store float32 BLOBs and do brute-force cosine in
    Python. (sqlite-vec is detected and reported but not currently relied on.)

Sqlite schema (`chunks`):
    id INTEGER PRIMARY KEY
    source_url TEXT
    chapter TEXT
    chunk_text TEXT
    embedding BLOB         -- packed float32 vector, may be NULL for TF-IDF rows
    tfidf_terms TEXT       -- space-separated tokens (kept for diagnostics)

A `meta` table stores:
    backend ('st' | 'tfidf'), embedding_dim, model_name, vocab_path
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

log = logging.getLogger(__name__)

try:  # optional: real embeddings
    from sentence_transformers import SentenceTransformer  # type: ignore

    HAS_ST = True
except Exception:
    HAS_ST = False

try:  # optional: sqlite-vec extension
    import sqlite_vec  # type: ignore

    HAS_SQLITE_VEC = True
except Exception:
    HAS_SQLITE_VEC = False

# scikit-learn ships with the venv per project requirements, but be defensive.
try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore

    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False


DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM_MINI_LM = 384


# ---------------------------------------------------------------------------
# Markdown loading + chunking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawDoc:
    source_url: str
    chapter: str
    text: str
    path: Path


_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def parse_markdown_file(path: Path) -> RawDoc:
    """Read a crawled markdown file and pull metadata out of its front matter.

    Files written by the crawler always have front matter; for hand-authored
    fixtures the metadata is best-effort (defaults derived from the path).
    """
    raw = path.read_text(encoding="utf-8")
    source_url = ""
    chapter = path.stem.replace("_", " ")
    body = raw
    m = _FRONT_MATTER_RE.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k == "source_url":
                source_url = v
            elif k == "chapter":
                chapter = v
        body = raw[m.end() :]
    return RawDoc(source_url=source_url, chapter=chapter, text=body, path=path)


def _approx_token_count(text: str) -> int:
    # Rough heuristic: ~0.75 words per token; we just count whitespace tokens.
    return len(text.split())


def chunk_markdown(
    text: str,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
) -> list[str]:
    """Split markdown into ~target_tokens chunks with overlap.

    Greedy by paragraph: accumulate paragraphs until we'd exceed target, emit,
    then back-fill `overlap_tokens` worth of trailing words for the next chunk.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for para in paragraphs:
        ptoks = _approx_token_count(para)
        if current_tokens + ptoks > target_tokens and current:
            chunk = "\n\n".join(current).strip()
            chunks.append(chunk)
            # Build overlap from trailing words of the emitted chunk.
            words = chunk.split()
            tail = " ".join(words[-overlap_tokens:]) if overlap_tokens > 0 else ""
            current = [tail] if tail else []
            current_tokens = _approx_token_count(tail)
        current.append(para)
        current_tokens += ptoks

    if current:
        last = "\n\n".join(current).strip()
        if last:
            chunks.append(last)
    return chunks


def iter_docs(raw_dir: Path) -> Iterator[RawDoc]:
    for p in sorted(raw_dir.glob("*.md")):
        yield parse_markdown_file(p)


# ---------------------------------------------------------------------------
# Embedding backends
# ---------------------------------------------------------------------------


class _EmbeddingBackend:
    """Common interface so the indexer doesn't care which one we picked."""

    name: str
    dim: int

    def fit(self, texts: list[str]) -> None:
        ...

    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def save_state(self, conn: sqlite3.Connection) -> None:
        ...


class _SentenceTransformerBackend(_EmbeddingBackend):
    name = "st"

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        if not HAS_ST:  # pragma: no cover - defensive
            raise RuntimeError("sentence-transformers not available")
        self._model = SentenceTransformer(model_name)
        self.model_name = model_name
        self.dim = int(self._model.get_sentence_embedding_dimension() or EMBED_DIM_MINI_LM)

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


class _TfidfBackend(_EmbeddingBackend):
    name = "tfidf"

    def __init__(self) -> None:
        if not HAS_SKLEARN:  # pragma: no cover
            raise RuntimeError("scikit-learn not available — cannot fall back to TF-IDF")
        self._vec = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            max_features=20000,
            stop_words="english",
        )
        self._fitted = False
        self.model_name = "tfidf-sklearn"
        self.dim = 0  # unknown until fit

    def fit(self, texts: list[str]) -> None:
        self._vec.fit(texts)
        self.dim = len(self._vec.get_feature_names_out())
        self._fitted = True

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("TfidfBackend.encode called before fit()")
        m = self._vec.transform(texts).astype(np.float32)
        # L2-normalize rows so cosine == dot product later.
        m = m.toarray()
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (m / norms).astype(np.float32)

    def save_state(self, conn: sqlite3.Connection) -> None:
        # Serialise the vocabulary so search-time queries can use the same vector space.
        # sklearn returns numpy ints/floats — coerce to native types for JSON.
        raw_vocab = self._vec.vocabulary_  # term -> column index (np.int64)
        vocab = {str(k): int(v) for k, v in raw_vocab.items()}
        idf = [float(x) for x in self._vec.idf_.tolist()]
        payload = json.dumps({"vocab": vocab, "idf": idf, "ngram": [1, 2]})
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("tfidf_state", payload),
        )


def pick_backend(prefer: str = "auto") -> _EmbeddingBackend:
    """Return the best embedding backend available.

    `prefer` may be 'auto', 'st', or 'tfidf'.
    """
    if prefer == "st":
        return _SentenceTransformerBackend()
    if prefer == "tfidf":
        return _TfidfBackend()
    if HAS_ST:
        try:
            return _SentenceTransformerBackend()
        except Exception as e:  # pragma: no cover
            log.warning("sentence-transformers load failed (%s); falling back to TF-IDF.", e)
    return _TfidfBackend()


# ---------------------------------------------------------------------------
# Sqlite schema + writes
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT,
    chapter TEXT,
    chunk_text TEXT NOT NULL,
    embedding BLOB,
    tfidf_terms TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_url);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    if HAS_SQLITE_VEC:
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            log.info("sqlite-vec loaded.")
        except Exception as e:  # pragma: no cover
            log.warning("sqlite-vec present but failed to load: %s", e)
    return conn


def _pack_vec(v: np.ndarray) -> bytes:
    arr = np.asarray(v, dtype=np.float32).ravel()
    return struct.pack(f"<{arr.size}f", *arr.tolist())


def build_index(
    raw_dir: Path,
    db_path: Path,
    backend: _EmbeddingBackend | None = None,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
    rebuild: bool = False,
    progress_cb: "callable | None" = None,
) -> dict:
    """Read raw_dir/*.md, chunk + embed, write to sqlite.

    Returns a small summary dict (chunk count, backend, etc.).
    """
    raw_dir = Path(raw_dir)
    db_path = Path(db_path)
    if rebuild and db_path.exists():
        db_path.unlink()

    if backend is None:
        backend = pick_backend("auto")

    docs = list(iter_docs(raw_dir))
    if not docs:
        raise FileNotFoundError(f"no markdown found in {raw_dir}")

    # Build (text, doc) pairs.
    rows: list[tuple[str, str, str]] = []  # (source_url, chapter, chunk_text)
    for d in docs:
        for chunk in chunk_markdown(d.text, target_tokens=target_tokens, overlap_tokens=overlap_tokens):
            rows.append((d.source_url, d.chapter, chunk))

    if not rows:
        raise RuntimeError("no chunks produced from corpus")

    texts = [r[2] for r in rows]
    log.info("indexing %d chunks from %d docs (backend=%s)", len(rows), len(docs), backend.name)

    # Fit (TF-IDF needs corpus first; ST is no-op).
    backend.fit(texts)
    embeddings = backend.encode(texts)
    if backend.dim == 0:
        backend.dim = embeddings.shape[1]

    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM meta")
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                ("backend", backend.name),
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                ("embedding_dim", str(backend.dim)),
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                ("model_name", getattr(backend, "model_name", backend.name)),
            )
            backend.save_state(conn)

            for i, ((src, chap, txt), vec) in enumerate(zip(rows, embeddings)):
                conn.execute(
                    "INSERT INTO chunks(source_url, chapter, chunk_text, embedding, tfidf_terms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (src, chap, txt, _pack_vec(vec), None),
                )
                if progress_cb and (i % 50 == 0):
                    progress_cb(i + 1, len(rows))
            if progress_cb:
                progress_cb(len(rows), len(rows))
    finally:
        conn.close()

    return {
        "chunks": len(rows),
        "docs": len(docs),
        "backend": backend.name,
        "model": getattr(backend, "model_name", backend.name),
        "embedding_dim": backend.dim,
        "db_path": str(db_path),
    }
