"""Tests for the bridge version handshake.

Covers the three cases the handshake exists to make legible:

1. Bridge is current → no warning, ``require_handler`` is a no-op.
2. Bridge is outdated but still major-compatible → warning logged,
   ``require_handler`` raises for missing ops, allowed ops pass.
3. Bridge predates the handshake (reports empty handler list) →
   ``require_handler`` is a no-op (we can't verify; let the call fail
   naturally on dispatch).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from ableton_mcp.bridge_client import (
    EXPECTED_BRIDGE_VERSION,
    AbletonBridgeClient,
    AbletonBridgeError,
    AbletonBridgeOutdated,
    _parse_semver,
)


# ---------------------------------------------------------------------------
# _parse_semver
# ---------------------------------------------------------------------------


def test_parse_semver_canonical() -> None:
    assert _parse_semver("1.2.3") == (1, 2, 3)


def test_parse_semver_short_forms() -> None:
    assert _parse_semver("1.2") == (1, 2, 0)
    assert _parse_semver("1") == (1, 0, 0)


def test_parse_semver_strips_prerelease_and_build_metadata() -> None:
    assert _parse_semver("1.2.3-rc1") == (1, 2, 3)
    assert _parse_semver("1.2.3+build7") == (1, 2, 3)


def test_parse_semver_garbage_returns_zeros() -> None:
    assert _parse_semver("not-a-version") == (0, 0, 0)
    assert _parse_semver("") == (0, 0, 0)


# ---------------------------------------------------------------------------
# version_info caching + logging
# ---------------------------------------------------------------------------


def _client_with_stubbed_call(reply: dict[str, Any]) -> AbletonBridgeClient:
    """Build a client whose ``call("system.version", ...)`` returns ``reply``."""
    client = AbletonBridgeClient()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(op: str, timeout: float | None = None, **args: Any) -> Any:
        calls.append((op, dict(args)))
        if op == "system.version":
            return reply
        raise AssertionError(f"unexpected op: {op}")

    client.call = fake_call  # type: ignore[assignment]
    client._stubbed_calls = calls  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_version_info_caches_after_first_call() -> None:
    """Second call shouldn't re-issue system.version."""
    client = _client_with_stubbed_call({
        "bridge_version": EXPECTED_BRIDGE_VERSION,
        "live_version": "11.3.43",
        "handlers": ["track.freeze", "track.is_frozen"],
    })
    a = await client.version_info()
    b = await client.version_info()
    assert a is b  # same cached object
    assert len(client._stubbed_calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_version_info_refresh_forces_requery() -> None:
    client = _client_with_stubbed_call({
        "bridge_version": EXPECTED_BRIDGE_VERSION,
        "live_version": "11.3.43",
        "handlers": [],
    })
    await client.version_info()
    await client.version_info(refresh=True)
    assert len(client._stubbed_calls) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_version_info_marks_compatible_when_major_matches() -> None:
    client = _client_with_stubbed_call({
        "bridge_version": EXPECTED_BRIDGE_VERSION,
        "live_version": "11.3.43",
        "handlers": ["track.is_frozen"],
    })
    info = await client.version_info()
    assert info["compatible"] is True
    assert info["outdated"] is False
    assert info["expected_bridge_version"] == EXPECTED_BRIDGE_VERSION


@pytest.mark.asyncio
async def test_version_info_marks_outdated_when_bridge_minor_is_lower(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Compose an older minor that's still the same major.
    expected_major = _parse_semver(EXPECTED_BRIDGE_VERSION)[0]
    older = f"{expected_major}.0.0"
    client = _client_with_stubbed_call({
        "bridge_version": older,
        "live_version": "11.3.43",
        "handlers": ["track.freeze"],  # no is_frozen → outdated
    })
    with caplog.at_level(logging.WARNING):
        info = await client.version_info()
    assert info["compatible"] is True
    assert info["outdated"] is True
    assert any("outdated" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_version_info_marks_incompatible_on_major_mismatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expected_major = _parse_semver(EXPECTED_BRIDGE_VERSION)[0]
    future = f"{expected_major + 1}.0.0"
    client = _client_with_stubbed_call({
        "bridge_version": future,
        "live_version": "11.3.43",
        "handlers": ["track.freeze"],
    })
    with caplog.at_level(logging.ERROR):
        info = await client.version_info()
    assert info["compatible"] is False
    assert any("major version mismatch" in rec.message.lower()
               for rec in caplog.records)


@pytest.mark.asyncio
async def test_version_info_logs_warning_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expected_major = _parse_semver(EXPECTED_BRIDGE_VERSION)[0]
    older = f"{expected_major}.0.0"
    client = _client_with_stubbed_call({
        "bridge_version": older,
        "live_version": "11.3.43",
        "handlers": [],
    })
    with caplog.at_level(logging.WARNING):
        await client.version_info()
        await client.version_info(refresh=True)
        await client.version_info(refresh=True)
    outdated_records = [r for r in caplog.records if "outdated" in r.message.lower()]
    assert len(outdated_records) == 1  # only on first call


@pytest.mark.asyncio
async def test_version_info_propagates_call_errors() -> None:
    client = AbletonBridgeClient()

    async def boom(op: str, timeout: float | None = None, **_args: Any) -> Any:
        raise AbletonBridgeError("simulated bridge failure")

    client.call = boom  # type: ignore[assignment]
    with pytest.raises(AbletonBridgeError):
        await client.version_info()


# ---------------------------------------------------------------------------
# require_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_handler_passes_for_known_op() -> None:
    client = _client_with_stubbed_call({
        "bridge_version": EXPECTED_BRIDGE_VERSION,
        "live_version": "11.3.43",
        "handlers": ["track.freeze", "track.is_frozen"],
    })
    # Should NOT raise.
    await client.require_handler("track.is_frozen")


@pytest.mark.asyncio
async def test_require_handler_raises_outdated_for_unknown_op() -> None:
    client = _client_with_stubbed_call({
        "bridge_version": "1.0.0",  # before is_frozen was added
        "live_version": "11.3.43",
        "handlers": ["track.freeze"],
    })
    with pytest.raises(AbletonBridgeOutdated) as excinfo:
        await client.require_handler("track.is_frozen")
    msg = str(excinfo.value)
    assert "track.is_frozen" in msg
    assert "install_bridge" in msg  # actionable hint


@pytest.mark.asyncio
async def test_require_handler_noop_when_bridge_predates_handshake() -> None:
    """Bridges built before the handshake report empty handler lists. We
    can't verify the op exists, so we let the actual call surface any
    failure rather than blocking the caller."""
    client = _client_with_stubbed_call({
        "bridge_version": "0.0.0",
        "live_version": "11.3.43",
        "handlers": [],
    })
    # Should NOT raise even for an op that doesn't exist on the old bridge.
    await client.require_handler("track.is_frozen")


@pytest.mark.asyncio
async def test_require_handler_swallows_call_errors() -> None:
    """If the version query itself fails (bridge unreachable etc.), let
    the original call show the error to the user — don't double-fail."""
    client = AbletonBridgeClient()

    async def boom(op: str, timeout: float | None = None, **_args: Any) -> Any:
        raise AbletonBridgeError("bridge down")

    client.call = boom  # type: ignore[assignment]
    # Should NOT raise — version query failed, we just don't enforce.
    await client.require_handler("track.is_frozen")


@pytest.mark.asyncio
async def test_expected_version_constant_is_stringy() -> None:
    """Smoke check that the constant the client ships with parses cleanly."""
    parts = _parse_semver(EXPECTED_BRIDGE_VERSION)
    assert parts != (0, 0, 0), "EXPECTED_BRIDGE_VERSION failed to parse"
