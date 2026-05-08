"""Sound modeling — Phase 3 offline pipeline.

Three tools:

- ``sound_probe_device``: sweep a device's parameter space, render each cell,
  extract features, and persist a sqlite probe dataset. Today only the
  ``synth_stub`` "device" runs end-to-end (it is rendered in-process with
  numpy/scipy). Real Live devices need the Phase 2 render-in-the-loop path
  through ``LiveRenderer``, which is wired up but raises ``NotImplementedError``
  pending Phase 2.

- ``sound_match``: load a target wav with librosa, extract features, kNN
  against a saved dataset, return top-k param recommendations. If
  ``apply=True`` and the dataset's device is a real Live device, push the
  best params via OSC.

- ``sound_explain_parameter``: sweep one param while holding the others
  fixed, measure feature deltas, and report which feature dimensions move
  most across the sweep — answers questions like "what does this knob do?".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
from mcp.server.fastmcp import FastMCP

from ..sound import (
    LiveRenderer,
    ProbeDataset,
    Renderer,
    SYNTH_STUB_PARAM_RANGES,
    SweepPlanner,
    SynthStubRenderer,
    extract_features,
    feature_vector,
    find_nearest,
    refine,
)
from ..sound.features import FEATURE_VECTOR_NAMES
from ..sound.synth_stub import SYNTH_STUB_DEFAULTS

SYNTH_STUB_DEVICE_ID = "synth_stub"


def _resolve_renderer(
    device_id: str,
    track_index: int | None,
    device_index: int | None,
    sample_rate: int,
    duration_sec: float,
) -> Renderer:
    """Pick a renderer for the given device id."""
    if device_id == SYNTH_STUB_DEVICE_ID:
        return SynthStubRenderer(sample_rate=sample_rate, duration_sec=duration_sec)
    if track_index is None or device_index is None:
        raise ValueError(
            "track_index and device_index are required for non-synth_stub devices"
        )
    return LiveRenderer(
        track_index=track_index,
        device_index=device_index,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
    )


def _resolve_param_ranges(
    renderer: Renderer,
    params: list[str] | None,
    overrides: Mapping[str, list[float]] | None,
) -> dict[str, tuple[float, float]]:
    """Pick which params to sweep + their min/max.

    Order of precedence: explicit ``overrides`` > renderer's declared ranges
    > synth-stub defaults if the renderer has none. ``params`` filters to a
    subset of names.
    """
    ranges = dict(renderer.param_ranges) if renderer.param_ranges else dict(SYNTH_STUB_PARAM_RANGES)
    if overrides:
        for name, pair in overrides.items():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"param_ranges[{name!r}] must be [min, max]")
            ranges[name] = (float(pair[0]), float(pair[1]))
    if params:
        wanted = [p for p in params if p in ranges]
        if not wanted:
            raise ValueError(
                f"none of params={params} are known to this renderer (have {sorted(ranges)})"
            )
        ranges = {p: ranges[p] for p in wanted}
    return ranges


def _push_params_via_osc(
    track_index: int, device_index: int, params: Mapping[str, float]
) -> dict[str, Any]:
    """Best-effort: set named params on a live device via OSC.

    Each name is matched case-insensitively against the device's reported
    parameter list. Unknown names are returned in ``unmatched``.
    """
    from ..osc_client import get_client  # local import: avoid OSC dep at test time

    async def _do() -> dict[str, Any]:
        client = await get_client()
        names = (
            await client.request(
                "/live/device/get/parameters/name", int(track_index), int(device_index)
            )
        )[2:]
        applied: list[dict[str, Any]] = []
        unmatched: list[str] = []
        lowered = [str(n).strip().lower() for n in names]
        for name, value in params.items():
            try:
                idx = lowered.index(name.strip().lower())
            except ValueError:
                unmatched.append(name)
                continue
            client.send(
                "/live/device/set/parameter/value",
                int(track_index),
                int(device_index),
                int(idx),
                float(value),
            )
            applied.append({"name": str(names[idx]), "index": idx, "value": float(value)})
        return {"applied": applied, "unmatched": unmatched}

    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # We are already inside an asyncio loop (FastMCP tool) — schedule and await.
        future = asyncio.ensure_future(_do())
        return future  # type: ignore[return-value]  # awaited by caller
    return asyncio.run(_do())


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def sound_probe_device(
        track_index: int | None = None,
        device_index: int | None = None,
        device_id: str = SYNTH_STUB_DEVICE_ID,
        params: list[str] | None = None,
        param_ranges: dict[str, list[float]] | None = None,
        steps_per_param: int = 5,
        strategy: str = "grid",
        sample_rate: int = 22050,
        duration_sec: float = 2.0,
        midi_note: int = 60,
        output_path: str = "data/probes/probes.sqlite",
        seed: int | None = 0,
    ) -> dict[str, Any]:
        """Sweep a device's parameter space, extract features per cell, persist a probe dataset.

        Pass ``device_id="synth_stub"`` to run the in-process numpy synth (no Live
        required) — useful for testing the rest of the pipeline. For real Live
        devices, also pass ``track_index`` and ``device_index``; rendering will
        fail with NotImplementedError until the Phase 2 capture pipeline lands.
        """
        try:
            renderer = _resolve_renderer(
                device_id, track_index, device_index, sample_rate, duration_sec
            )
        except ValueError as exc:
            return {"error": str(exc), "phase": 3}

        try:
            ranges = _resolve_param_ranges(renderer, params, param_ranges)
        except ValueError as exc:
            return {"error": str(exc), "phase": 3}

        planner = SweepPlanner(
            ranges, steps_per_param=steps_per_param, strategy=strategy, seed=seed
        )

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        probed = 0
        errors: list[dict[str, Any]] = []
        with ProbeDataset(out_path, device_id=device_id) as ds:
            ds.set_meta("strategy", strategy)
            ds.set_meta("sample_rate", str(sample_rate))
            ds.set_meta("duration_sec", str(duration_sec))
            ds.set_meta("param_names", ",".join(planner.param_names))
            for cell in planner:
                try:
                    audio = renderer.render(cell)
                    feats = extract_features(np.asarray(audio, dtype=np.float32), sr=sample_rate)
                    ds.append(cell, feats, audio_path=None, device_id=device_id)
                    probed += 1
                except NotImplementedError as exc:
                    return {
                        "status": "not_implemented",
                        "phase": 3,
                        "device_id": device_id,
                        "depends_on": ["render_clip (Phase 2)"],
                        "message": str(exc),
                        "params": list(ranges.keys()),
                        "planned_cells": len(planner),
                    }
                except Exception as exc:  # pragma: no cover — keep sweep going on rare cell errors
                    errors.append({"params": cell, "error": repr(exc)})
            total = len(ds)

        return {
            "status": "ok",
            "device_id": device_id,
            "dataset_path": str(out_path.resolve()),
            "strategy": strategy,
            "params": list(ranges.keys()),
            "param_ranges": {k: [float(v[0]), float(v[1])] for k, v in ranges.items()},
            "planned_cells": len(planner),
            "rows_added": probed,
            "rows_total": total,
            "sample_rate": sample_rate,
            "duration_sec": duration_sec,
            "errors": errors,
            "midi_note": midi_note,
        }

    @mcp.tool()
    async def sound_match(
        target_audio_path: str,
        dataset_path: str,
        device_id: str | None = None,
        track_index: int | None = None,
        device_index: int | None = None,
        k: int = 5,
        refine_top: bool = False,
        max_refine_iter: int = 20,
        sample_rate: int = 22050,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Find probe cells whose features most closely match a target audio file.

        ``apply=True`` pushes the best params via OSC — only honoured when the
        dataset's device is a real Live device with ``track_index``/``device_index``
        provided. Set ``refine_top=True`` to run scipy.optimize starting from the
        kNN best for an extra polish (synth_stub only — real-device refinement
        needs Phase 2 capture).
        """
        import librosa  # local to keep the rest of the module lightweight

        target = Path(target_audio_path)
        if not target.exists():
            return {"error": f"file not found: {target_audio_path}"}

        ds_path = Path(dataset_path)
        if not ds_path.exists():
            return {"error": f"dataset not found: {dataset_path}"}

        y, sr = librosa.load(str(target), sr=sample_rate, mono=True)
        target_features = extract_features(np.asarray(y, dtype=np.float32), sr=int(sr))

        ds = ProbeDataset.load(ds_path, device_id=device_id)
        try:
            matches = find_nearest(target_features, ds, k=k, device_id=device_id)
        finally:
            ds.close()

        if not matches:
            return {
                "error": "dataset is empty (or no rows for the given device_id)",
                "dataset_path": str(ds_path.resolve()),
                "device_id": device_id,
            }

        out: dict[str, Any] = {
            "status": "ok",
            "target": str(target.resolve()),
            "dataset_path": str(ds_path.resolve()),
            "device_id": matches[0].device_id,
            "target_features": target_features.to_dict(),
            "matches": [m.to_dict() for m in matches],
        }

        if refine_top:
            top = matches[0]
            try:
                renderer = _resolve_renderer(
                    top.device_id, track_index, device_index, sample_rate, target_features.duration_sec or 2.0
                )
                ranges = _resolve_param_ranges(renderer, list(top.params.keys()), None)
                refined = refine(
                    top.params,
                    target_features,
                    renderer.render,
                    param_ranges=ranges,
                    sample_rate=sample_rate,
                    max_iter=max_refine_iter,
                )
                out["refined"] = {
                    "best_params": refined["best_params"],
                    "best_distance": refined["best_distance"],
                    "n_evaluations": refined["n_evaluations"],
                    "converged": refined["converged"],
                    "method": refined["method"],
                }
            except NotImplementedError as exc:
                out["refined"] = {"status": "not_implemented", "message": str(exc)}
            except Exception as exc:  # pragma: no cover — refinement is best-effort
                out["refined"] = {"error": repr(exc)}

        if apply:
            best_params = (
                out.get("refined", {}).get("best_params") if isinstance(out.get("refined"), dict) else None
            )
            best_params = best_params or matches[0].params
            if matches[0].device_id == SYNTH_STUB_DEVICE_ID:
                out["apply"] = {
                    "status": "skipped",
                    "reason": "synth_stub is an in-process renderer; nothing to push",
                    "best_params": best_params,
                }
            elif track_index is None or device_index is None:
                out["apply"] = {
                    "status": "skipped",
                    "reason": "track_index and device_index required to apply to a Live device",
                    "best_params": best_params,
                }
            else:
                try:
                    push_result = _push_params_via_osc(track_index, device_index, best_params)
                    if hasattr(push_result, "__await__"):
                        push_result = await push_result  # type: ignore[assignment]
                    out["apply"] = {"status": "ok", **push_result, "best_params": best_params}
                except Exception as exc:  # pragma: no cover — OSC errors surface here
                    out["apply"] = {"status": "error", "error": repr(exc), "best_params": best_params}

        return out

    @mcp.tool()
    async def sound_explain_parameter(
        parameter_name: str,
        device_id: str = SYNTH_STUB_DEVICE_ID,
        track_index: int | None = None,
        device_index: int | None = None,
        param_ranges: dict[str, list[float]] | None = None,
        steps: int = 11,
        sample_rate: int = 22050,
        duration_sec: float = 2.0,
        top_dimensions: int = 6,
    ) -> dict[str, Any]:
        """Sweep one parameter while holding the others at their midpoint.

        Reports per-dimension feature deltas across the sweep so callers can
        say "centroid +30%, flatness +20% — opens up the highs and adds noise".
        """
        try:
            renderer = _resolve_renderer(
                device_id, track_index, device_index, sample_rate, duration_sec
            )
            ranges = _resolve_param_ranges(renderer, None, param_ranges)
        except ValueError as exc:
            return {"error": str(exc)}
        if parameter_name not in ranges:
            return {"error": f"parameter {parameter_name!r} unknown; have {sorted(ranges)}"}

        planner = SweepPlanner(ranges, steps_per_param=steps, strategy="grid", seed=0)

        # Hold every other param at its midpoint while sweeping the named one.
        defaults: dict[str, float] = {
            name: 0.5 * (lo + hi) for name, (lo, hi) in ranges.items()
        }
        if device_id == SYNTH_STUB_DEVICE_ID:
            for k, v in SYNTH_STUB_DEFAULTS.items():
                if k in defaults:
                    defaults[k] = v

        sweep_values = list(np.linspace(ranges[parameter_name][0], ranges[parameter_name][1], steps))
        rows: list[dict[str, Any]] = []
        try:
            for v in sweep_values:
                cell = dict(defaults)
                cell[parameter_name] = float(v)
                audio = renderer.render(cell)
                feats = extract_features(np.asarray(audio, dtype=np.float32), sr=sample_rate)
                rows.append(
                    {
                        "value": float(v),
                        "feature_vector": feature_vector(feats),
                        "features": feats.to_dict(),
                    }
                )
        except NotImplementedError as exc:
            return {
                "status": "not_implemented",
                "phase": 3,
                "depends_on": ["render_clip (Phase 2)"],
                "message": str(exc),
                "parameter_name": parameter_name,
                "device_id": device_id,
            }

        # Aggregate: range / abs delta / relative delta per feature dim.
        mat = np.stack([r["feature_vector"] for r in rows], axis=0).astype(np.float64)
        mins = mat.min(axis=0)
        maxs = mat.max(axis=0)
        ranges_per_dim = maxs - mins
        # Relative change: span / |mean|, guarded for tiny means.
        means = mat.mean(axis=0)
        rel = ranges_per_dim / np.where(np.abs(means) < 1e-6, 1e-6, np.abs(means))

        ordered = np.argsort(-ranges_per_dim)
        top = []
        for idx in ordered[: int(top_dimensions)]:
            top.append(
                {
                    "feature": FEATURE_VECTOR_NAMES[int(idx)],
                    "min": float(mins[idx]),
                    "max": float(maxs[idx]),
                    "abs_delta": float(ranges_per_dim[idx]),
                    "relative_delta": float(rel[idx]),
                }
            )

        return {
            "status": "ok",
            "device_id": device_id,
            "parameter_name": parameter_name,
            "parameter_range": [
                float(ranges[parameter_name][0]),
                float(ranges[parameter_name][1]),
            ],
            "steps": int(steps),
            "fixed_params": {k: v for k, v in defaults.items() if k != parameter_name},
            "top_dimensions": top,
            "sweep": [
                {"value": r["value"], "features": r["features"]} for r in rows
            ],
        }
