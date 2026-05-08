"""Semantic vocabulary mapping NL sound descriptors to audio-feature predicates.

Public surface:

- :class:`Descriptor` — one descriptor's full schema (label, aliases,
  category, anchors, opposite, etc.).
- :data:`VOCABULARY` — canonical dict, keyed by label.
- :func:`describe_features` — features → ranked descriptors.
- :func:`descriptor_to_feature_delta` — descriptor → per-feature relative shifts.
- :func:`combine_deltas` — merge several deltas (e.g. "brighter and punchier").

The module is the bridge between free-text shaping requests and the
quantitative feature deltas the rest of the engine speaks. See
:mod:`ableton_mcp.tools.semantics` for the MCP tool layer on top.
"""

from .describer import describe_features
from .reference_distributions import (
    DEFAULT as REFERENCE_DISTRIBUTIONS,
    ReferenceDistributions,
)
from .transforms import (
    combine_deltas,
    descriptor_to_feature_delta,
    parse_descriptors,
    parse_text_to_combined_delta,
)
from .vocabulary import (
    Category,
    Descriptor,
    FEATURE_NAMES,
    FeatureAnchor,
    VOCABULARY,
    descriptors_in_category,
    lookup,
)

__all__ = [
    "Category",
    "Descriptor",
    "FEATURE_NAMES",
    "FeatureAnchor",
    "REFERENCE_DISTRIBUTIONS",
    "ReferenceDistributions",
    "VOCABULARY",
    "combine_deltas",
    "describe_features",
    "descriptor_to_feature_delta",
    "descriptors_in_category",
    "lookup",
    "parse_descriptors",
    "parse_text_to_combined_delta",
]
