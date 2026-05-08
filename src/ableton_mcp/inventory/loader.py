"""Load a browser item onto a temp track, dump its parameter list, delete the track.

This is the "introspection" step of the inventory pipeline. It is the only
piece that mutates the user's Live session, so it goes to length to:

1. Always create a fresh dedicated probe track named
   ``__inventory_probe__`` rather than touching anything that already
   exists.
2. Wrap the load+introspect+delete in a try/finally that ALWAYS attempts
   to delete the probe track, even if loading or introspection raises.
3. Stagger bridge / OSC calls with a small sleep so we don't hammer
   either Remote Script.

The loader does not know about manifests, schemas, or coverage — it only
returns one :class:`InstrumentSnapshot` per call. Callers compose those
into a manifest and run the matcher separately.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..bridge_client import AbletonBridgeClient, get_bridge_client
from ..osc_client import AbletonOSCClient, get_client


log = logging.getLogger(__name__)


PROBE_TRACK_NAME = "__inventory_probe__"

# Per-step pause. Conservative — the bridge does its work on Live's main
# thread on the next display update, so a 100 ms gap is plenty.
DEFAULT_SETTLE = 0.1


class InventoryError(RuntimeError):
    """Raised when the loader can't make progress (e.g. track-create failed)."""


@dataclass
class InstrumentSnapshot:
    """One row of the inventory manifest: parameter dump for one device.

    `class_name` is what Ableton's LOM reports — a string like ``Operator``
    or ``OriginalSimpler`` for natives, or the plugin name reported by Live
    for VST/VST3/AU. `error` is non-None only when the load/dump round trip
    failed; in that case `parameters` is an empty list and `class_name`
    may be empty.
    """

    name: str
    uri: Optional[str]
    category: str
    class_name: str
    parameters: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# OSC + bridge calls injected as overrides for tests. They mirror the
# free functions that ship in `tools/tracks.py`, `tools/devices.py`, and
# `tools/browser.py` but go straight to the underlying clients so we don't
# have to instantiate a FastMCP and look up tool implementations.

OscClientFactory = Callable[[], Awaitable[AbletonOSCClient]]
BridgeClientFactory = Callable[[], AbletonBridgeClient]


async def _create_track(
    osc: AbletonOSCClient, *, midi: bool, name: str
) -> int:
    addr = "/live/song/create_midi_track" if midi else "/live/song/create_audio_track"
    osc.send(addr, -1)
    # Track count tells us where the new track landed; AbletonOSC appends.
    n_args = await osc.request("/live/song/get/num_tracks")
    n = int(n_args[0])
    new_index = n - 1
    osc.send("/live/track/set/name", new_index, name)
    return new_index


async def _delete_track(osc: AbletonOSCClient, track_index: int) -> None:
    osc.send("/live/song/delete_track", int(track_index))


async def _device_count(osc: AbletonOSCClient, track_index: int) -> int:
    args = await osc.request("/live/track/get/num_devices", int(track_index))
    # Reply shape: (track_index, count) — the OSC client's prefix-resolver
    # delivers it back to us once.
    return int(args[1])


async def _device_class_name(osc: AbletonOSCClient, track_index: int, device_index: int) -> str:
    args = await osc.request("/live/track/get/devices/class_name", int(track_index))
    classes = list(args[1:])
    if device_index < 0 or device_index >= len(classes):
        return ""
    return str(classes[device_index] or "")


async def _device_parameters(
    osc: AbletonOSCClient, track_index: int, device_index: int
) -> list[dict[str, Any]]:
    n_args = await osc.request(
        "/live/device/get/num_parameters", int(track_index), int(device_index)
    )
    n = int(n_args[2])
    names = (await osc.request(
        "/live/device/get/parameters/name", int(track_index), int(device_index)
    ))[2:]
    values = (await osc.request(
        "/live/device/get/parameters/value", int(track_index), int(device_index)
    ))[2:]
    mins = (await osc.request(
        "/live/device/get/parameters/min", int(track_index), int(device_index)
    ))[2:]
    maxs = (await osc.request(
        "/live/device/get/parameters/max", int(track_index), int(device_index)
    ))[2:]
    quant = (await osc.request(
        "/live/device/get/parameters/is_quantized", int(track_index), int(device_index)
    ))[2:]
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "index": i,
                "name": str(names[i]) if i < len(names) else "",
                "value": float(values[i]) if i < len(values) else 0.0,
                "min": float(mins[i]) if i < len(mins) else 0.0,
                "max": float(maxs[i]) if i < len(maxs) else 1.0,
                "quantized": bool(quant[i]) if i < len(quant) else False,
            }
        )
    return out


