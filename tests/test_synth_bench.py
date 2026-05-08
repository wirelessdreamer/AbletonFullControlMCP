"""Tests for the in-process synth bench."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ableton_mcp.sound import (
    ProbeDataset,
    SweepPlanner,
    extract_features,
    feature_distance,
    feature_vector,
    find_nearest,
)
from ableton_mcp.synth_bench import (
    SYNTH_REGISTRY,
    DelayFX,
    FilterFX,
    FM4OpRenderer,
    FXChain,
    ReverbFX,
    SaturatorFX,
    SubtractiveRenderer,
    get,
    list_synths,
)


# ---------- registry ---------------------------------------------------------


def test_registry_has_six_synths() -> None:
    """All six expected bench synths are registered."""
    expected = {"subtractive", "fm_2op", "fm_4op", "wavetable", "additive", "granular"}
    assert set(list_synths()) == expected
    assert set(SYNTH_REGISTRY.keys()) == expected


# ---------- per-synth render smoke -------------------------------------------


@pytest.mark.parametrize("name", sorted(SYNTH_REGISTRY.keys()))
def test_synth_renders_without_error(name: str) -> None:
    """Every synth renders default params to a non-empty float32 array."""
    r = get(name, duration_sec=1.0)
    audio = r.render(r.param_defaults)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert audio.shape[0] == int(r.duration_sec * r.sample_rate)
    assert np.all(np.isfinite(audio))


@pytest.mark.parametrize("name", sorted(SYNTH_REGISTRY.keys()))
def test_synth_produces_non_silent_audio(name: str) -> None:
    """RMS of default render must clear a small noise floor."""
    r = get(name, duration_sec=1.0)
    audio = r.render(r.param_defaults)
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    assert rms > 0.01, f"{name} produced near-silence (rms={rms:.4f})"


# ---------- fm_4op algorithm distinction -------------------------------------


def test_fm_4op_algorithms_are_distinct() -> None:
    """Algorithm 1 (chain) vs algorithm 7 (3 mods on op1) must differ in features."""
    r = FM4OpRenderer(duration_sec=1.0)
    p = dict(r.param_defaults)
    # Force strong FM so the wiring matters.
    p["op2_index"] = 4.0
    p["op3_index"] = 4.0
    p["op4_index"] = 4.0
    p["op2_ratio"] = 2.0
    p["op3_ratio"] = 3.5
    p["op4_ratio"] = 5.5

    p["algorithm"] = 1
    a1 = r.render(p)
    p["algorithm"] = 7
    a7 = r.render(p)

    f1 = feature_vector(extract_features(a1, sr=22050))
    f7 = feature_vector(extract_features(a7, sr=22050))
    cos_dist = feature_distance(f1, f7, metric="cosine")
    # Cosine distance over MFCCs is naturally small in absolute terms but
    # > 0.001 indicates structurally different timbre.
    assert cos_dist > 1e-3, f"alg1 ≈ alg7 (cosine={cos_dist})"


# ---------- FX chain composition --------------------------------------------


def test_fx_chain_extends_energy_past_original_duration() -> None:
    """Adding delay+reverb tail should leave audible energy beyond the original duration."""
    base = SubtractiveRenderer(duration_sec=0.5)
    chain = FXChain(base, [DelayFX(0.2, 0.6, 0.5), ReverbFX(0.7, 0.3, 0.5)])
    p = dict(base.param_defaults)
    p["release"] = 0.05  # cut release short so the dry signal really ends
    p["sustain"] = 0.0   # decay-then-stop, so tail energy is FX-only

    base_audio = base.render(p)
    chain_audio = chain.render(p)

    # Chain output is longer (delay+reverb pad the buffer).
    assert chain_audio.shape[0] > base_audio.shape[0]

    n_orig = base_audio.shape[0]
    base_tail = float(np.sum(base_audio[max(0, n_orig - 100):] ** 2))
    chain_tail = float(np.sum(chain_audio[n_orig:] ** 2))
    assert chain_tail > base_tail, (
        f"FX tail energy {chain_tail:.4f} not > base tail {base_tail:.4f}"
    )


def test_fx_chain_individual_fx_run() -> None:
    """Each FX runs and returns finite float audio (smoke-test all four)."""
    base = SubtractiveRenderer(duration_sec=0.3)
    for fx in [
        FilterFX(cutoff=800, q=2.0, kind="lp"),
        SaturatorFX(drive=0.6, asymmetry=0.3),
        DelayFX(0.1, 0.4, 0.3),
        # Skip ReverbFX in this combinatorial smoke — it's exercised above and is slow.
    ]:
        chain = FXChain(base, [fx])
        out = chain.render(base.param_defaults)
        assert np.all(np.isfinite(out))
        assert out.dtype == np.float32


# ---------- probe → match round-trip on subtractive --------------------------


def test_subtractive_probe_match_round_trip(tmp_path: Path) -> None:
    """Build a probe, render a known target, recover its params via kNN."""
    renderer = SubtractiveRenderer(sample_rate=22050, duration_sec=1.0)
    # Pick three distinguishing axes; a small grid keeps this test fast.
    ranges = {
        "freq": (220.0, 880.0),
        "cutoff": (500.0, 6000.0),
        "resonance": (0.5, 6.0),
    }
    db_path = tmp_path / "subtractive_probe.sqlite"

    # Build the probe dataset with a 3-step grid (27 cells).
    with ProbeDataset(db_path, device_id="bench:subtractive") as ds:
        planner = SweepPlanner(ranges, steps_per_param=3, strategy="grid")
        for cell in planner:
            full = dict(renderer.param_defaults)
            full.update(cell)
            audio = renderer.render(full)
            feats = extract_features(audio, sr=renderer.sample_rate)
            ds.append(full, feats)

    # Target = an exact grid cell (corners of the cube).
    truth = {"freq": 220.0, "cutoff": 6000.0, "resonance": 0.5}
    target_full = dict(renderer.param_defaults)
    target_full.update(truth)
    target_audio = renderer.render(target_full)
    target_features = extract_features(target_audio, sr=renderer.sample_rate)

    with ProbeDataset.load(db_path, device_id="bench:subtractive") as ds:
        matches = find_nearest(target_features, ds, k=3)

    assert matches, "kNN returned no matches"
    best = matches[0]
    # Best match must be at distance ~0 (identical render) and recover all three axes.
    assert best.distance < 1e-3, f"best distance too large: {best.distance}"
    for key, val in truth.items():
        recovered = best.params[key]
        assert abs(recovered - val) < 1e-3, (
            f"param {key}: truth={val} recovered={recovered}"
        )


# ---------- MCP tool registration --------------------------------------------


@pytest.mark.asyncio
async def test_bench_register_adds_tools() -> None:
    """``bench.register`` must add at least four bench_* tools to a FastMCP."""
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import bench

    mcp = FastMCP("test-bench")
    bench.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    bench_names = {n for n in names if n.startswith("bench_")}
    assert len(bench_names) >= 4, f"expected ≥4 bench_* tools, got {bench_names}"
    for expected in (
        "bench_list_synths",
        "bench_render",
        "bench_probe",
        "bench_describe",
    ):
        assert expected in names, f"missing tool: {expected}"
