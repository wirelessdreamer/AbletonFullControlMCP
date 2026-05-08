"""Tests for the natural-language sound-shaping flow.

Covers:
- parser handling of common phrases (intensity polarity + magnitude),
- planner using the fallback vocab when the semantics package is missing,
- end-to-end shape_predict against a synth_stub probe dataset,
- apply_to_live_device graceful failure when OSC is unreachable.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.shaping import (
    apply_to_live_device,
    parse_shape_request,
    plan_target_features,
    semantics_source,
)
from ableton_mcp.shaping import fallback_vocab
from ableton_mcp.shaping.applier import apply_to_live_device_async
from ableton_mcp.sound import (
    ProbeDataset,
    SweepPlanner,
    SynthStubRenderer,
    extract_features,
    synth_render,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _force_fallback(monkeypatch) -> None:
    """Force the planner to use the hardcoded fallback vocab.

    Done by stubbing the ``ableton_mcp.semantics`` namespace with a module
    that lacks the ``transforms.descriptor_to_feature_delta`` symbol. The
    planner treats that as "semantics package missing" and uses the fallback
    vocab instead.
    """
    fake = types.ModuleType("ableton_mcp.semantics_stub_for_tests")
    monkeypatch.setattr(
        "ableton_mcp.shaping.planner._try_import_semantics", lambda: None
    )


def _build_synth_stub_probe(db_path: Path) -> dict:
    """3^3 probe over freq / cutoff / noise_amount on the synth stub."""
    renderer = SynthStubRenderer(sample_rate=22050, duration_sec=1.0)
    ranges = {
        "freq": (220.0, 660.0),
        "cutoff": (500.0, 7000.0),
        "noise_amount": (0.0, 0.4),
    }
    ds = ProbeDataset(db_path, device_id="synth_stub")
    planner = SweepPlanner(ranges, steps_per_param=3, strategy="grid")
    for cell in planner:
        audio = renderer.render(cell)
        feats = extract_features(audio, sr=renderer.sample_rate)
        ds.append(cell, feats)
    ds.close()
    return ranges


def _render_audio_to_wav(params: dict, path: Path, sr: int = 22050, dur: float = 1.0) -> None:
    audio = synth_render(params, sr=sr, dur=dur)
    sf.write(str(path), audio.astype(np.float32), sr, subtype="FLOAT")


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parser_brighter_only() -> None:
    r = parse_shape_request("brighter")
    assert r.descriptors == [("bright", +1)]
    assert r.compare_to is None
    assert r.device_hint is None
    assert r.targets_specific == []


def test_parser_much_warmer() -> None:
    r = parse_shape_request("much warmer")
    assert r.descriptors == [("warm", +2)]


def test_parser_less_air() -> None:
    r = parse_shape_request("less air")
    assert r.descriptors == [("airy", -1)]


def test_parser_brighter_and_punchier() -> None:
    r = parse_shape_request("brighter and punchier")
    labels = {label for label, _ in r.descriptors}
    assert {"bright", "punchy"} <= labels
    # Both default to +1 with no qualifier.
    for label, intensity in r.descriptors:
        assert intensity == +1


def test_parser_make_it_sound_like_x() -> None:
    r = parse_shape_request("make the lead sound like a vintage Rhodes")
    labels = {label for label, _ in r.descriptors}
    assert "vintage" in labels
    assert r.compare_to == "rhodes"
    assert r.device_hint == "lead"


def test_parser_specific_target() -> None:
    r = parse_shape_request("brighter with centroid above 3000Hz")
    assert any(t["feature"] == "centroid" for t in r.targets_specific)
    spec = next(t for t in r.targets_specific if t["feature"] == "centroid")
    assert spec["comparator"] == ">"
    assert spec["value"] == 3000.0


def test_parser_much_warmer_with_less_air() -> None:
    r = parse_shape_request("much warmer with less air")
    d = dict(r.descriptors)
    assert d.get("warm") == +2
    assert d.get("airy") == -1


# ---------------------------------------------------------------------------
# Planner / fallback-vocab tests
# ---------------------------------------------------------------------------


def test_planner_uses_fallback_when_semantics_missing(monkeypatch) -> None:
    """No semantics package importable → planner falls back, source is 'fallback'."""
    _force_fallback(monkeypatch)
    assert semantics_source() == "fallback"

    # Build a baseline current_features dict.
    current = {
        "spectral_centroid": 2000.0,
        "spectral_bandwidth": 1500.0,
        "spectral_rolloff": 4000.0,
        "zcr": 0.05,
        "rms": 0.2,
        "spectral_flatness": 0.1,
        "mfcc_mean": [0.0] * 13,
        "mfcc_std": [0.0] * 13,
    }

    request = parse_shape_request("brighter")
    targets = plan_target_features(current, request)
    # "brighter" should push centroid up, not down.
    assert targets["spectral_centroid"] > current["spectral_centroid"]


def test_planner_intensity_scaling(monkeypatch) -> None:
    """Intensity modifier should scale the delta linearly."""
    _force_fallback(monkeypatch)
    current = {
        "spectral_centroid": 2000.0,
        "spectral_bandwidth": 1500.0,
        "spectral_rolloff": 4000.0,
        "zcr": 0.05,
        "rms": 0.2,
        "spectral_flatness": 0.1,
        "mfcc_mean": [0.0] * 13,
        "mfcc_std": [0.0] * 13,
    }
    bright_one = plan_target_features(current, parse_shape_request("brighter"))
    bright_two = plan_target_features(current, parse_shape_request("much brighter"))
    # "much brighter" (intensity 2) should push centroid >= "brighter" (intensity 1).
    assert (
        bright_two["spectral_centroid"] - current["spectral_centroid"]
        > bright_one["spectral_centroid"] - current["spectral_centroid"] - 1e-6
    )


def test_planner_less_air_lowers_high_freq(monkeypatch) -> None:
    _force_fallback(monkeypatch)
    current = {
        "spectral_centroid": 2000.0,
        "spectral_bandwidth": 1500.0,
        "spectral_rolloff": 4000.0,
        "zcr": 0.05,
        "rms": 0.2,
        "spectral_flatness": 0.1,
        "mfcc_mean": [0.0] * 13,
        "mfcc_std": [0.0] * 13,
    }
    targets = plan_target_features(current, parse_shape_request("less air"))
    assert targets["spectral_rolloff"] < current["spectral_rolloff"]


def test_fallback_vocab_known_labels_size() -> None:
    """Sanity: fallback vocab is small (~25+) and well-formed."""
    labels = fallback_vocab.known_labels()
    assert 20 <= len(labels) <= 60
    for label in labels:
        deltas = fallback_vocab.descriptor_to_feature_delta(label)
        assert deltas, f"empty deltas for {label!r}"
        for k in deltas:
            assert k in {
                "spectral_centroid",
                "spectral_bandwidth",
                "spectral_rolloff",
                "zcr",
                "rms",
                "spectral_flatness",
            }


# ---------------------------------------------------------------------------
# End-to-end: shape_predict against a synth_stub probe dataset
# ---------------------------------------------------------------------------


def test_shape_predict_brighter_picks_higher_cutoff(tmp_path: Path, monkeypatch) -> None:
    """Render a dark current sound, ask for 'brighter', expect kNN to suggest higher cutoff."""
    _force_fallback(monkeypatch)

    db_path = tmp_path / "probes.sqlite"
    _build_synth_stub_probe(db_path)

    # Current sound: low cutoff = dark.
    current_params = {"freq": 220.0, "cutoff": 500.0, "noise_amount": 0.0}
    current_wav = tmp_path / "current.wav"
    _render_audio_to_wav(current_params, current_wav)

    from ableton_mcp.shaping import find_params_matching_target
    from ableton_mcp.sound.features import extract_features as extract

    audio, sr = _load_with_librosa(current_wav)
    cur_feats = extract(audio, sr=sr)

    request = parse_shape_request("brighter")
    targets = plan_target_features(cur_feats, request)
    matches = find_params_matching_target(targets, db_path, k=3)

    assert matches, "expected at least one match"
    # The top match's cutoff should be higher than current's cutoff.
    assert (
        matches[0].params["cutoff"] > current_params["cutoff"]
    ), f"top match cutoff {matches[0].params['cutoff']} not greater than current {current_params['cutoff']}"


def test_shape_predict_via_mcp_tool(tmp_path: Path, monkeypatch) -> None:
    """End-to-end through the registered MCP tool layer."""
    _force_fallback(monkeypatch)

    db_path = tmp_path / "probes.sqlite"
    _build_synth_stub_probe(db_path)

    current_params = {"freq": 220.0, "cutoff": 500.0, "noise_amount": 0.0}
    current_wav = tmp_path / "current.wav"
    _render_audio_to_wav(current_params, current_wav)

    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import sound_shaping

    mcp = FastMCP("test")
    sound_shaping.register(mcp)

    async def run() -> dict:
        # Get the underlying handler and call it directly.
        from ableton_mcp.tools.sound_shaping import register as _register  # noqa: F401

        # FastMCP exposes registered tools; invoke via call_tool.
        return await mcp.call_tool(
            "shape_predict",
            {
                "description": "brighter",
                "current_audio_path": str(current_wav),
                "dataset_path": str(db_path),
                "k": 3,
            },
        )

    result = asyncio.run(run())
    # FastMCP.call_tool returns (content, structured); extract the dict.
    payload = _coerce_call_result(result)
    assert payload["status"] == "ok"
    assert payload["semantics_source"] == "fallback"
    assert payload["matches"], "expected matches"
    assert payload["matches"][0]["params"]["cutoff"] > current_params["cutoff"]
    # Per-descriptor reasoning is included.
    assert any(
        i["label"] == "bright" and i["intensity_modifier"] == 1
        for i in payload["interpretations"]
    )


def _coerce_call_result(result) -> dict:
    """FastMCP.call_tool may return content+structured tuple or a dict; normalise."""
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and len(result) >= 2:
        # (content_list, structured_result)
        structured = result[1]
        if isinstance(structured, dict):
            # Some FastMCP versions wrap the dict under a "result" key.
            return structured.get("result", structured) if "result" in structured and isinstance(structured["result"], dict) else structured
        # Fall back to parsing the first text content.
        try:
            import json

            first = result[0][0]
            text = getattr(first, "text", None) or first.get("text")
            return json.loads(text)
        except Exception:  # pragma: no cover
            pass
    raise AssertionError(f"unexpected call_tool result shape: {type(result)!r}")


def _load_with_librosa(path: Path):
    import librosa

    y, sr = librosa.load(str(path), sr=22050, mono=True)
    return np.asarray(y, dtype=np.float32), int(sr)


# ---------------------------------------------------------------------------
# OSC graceful failure
# ---------------------------------------------------------------------------


def test_apply_to_live_device_graceful_when_osc_unreachable(monkeypatch) -> None:
    """If OSC isn't reachable, apply_to_live_device returns an error dict, doesn't crash."""

    async def _fake_get_client(*_args, **_kwargs):
        raise ConnectionRefusedError("OSC not running in test env")

    import ableton_mcp.osc_client as oc

    monkeypatch.setattr(oc, "get_client", _fake_get_client)

    result = apply_to_live_device(0, 0, {"freq": 440.0, "cutoff": 2000.0})
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "error" in result
    # Inputs preserved so the caller can show what would have been pushed.
    assert result["track_index"] == 0
    assert result["device_index"] == 0
    assert result["params"] == {"freq": 440.0, "cutoff": 2000.0}


def test_apply_to_live_device_async_graceful(monkeypatch) -> None:
    """Async variant: same graceful return for OSC failure."""

    async def _fake_get_client(*_args, **_kwargs):
        raise TimeoutError("OSC timed out")

    import ableton_mcp.osc_client as oc

    monkeypatch.setattr(oc, "get_client", _fake_get_client)

    result = asyncio.run(apply_to_live_device_async(2, 1, {"cutoff": 1000.0}))
    assert result["status"] == "error"
    assert "error" in result