def _is_midi_category(category: str) -> bool:
    """A loaded item belongs on a MIDI track if it produces or processes MIDI.

    `instruments`, `midi_effects`, `drums`, and `plugins` (which we don't
    know — we assume MIDI to keep the introspection working for synths;
    for plugin-FX users will pass `category="audio_effects"` directly).
    """
    return category in {"instruments", "midi_effects", "drums", "plugins"}


async def load_and_introspect(
    uri_or_path: str,
    category: str,
    *,
    name: Optional[str] = None,
    osc_factory: Optional[OscClientFactory] = None,
    bridge_factory: Optional[BridgeClientFactory] = None,
    settle: float = DEFAULT_SETTLE,
) -> InstrumentSnapshot:
    """Create temp track, load device, dump params, delete track.

    Returns an :class:`InstrumentSnapshot` with ``error`` set on any
    failure. Even on failure the temp track is deleted on a best-effort
    basis.

    Args:
        uri_or_path: Either a slash-delimited browser path (e.g.
            ``instruments/Operator``) or a Live URI. The bridge accepts
            both via the same handler.
        category: Browser category — controls whether we create a MIDI
            or audio probe track and whether we use ``browser.load_device``
            or ``browser.load_drum_kit``.
        name: Display name to record in the snapshot. Defaults to the
            last segment of ``uri_or_path``.
        osc_factory / bridge_factory: Test overrides.
        settle: Pause between bridge / OSC ops.
    """
    osc_get = osc_factory or get_client
    bridge_get = bridge_factory or get_bridge_client
    osc = await osc_get()
    bridge = bridge_get()

    display_name = name or uri_or_path.rsplit("/", 1)[-1]
    midi = _is_midi_category(category)
    op = "browser.load_drum_kit" if category == "drums" else "browser.load_device"

    track_index: Optional[int] = None
    snapshot = InstrumentSnapshot(
        name=display_name, uri=uri_or_path, category=category, class_name=""
    )

    try:
        track_index = await _create_track(osc, midi=midi, name=PROBE_TRACK_NAME)
        if settle:
            await asyncio.sleep(settle)

        # `browser.load_device` accepts either `path` or `uri`, exactly
        # like `tools/browser.py:browser_load_device` decides. Mirror that
        # heuristic here so callers can pass either.
        if "/" in uri_or_path and not uri_or_path.startswith(("live:", "query:")):
            args: dict[str, Any] = {"path": uri_or_path, "track_index": track_index}
        else:
            args = {"uri": uri_or_path, "track_index": track_index}
        await bridge.call(op, **args)
        if settle:
            await asyncio.sleep(settle)

        n = await _device_count(osc, track_index)
        if n <= 0:
            raise InventoryError(
                f"load reported success but track has zero devices (uri={uri_or_path!r})"
            )

        # Plugins occasionally report a wrapping rack: take the first
        # device on the chain. The class_name we return is THIS top-level
        # device — for racks containing a plugin, that's the rack class
        # which is honest about what was loaded.
        device_index = 0
        class_name = await _device_class_name(osc, track_index, device_index)
        params = await _device_parameters(osc, track_index, device_index)
        snapshot.class_name = class_name
        snapshot.parameters = params
    except Exception as exc:  # noqa: BLE001 — top-level guard, we report below
        snapshot.error = f"{type(exc).__name__}: {exc}"
        log.warning("load_and_introspect failed for %s: %s", uri_or_path, exc)
    finally:
        if track_index is not None:
            try:
                await _delete_track(osc, track_index)
                if settle:
                    await asyncio.sleep(settle)
            except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
                log.error(
                    "cleanup of probe track %d failed; user may need to delete %r manually: %s",
                    track_index, PROBE_TRACK_NAME, exc,
                )

    return snapshot
