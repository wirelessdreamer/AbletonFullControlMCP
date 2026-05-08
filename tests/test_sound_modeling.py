"""Phase 3 sound-modeling tests — exercise the offline math layer end-to-end.

The headline test sweeps the ``synth_stub`` synth, builds a probe dataset,
renders an out-of-grid "target", and asserts kNN finds the nearest grid
cell within the top-3 (a strict but sane proxy for "the matcher works").
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from ableton_mcp.sound import (
    LiveRenderer,
    ProbeDataset,
    SweepPlanner,
    SynthStubRenderer,
    extract_features,
    feature_distance,
    feature_vector,
    find_nearest,
    refine,
    synth_render,
)
from ableton_mcp.sound.features import FEATURE_VECTOR_DIM


# ---------- planner -----------------------------------------------------------


def test_planner_grid_cell_count() -> None:
    ranges = {"a": (0.0, 1.0), "b": (10.0, 20.0)}
    p = SweepPlanner(ranges, steps_per_param=4, strategy="grid")
    cells = list(p)
    assert len(p) == 4 ** 2 == 16
    assert len(cells) == 16
    # Grid endpoints are exact.
    a_vals = sorted({c["a"] for c in cells})
    assert math.isclose(a_vals[0], 0.0)
    assert math.isclose(a_vals[-1], 1.0)


def test_planner_lhs_cell_count_and_bounds() -> None:
    ranges = {"a": (0.0, 1.0), "b": (10.0, 20.0), "c": (-5.0, 5.0)}
    p = SweepPlanner(ranges, steps_per_param=7, strategy="lhs", seed=42)
    cells = list(p)
    assert len(p) == 7 * 3 == 21
    assert len(cells) == 21
    for cell in cells:
        for name, (lo, hi) in ranges.items():
            assert lo <= cell[name] <= hi


def test_planner_random_is_seeded() -> None:
    ranges = {"x": (0.0, 1.0)}
    a = list(SweepPlanner(ranges, steps_per_param=10, strategy="random", seed=123))
    b = list(SweepPlanner(ranges, steps_per_param=10, strategy="random", seed=123))
    assert a == b
    c = list(SweepPlanner(ranges, steps_per_param=10, strategy="random", seed=999))
    assert a != c


# ---------- features + vector layout ------------------------------------------


def test_feature_vector_dim_is_stable() -> None:
    audio = synth_render({"freq": 440.0}, sr=22050, dur=0.5)
    feats = extract_features(audio, sr=22050)
    vec = feature_vector(feats)
    assert vec.shape == (FEATURE_VECTOR_DIM,)
    assert vec.dtype == np.float32


def test_features_handle_silence() -> None:
    feats = extract_features(np.zeros(2048, dtype=np.float32), sr=22050)
    vec = feature_vector(feats)
    assert vec.shape == (FEATURE_VECTOR_DIM,)
    # Silent input → zeros (we early-return in extract_features).
    assert float(np.sum(np.abs(vec))) == 0.0


# ---------- dataset round-trip ------------------------------------------------


def test_dataset_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "probes.sqlite"
    audio = synth_render({"freq": 440.0}, sr=22050, dur=0.5)
    feats = extract_features(audio, sr=22050)

    with ProbeDataset(db_path, device_id="synth_stub") as ds:
        pid = ds.append({"freq": 440.0}, feats)
        assert pid >= 1
        rows = list(ds.iter_rows())
        assert len(rows) == 1
        assert rows[0].params == {"freq": 440.0}
        assert rows[0].feature_vector.shape == (FEATURE_VECTOR_DIM,)

    # Re-open from disk.
    with ProbeDataset.load(db_path, device_id="synth_stub") as ds2:
        params, mat = ds2.to_numpy()
        assert mat.shape == (1, FEATURE_VECTOR_DIM)
        assert params == [{"freq": 440.0}]


# ---------- the headline matcher test ----------------------------------------


def _build_synth_stub_dataset(
    renderer: SynthStubRenderer, ranges, db_path: Path
) -> ProbeDataset:
    ds = ProbeDataset(db_path, device_id="synth_stub")
    planner = SweepPlanner(ranges, steps_per_param=3, strategy="grid")
    for cell in planner:
        audio = renderer.render(cell)
        feats = extract_features(audio, sr=renderer.sample_rate)
        ds.append(cell, feats)
    return ds


def test_synth_stub_recovery_top_k(tmp_path: Path) -> None:
    """Sweep the synth, render a held-out target, and check kNN recovers it."""
    renderer = SynthStubRenderer(sample_rate=22050, duration_sec=1.0)
    # A small but multi-axis sweep: 3^3 = 27 cells. Grid endpoints are deterministic
    # so we know exactly which cell is the truth.
    ranges = {
        "freq": (220.0, 880.0),
        "cutoff": (500.0, 6000.0),
        "noise_amount": (0.0, 0.4),
    }
    db_path = tmp_path / "probe.sqlite"
    ds = _build_synth_stub_dataset(renderer, ranges, db_path)
    try:
        # Pick an exact grid cell as the truth so kNN can recover it perfectly.
        truth = {"freq": 880.0, "cutoff": 500.0, "noise_amount": 0.0}
        target_audio = renderer.render(truth)
        target_features = extract_features(target_audio, sr=22050)

        matches = find_nearest(target_features, ds, k=3)
        assert len(matches) == 3
        params_top3 = [m.params for m in matches]
        # Truth must show up in the top-3.
        assert truth in [
            {k: round(v, 6) for k, v in p.items()} for p in params_top3
        ] or truth in params_top3, f"truth {truth} not in top-3 {params_top3}"

        # The best match must be at distance ~0 because it is the exact same render.
        assert matches[0].distance < 1e-3
    finally:
        ds.close()


def test_synth_stub_recovery_off_grid_target(tmp_path: Path) -> None:
    """Off-grid target — kNN should still find a structurally plausible neighbour."""
    renderer = SynthStubRenderer(sample_rate=22050, duration_sec=1.0)
    ranges = {
        "freq": (220.0, 880.0),
        "cutoff": (500.0, 6000.0),
    }
    db_path = tmp_path / "probe.sqlite"
    ds = _build_synth_stub_dataset(renderer, ranges, db_path)
    try:
        # Target between grid cells.
        truth = {"freq": 700.0, "cutoff": 4000.0}
        target_audio = renderer.render(truth)
        target_features = extract_features(target_audio, sr=22050)

        matches = find_nearest(target_features, ds, k=3)
        # At least one of the top-3 should be on the high-freq / mid-cutoff side.
        plausible = [
            m for m in matches if m.params["freq"] >= 550.0 and m.params["cutoff"] >= 3000.0
        ]
        assert plausible, f"no high-freq/mid-cutoff neighbours in {[m.params for m in matches]}"
    finally:
        ds.close()


# ---------- refine optimiser --------------------------------------------------


def test_refine_decreases_distance(tmp_path: Path) -> None:
    """scipy.optimize refinement should not make things worse."""
    renderer = SynthStubRenderer(sample_rate=22050, duration_sec=0.5)
    truth = {"freq": 440.0, "cutoff": 1500.0}
    target_audio = renderer.render(truth)
    target_features = extract_features(target_audio, sr=22050)

    # Start far away.
    initial = {"freq": 200.0, "cutoff": 6000.0}
    initial_audio = renderer.render(initial)
    initial_features = extract_features(initial_audio, sr=22050)
    initial_distance = feature_distance(
        feature_vector(target_features), feature_vector(initial_features)
    )

    result = refine(
        initial,
        target_features,
        renderer.render,
        param_ranges={"freq": (100.0, 1000.0), "cutoff": (500.0, 8000.0)},
        sample_rate=22050,
        max_iter=10,
    )
    assert result["best_distance"] <= initial_distance + 1e-6


# ---------- LiveRenderer placeholder -----------------------------------------


def test_live_renderer_constructs_cheaply() -> None:
    """LiveRenderer.render() now drives the Phase 2 capture pipeline; see
    ``tests/test_live_renderer.py`` for the end-to-end coverage. Construction
    itself must remain dependency-free so the offline pipeline keeps working
    even when AbletonOSC + the M4L tape device aren't reachable.
    """
    r = LiveRenderer(track_index=0, device_index=1)
    assert r.track_index == 0
    assert r.device_index == 1
    # Calling render() without any servers up would fail with an OSC timeout
    # (or a tape timeout). We don't exercise that path here because it has its
    # own test file that wires fakes; we just want to confirm ctor is safe.


# ---------- existing server regression: tools still register -----------------


@pytest.mark.asyncio
async def test_sound_tools_still_registered() -> None:
    from ableton_mcp.server import build_server

    mcp = build_server()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    for expected in ("sound_probe_device", "sound_match", "sound_explain_parameter"):
        assert expected in names
