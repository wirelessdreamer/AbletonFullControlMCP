"""Sqlite-backed preset store.

One row per preset. ``feature_vector`` is nullable so curated presets
(which have no rendered audio attached) coexist with discovered ones.

The store is **append-only by name** — re-seeding the curated library is a
no-op once the rows exist. Rename a curated preset to force a re-insert.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from .library import LIBRARY, Preset


# Default location, relative to repo root. Mirror of probe storage at
# ``data/probes/``.
DEFAULT_DB_PATH = Path("data/presets/library.sqlite")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    device_class TEXT NOT NULL,
    params_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'curated',
    feature_vector BLOB
);
CREATE INDEX IF NOT EXISTS presets_device_idx ON presets(device_class);
CREATE INDEX IF NOT EXISTS presets_source_idx ON presets(source);
"""


def _connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and create) the preset DB. ``path=None`` uses :data:`DEFAULT_DB_PATH`."""
    target = Path(path) if path is not None else DEFAULT_DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _row_to_preset(row: sqlite3.Row | tuple) -> Preset:
    """Materialise a sqlite row tuple back into a :class:`Preset`."""
    # Tuple order matches SELECT below — keep aligned.
    (
        _id,
        name,
        device_class,
        params_json,
        tags_json,
        description,
        source,
        _feat,
    ) = row
    return Preset(
        name=str(name),
        device_class=str(device_class),
        params={k: float(v) for k, v in json.loads(params_json).items()},
        tags=list(json.loads(tags_json)),
        description=str(description or ""),
        source=str(source or "curated"),
    )


_SELECT_COLS = (
    "id, name, device_class, params_json, tags_json, description, source, feature_vector"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed_curated(
    db_path: str | os.PathLike | None = None,
    *,
    presets: Sequence[Preset] | None = None,
) -> int:
    """Idempotently insert :data:`LIBRARY` (or ``presets``) into the DB.

    Returns the number of rows inserted *this call* (zero on the second run).
    Existing rows are left untouched — name is the dedup key.
    """
    rows = presets if presets is not None else LIBRARY
    conn = _connect(db_path)
    try:
        inserted = 0
        for p in rows:
            cur = conn.execute("SELECT 1 FROM presets WHERE name=?", (p.name,))
            if cur.fetchone():
                continue
            conn.execute(
                "INSERT INTO presets (name, device_class, params_json, tags_json, "
                "description, source, feature_vector) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    p.name,
                    p.device_class,
                    json.dumps(p.params, separators=(",", ":")),
                    json.dumps(p.tags, separators=(",", ":")),
                    p.description,
                    p.source,
                    None,
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def add_preset(
    preset: Preset,
    *,
    db_path: str | os.PathLike | None = None,
    feature_vector: bytes | None = None,
    replace: bool = False,
) -> int:
    """Insert (or upsert with ``replace=True``) a single preset. Returns rowid."""
    conn = _connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM presets WHERE name=?", (preset.name,)
        ).fetchone()
        if existing and not replace:
            return int(existing[0])
        if existing and replace:
            conn.execute(
                "UPDATE presets SET device_class=?, params_json=?, tags_json=?, "
                "description=?, source=?, feature_vector=? WHERE name=?",
                (
                    preset.device_class,
                    json.dumps(preset.params, separators=(",", ":")),
                    json.dumps(preset.tags, separators=(",", ":")),
                    preset.description,
                    preset.source,
                    feature_vector,
                    preset.name,
                ),
            )
            conn.commit()
            return int(existing[0])
        cur = conn.execute(
            "INSERT INTO presets (name, device_class, params_json, tags_json, "
            "description, source, feature_vector) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                preset.name,
                preset.device_class,
                json.dumps(preset.params, separators=(",", ":")),
                json.dumps(preset.tags, separators=(",", ":")),
                preset.description,
                preset.source,
                feature_vector,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_presets(
    device_class: str | None = None,
    tag: str | None = None,
    *,
    source: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> list[Preset]:
    """Return all presets matching the optional filters.

    ``tag`` matches case-insensitively against any of the preset's tag list.
    """
    conn = _connect(db_path)
    try:
        sql = f"SELECT {_SELECT_COLS} FROM presets WHERE 1=1"
        params: list = []
        if device_class is not None:
            sql += " AND device_class=?"
            params.append(device_class)
        if source is not None:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY id ASC"
        rows = conn.execute(sql, params).fetchall()
        out = [_row_to_preset(r) for r in rows]
        if tag is not None:
            tag_lc = tag.strip().lower()
            out = [p for p in out if any(t.lower() == tag_lc for t in p.tags)]
        return out
    finally:
        conn.close()


def find_by_name(
    name: str, *, db_path: str | os.PathLike | None = None
) -> Preset | None:
    """Look up a preset by exact name (case-insensitive)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM presets WHERE LOWER(name)=LOWER(?)",
            (name,),
        ).fetchone()
        return _row_to_preset(row) if row else None
    finally:
        conn.close()


