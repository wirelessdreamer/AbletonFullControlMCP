"""Apply target features to a probe dataset and (optionally) push to a Live device.

Two layers:

- :func:`find_params_matching_target` — wraps
  :func:`ableton_mcp.sound.matcher.find_nearest`. Takes a target feature
  vector (or feature dict) and a path to an on-disk probe dataset, returns
  the top-k :class:`Match` rows.
- :func:`apply_to_live_device` — best-effort: looks up the device's
  parameter list via OSC, matches names case-insensitively, and pushes the
  numeric values. Mirrors the helper in
  :func:`ableton_mcp.tools.sound_modeling._push_params_via_osc` but lives
  here so the shaping tool can call it independently. Graceful failure: if
  OSC isn't reachable / the request times out we return an error dict
  rather than raising.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..sound.dataset import ProbeDataset
from ..sound.features import FEATURE_VECTOR_DIM, FEATURE_VECTOR_NAMES, Features, feature_vector
from ..sound.matcher import Match, find_nearest

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# kNN against a saved dataset
# ---------------------------------------------------------------------------


def _coerce_target_vector(
    target: Features | np.ndarray | Mapping[str, float],
) -> np.ndarray:
    """Turn whatever the caller passed into a (FEATURE_VECTOR_DIM,) float32 array."""
    if isinstance(target, Features):
        return feature_vector(target)
    if isinstance(target, np.ndarray):
        arr = np.asarray(target, dtype=np.float32).ravel()
        if arr.shape[0] != FEATURE_VECTOR_DIM:
            raise ValueError(
                f"target vector must have length {FEATURE_VECTOR_DIM}, got {arr.shape[0]}"
            )
        return arr
    if isinstance(target, Mapping):
        out = np.zeros(FEATURE_VECTOR_DIM, dtype=np.float32)
        for i, name in enumerate(FEATURE_VECTOR_NAMES):
            if name in target:
                out[i] = float(target[name])
        return out
    raise TypeError(f"unsupported target type: {type(target)!r}")


def find_params_matching_target(
    target_features: Features | np.ndarray | Mapping[str, float],
    dataset_path: str | Path,
    *,
    k: int = 5,
    device_id: str | None = None,
    metric: str = "cosine",
) -> list[Match]:
    """kNN lookup against a saved probe dataset.

    Returns the top-``k`` :class:`Match` rows (already-instantiated, so the
    caller can call ``.to_dict()`` for the final response).
    """
    ds_path = Path(dataset_path)
    if not ds_path.exists():
        raise FileNotFoundError(f"dataset not found: {ds_path}")

    target_vec = _coerce_target_vector(target_features)
    ds = ProbeDataset.load(ds_path, device_id=device_id)
    try:
        return find_nearest(target_vec, ds, k=int(k), device_id=device_id, metric=metric)
    finally:
        ds.close()


# ---------------------------------------------------------------------------
# Apply to a real Live device
# ---------------------------------------------------------------------------


async def _apply_async(
    track_index: int, device_index: int, params: Mapping[str, float]
) -> dict[str, Any]:
    """Resolve param names → indices via OSC, push the values."""
    from ..osc_client import get_client  # local: keeps OSC import out of pure-math callers

    client = await get_client()
    reply = await client.request(
        "/live/device/get/parameters/name", int(track_index), int(device_index)
    )
    # AbletonOSC echoes the LOM selectors (track, device) before the data.
    names = list(reply[2:])
    lowered = [str(n).strip().lower() for n in names]
    applied: list[dict[str, Any]] = []
    unmatched: list[str] = []
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


def apply_to_live_device(
    track_index: int,
    device_index: int,
    params: Mapping[str, float],
) -> dict[str, Any]:
    """Push name-keyed params to a Live device. Graceful on OSC failure.

    Returns ``{"status": "ok", "applied": [...], "unmatched": [...]}`` on
    success, or ``{"status": "error", "error": "..."}`` if OSC isn't reachable.
    Never raises.
    """
    coro = _apply_async(int(track_index), int(device_index), dict(params))
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # We're inside an async context (e.g. a FastMCP tool handler). The
            # caller is responsible for awaiting; return an awaitable so they
            # can `await apply_to_live_device(...)`.
            future = asyncio.ensure_future(coro)
            return future  # type: ignore[return-value]
        result = asyncio.run(coro)
        return {"status": "ok", **result}
    except Exception as exc:  # OSC timeout, connection refused, etc.
        log.info("apply_to_live_device failed: %r", exc)
        return {
            "status": "error",
            "error": repr(exc),
            "track_index": int(track_index),
            "device_index": int(device_index),
            "params": {k: float(v) for k, v in params.items()},
        }


async def apply_to_live_device_async(
    track_index: int,
    device_index: int,
    params: Mapping[str, float],
) -> dict[str, Any]:
    """Async-friendly wrapper that always returns a result dict (never raises)."""
    try:
        result = await _apply_async(int(track_index), int(device_index), dict(params))
        return {"status": "ok", **result}
    except Exception as exc:  # OSC timeout, connection refused, etc.
        log.info("apply_to_live_device_async failed: %r", exc)
        return {
            "status": "error",
            "error": repr(exc),
            "track_index": int(track_index),
            "device_index": int(device_index),
            "params": {k: float(v) for k, v in params.items()},
        }
