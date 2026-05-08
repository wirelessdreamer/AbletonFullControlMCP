"""Auto-discovery of presets via KMeans clustering on a probe dataset.

Pipeline:

1. Load a :class:`ableton_mcp.sound.ProbeDataset` (params + feature vectors).
2. KMeans cluster in feature-vector space (sklearn).
3. For each cluster pick the row whose feature vector is closest to the
   centroid — that row's params become a discovered preset.
4. Auto-name the preset from cluster index + dominant features
   (``"Cluster 3 — bright, plucky"``).
5. Tag with descriptors. If :mod:`ableton_mcp.semantics` is shipped and
   exposes a ``describe(features)`` callable, defer to that. Otherwise
   fall back to hardcoded heuristics on the feature vector.

The discovered presets are appended to the preset library with
``source='discovered'``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

import numpy as np

from ..sound import ProbeDataset
from ..sound.features import FEATURE_VECTOR_NAMES
from .library import Preset
from .storage import add_preset


# ---------------------------------------------------------------------------
# Feature-vector heuristics — used when semantics module isn't available.
# Indices below align with sound.features.FEATURE_VECTOR_NAMES:
#   0..12  mfcc_mean_*
#   13..25 mfcc_std_*
#   26     spectral_centroid    (Hz)
#   27     spectral_bandwidth   (Hz)
#   28     spectral_rolloff     (Hz)
#   29     zcr                  (0..1)
#   30     rms                  (0..1ish)
#   31     spectral_flatness    (0..1)
# ---------------------------------------------------------------------------

_IDX_CENTROID = 26
_IDX_BANDWIDTH = 27
_IDX_ROLLOFF = 28
_IDX_ZCR = 29
_IDX_RMS = 30
_IDX_FLATNESS = 31


def _semantics_describe(vec: np.ndarray) -> list[str] | None:
    """Try the optional semantics module; return None if it's not shipped."""
    try:
        from ..semantics import describe  # type: ignore[attr-defined]
    except Exception:
        return None
    try:
        result = describe(vec)
    except Exception:
        return None
    if not result:
        return None
    if isinstance(result, str):
        return [result]
    if isinstance(result, (list, tuple)):
        return [str(x) for x in result if x]
    return None


def _heuristic_descriptors(vec: np.ndarray) -> list[str]:
    """Fallback tag list derived from the spectral block of the vector.

    Thresholds are intentionally generous — they're meant to label a cluster
    with one or two leading qualities, not to be precise.
    """
    vec = np.asarray(vec, dtype=np.float32).ravel()
    if vec.shape[0] < 32:  # safety
        return []
    centroid = float(vec[_IDX_CENTROID])
    bandwidth = float(vec[_IDX_BANDWIDTH])
    rolloff = float(vec[_IDX_ROLLOFF])
    zcr = float(vec[_IDX_ZCR])
    rms = float(vec[_IDX_RMS])
    flatness = float(vec[_IDX_FLATNESS])

    tags: list[str] = []

    # Brightness from centroid (Hz). 22050 Nyquist; centroids ~3000+ are bright.
    if centroid >= 3000.0:
        tags.append("bright")
    elif centroid >= 1500.0:
        tags.append("mid")
    elif centroid > 0.0:
        tags.append("dark")

    # Width from bandwidth.
    if bandwidth >= 2500.0:
        tags.append("wide")
    elif bandwidth > 0.0 and bandwidth < 800.0:
        tags.append("narrow")

    # Rolloff.
    if rolloff >= 6000.0:
        tags.append("airy")
    elif rolloff > 0.0 and rolloff < 1500.0:
        tags.append("muffled")

    # Noisiness.
    if flatness >= 0.25 or zcr >= 0.25:
        tags.append("noisy")
    elif zcr <= 0.04 and flatness <= 0.05:
        tags.append("tonal")

    # Loudness / energy.
    if rms >= 0.25:
        tags.append("loud")
    elif rms > 0 and rms < 0.05:
        tags.append("quiet")

    # MFCC2 polarity ~ tilt: positive often = brighter / more high content.
    # We keep this conservative; we already have centroid-based tags.

    # If we ended up with nothing (e.g. silent cluster) fall back to neutral.
    if not tags:
        tags.append("neutral")
    return tags


def _descriptor_for_centroid(centroid_vec: np.ndarray) -> list[str]:
    """Top descriptors for a cluster centroid: prefer semantics, fall back to heuristics."""
    sem = _semantics_describe(centroid_vec)
    if sem:
        return sem
    return _heuristic_descriptors(centroid_vec)


