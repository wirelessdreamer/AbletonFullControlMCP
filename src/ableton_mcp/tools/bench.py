"""MCP tools for the in-process synth bench.

Exposes the bench as a free render-in-the-loop testing surface — no Ableton
required. Mirrors the synth-bench Renderer surface so callers can:

- list every registered synth + its parameter schema (``bench_list_synths``)
- describe a single synth's params + a recommended sweep set (``bench_describe``)
- render a synth to wav for a given param dict (``bench_render``)
- run a full probe sweep + persist features to a sqlite dataset (``bench_probe``)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP

from ..sound import (
    ProbeDataset,
    SweepPlanner,
    extract_features,
)
from ..synth_bench import SYNTH_REGISTRY, get, list_synths


def _synth_schema(name: str) -> dict[str, Any]:
    """Return the human-readable parameter schema for a registered synth."""
    cls = SYNTH_REGISTRY[name]
    inst = cls()
    return {
        "name": name,
        "param_count": len(cls.PARAM_DEFAULTS),
        "param_names": list(cls.PARAM_DEFAULTS.keys()),
        "param_ranges": {
            k: [float(v[0]), float(v[1])] for k, v in cls.PARAM_RANGES.items()
        },
        "param_defaults": {k: float(v) for k, v in cls.PARAM_DEFAULTS.items()},
        "sample_rate": inst.sample_rate,
        "duration_sec": inst.duration_sec,
    }


def _recommended_sweep_params(name: str) -> list[str]:
    """Cheap recommendation: a 3-axis subset that exercises the synth's character."""
    table = {
        "subtractive": ["freq", "cutoff", "resonance"],
        "fm_2op": ["freq", "mod_ratio", "mod_index"],
        "fm_4op": ["freq", "algorithm", "op2_index"],
        "wavetable": ["freq", "position", "tone"],
        "additive": ["freq", "tilt", "even_odd_balance"],
        "granular": ["grain_size_ms", "density", "pitch"],
    }
    return table.get(name, list(SYNTH_REGISTRY[name].PARAM_DEFAULTS.keys())[:3])


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def bench_list_synths() -> dict[str, Any]:
        """List every in-process bench synth with its full parameter schema."""
        return {
            "synths": [_synth_schema(name) for name in list_synths()],
            "count": len(SYNTH_REGISTRY),
        }

    @mcp.tool()
    async def bench_describe(synth_name: str) -> dict[str, Any]:
        """Describe one bench synth: param ranges, defaults, recommended sweep set."""
        if synth_name not in SYNTH_REGISTRY:
            return {"error": f"unknown synth {synth_name!r}; have {list_synths()}"}
        schema = _synth_schema(synth_name)
        schema["recommended_sweep_params"] = _recommended_sweep_params(synth_name)
        return schema

    @mcp.tool()
    async def bench_render(
        synth_name: str,
        params: dict[str, float] | None = None,
        output_path: str = "data/bench/render.wav",
        sample_rate: int = 22050,
        duration_sec: float = 2.0,
        midi_note: int = 60,
    ) -> dict[str, Any]:
        """Render a bench synth to a wav file. Defaults fill any unspecified param."""
        import soundfile as sf

        if synth_name not in SYNTH_REGISTRY:
            return {"error": f"unknown synth {synth_name!r}; have {list_synths()}"}

        renderer = get(
            synth_name,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            midi_note=midi_note,
        )
        audio = renderer.render(params or {})

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), audio, sample_rate, subtype="PCM_16")

        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        peak = float(np.max(np.abs(audio)))
        return {
            "status": "ok",
            "synth_name": synth_name,
            "output_path": str(out.resolve()),
            "sample_rate": sample_rate,
            "duration_sec": duration_sec,
            "samples": int(audio.shape[0]),
            "rms": rms,
            "peak": peak,
            "params_resolved": renderer._resolve(params or {}),
        }

    @mcp.tool()
    async def bench_probe(
        synth_name: str,
        params: list[str] | None = None,
        steps_per_param: int = 5,
        strategy: str = "grid",
        output_path: str = "data/bench/probes.sqlite",
        sample_rate: int = 22050,
        duration_sec: float = 2.0,
        midi_note: int = 60,
        seed: int | None = 0,
    ) -> dict[str, Any]:
        """Run a full probe sweep across a bench synth and persist features.

        Uses the existing ``SweepPlanner`` + ``ProbeDataset`` so the resulting
        dataset is interchangeable with ``sound_match`` / ``sound_explain_parameter``
        queries — same on-disk schema as the real ``sound_probe_device``.
        """
        if synth_name not in SYNTH_REGISTRY:
            return {"error": f"unknown synth {synth_name!r}; have {list_synths()}"}

        renderer = get(
            synth_name,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            midi_note=midi_note,
            seed=seed,
        )
        all_ranges = renderer.param_ranges
        if params:
            wanted = [p for p in params if p in all_ranges]
            if not wanted:
                return {
                    "error": f"none of params={params} are known to {synth_name!r} "
                    f"(have {sorted(all_ranges)})"
                }
            ranges = {p: all_ranges[p] for p in wanted}
        else:
            ranges = {p: all_ranges[p] for p in _recommended_sweep_params(synth_name)}

        planner = SweepPlanner(
            ranges, steps_per_param=steps_per_param, strategy=strategy, seed=seed
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        device_id = f"bench:{synth_name}"
        probed = 0
        with ProbeDataset(out, device_id=device_id) as ds:
            ds.set_meta("synth_name", synth_name)
            ds.set_meta("strategy", strategy)
            ds.set_meta("sample_rate", str(sample_rate))
            ds.set_meta("duration_sec", str(duration_sec))
            ds.set_meta("param_names", ",".join(planner.param_names))
            for cell in planner:
                # Hold the rest at defaults; the planner only varies the selected axes.
                full = dict(renderer.param_defaults)
                full.update(cell)
                audio = renderer.render(full)
                feats = extract_features(np.asarray(audio, dtype=np.float32), sr=sample_rate)
                ds.append(full, feats, audio_path=None, device_id=device_id)
                probed += 1
            total = len(ds)

        return {
            "status": "ok",
            "synth_name": synth_name,
            "device_id": device_id,
            "dataset_path": str(out.resolve()),
            "strategy": strategy,
            "swept_params": list(ranges.keys()),
            "param_ranges": {k: [float(v[0]), float(v[1])] for k, v in ranges.items()},
            "planned_cells": len(planner),
            "rows_added": probed,
            "rows_total": total,
            "sample_rate": sample_rate,
            "duration_sec": duration_sec,
            "midi_note": midi_note,
        }
