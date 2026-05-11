"""Live version awareness — parse the version Live reports + check capabilities.

Builds on the bridge handshake (PR #11) which reports Live's version
string in ``system.version``. This module adds:

- :class:`LiveVersion` — parsed (major, minor, patch) with comparison
  helpers.
- :func:`get_live_version` — async lookup via the bridge's cached
  ``version_info``. Falls back to ``LiveVersion.UNKNOWN`` if the bridge
  is unreachable (so callers don't crash on AbletonOSC-only setups).
- A small :data:`KNOWN_FEATURES` registry of Live capabilities tied to
  the version they shipped in, plus :func:`requires_at_least` for
  fail-fast guards before tools call into a Live-12-only API.

The intent is INFRASTRUCTURE, not exhaustive feature coverage. Add a
feature flag to the registry when you write a tool that depends on it;
the same pattern catches both forward (Live 12-only features on Live 11
sessions) and backward (Live 11-only API removed in 12) compat.

Real call sites will use this from PRs that introduce Live-version-
dependent behaviour. This commit just lays the rails.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

# Module-level import (rather than local-in-function) so tests can
# monkeypatch the symbol on this module. bridge_client is small enough
# at import time that there's no cost here.
from .bridge_client import AbletonBridgeError, get_bridge_client

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveVersion:
    """Parsed Live version. Compares lexicographically by (major, minor, patch).

    ``major == 0`` denotes the unknown sentinel returned when the bridge
    is unreachable or reports a version Live never shipped (i.e. anything
    we couldn't parse). Capability checks against UNKNOWN return False —
    we don't make optimistic assumptions about an unidentified Live.
    """

    major: int
    minor: int
    patch: int
    raw: str = ""

    @classmethod
    def parse(cls, s: str | None) -> "LiveVersion":
        """Parse a Live version string. Accepts ``"11.3.43"``, ``"12.0"``,
        ``"11"``, etc. Returns :attr:`UNKNOWN` for None or unparseable input."""
        if not s:
            return cls.UNKNOWN
        m = re.match(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(s))
        if not m:
            return cls.UNKNOWN
        major = int(m.group(1))
        minor = int(m.group(2) or 0)
        patch = int(m.group(3) or 0)
        return cls(major=major, minor=minor, patch=patch, raw=str(s))

    @property
    def is_known(self) -> bool:
        return self.major > 0

    def is_at_least(self, major: int, minor: int = 0, patch: int = 0) -> bool:
        """True if ``self >= (major, minor, patch)``. False for UNKNOWN."""
        if not self.is_known:
            return False
        return (self.major, self.minor, self.patch) >= (major, minor, patch)

    def __str__(self) -> str:
        if not self.is_known:
            return "unknown"
        return f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, LiveVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)


# Public sentinel for "we couldn't determine the version". Distinct from
# any real Live version because Live never shipped a 0.x.x release.
LiveVersion.UNKNOWN = LiveVersion(major=0, minor=0, patch=0, raw="")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Feature registry
# ---------------------------------------------------------------------------


# Map of feature name → minimum Live version that ships it. Keep this list
# pragmatic — only add a key when a tool actually depends on it. Each entry
# should link to the relevant code path so future readers can find why it
# was added.
#
# Format: feature_name → (major, minor, patch)
KNOWN_FEATURES: dict[str, tuple[int, int, int]] = {
    # Live 11+ — current baseline. Listed for completeness; bumping the
    # baseline to 11.0 is a no-op for every modern install.
    "follow_actions": (11, 0, 0),
    "mpe_clip_modulation": (11, 0, 0),
    "complex_pro_warp": (11, 0, 0),

    # Live 11.3+ specifics our tooling has assumed (the Roland DUO-CAPTURE
    # EX work in PR #8 targeted 11.3.43; not strictly version-gated but
    # surfaces here so we can validate against pre-11.3 if a user reports
    # weirdness).
    "stable_arrangement_listeners": (11, 2, 0),

    # Live 12+ features. Each one should be paired with a tool that uses
    # `requires_at_least` to gate the call.
    "take_lanes": (12, 0, 0),
    "follow_actions_probability_weighted": (12, 0, 0),
    "modulation_matrix": (12, 0, 0),
    "scale_ui_v2": (12, 0, 0),
}


class LiveVersionTooOld(RuntimeError):
    """Raised when a tool requires a Live version newer than what's running."""


def requires_at_least(
    version: LiveVersion, feature: str, *, raise_on_missing: bool = True,
) -> bool:
    """Check whether ``version`` supports ``feature``.

    Returns True if supported. If not supported and ``raise_on_missing``
    is True (default), raises :class:`LiveVersionTooOld` with an
    actionable message. With ``raise_on_missing=False`` just returns
    False so callers can branch.

    Unknown features (not in :data:`KNOWN_FEATURES`) return True with a
    debug log — we don't want to fail-stop on a typo'd feature name in
    a new tool, and an unrecognised feature is best treated as "trust
    the caller and let Live raise if the LOM disagrees".
    """
    min_ver = KNOWN_FEATURES.get(feature)
    if min_ver is None:
        log.debug(
            "requires_at_least: unknown feature %r — passing through "
            "(add to KNOWN_FEATURES to enforce)", feature,
        )
        return True
    ok = version.is_at_least(*min_ver)
    if ok or not raise_on_missing:
        return ok
    raise LiveVersionTooOld(
        f"Feature {feature!r} requires Live "
        f"{'.'.join(str(x) for x in min_ver)} or newer; running {version}. "
        f"Upgrade Live, or use the documented workaround for this tool."
    )


# ---------------------------------------------------------------------------
# Live version lookup (via the bridge handshake cache)
# ---------------------------------------------------------------------------


async def get_live_version(*, refresh: bool = False) -> LiveVersion:
    """Return the running Live's version, or :attr:`LiveVersion.UNKNOWN`.

    Uses the bridge's cached ``version_info`` (PR #11 handshake). If the
    bridge is unreachable, returns UNKNOWN rather than raising — callers
    that need fail-stop behaviour should compose with ``requires_at_least``
    after.
    """
    try:
        info = await get_bridge_client().version_info(refresh=refresh)
    except AbletonBridgeError:
        return LiveVersion.UNKNOWN
    return LiveVersion.parse(info.get("live_version"))