def search_by_tag(
    tag: str, *, db_path: str | os.PathLike | None = None
) -> list[Preset]:
    """Return all presets whose tag list contains ``tag`` (case-insensitive)."""
    return list_presets(tag=tag, db_path=db_path)


def search_by_text(
    query: str,
    *,
    db_path: str | os.PathLike | None = None,
    limit: int | None = None,
) -> list[Preset]:
    """Fuzzy match ``query`` against name / tags / description.

    Ranking is deliberately simple: count the number of query tokens that
    appear (substring, case-insensitive) in any searchable field. Ties
    break by name. Empty query returns everything.
    """
    q = (query or "").strip().lower()
    tokens = [tok for tok in q.split() if tok]
    presets = list_presets(db_path=db_path)
    if not tokens:
        return presets[: limit or len(presets)]

    def score(p: Preset) -> tuple[int, int, int, int, int]:
        name_lc = p.name.lower()
        haystack_parts = [name_lc, p.description.lower()] + [t.lower() for t in p.tags]
        haystack = " | ".join(haystack_parts)
        # Total token hits across name + tags + description.
        token_hits = sum(1 for tok in tokens if tok in haystack)
        # Name-only token hits (weighted heavier).
        name_hits = sum(1 for tok in tokens if tok in name_lc)
        # Bonus: every query token landed in the name (strong signal).
        all_in_name = 1 if name_hits == len(tokens) else 0
        # Bonus: full query phrase appears anywhere.
        phrase_hit = 1 if q in haystack else 0
        # Penalise long names so shorter, tighter matches win ties.
        name_len_penalty = -len(name_lc)
        return (token_hits, all_in_name, name_hits, phrase_hit, name_len_penalty)

    scored = [(score(p), p) for p in presets]
    scored = [s for s in scored if s[0][0] > 0]
    scored.sort(key=lambda kv: (-kv[0][0], -kv[0][1], -kv[0][2], -kv[0][3], -kv[0][4], kv[1].name.lower()))
    out = [p for _, p in scored]
    return out[: limit or len(out)]


def all_presets(db_path: str | os.PathLike | None = None) -> list[Preset]:
    """Return every preset row (curated + discovered)."""
    return list_presets(db_path=db_path)


def device_classes(db_path: str | os.PathLike | None = None) -> list[str]:
    """Distinct device_class values present in the store."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT device_class FROM presets ORDER BY device_class"
        ).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        conn.close()


def clear_all(db_path: str | os.PathLike | None = None) -> int:
    """Delete every preset row. Returns the number deleted. Used by tests."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM presets")
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


__all__ = [
    "DEFAULT_DB_PATH",
    "add_preset",
    "all_presets",
    "clear_all",
    "device_classes",
    "find_by_name",
    "list_presets",
    "search_by_tag",
    "search_by_text",
    "seed_curated",
]
