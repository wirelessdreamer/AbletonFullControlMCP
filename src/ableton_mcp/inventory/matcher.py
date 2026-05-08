"""Match introspected snapshots against the canonical 57-device schema library.

The matcher is purely a folding step over a list of :class:`InstrumentSnapshot`.
It looks each snapshot's ``class_name`` up in
``device_schemas.lookup_schema``; if there's a hit we further compare the
parameter-name overlap and pick a coverage tier:

* ``full`` — class_name has a canonical schema and >=80% of the snapshot's
  parameter names appear in the schema.
* ``partial`` — class_name has a canonical schema but the parameter
  overlap is below the 80% bar (common for plugin variants or for
  schemas we shipped as deliberately-incomplete).
* ``unknown`` — no canonical schema for this class_name (e.g. a third
  party VST).

The matcher never mutates the snapshot; it returns wrapper :class:`Match`
records.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from ..device_schemas import DeviceSchema, lookup_schema
from .loader import InstrumentSnapshot


# Coverage tier thresholds.
FULL_OVERLAP = 0.80


@dataclass
class Match:
    """One row of the schema-matching report."""

    snapshot: InstrumentSnapshot
    schema_class_name: Optional[str]
    coverage: str  # "full" | "partial" | "unknown"
    overlap_ratio: float = 0.0
    unmatched_params: list[str] = field(default_factory=list)
    extra_schema_params: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "snapshot": self.snapshot.to_dict(),
            "schema_class_name": self.schema_class_name,
            "coverage": self.coverage,
            "overlap_ratio": self.overlap_ratio,
            "unmatched_params": list(self.unmatched_params),
            "extra_schema_params": list(self.extra_schema_params),
        }
        return d


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _diff_params(
    snapshot: InstrumentSnapshot, schema: DeviceSchema
) -> tuple[float, list[str], list[str]]:
    """Return (overlap_ratio, unmatched_in_snapshot, extra_in_schema)."""
    snap_names = {_norm(p["name"]) for p in snapshot.parameters if p.get("name")}
    schema_names = {_norm(p.name) for p in schema.parameters}
    if not snap_names:
        return 0.0, [], sorted(schema_names)
    overlap = snap_names & schema_names
    ratio = len(overlap) / len(snap_names)
    # Preserve original casing for readability when reporting.
    snap_lookup = {_norm(p["name"]): p["name"] for p in snapshot.parameters if p.get("name")}
    schema_lookup = {_norm(p.name): p.name for p in schema.parameters}
    unmatched = sorted(
        snap_lookup[k] for k in (snap_names - schema_names)
    )
    extra = sorted(
        schema_lookup[k] for k in (schema_names - snap_names)
    )
    return ratio, unmatched, extra


def match_to_schemas(snapshots: Iterable[InstrumentSnapshot]) -> list[Match]:
    """Run :func:`device_schemas.lookup_schema` against every snapshot.

    See module docstring for tier semantics.
    """
    out: list[Match] = []
    for snap in snapshots:
        schema = lookup_schema(snap.class_name) if snap.class_name else None
        if schema is None:
            out.append(
                Match(
                    snapshot=snap,
                    schema_class_name=None,
                    coverage="unknown",
                    overlap_ratio=0.0,
                    unmatched_params=[p.get("name", "") for p in snap.parameters],
                    extra_schema_params=[],
                )
            )
            continue
        ratio, unmatched, extra = _diff_params(snap, schema)
        coverage = "full" if ratio >= FULL_OVERLAP else "partial"
        out.append(
            Match(
                snapshot=snap,
                schema_class_name=schema.class_name,
                coverage=coverage,
                overlap_ratio=ratio,
                unmatched_params=unmatched,
                extra_schema_params=extra,
            )
        )
    return out


def build_coverage_summary(matches: list[Match]) -> dict:
    """High-level stats over a list of matches.

    Returned shape::

        {
            "total": 57,
            "by_coverage": {"full": 12, "partial": 4, "unknown": 41},
            "by_category": {"instruments": 24, "audio_effects": 31, ...},
            "schemas_hit": ["Operator", "Wavetable", ...],  # unique class names
        }
    """
    by_cov: dict[str, int] = {"full": 0, "partial": 0, "unknown": 0}
    by_cat: dict[str, int] = {}
    schemas_hit: set[str] = set()
    for m in matches:
        by_cov[m.coverage] = by_cov.get(m.coverage, 0) + 1
        cat = m.snapshot.category
        by_cat[cat] = by_cat.get(cat, 0) + 1
        if m.schema_class_name:
            schemas_hit.add(m.schema_class_name)
    return {
        "total": len(matches),
        "by_coverage": by_cov,
        "by_category": by_cat,
        "schemas_hit": sorted(schemas_hit),
    }
