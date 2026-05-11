"""Tests for ``ableton_mcp.live_version`` — version parsing + capability checks."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from ableton_mcp.live_version import (
    KNOWN_FEATURES,
    LiveVersion,
    LiveVersionTooOld,
    get_live_version,
    requires_at_least,
)


# ---------------------------------------------------------------------------
# LiveVersion.parse
# ---------------------------------------------------------------------------


def test_parse_canonical_three_part_version() -> None:
    v = LiveVersion.parse("11.3.43")
    assert v.major == 11
    assert v.minor == 3
    assert v.patch == 43
    assert v.is_known


def test_parse_two_part_version_zero_pads_patch() -> None:
    v = LiveVersion.parse("12.0")
    assert (v.major, v.minor, v.patch) == (12, 0, 0)


def test_parse_one_part_version_zero_pads_minor_and_patch() -> None:
    v = LiveVersion.parse("11")
    assert (v.major, v.minor, v.patch) == (11, 0, 0)


def test_parse_trailing_suffix_is_ignored() -> None:
    """Live 12 beta builds report things like '12.0.0b1' — we accept the
    numeric prefix and ignore the rest."""
    v = LiveVersion.parse("12.0.0b1")
    assert (v.major, v.minor, v.patch) == (12, 0, 0)


def test_parse_none_returns_unknown() -> None:
    assert LiveVersion.parse(None) == LiveVersion.UNKNOWN


def test_parse_empty_returns_unknown() -> None:
    assert LiveVersion.parse("") == LiveVersion.UNKNOWN


def test_parse_garbage_returns_unknown() -> None:
    assert LiveVersion.parse("not-a-version") == LiveVersion.UNKNOWN


def test_unknown_is_not_known() -> None:
    assert LiveVersion.UNKNOWN.is_known is False
    assert str(LiveVersion.UNKNOWN) == "unknown"


def test_is_at_least_handles_each_field() -> None:
    v = LiveVersion.parse("11.3.43")
    assert v.is_at_least(11)
    assert v.is_at_least(11, 3)
    assert v.is_at_least(11, 3, 43)
    assert not v.is_at_least(11, 4)
    assert not v.is_at_least(12)
    assert not v.is_at_least(11, 3, 44)


def test_is_at_least_returns_false_for_unknown() -> None:
    """Unknown version should NOT pass any capability check — we don't
    optimistically assume a Live we can't identify supports anything."""
    assert not LiveVersion.UNKNOWN.is_at_least(1)
    assert not LiveVersion.UNKNOWN.is_at_least(11)


def test_comparison_operators() -> None:
    v1 = LiveVersion.parse("11.3.0")
    v2 = LiveVersion.parse("11.3.43")
    v3 = LiveVersion.parse("12.0.0")
    assert v1 < v2 < v3


def test_str_format() -> None:
    v = LiveVersion.parse("11.3.43")
    assert str(v) == "11.3.43"


# ---------------------------------------------------------------------------
# requires_at_least
# ---------------------------------------------------------------------------


def test_requires_at_least_passes_for_supported_feature() -> None:
    v = LiveVersion.parse("12.0.0")
    assert requires_at_least(v, "take_lanes") is True


def test_requires_at_least_raises_when_version_too_old() -> None:
    v = LiveVersion.parse("11.3.43")
    with pytest.raises(LiveVersionTooOld) as excinfo:
        requires_at_least(v, "take_lanes")
    msg = str(excinfo.value)
    assert "take_lanes" in msg
    assert "12" in msg  # mentions the required version
    assert "Upgrade" in msg


def test_requires_at_least_returns_false_when_raise_disabled() -> None:
    """raise_on_missing=False lets callers branch instead of fail-stop."""
    v = LiveVersion.parse("11.3.43")
    assert requires_at_least(v, "take_lanes", raise_on_missing=False) is False


def test_requires_at_least_unknown_feature_passes_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Typo'd or new features (not in KNOWN_FEATURES) should pass through
    with a debug log rather than blocking the caller."""
    v = LiveVersion.parse("11.3.43")
    with caplog.at_level(logging.DEBUG):
        ok = requires_at_least(v, "some_future_feature_not_in_registry")
    assert ok is True


def test_requires_at_least_unknown_version_fails_known_feature() -> None:
    """When Live's version is UNKNOWN, even baseline features fail —
    we don't trust an unidentified Live."""
    with pytest.raises(LiveVersionTooOld):
        requires_at_least(LiveVersion.UNKNOWN, "follow_actions")


def test_known_features_dict_has_expected_entries() -> None:
    """Sanity check that the registry covers the features mentioned in
    the module docstring."""
    expected = {
        "follow_actions", "complex_pro_warp", "take_lanes",
        "follow_actions_probability_weighted", "modulation_matrix",
        "scale_ui_v2",
    }
    assert expected.issubset(set(KNOWN_FEATURES.keys()))


# ---------------------------------------------------------------------------
# get_live_version
# ---------------------------------------------------------------------------


class _FakeBridge:
    def __init__(self, version_info: dict[str, Any] | None = None,
                 raises: bool = False) -> None:
        self._info = version_info
        self._raises = raises

    async def version_info(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._raises:
            from ableton_mcp.bridge_client import AbletonBridgeError
            raise AbletonBridgeError("simulated bridge down")
        return self._info or {}


@pytest.mark.asyncio
async def test_get_live_version_returns_parsed_from_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _FakeBridge({"live_version": "12.0.5"})
    monkeypatch.setattr(
        "ableton_mcp.live_version.get_bridge_client", lambda: bridge,
    )
    v = await get_live_version()
    assert (v.major, v.minor, v.patch) == (12, 0, 5)


@pytest.mark.asyncio
async def test_get_live_version_returns_unknown_when_bridge_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _FakeBridge(raises=True)
    monkeypatch.setattr(
        "ableton_mcp.live_version.get_bridge_client", lambda: bridge,
    )
    v = await get_live_version()
    assert v == LiveVersion.UNKNOWN


@pytest.mark.asyncio
async def test_get_live_version_returns_unknown_for_missing_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the bridge returns version_info but no live_version key, we
    treat it as unknown (Live didn't report; safer than guessing)."""
    bridge = _FakeBridge({"bridge_version": "1.3.0"})  # no live_version
    monkeypatch.setattr(
        "ableton_mcp.live_version.get_bridge_client", lambda: bridge,
    )
    v = await get_live_version()
    assert v == LiveVersion.UNKNOWN
