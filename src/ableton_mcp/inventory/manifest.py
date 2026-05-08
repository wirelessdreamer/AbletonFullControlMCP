"""Persist the inventory output as a portable JSON manifest.

The manifest is a single JSON file that captures everything the bulk
``inventory_scan_all`` op produced: the timestamp + Live version it was
collected on, the snapshot list, and a coverage summary computed by the
schema matcher. It is intentionally schema-light so future versions of
this tool can keep round-tripping older manifests.

Wire format::

    {
        "version": 1,
        "created_at": "2026-05-07T12:00:00Z",
        "live_version": "11.x.x" | null,
        "totals": {
            "instruments": int,
            "audio_effects": int,
            "midi_effects": int,
            "drums": int,
            "plugins": int,
            "other": int
        },
        "coverage_summary": { ... matcher.build_coverage_summary output ... },
        "instruments": [ ... InstrumentSnapshot.to_dict() ... ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .loader import InstrumentSnapshot


MANIFEST_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Manifest:
    """In-memory representation of the on-disk JSON manifest."""

    instruments: list[InstrumentSnapshot] = field(default_factory=list)
    coverage_summary: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    live_version: Optional[str] = None
    version: int = MANIFEST_VERSION

    @property
    def total_instruments(self) -> int:
        return sum(1 for s in self.instruments if s.category == "instruments")

    @property
    def total_audio_effects(self) -> int:
        return sum(1 for s in self.instruments if s.category == "audio_effects")

    @property
    def total_midi_effects(self) -> int:
        return sum(1 for s in self.instruments if s.category == "midi_effects")

    def totals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.instruments:
            out[s.category] = out.get(s.category, 0) + 1
        return out

    # ----- IO -----

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "live_version": self.live_version,
            "totals": self.totals(),
            "coverage_summary": dict(self.coverage_summary),
            "instruments": [s.to_dict() for s in self.instruments],
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        instruments = [
            InstrumentSnapshot(
                name=item.get("name", ""),
                uri=item.get("uri"),
                category=item.get("category", ""),
                class_name=item.get("class_name", ""),
                parameters=list(item.get("parameters") or []),
                error=item.get("error"),
            )
            for item in (raw.get("instruments") or [])
        ]
        return cls(
            instruments=instruments,
            coverage_summary=dict(raw.get("coverage_summary") or {}),
            created_at=raw.get("created_at", _now_iso()),
            live_version=raw.get("live_version"),
            version=int(raw.get("version", MANIFEST_VERSION)),
        )
