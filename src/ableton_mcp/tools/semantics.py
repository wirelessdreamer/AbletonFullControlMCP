"""MCP tools for the natural-language sound vocabulary.

Exposes:

- ``sound_describe`` — load a wav, extract features, return ranked descriptors.
- ``sound_descriptors_list`` — list all known descriptors (filterable by category).
- ``sound_descriptor_explain`` — show a single descriptor's anchors / aliases / opposite.
- ``sound_target_for_description`` — parse free-text ("brighter, with more
  warmth") into a per-feature delta plus an absolute target preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP

from ..semantics import (
    REFERENCE_DISTRIBUTIONS,
    VOCABULARY,
    combine_deltas,
    describe_features,
    descriptor_to_feature_delta,
    descriptors_in_category,
    lookup,
    parse_descriptors,
    parse_text_to_combined_delta,
)
from ..semantics.vocabulary import FEATURE_NAMES, FeatureAnchor


def _anchor_to_dict(anchor: FeatureAnchor) -> dict[str, Any]:
    return {
        "feature": anchor.feature,
        "predicate": anchor.predicate,
        "threshold": float(anchor.threshold),
        "weight": float(anchor.weight),
    }


def _descriptor_to_dict(label: str) -> dict[str, Any]:
    d = VOCABULARY[label]
    return {
        "label": d.label,
        "aliases": list(d.aliases),
        "category": d.category,
        "feature_anchors": [_anchor_to_dict(a) for a in d.feature_anchors],
        "opposite": d.opposite,
        "intensity_scale": float(d.intensity_scale),
        "description": d.description,
    }


def _features_dict(features) -> dict[str, float]:
    """Same flattening the describer uses — exposed here for tool output."""
    out: dict[str, float] = {
        "spectral_centroid": float(features.spectral_centroid),
        "spectral_bandwidth": float(features.spectral_bandwidth),
        "spectral_rolloff": float(features.spectral_rolloff),
        "zcr": float(features.zcr),
        "rms": float(features.rms),
        "spectral_flatness": float(features.spectral_flatness),
    }
    for i, v in enumerate(features.mfcc_mean):
        out[f"mfcc_mean_{i}"] = float(v)
    for i, v in enumerate(features.mfcc_std):
        out[f"mfcc_std_{i}"] = float(v)
    return out


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def sound_describe(audio_path: str, top_k: int = 8, sr: int = 22050) -> dict[str, Any]:
        """Describe an audio file with ranked natural-language descriptors.

        Loads the wav with librosa, extracts the 32-dim feature vector via
        :func:`ableton_mcp.sound.features.extract_features`, then ranks every
        descriptor in the vocabulary by how well its anchors fit. Returns the
        top ``top_k`` matches + the raw features (for debugging).
        """
        import librosa

        from ..sound.features import extract_features

        p = Path(audio_path)
        if not p.exists():
            return {"error": f"file not found: {audio_path}"}

        try:
            y, sample_rate = librosa.load(str(p), sr=sr, mono=True)
        except Exception as exc:  # pragma: no cover — librosa errors surface here
            return {"error": f"failed to load audio: {exc!r}"}
        if y.size == 0:
            return {"error": "empty audio"}

        features = extract_features(np.asarray(y, dtype=np.float32), sr=int(sample_rate))
        ranked = describe_features(features, top_k=int(top_k))

        return {
            "status": "ok",
            "path": str(p.resolve()),
            "duration_sec": float(features.duration_sec),
            "descriptors": [
                {"label": label, "confidence": float(conf)} for label, conf in ranked
            ],
            "features": features.to_dict(),
        }

    @mcp.tool()
    async def sound_descriptors_list(category: str | None = None) -> dict[str, Any]:
        """List every known descriptor, optionally filtered by category."""
        if category:
            descriptors = descriptors_in_category(category)
            if not descriptors:
                return {
                    "error": f"unknown category: {category!r}",
                    "categories": sorted({d.category for d in VOCABULARY.values()}),
                }
        else:
            descriptors = list(VOCABULARY.values())
        return {
            "status": "ok",
            "count": len(descriptors),
            "descriptors": [_descriptor_to_dict(d.label) for d in descriptors],
        }

    @mcp.tool()
    async def sound_descriptor_explain(label: str) -> dict[str, Any]:
        """Return the full schema of one descriptor (anchors, aliases, opposite)."""
        d = lookup(label)
        if d is None:
            return {
                "error": f"unknown descriptor: {label!r}",
                "hint": "use sound_descriptors_list to see all known labels + aliases",
            }
        return {"status": "ok", **_descriptor_to_dict(d.label)}

    @mcp.tool()
    async def sound_target_for_description(
        description: str,
        current_audio_path: str | None = None,
        intensity: float = 1.0,
        sr: int = 22050,
    ) -> dict[str, Any]:
        """Translate a free-text description into a target feature delta.

        ``description`` is a free-form phrase like "brighter, with more warmth".
        We parse out every known descriptor (with negation/intensifier cues),
        compute each one's delta against ``current_audio_path`` (if supplied),
        and combine. Returns the per-feature delta dict plus the parsed
        descriptors and, when audio was supplied, a preview of the target
        absolute feature values.
        """
        import librosa

        from ..sound.features import extract_features

        parsed = parse_descriptors(description)
        if not parsed:
            return {
                "status": "no_match",
                "description": description,
                "hint": "no known descriptor or alias found in the description",
            }

        current_features = None
        if current_audio_path:
            p = Path(current_audio_path)
            if not p.exists():
                return {"error": f"file not found: {current_audio_path}"}
            y, sample_rate = librosa.load(str(p), sr=sr, mono=True)
            if y.size == 0:
                return {"error": "empty audio"}
            current_features = extract_features(
                np.asarray(y, dtype=np.float32), sr=int(sample_rate)
            )

        if current_features is not None:
            combined = parse_text_to_combined_delta(
                description, current_features, base_intensity=float(intensity)
            )
        else:
            # No reference audio — synthesise neutral "current" features at the 50th
            # percentile so deltas still convey direction.
            neutral = {
                f: float(REFERENCE_DISTRIBUTIONS.percentile(f, 0.5))
                for f in FEATURE_NAMES
            }
            deltas: list[dict[str, float]] = []
            for label, signed in parsed:
                if signed >= 0:
                    deltas.append(
                        descriptor_to_feature_delta(label, neutral, intensity=float(intensity) * float(signed))
                    )
                else:
                    descriptor = VOCABULARY[label]
                    mag = float(intensity) * float(-signed)
                    if descriptor.opposite and descriptor.opposite in VOCABULARY:
                        deltas.append(
                            descriptor_to_feature_delta(descriptor.opposite, neutral, intensity=mag)
                        )
                    else:
                        d = descriptor_to_feature_delta(label, neutral, intensity=mag)
                        deltas.append({k: -v for k, v in d.items()})
            combined = combine_deltas(deltas)

        target_preview: dict[str, float] | None = None
        if current_features is not None:
            cur_dict = _features_dict(current_features)
            target_preview = {}
            for feature, delta in combined.items():
                base = cur_dict.get(feature, 0.0)
                target_preview[feature] = float(base * (1.0 + float(delta)))

        return {
            "status": "ok",
            "description": description,
            "parsed_descriptors": [
                {"label": label, "signed_intensity": float(signed)} for label, signed in parsed
            ],
            "delta_pct": combined,
            "target_preview": target_preview,
            "current_audio_path": current_audio_path,
        }
