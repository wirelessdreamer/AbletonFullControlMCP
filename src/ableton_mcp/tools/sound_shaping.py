"""MCP tools for the natural-language sound-shaping workflow.

Four tools, all exposed under the ``shape_*`` prefix:

- ``shape_parse(description)``: debug — parse text into a :class:`ShapeRequest`.
- ``shape_predict(description, current_audio_path, dataset_path, k)``:
  feature-extract the current audio, build target features, kNN against the
  probe dataset, return the top-k param recommendations + per-descriptor
  reasoning + a ``semantics_source`` tag.
- ``shape_apply(description, track_index, device_index, dataset_path, dry_run)``:
  same as ``shape_predict`` but, when ``dry_run=False``, pushes the best
  match to the Live device via OSC. Requires a probe dataset that maps to a
  real Live device (synth_stub has nothing to push to).
- ``shape_compare_apply(reference_audio_path, track_index, device_index, dataset_path, dry_run)``:
  "make my device sound like *this* reference clip" — feature-extract the
  reference, kNN, optionally apply.

The whole pipeline is built on top of :mod:`ableton_mcp.shaping`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP

from ..shaping import (
    apply_to_live_device,
    find_params_matching_target,
    parse_shape_request,
    plan_target_features,
    semantics_source,
)
from ..shaping.applier import apply_to_live_device_async
from ..sound.features import extract_features, feature_vector

log = logging.getLogger(__name__)


def _load_audio(path: str, sample_rate: int) -> np.ndarray:
    """Best-effort audio loader. Uses librosa locally so the import stays cheap."""
    import librosa  # local: only when actually shaping

    y, _ = librosa.load(str(path), sr=sample_rate, mono=True)
    return np.asarray(y, dtype=np.float32)


def _describe_descriptor(label: str, intensity: int) -> dict[str, Any]:
    """Human-readable per-descriptor reasoning."""
    polarity = (
        "much less" if intensity == -2
        else "less" if intensity == -1
        else "more" if intensity == +1
        else "much more" if intensity == +2
        else "neutral"
    )
    return {"label": label, "intensity_modifier": int(intensity), "interpretation": polarity}


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def shape_parse(description: str) -> dict[str, Any]:
        """Debug helper — parse free-text into a structured ShapeRequest.

        Returns the canonical descriptors (label + intensity in {-2, -1, +1, +2}),
        any explicit feature targets ("centroid above 3000Hz"), an optional
        ``compare_to`` clause, and the device hint pulled from "on the lead" /
        "for the bass".
        """
        request = parse_shape_request(description)
        return {
            "status": "ok",
            "request": request.to_dict(),
            "semantics_source": semantics_source(),
        }

    @mcp.tool()
    async def shape_predict(
        description: str,
        current_audio_path: str,
        dataset_path: str,
        k: int = 5,
        sample_rate: int = 22050,
    ) -> dict[str, Any]:
        """Predict the top-k param settings that match a free-text shaping request.

        Pipeline: parse the text → extract features from ``current_audio_path``
        → compute target features (semantics package if available, else
        fallback vocab) → kNN against the dataset.

        Does not touch Live. Use ``shape_apply`` to actually push the params.
        """
        cur_path = Path(current_audio_path)
        if not cur_path.exists():
            return {"status": "error", "error": f"current_audio_path not found: {current_audio_path}"}
        ds_path = Path(dataset_path)
        if not ds_path.exists():
            return {"status": "error", "error": f"dataset_path not found: {dataset_path}"}

        request = parse_shape_request(description)
        try:
            audio = _load_audio(str(cur_path), int(sample_rate))
        except Exception as exc:  # pragma: no cover — librosa failure path
            return {"status": "error", "error": f"failed to load current audio: {exc!r}"}
        current_features = extract_features(audio, sr=int(sample_rate))

        targets = plan_target_features(current_features, request)

        try:
            matches = find_params_matching_target(targets, ds_path, k=int(k))
        except FileNotFoundError as exc:
            return {"status": "error", "error": str(exc)}

        return {
            "status": "ok",
            "description": description,
            "request": request.to_dict(),
            "semantics_source": semantics_source(),
            "interpretations": [
                _describe_descriptor(label, intensity)
                for label, intensity in request.descriptors
            ],
            "current_features": current_features.to_dict(),
            "target_features": targets,
            "matches": [m.to_dict() for m in matches],
        }

    @mcp.tool()
    async def shape_apply(
        description: str,
        track_index: int,
        device_index: int,
        current_audio_path: str,
        dataset_path: str,
        k: int = 5,
        dry_run: bool = True,
        sample_rate: int = 22050,
    ) -> dict[str, Any]:
        """Predict + (optionally) apply the best-matching params to a Live device.

        ``dry_run=True`` (default) returns the prediction without touching
        Live. Set ``dry_run=False`` to push the top match's params via OSC.
        OSC failures are returned in the ``apply`` field — they don't raise.
        """
        prediction = await shape_predict(
            description, current_audio_path, dataset_path, k=k, sample_rate=sample_rate
        )
        if prediction.get("status") != "ok" or not prediction.get("matches"):
            return prediction

        best_params = prediction["matches"][0]["params"]

        if dry_run:
            prediction["apply"] = {
                "status": "dry_run",
                "best_params": dict(best_params),
                "track_index": int(track_index),
                "device_index": int(device_index),
            }
            return prediction

        prediction["apply"] = await apply_to_live_device_async(
            int(track_index), int(device_index), best_params
        )
        prediction["apply"]["best_params"] = dict(best_params)
        return prediction

    @mcp.tool()
    async def shape_compare_apply(
        reference_audio_path: str,
        track_index: int,
        device_index: int,
        dataset_path: str,
        k: int = 5,
        dry_run: bool = True,
        sample_rate: int = 22050,
    ) -> dict[str, Any]:
        """"Make my device sound like *this* reference clip."

        Feature-extract the reference, kNN against the dataset, optionally
        push the top match. Mirrors ``shape_apply`` but driven by audio
        rather than text.
        """
        ref_path = Path(reference_audio_path)
        if not ref_path.exists():
            return {
                "status": "error",
                "error": f"reference_audio_path not found: {reference_audio_path}",
            }
        ds_path = Path(dataset_path)
        if not ds_path.exists():
            return {"status": "error", "error": f"dataset_path not found: {dataset_path}"}

        try:
            audio = _load_audio(str(ref_path), int(sample_rate))
        except Exception as exc:  # pragma: no cover
            return {"status": "error", "error": f"failed to load reference: {exc!r}"}
        ref_features = extract_features(audio, sr=int(sample_rate))
        target_vec = feature_vector(ref_features)

        try:
            matches = find_params_matching_target(target_vec, ds_path, k=int(k))
        except FileNotFoundError as exc:
            return {"status": "error", "error": str(exc)}

        out: dict[str, Any] = {
            "status": "ok",
            "reference": str(ref_path.resolve()),
            "reference_features": ref_features.to_dict(),
            "matches": [m.to_dict() for m in matches],
        }
        if not matches:
            out["apply"] = {"status": "skipped", "reason": "no matches"}
            return out

        best_params = matches[0].params
        if dry_run:
            out["apply"] = {
                "status": "dry_run",
                "best_params": dict(best_params),
                "track_index": int(track_index),
                "device_index": int(device_index),
            }
            return out

        out["apply"] = await apply_to_live_device_async(
            int(track_index), int(device_index), best_params
        )
        out["apply"]["best_params"] = dict(best_params)
        return out
