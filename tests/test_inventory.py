"""Tests for the bulk-inventory pipeline.

We don't depend on a running Ableton: a small fake bridge server (mirroring
``tests/test_bridge_client.py``) supplies a synthetic browser tree and a
fake OSC client supplies synthetic device parameters. Coverage:

* scanner walks the synthetic tree and flattens it correctly
* loader runs create→load→introspect→delete in the right order even when
  introspection fails (the temp track must still be deleted)
* loader returns a populated InstrumentSnapshot on the happy path
* matcher categorises full / partial / unknown coverage correctly
* manifest round-trips through JSON without losing fields
* MCP tools register on a FastMCP instance
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

import pytest

from ableton_mcp import bridge_client
from ableton_mcp.bridge_client import AbletonBridgeClient, BridgeConfig
from ableton_mcp.inventory import (
    BrowserItem,
    InstrumentSnapshot,
    Manifest,
    build_coverage_summary,
    load_and_introspect,
    match_to_schemas,
    scan_browser,
)
from ableton_mcp.inventory.loader import PROBE_TRACK_NAME


# --------------------------------------------------------------------------
# Fake bridge — mirrors tests/test_bridge_client.py
# --------------------------------------------------------------------------


HandlerFn = Callable[[dict[str, Any]], Any]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeBridge:
    def __init__(self, handlers: dict[str, HandlerFn]) -> None:
        self.handlers = handlers
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode("utf-8"))
            self.requests.append(req)
            op = req.get("op")
            handler = self.handlers.get(op)
            if handler is None:
                resp = {"id": req.get("id"), "ok": False, "error": f"unknown op: {op}"}
            else:
                try:
                    result = handler(req.get("args") or {})
                    resp = {"id": req.get("id"), "ok": True, "result": result}
                except Exception as exc:  # noqa: BLE001
                    resp = {
                        "id": req.get("id"),
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


@contextlib.asynccontextmanager
async def _bridge(
    handlers: dict[str, HandlerFn],
) -> AsyncIterator[tuple[FakeBridge, AbletonBridgeClient]]:
    fake = FakeBridge(handlers)
    await fake.start()
    bridge_client.reset_bridge_client()
    cfg = BridgeConfig(host="127.0.0.1", port=fake.port, request_timeout=2.0)
    client = AbletonBridgeClient(cfg)
    try:
        yield fake, client
    finally:
        await fake.stop()
        bridge_client.reset_bridge_client()


# Synthetic browser tree handler. Children for each path are returned by a
# tiny lookup; anything else is empty.
_BROWSER_TREE: dict[str, list[dict[str, Any]]] = {
    "instruments": [
        {"name": "Operator", "is_loadable": True, "uri": "live:op"},
        {"name": "Wavetable", "is_loadable": True, "uri": "live:wt"},
        {"name": "Drift", "is_loadable": True, "uri": "live:drift"},
        {"name": "VST3 Plugins", "is_loadable": False, "uri": None},
    ],
    "instruments/VST3 Plugins": [
        {"name": "Diva", "is_loadable": True, "uri": "live:diva"},
        {"name": "Massive", "is_loadable": True, "uri": "live:massive"},
    ],
    "audio_effects": [
        {"name": "EQ Eight", "is_loadable": True, "uri": "live:eq8"},
    ],
    "midi_effects": [],
    "drums": [],
    "sounds": [],
    "samples": [],
    "plugins": [],
    "user_library": [],
    "current_project": [],
    "packs": [],
}


def _list_at_path(args: dict[str, Any]) -> dict[str, Any]:
    p = args.get("path") or ""
    children = list(_BROWSER_TREE.get(p, []))
    return {"path": p, "children": children}


# --------------------------------------------------------------------------
# Fake OSC — matches the surface the loader uses
# --------------------------------------------------------------------------


class FakeOSC:
    """Stand-in for AbletonOSCClient; records every call so tests can assert order."""

    def __init__(
        self,
        *,
        param_table: dict[int, list[dict[str, Any]]] | None = None,
        class_table: dict[int, str] | None = None,
        load_should_fail: bool = False,
    ) -> None:
        self.sent: list[tuple[str, tuple]] = []
        self.requests: list[tuple[str, tuple]] = []
        self.tracks: list[str] = []
        # When set, the requested device on track i has these params.
        self._params = param_table or {0: []}
        self._class = class_table or {0: ""}
        self._load_should_fail = load_should_fail

    # send is fire-and-forget on the real client
    def send(self, address: str, *args: Any) -> None:
        self.sent.append((address, args))
        if address == "/live/song/create_midi_track":
            self.tracks.append(PROBE_TRACK_NAME)
        elif address == "/live/song/create_audio_track":
            self.tracks.append(PROBE_TRACK_NAME)
        elif address == "/live/song/delete_track":
            idx = int(args[0])
            if 0 <= idx < len(self.tracks):
                self.tracks.pop(idx)
        elif address == "/live/track/set/name":
            idx = int(args[0])
            if 0 <= idx < len(self.tracks):
                self.tracks[idx] = str(args[1])

    async def request(self, address: str, *args: Any) -> tuple[Any, ...]:
        self.requests.append((address, args))
        if address == "/live/song/get/num_tracks":
            return (len(self.tracks),)
        if address == "/live/track/get/num_devices":
            track_index = int(args[0])
            n = 1 if 0 <= track_index < len(self.tracks) else 0
            if self._load_should_fail:
                n = 0
            return (track_index, n)
        if address == "/live/track/get/devices/class_name":
            track_index = int(args[0])
            cls = self._class.get(track_index, "")
            return (track_index, cls)
        if address == "/live/device/get/num_parameters":
            track_index = int(args[0])
            params = self._params.get(track_index, [])
            return (track_index, int(args[1]), len(params))
        if address == "/live/device/get/parameters/name":
            track_index = int(args[0])
            params = self._params.get(track_index, [])
            return (track_index, int(args[1]), *[p["name"] for p in params])
        if address == "/live/device/get/parameters/value":
            track_index = int(args[0])
            params = self._params.get(track_index, [])
            return (track_index, int(args[1]), *[p["value"] for p in params])
        if address == "/live/device/get/parameters/min":
            track_index = int(args[0])
            params = self._params.get(track_index, [])
            return (track_index, int(args[1]), *[p["min"] for p in params])
        if address == "/live/device/get/parameters/max":
            track_index = int(args[0])
            params = self._params.get(track_index, [])
            return (track_index, int(args[1]), *[p["max"] for p in params])
        if address == "/live/device/get/parameters/is_quantized":
            track_index = int(args[0])
            params = self._params.get(track_index, [])
            return (track_index, int(args[1]), *[p.get("quantized", False) for p in params])
        raise AssertionError(f"unexpected request {address!r} args={args!r}")


# --------------------------------------------------------------------------
# scanner
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scanner_walks_one_category() -> None:
    async with _bridge({"browser.list_at_path": _list_at_path}) as (_, client):
        items = await scan_browser(category="instruments", client=client)
    names = [it.name for it in items]
    assert "Operator" in names
    assert "Wavetable" in names
    assert "Diva" in names  # nested inside VST3 Plugins folder
    assert "Massive" in names
    # All items live under the "instruments" category.
    assert {it.category for it in items} == {"instruments"}


@pytest.mark.asyncio
async def test_scanner_loadable_filter_paths_correct() -> None:
    async with _bridge({"browser.list_at_path": _list_at_path}) as (_, client):
        items = await scan_browser(category="instruments", client=client)
    operator = next(it for it in items if it.name == "Operator")
    assert operator.is_loadable is True
    assert operator.path == "instruments/Operator"
    diva = next(it for it in items if it.name == "Diva")
    assert diva.path == "instruments/VST3 Plugins/Diva"
    folder = next(it for it in items if it.name == "VST3 Plugins")
    assert folder.is_loadable is False


@pytest.mark.asyncio
async def test_scanner_walks_all_categories_when_none() -> None:
    async with _bridge({"browser.list_at_path": _list_at_path}) as (_, client):
        items = await scan_browser(client=client)
    cats = {it.category for it in items}
    # Every populated category in our synthetic tree should appear.
    assert "instruments" in cats
    assert "audio_effects" in cats


@pytest.mark.asyncio
async def test_scanner_unknown_category_raises() -> None:
    async with _bridge({"browser.list_at_path": _list_at_path}) as (_, client):
        with pytest.raises(ValueError):
            await scan_browser(category="not_a_category", client=client)


# --------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------


_OPERATOR_PARAMS = [
    {"name": "Algorithm", "value": 0.0, "min": 0.0, "max": 10.0, "quantized": True},
    {"name": "Volume", "value": 0.85, "min": 0.0, "max": 1.0, "quantized": False},
    {"name": "Transpose", "value": 0.0, "min": -48.0, "max": 48.0, "quantized": True},
]


@pytest.mark.asyncio
async def test_loader_happy_path_creates_loads_dumps_deletes() -> None:
    fake_osc = FakeOSC(
        param_table={0: _OPERATOR_PARAMS},
        class_table={0: "Operator"},
    )
    handlers: dict[str, HandlerFn] = {
        "browser.load_device": lambda a: {"loaded": "Operator", "track_index": a["track_index"]},
    }
    async with _bridge(handlers) as (fake, client):
        snap = await load_and_introspect(
            "instruments/Operator",
            "instruments",
            osc_factory=lambda: _async_return(fake_osc),
            bridge_factory=lambda: client,
            settle=0.0,
        )
    assert snap.error is None, snap.error
    assert snap.class_name == "Operator"
    assert [p["name"] for p in snap.parameters] == ["Algorithm", "Volume", "Transpose"]
    # Bridge saw the load call.
    assert any(req["op"] == "browser.load_device" for req in fake.requests)
    # Track was created AND deleted.
    addresses = [a for a, _ in fake_osc.sent]
    assert "/live/song/create_midi_track" in addresses
    assert "/live/song/delete_track" in addresses
    # ...and after the dance the probe track is gone.
    assert PROBE_TRACK_NAME not in fake_osc.tracks


@pytest.mark.asyncio
async def test_loader_failure_still_deletes_temp_track() -> None:
    fake_osc = FakeOSC(class_table={0: "Operator"})  # zero params would still work
    # Make the bridge load_device throw.
    handlers: dict[str, HandlerFn] = {
        "browser.load_device": lambda a: (_ for _ in ()).throw(RuntimeError("plugin crashed")),
    }
    async with _bridge(handlers) as (_, client):
        snap = await load_and_introspect(
            "instruments/Diva",
            "instruments",
            osc_factory=lambda: _async_return(fake_osc),
            bridge_factory=lambda: client,
            settle=0.0,
        )
    assert snap.error is not None
    assert "plugin crashed" in snap.error
    assert "/live/song/delete_track" in [a for a, _ in fake_osc.sent]
    assert PROBE_TRACK_NAME not in fake_osc.tracks


@pytest.mark.asyncio
async def test_loader_uses_audio_track_for_audio_effects() -> None:
    fake_osc = FakeOSC(
        param_table={0: [{"name": "Frequency", "value": 0.5, "min": 0, "max": 1, "quantized": False}]},
        class_table={0: "Eq8"},
    )
    handlers: dict[str, HandlerFn] = {
        "browser.load_device": lambda a: {"loaded": "EQ Eight", "track_index": a["track_index"]},
    }
    async with _bridge(handlers) as (_, client):
        await load_and_introspect(
            "audio_effects/EQ Eight",
            "audio_effects",
            osc_factory=lambda: _async_return(fake_osc),
            bridge_factory=lambda: client,
            settle=0.0,
        )
    addresses = [a for a, _ in fake_osc.sent]
    assert "/live/song/create_audio_track" in addresses
    assert "/live/song/create_midi_track" not in addresses


@pytest.mark.asyncio
async def test_loader_drum_kit_uses_drum_handler() -> None:
    fake_osc = FakeOSC(
        param_table={0: []},
        class_table={0: "DrumGroupDevice"},
    )
    seen_op: list[str] = []

    def drum(args: dict[str, Any]) -> Any:
        seen_op.append("drum")
        return {"loaded": "Kit", "track_index": args["track_index"]}

    handlers: dict[str, HandlerFn] = {"browser.load_drum_kit": drum}
    async with _bridge(handlers) as (_, client):
        snap = await load_and_introspect(
            "drums/Kit",
            "drums",
            osc_factory=lambda: _async_return(fake_osc),
            bridge_factory=lambda: client,
            settle=0.0,
        )
    assert seen_op == ["drum"]
    assert snap.class_name == "DrumGroupDevice"


# --------------------------------------------------------------------------
# matcher
# --------------------------------------------------------------------------


def _operator_full_snapshot() -> InstrumentSnapshot:
    # 3 of 29 schema params is below the 80% bar but ALL of these names are
    # in the schema, so overlap_ratio = 1.0 → "full".
    return InstrumentSnapshot(
        name="Operator",
        uri="live:op",
        category="instruments",
        class_name="Operator",
        parameters=[{"name": "Algorithm"}, {"name": "Volume"}, {"name": "Transpose"}],
    )


def _operator_partial_snapshot() -> InstrumentSnapshot:
    # Only 1 of 5 names matches the Operator schema → 20% overlap → "partial".
    return InstrumentSnapshot(
        name="Operator",
        uri="live:op",
        category="instruments",
        class_name="Operator",
        parameters=[
            {"name": "Algorithm"},
            {"name": "Mystery"},
            {"name": "Foo"},
            {"name": "Bar"},
            {"name": "Baz"},
        ],
    )


def _plugin_unknown_snapshot(class_name: str = "DivaVST") -> InstrumentSnapshot:
    return InstrumentSnapshot(
        name="Diva",
        uri="live:diva",
        category="instruments",
        class_name=class_name,
        parameters=[{"name": "Cutoff"}, {"name": "Resonance"}],
    )


def test_matcher_full_partial_unknown_tiers() -> None:
    snaps = [
        _operator_full_snapshot(),
        _operator_partial_snapshot(),
        _plugin_unknown_snapshot(),
    ]
    matches = match_to_schemas(snaps)
    coverages = [m.coverage for m in matches]
    assert coverages == ["full", "partial", "unknown"]
    # The partial match knows which params live only in the schema.
    partial = matches[1]
    assert partial.schema_class_name == "Operator"
    assert "Mystery" in partial.unmatched_params
    # The schema has many params we did NOT see; confirm "Volume" is in extras.
    assert "Volume" in partial.extra_schema_params
    # Unknown gets None for its schema and lists all snapshot params as
    # unmatched.
    unk = matches[2]
    assert unk.schema_class_name is None
    assert set(unk.unmatched_params) == {"Cutoff", "Resonance"}


def test_matcher_coverage_summary_counts() -> None:
    # 1 native (full), 1 native (partial), 2 plugins (unknown)
    snaps = [
        _operator_full_snapshot(),
        InstrumentSnapshot(
            name="Wavetable",
            uri="live:wt",
            category="instruments",
            class_name="InstrumentVector",
            parameters=[
                # full param-name overlap → "full"
                {"name": "Oscillator 1 On"},
                {"name": "Oscillator 1 Pitch"},
            ],
        ),
        _plugin_unknown_snapshot("DivaVST"),
        _plugin_unknown_snapshot("MassiveVST"),
    ]
    matches = match_to_schemas(snaps)
    summary = build_coverage_summary(matches)
    assert summary["total"] == 4
    assert summary["by_coverage"] == {"full": 2, "partial": 0, "unknown": 2}
    assert summary["by_category"] == {"instruments": 4}
    assert "Operator" in summary["schemas_hit"]
    assert "InstrumentVector" in summary["schemas_hit"]


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


def test_manifest_round_trips_through_json(tmp_path: Path) -> None:
    snaps = [
        _operator_full_snapshot(),
        _plugin_unknown_snapshot("DivaVST"),
    ]
    matches = match_to_schemas(snaps)
    manifest = Manifest(
        instruments=snaps,
        coverage_summary=build_coverage_summary(matches),
        live_version="11.3.21",
    )
    out = manifest.save(tmp_path / "inv" / "manifest.json")
    assert out.exists()
    reloaded = Manifest.load(out)
    assert reloaded.live_version == "11.3.21"
    assert len(reloaded.instruments) == 2
    assert reloaded.instruments[0].class_name == "Operator"
    assert reloaded.coverage_summary["by_coverage"]["full"] == 1
    # totals reflect the snapshot list.
    assert reloaded.totals() == {"instruments": 2}


# --------------------------------------------------------------------------
# tools/inventory.py registers
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_register_on_fastmcp() -> None:
    from mcp.server.fastmcp import FastMCP

    from ableton_mcp.tools import inventory as inv_tools

    mcp = FastMCP("test")
    inv_tools.register(mcp)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "inventory_scan_browser",
        "inventory_introspect",
        "inventory_scan_all",
        "inventory_match_manifest",
        "inventory_load_manifest",
        "inventory_summary",
    }
    assert expected <= names


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _async_return(value: Any) -> Any:
    """Adapter so a plain object can be returned by an async factory."""
    return value