def _build_name(cluster_index: int, descriptors: Sequence[str]) -> str:
    """Compose ``"Cluster N — desc1, desc2"`` (en-dash for parsing-friendliness)."""
    if descriptors:
        joined = ", ".join(list(descriptors)[:2])
        return f"Cluster {cluster_index} - {joined}"
    return f"Cluster {cluster_index}"


def discover_presets_from_dataset(
    dataset_path: str | os.PathLike,
    k: int = 8,
    *,
    device_id: str | None = None,
    db_path: str | os.PathLike | None = None,
    name_prefix: str | None = None,
    persist: bool = True,
    seed: int | None = 0,
) -> list[Preset]:
    """Cluster a probe dataset and return one :class:`Preset` per cluster.

    Parameters
    ----------
    dataset_path
        Path to a sqlite probe DB previously built by ``sound_probe_device``.
    k
        Number of clusters / discovered presets. Capped to dataset size.
    device_id
        Optional filter — only rows for this device_id are clustered.
        ``None`` clusters every row in the dataset.
    db_path
        Where to insert the discovered rows. ``None`` uses the default
        preset library DB.
    name_prefix
        Optional name prefix (e.g. ``"FM"``) — useful when discovering
        presets across multiple synths to avoid name collisions.
    persist
        If True (default), discovered presets are inserted via :func:`add_preset`.
        Tests can pass ``False`` to inspect without writing.
    seed
        KMeans random_state. ``None`` lets sklearn pick.
    """
    from sklearn.cluster import KMeans  # local import: heavy

    ds_path = Path(dataset_path)
    if not ds_path.exists():
        raise FileNotFoundError(f"probe dataset not found: {ds_path}")

    # Load all rows into a (params_list, feature_matrix) pair.
    ds = ProbeDataset.load(ds_path, device_id=device_id)
    try:
        params_list, mat = ds.to_numpy(device_id=device_id)
        # We also need the underlying device_id of the rows we picked, so iterate.
        rows = list(ds.iter_rows(device_id=device_id))
    finally:
        ds.close()

    if mat.shape[0] == 0:
        return []

    n_samples = int(mat.shape[0])
    k_eff = max(1, min(int(k), n_samples))

    # KMeans on feature vectors. Use n_init=10 for reproducibility independence
    # on tiny datasets where one init can land in a bad local min.
    km = KMeans(n_clusters=k_eff, n_init=10, random_state=seed)
    labels = km.fit_predict(mat.astype(np.float64, copy=False))
    centroids = km.cluster_centers_

    discovered: list[Preset] = []
    for cluster_idx in range(k_eff):
        members_mask = labels == cluster_idx
        members_indices = np.flatnonzero(members_mask)
        if members_indices.size == 0:
            continue
        # Pick the cluster member closest to the centroid.
        diffs = mat[members_indices].astype(np.float64) - centroids[cluster_idx]
        dists = np.linalg.norm(diffs, axis=1)
        winner_local = int(np.argmin(dists))
        winner_idx = int(members_indices[winner_local])

        winner_row = rows[winner_idx]
        winner_params = {k_: float(v) for k_, v in winner_row.params.items()}
        winner_device = winner_row.device_id

        # Centroid descriptors → tags + name.
        descriptors = _descriptor_for_centroid(centroids[cluster_idx])
        tags = list(descriptors[:3])
        if winner_device:
            tags.append(winner_device)
        # De-dup while preserving order.
        seen: set[str] = set()
        tags = [t for t in tags if not (t in seen or seen.add(t))]

        base_name = _build_name(cluster_idx, descriptors)
        name = f"{name_prefix} {base_name}" if name_prefix else base_name

        # If the same name already exists in the library, suffix with the
        # device_id and cluster size to disambiguate.
        if persist:
            from .storage import find_by_name

            attempt = name
            suffix = 1
            while find_by_name(attempt, db_path=db_path) is not None:
                suffix += 1
                attempt = f"{name} ({suffix})"
            name = attempt

        description = (
            f"Auto-discovered cluster of {int(members_indices.size)} probe(s) "
            f"from device '{winner_device}' centred on: "
            + ", ".join(descriptors[:3])
        )

        preset = Preset(
            name=name,
            device_class=winner_device,
            params=winner_params,
            tags=tags,
            description=description,
            source="discovered",
        )

        if persist:
            feat_blob = winner_row.feature_vector.astype(np.float32).tobytes()
            add_preset(preset, db_path=db_path, feature_vector=feat_blob)

        discovered.append(preset)

    return discovered


__all__ = ["discover_presets_from_dataset"]
