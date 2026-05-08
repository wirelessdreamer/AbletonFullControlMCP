"""Sqlite-backed probe dataset.

We store one row per probe cell:

- ``probe_id``: autoincrement primary key
- ``device_id``: free-form string (e.g. ``"synth_stub"`` or ``"Operator/MyPreset"``)
- ``params_json``: JSON blob of the param dict
- ``feature_vector``: raw float32 bytes of ``feature_vector(features)``
- ``feature_meta_json``: JSON of the full ``Features.to_dict()`` (handy for debugging)
- ``audio_path``: nullable, absolute path to the rendered wav (Phase 2 captures it)

The on-disk format is just sqlite — portable, queryable from any tool, and
``stdlib only`` (no parquet dependency).
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

import numpy as np

from .features import FEATURE_VECTOR_DIM, Features, feature_vector

_SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    probe_id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    params_json TEXT NOT NULL,
    feature_vector BLOB NOT NULL,
    feature_meta_json TEXT,
    audio_path TEXT
);
CREATE INDEX IF NOT EXISTS probes_device_idx ON probes(device_id);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ProbeRow:
    """One row read back out of the dataset."""

    probe_id: int
    device_id: str
    params: dict[str, float]
    feature_vector: np.ndarray
    audio_path: str | None
    feature_meta: dict | None = None


class ProbeDataset:
    """Append-only sqlite-backed dataset of probed parameter cells.

    Use as a context manager or call :meth:`close` when you are done. ``path=None``
    creates an in-memory database — handy for tests.
    """

    def __init__(self, path: str | os.PathLike | None = None, *, device_id: str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._device_id = device_id
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
        else:
            self._conn = sqlite3.connect(":memory:")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- ergonomics ----------------------------------------------------------
    def __enter__(self) -> "ProbeDataset":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.close()

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def device_id(self) -> str | None:
        return self._device_id

    # ---- writes --------------------------------------------------------------
    def append(
        self,
        params: Mapping[str, float],
        features: Features,
        audio_path: str | os.PathLike | None = None,
        device_id: str | None = None,
    ) -> int:
        """Insert one probe row. Returns the new probe_id."""
        dev = device_id or self._device_id
        if dev is None:
            raise ValueError("device_id must be set on the dataset or passed to append()")
        vec = feature_vector(features).astype(np.float32, copy=False)
        if vec.shape[0] != FEATURE_VECTOR_DIM:  # pragma: no cover — invariant
            raise AssertionError(f"feature vector dim mismatch: {vec.shape[0]} != {FEATURE_VECTOR_DIM}")
        cur = self._conn.execute(
            "INSERT INTO probes (device_id, params_json, feature_vector, feature_meta_json, audio_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(dev),
                json.dumps({k: float(v) for k, v in params.items()}, separators=(",", ":")),
                sqlite3.Binary(vec.tobytes()),
                json.dumps(features.to_dict(), separators=(",", ":")),
                str(audio_path) if audio_path is not None else None,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(key), str(value)),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (str(key),)).fetchone()
        return row[0] if row else None

    # ---- reads ---------------------------------------------------------------
    def __len__(self) -> int:
        if self._device_id is None:
            row = self._conn.execute("SELECT COUNT(*) FROM probes").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM probes WHERE device_id=?", (self._device_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def iter_rows(self, device_id: str | None = None) -> Iterator[ProbeRow]:
        """Yield every probe row, optionally filtered by ``device_id``."""
        dev = device_id or self._device_id
        if dev is None:
            cursor = self._conn.execute(
                "SELECT probe_id, device_id, params_json, feature_vector, feature_meta_json, audio_path "
                "FROM probes ORDER BY probe_id ASC"
            )
        else:
            cursor = self._conn.execute(
                "SELECT probe_id, device_id, params_json, feature_vector, feature_meta_json, audio_path "
                "FROM probes WHERE device_id=? ORDER BY probe_id ASC",
                (dev,),
            )
        for probe_id, did, params_json, vec_blob, meta_json, audio_path in cursor:
            yield ProbeRow(
                probe_id=int(probe_id),
                device_id=str(did),
                params=json.loads(params_json),
                feature_vector=np.frombuffer(vec_blob, dtype=np.float32).copy(),
                audio_path=audio_path,
                feature_meta=json.loads(meta_json) if meta_json else None,
            )

    def to_numpy(self, device_id: str | None = None) -> tuple[list[dict[str, float]], np.ndarray]:
        """Return (params_list, feature_matrix) for fast vectorised queries."""
        params: list[dict[str, float]] = []
        vectors: list[np.ndarray] = []
        for row in self.iter_rows(device_id=device_id):
            params.append(row.params)
            vectors.append(row.feature_vector)
        if not vectors:
            return params, np.zeros((0, FEATURE_VECTOR_DIM), dtype=np.float32)
        return params, np.stack(vectors, axis=0).astype(np.float32, copy=False)

    # ---- file lifecycle ------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike, *, device_id: str | None = None) -> "ProbeDataset":
        """Open an existing dataset (or create empty if the file does not yet exist)."""
        return cls(path=path, device_id=device_id)

    def save(self, path: str | os.PathLike | None = None) -> Path:
        """Persist the dataset to ``path`` (no-op for already-on-disk DBs unless redirected).

        For in-memory DBs this performs an ``sqlite3`` backup into the destination file,
        which is the cheapest way to dump :memory: → file.
        """
        target = Path(path) if path is not None else self._path
        if target is None:
            raise ValueError("save() needs a path when the dataset is in-memory")
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._path is not None and target.resolve() == self._path.resolve():
            self._conn.commit()
            return target
        # Backup into a fresh on-disk DB.
        with sqlite3.connect(str(target)) as dst:
            self._conn.commit()
            self._conn.backup(dst)
        return target

    def device_ids(self) -> Sequence[str]:
        cur = self._conn.execute("SELECT DISTINCT device_id FROM probes ORDER BY device_id")
        return [str(r[0]) for r in cur.fetchall()]
