"""Probe every available synth_bench synth and persist a probe dataset per synth.

Run from the repo root:

    # Estimate runtime without rendering anything:
    python -m ableton_mcp.scripts.probe_orchestrator --dry-run

    # Probe everything synth_bench exposes (plus the synth_stub fallback):
    python -m ableton_mcp.scripts.probe_orchestrator

    # Restrict to a few synths:
    python -m ableton_mcp.scripts.probe_orchestrator --synths subtractive fm_2op

Output goes to ``data/probes/{synth_name}.sqlite`` — one file per synth so
they can be clustered independently by ``preset_discover``.

Synth_bench may not be shipped yet (Agent 4 is concurrent work). If
:mod:`ableton_mcp.synth_bench` is empty / missing, only the in-process
``synth_stub`` is probed.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..sound import (
    ProbeDataset,
    SweepPlanner,
    SynthStubRenderer,
    extract_features,
)
from ..sound.synth_stub import SYNTH_STUB_PARAM_RANGES


log = logging.getLogger("probe_orchestrator")


# Recommended sweep params per synth, keyed by synth name. The orchestrator
# uses these as the default param subset; --params overrides per-call. Names
# match the actual synth_bench schemas; if a name isn't found we fall back to
# the renderer's full param_ranges.
_RECOMMENDED_SWEEPS: dict[str, list[str]] = {
    "synth_stub": ["freq", "cutoff", "resonance", "noise_amount"],
    "subtractive": ["freq", "waveform", "attack", "release"],
    "fm_2op": ["freq", "mod_ratio", "mod_index", "amp_attack", "amp_release"],
    "fm_4op": ["freq", "algorithm", "op1_ratio", "op2_ratio", "op1_index"],
    "wavetable": ["freq", "table_a", "position", "tone", "attack"],
    "additive": ["freq", "tilt", "attack", "release"],
    "granular": ["grain_size_ms", "density", "position", "pitch_jitter"],
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _list_synth_bench_synths() -> list[str]:
    """Return the synth names exposed by :mod:`ableton_mcp.synth_bench`.

    Tolerates several plausible APIs because synth_bench may still be in
    flux when this script is exercised.
    """
    try:
        from .. import synth_bench as sb
    except Exception:
        return []
    # Common conventions.
    for attr in ("SYNTHS", "SYNTH_NAMES", "available_synths", "list_synths"):
        candidate = getattr(sb, attr, None)
        if callable(candidate):
            try:
                value = candidate()
            except Exception:
                continue
        else:
            value = candidate
        if isinstance(value, dict):
            return sorted(str(k) for k in value)
        if isinstance(value, (list, tuple, set)):
            return sorted(str(x) for x in value)
    return []


def _resolve_renderer(synth_name: str, sample_rate: int, duration_sec: float):
    """Get a renderer for ``synth_name``. Returns ``None`` if unavailable.

    Tries synth_bench first via several common API shapes; falls back to
    the in-process synth_stub for the special name ``"synth_stub"``.
    """
    if synth_name == "synth_stub":
        return SynthStubRenderer(sample_rate=sample_rate, duration_sec=duration_sec)

    try:
        from .. import synth_bench as sb
    except Exception:
        return None

    # Several plausible API shapes — try each.
    for fn_name in ("get", "get_renderer", "get_synth", "renderer_for"):
        fn = getattr(sb, fn_name, None)
        if callable(fn):
            try:
                obj = fn(synth_name)
            except Exception:
                continue
            if obj is not None and hasattr(obj, "render"):
                return obj
    return None


def _resolve_param_ranges(renderer, synth_name: str) -> dict[str, tuple[float, float]]:
    """Pick legal ranges. Renderer-declared ranges win; stub fallback otherwise."""
    declared = getattr(renderer, "param_ranges", None)
    if isinstance(declared, dict) and declared:
        return {k: (float(v[0]), float(v[1])) for k, v in declared.items()}
    if synth_name == "synth_stub":
        return dict(SYNTH_STUB_PARAM_RANGES)
    # Last-ditch — clusterer / discovery still works on whatever the synth
    # accepts; we just won't have a rich sweep without declared ranges.
    return {}


def _filter_to_recommended(
    ranges: dict[str, tuple[float, float]], synth_name: str, override: list[str] | None
) -> dict[str, tuple[float, float]]:
    """Filter the full ranges dict to the recommended sweep params."""
    wanted = override or _RECOMMENDED_SWEEPS.get(synth_name, list(ranges))
    out = {p: ranges[p] for p in wanted if p in ranges}
    if not out:
        # If none of the recommended params are present (synth_bench schema
        # different than expected), fall back to the full set.
        return ranges
    return out


def probe_one_synth(
    synth_name: str,
    *,
    output_dir: Path,
    steps_per_param: int = 5,
    duration_sec: float = 1.5,
    sample_rate: int = 22050,
    params_override: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Probe one synth, write ``data/probes/{name}.sqlite``."""
    renderer = _resolve_renderer(synth_name, sample_rate, duration_sec)
    if renderer is None:
        return {
            "synth": synth_name,
            "status": "unavailable",
            "reason": "renderer not exposed by synth_bench (or module not shipped yet)",
        }

    ranges = _resolve_param_ranges(renderer, synth_name)
    if not ranges:
        return {
            "synth": synth_name,
            "status": "skipped",
            "reason": "no param ranges available",
        }
    swept = _filter_to_recommended(ranges, synth_name, params_override)

    planner = SweepPlanner(swept, steps_per_param=steps_per_param, strategy="grid", seed=0)
    n_cells = len(planner)

    if dry_run:
        return {
            "synth": synth_name,
            "status": "dry_run",
            "params": list(swept),
            "param_ranges": {k: [v[0], v[1]] for k, v in swept.items()},
            "steps_per_param": steps_per_param,
            "planned_cells": n_cells,
            "estimated_seconds": float(n_cells * duration_sec),
        }

    db_path = output_dir / f"{synth_name}.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log.info(
        "probing %s: %d cells across %d params → %s",
        synth_name,
        n_cells,
        len(swept),
        db_path,
    )

    t0 = time.monotonic()
    probed = 0
    errors: list[dict[str, Any]] = []
    with ProbeDataset(db_path, device_id=synth_name) as ds:
        ds.set_meta("strategy", "grid")
        ds.set_meta("sample_rate", str(sample_rate))
        ds.set_meta("duration_sec", str(duration_sec))
        ds.set_meta("param_names", ",".join(planner.param_names))
        for cell in planner:
            try:
                audio = renderer.render(cell)
                feats = extract_features(np.asarray(audio, dtype=np.float32), sr=sample_rate)
                ds.append(cell, feats, audio_path=None, device_id=synth_name)
                probed += 1
            except NotImplementedError as exc:
                return {
                    "synth": synth_name,
                    "status": "not_implemented",
                    "message": str(exc),
                    "planned_cells": n_cells,
                }
            except Exception as exc:  # pragma: no cover — keep going on rare cell errors
                errors.append({"params": cell, "error": repr(exc)})
        total = len(ds)

    return {
        "synth": synth_name,
        "status": "ok",
        "dataset_path": str(db_path.resolve()),
        "params": list(swept),
        "planned_cells": n_cells,
        "rows_added": probed,
        "rows_total": total,
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe synth_bench synths systematically into data/probes/.",
    )
    ap.add_argument(
        "--synths",
        nargs="+",
        default=None,
        help="Restrict to a subset of synth names. Default = synth_stub + every "
        "synth_bench synth that's available.",
    )
    ap.add_argument(
        "--steps-per-param",
        type=int,
        default=5,
        help="Grid steps per swept parameter (default: 5).",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=1.5,
        help="Render duration per cell in seconds (default: 1.5).",
    )
    ap.add_argument(
        "--sample-rate",
        type=int,
        default=22050,
        help="Sample rate (default: 22050).",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/probes"),
        help="Where to write the per-synth sqlite probe DBs.",
    )
    ap.add_argument(
        "--params",
        nargs="+",
        default=None,
        help="Override the recommended sweep param subset (applied to every synth).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be probed (planned cells, runtime estimate) without rendering.",
    )
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)

    discovered = _list_synth_bench_synths()
    available = ["synth_stub"] + discovered
    log.info("available synths: %s", available)

    if args.synths:
        wanted = list(args.synths)
        unknown = [s for s in wanted if s not in available]
        if unknown:
            log.warning("requested synths not currently available (will skip): %s", unknown)
    else:
        wanted = available

    summary: list[dict[str, Any]] = []
    for synth_name in wanted:
        result = probe_one_synth(
            synth_name,
            output_dir=args.output_dir,
            steps_per_param=args.steps_per_param,
            duration_sec=args.duration,
            sample_rate=args.sample_rate,
            params_override=args.params,
            dry_run=args.dry_run,
        )
        summary.append(result)
        log.info("%s: %s", synth_name, result.get("status"))

    if args.dry_run:
        total = sum(r.get("estimated_seconds", 0.0) for r in summary if r.get("status") == "dry_run")
        log.info("dry-run total estimated render time: %.1fs", total)

    # Also emit a JSON summary on stdout for scripting.
    import json as _json

    print(_json.dumps({"runs": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
