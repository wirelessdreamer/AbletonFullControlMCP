"""Match a target sound against a probe dataset, with optional refinement.

Two entry points:

- :func:`find_nearest`: cosine kNN over the dataset's feature matrix. Cheap.
- :func:`refine`: starting from a candidate param dict, run scipy.optimize over
  the renderer's param ranges to minimise the feature distance to the target.
  Stays inside ``param_ranges`` and bails out when ``max_iter`` is hit.

Cosine distance is the default for kNN — it is robust to overall amplitude
differences that ``RMS`` already encodes separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

import numpy as np

from .dataset import ProbeDataset
from .features import (
    FEATURE_VECTOR_DIM,
    Features,
    extract_features,
    feature_distance,
    feature_vector,
)

RenderFn = Callable[[Mapping[str, float]], np.ndarray]


@dataclass(frozen=True)
class Match:
    """One kNN candidate for a target."""

    rank: int
    probe_id: int
    device_id: str
    params: dict[str, float]
    distance: float
    audio_path: str | None = None
    feature_vector: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))

    def to_dict(self) -> dict:
        return {
            "rank": int(self.rank),
            "probe_id": int(self.probe_id),
            "device_id": self.device_id,
            "params": dict(self.params),
            "distance": float(self.distance),
            "audio_path": self.audio_path,
        }


def _cosine_distance_matrix(target: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """Cosine distance from one vector to every row of a matrix."""
    if mat.size == 0:
        return np.zeros(0, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64).ravel()
    mat = np.asarray(mat, dtype=np.float64)
    target_norm = float(np.linalg.norm(target))
    row_norms = np.linalg.norm(mat, axis=1)
    denom = row_norms * target_norm
    safe = denom > 1e-12
    sims = np.zeros(mat.shape[0], dtype=np.float64)
    sims[safe] = (mat[safe] @ target) / denom[safe]
    return 1.0 - sims


def find_nearest(
    target: Features | np.ndarray,
    dataset: ProbeDataset,
    k: int = 5,
    *,
    device_id: str | None = None,
    metric: str = "cosine",
) -> list[Match]:
    """Return the top-``k`` matches for ``target`` against rows of ``dataset``.

    ``target`` may be a :class:`Features` (we flatten it) or an already-flattened
    feature vector of length ``FEATURE_VECTOR_DIM``.
    """
    if isinstance(target, Features):
        target_vec = feature_vector(target)
    else:
        target_vec = np.asarray(target, dtype=np.float32).ravel()
    if target_vec.shape[0] != FEATURE_VECTOR_DIM:
        raise ValueError(
            f"target feature vector dim {target_vec.shape[0]} != {FEATURE_VECTOR_DIM}"
        )

    rows = list(dataset.iter_rows(device_id=device_id))
    if not rows:
        return []

    mat = np.stack([r.feature_vector for r in rows], axis=0)
    if metric == "cosine":
        dists = _cosine_distance_matrix(target_vec, mat)
    elif metric == "euclidean":
        dists = np.linalg.norm(mat.astype(np.float64) - target_vec.astype(np.float64), axis=1)
    else:
        raise ValueError(f"unknown metric: {metric!r}")

    k = max(1, min(int(k), len(rows)))
    order = np.argsort(dists)[:k]
    return [
        Match(
            rank=i,
            probe_id=rows[idx].probe_id,
            device_id=rows[idx].device_id,
            params=dict(rows[idx].params),
            distance=float(dists[idx]),
            audio_path=rows[idx].audio_path,
            feature_vector=rows[idx].feature_vector,
        )
        for i, idx in enumerate(order)
    ]


def _params_to_vec(params: Mapping[str, float], names: list[str]) -> np.ndarray:
    return np.array([float(params[n]) for n in names], dtype=np.float64)


def _vec_to_params(vec: np.ndarray, names: list[str]) -> dict[str, float]:
    return {n: float(vec[i]) for i, n in enumerate(names)}


def refine(
    initial_params: Mapping[str, float],
    target_features: Features,
    render_fn: RenderFn,
    *,
    param_ranges: Mapping[str, tuple[float, float]],
    sample_rate: int = 22050,
    max_iter: int = 20,
    method: str = "Powell",
    metric: str = "cosine",
) -> dict:
    """Locally optimise ``initial_params`` to minimise distance to ``target_features``.

    Uses ``scipy.optimize.minimize`` with explicit bounds. Defaults to Powell
    (no gradient — feature extraction is non-smooth) but the caller may pass
    any compatible method.

    Returns ``{"best_params", "best_distance", "n_evaluations", "converged",
    "history"}``. ``render_fn`` is the renderer's ``render`` method (taking a
    param dict and returning audio).
    """
    from scipy.optimize import minimize

    names = list(param_ranges.keys())
    if not names:
        raise ValueError("param_ranges must not be empty")
    bounds = [(float(param_ranges[n][0]), float(param_ranges[n][1])) for n in names]

    target_vec = feature_vector(target_features).astype(np.float64)

    history: list[dict] = []

    def loss(vec: np.ndarray) -> float:
        params = _vec_to_params(vec, names)
        # Clamp defensively — some methods (e.g. Powell) overshoot the bounds.
        for i, name in enumerate(names):
            lo, hi = bounds[i]
            params[name] = float(np.clip(params[name], lo, hi))
        audio = render_fn(params)
        feats = extract_features(np.asarray(audio, dtype=np.float32), sr=sample_rate)
        d = feature_distance(target_vec, feature_vector(feats), metric=metric)
        history.append({"params": params, "distance": float(d)})
        return float(d)

    x0 = _params_to_vec(initial_params, names)
    # Clamp initial guess into bounds.
    for i, (lo, hi) in enumerate(bounds):
        x0[i] = float(np.clip(x0[i], lo, hi))

    try:
        result = minimize(
            loss,
            x0,
            method=method,
            bounds=bounds,
            options={"maxiter": int(max_iter), "maxfev": int(max_iter), "xtol": 1e-3, "ftol": 1e-4},
        )
    except (TypeError, ValueError):
        # Some methods (Powell on older scipy) reject ``bounds=`` — retry without.
        result = minimize(
            loss,
            x0,
            method=method,
            options={"maxiter": int(max_iter), "maxfev": int(max_iter), "xtol": 1e-3, "ftol": 1e-4},
        )

    best_x = np.array(result.x, dtype=np.float64)
    for i, (lo, hi) in enumerate(bounds):
        best_x[i] = float(np.clip(best_x[i], lo, hi))
    best_params = _vec_to_params(best_x, names)
    best_distance = float(result.fun)

    return {
        "best_params": best_params,
        "best_distance": best_distance,
        "n_evaluations": int(getattr(result, "nfev", len(history))),
        "converged": bool(getattr(result, "success", False)),
        "method": method,
        "history": history[-min(len(history), 50):],  # cap to last 50 evals
    }
