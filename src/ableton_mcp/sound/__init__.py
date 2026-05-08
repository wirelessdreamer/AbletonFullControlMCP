"""Phase 3 sound modeling — offline math/data layer.

Pieces:

- ``features`` — librosa-backed timbre feature extractor.
- ``planner`` — sweep-cell generator (grid / Latin Hypercube / random).
- ``dataset`` — sqlite-backed probe dataset (params + feature_vector + optional wav path).
- ``renderer`` — abstract render interface; ``LiveRenderer`` waits for Phase 2,
  ``SynthStubRenderer`` lets us prove the matcher end-to-end without Live.
- ``synth_stub`` — tiny numpy/scipy synth (sine + ADSR + biquad LP + noise) for tests.
- ``matcher`` — kNN over the dataset plus an optional scipy.optimize refinement loop.

Phase 3 ships the offline pieces. Render-in-the-loop against a real Live device
arrives once the Phase 2 audio-capture pipeline lands and ``LiveRenderer`` can
be implemented.
"""

from .dataset import ProbeDataset, ProbeRow
from .features import Features, extract_features, feature_distance, feature_vector
from .matcher import Match, find_nearest, refine
from .planner import SweepPlanner
from .renderer import LiveRenderer, Renderer, SynthStubRenderer
from .synth_stub import SYNTH_STUB_PARAM_RANGES, synth_render

__all__ = [
    "Features",
    "LiveRenderer",
    "Match",
    "ProbeDataset",
    "ProbeRow",
    "Renderer",
    "SYNTH_STUB_PARAM_RANGES",
    "SweepPlanner",
    "SynthStubRenderer",
    "extract_features",
    "feature_distance",
    "feature_vector",
    "find_nearest",
    "refine",
    "synth_render",
]
