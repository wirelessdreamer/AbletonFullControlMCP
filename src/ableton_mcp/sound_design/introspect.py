"""Read a track's device chain via OSC and return a musician-readable summary.

The summary identifies the instrument, downstream effect chain, applies a
short architecture blurb (from :mod:`ableton_mcp.device_schemas`), and
lists the knobs that move the sound's character — pulled from the rule
catalogue in :mod:`ableton_mcp.sound_design.device_rules`.

Example output (paraphrased):

    Track 7 'Lead Guitar': Hard Picked Guitar (Tension physical-modelling)
    → Basic Lead Guitar Amp. Currently bright with moderate sustain.
    Knobs that move character: Bow Force, Damping, Pickup Position, Amp Drive.

The summary is heuristic, not a live audio analysis — it inspects param
values, cross-references the rule set, and reports which params are
"interesting to twist" given the device's character knobs.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..device_schemas import DeviceSchema, lookup_schema
from .device_rules import (
    DEVICE_RULES,
    ParamRule,
    REQUIRED_DESCRIPTORS,
    get_rules,
    supported_descriptors_for,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OSC reads
# ---------------------------------------------------------------------------


async def _list_devices_on_track(track_index: int) -> list[dict[str, Any]]:
    """Fetch lightweight (name, class_name, type) per device on the track."""
    from ..osc_client import get_client  # local

    client = await get_client()
    n_args = await client.request("/live/track/get/num_devices", int(track_index))
    n = int(n_args[1])
    if n == 0:
        return []
    names = await client.request("/live/track/get/devices/name", int(track_index))
    types = await client.request("/live/track/get/devices/type", int(track_index))
    classes = await client.request("/live/track/get/devices/class_name", int(track_index))
    name_list = list(names[1:])
    type_list = list(types[1:])
    class_list = list(classes[1:])
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "device_index": i,
                "name": str(name_list[i]) if i < len(name_list) else None,
                "type": type_list[i] if i < len(type_list) else None,
                "class_name": str(class_list[i]) if i < len(class_list) else None,
            }
        )
    return out


async def _track_name(track_index: int) -> Optional[str]:
    from ..osc_client import get_client  # local

    client = await get_client()
    try:
        names = await client.request("/live/song/get/track_names")
        if track_index < 0 or track_index >= len(names):
            return None
        return str(names[track_index])
    except Exception:  # pragma: no cover
        return None


async def _track_count() -> int:
    from ..osc_client import get_client  # local

    client = await get_client()
    n_args = await client.request("/live/song/get/num_tracks")
    return int(n_args[0])


async def _track_audible(track_index: int) -> bool:
    """Cheap audibility hint: not muted."""
    from ..osc_client import get_client  # local

    client = await get_client()
    try:
        mute = (await client.request("/live/track/get/mute", int(track_index)))[1]
        return not bool(mute)
    except Exception:  # pragma: no cover
        return True


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _short_architecture_blurb(class_name: str) -> str:
    """One-sentence description of the device's synthesis / processing model."""
    schema: Optional[DeviceSchema] = lookup_schema(class_name)
    if schema is not None and schema.description:
        # Take the first sentence from the schema description.
        text = schema.description.strip()
        for sep in (". ", ".\n"):
            if sep in text:
                return text.split(sep, 1)[0] + "."
        return text
    return f"Unknown device class {class_name!r} (no schema in catalogue)."


def _character_param_names(class_name: str) -> list[str]:
    """Param names that show up across the rule set for this device.

    These are the knobs that move the sound's character — useful for the
    summary line "Knobs that move character: ...".
    """
    rules = get_rules(class_name)
    if not rules:
        return []
    seen: dict[str, float] = {}
    for descriptor, rule_list in rules.items():
        for r in rule_list:
            seen[r.param_name] = max(seen.get(r.param_name, 0.0), r.weight)
    # Return names sorted by max weight desc, then alpha.
    ordered = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ordered]


def summarise_device(device: dict[str, Any]) -> dict[str, Any]:
    """Produce a per-device summary record for a single chain entry."""
    class_name = device.get("class_name") or ""
    schema = lookup_schema(class_name)
    rules = get_rules(class_name)

    return {
        "device_index": device.get("device_index"),
        "name": device.get("name"),
        "class_name": class_name,
        "type": device.get("type"),
        "display_name": schema.display_name if schema else class_name,
        "device_type": schema.device_type if schema else "unknown",
        "architecture": _short_architecture_blurb(class_name),
        "schema_known": schema is not None,
        "rules_known": rules is not None,
        "supported_descriptors": supported_descriptors_for(class_name),
        "character_params": _character_param_names(class_name),
    }


def _format_chain_line(devices: list[dict[str, Any]]) -> str:
    """' Drift -> Reverb -> Compressor' style chain text."""
    if not devices:
        return "(empty chain)"
    return " -> ".join(d.get("display_name", d.get("name", "?")) for d in devices)


def _instrument_for(devices: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """First device on the chain whose schema device_type is 'instrument' or 'drum'."""
    for d in devices:
        if d.get("device_type") in ("instrument", "drum"):
            return d
    # Fall back to first device if any.
    return devices[0] if devices else None


def _build_human_summary(
    track_index: int, track_name: Optional[str], devices: list[dict[str, Any]]
) -> str:
    """Assemble the one-paragraph musician-facing summary."""
    name_part = f"'{track_name}'" if track_name else f"index {track_index}"
    if not devices:
        return f"Track {track_index} {name_part}: (no devices)."

    inst = _instrument_for(devices)
    chain_text = _format_chain_line(devices)

    if inst is not None and inst.get("device_type") in ("instrument", "drum"):
        inst_text = f"{inst['name']} ({inst['display_name']} {inst['architecture']})"
    else:
        inst_text = chain_text

    # Knobs that move character — union from all devices.
    chars: list[str] = []
    for d in devices:
        chars.extend(d.get("character_params", [])[:3])
    # De-dup preserving order.
    seen = set()
    unique_chars: list[str] = []
    for c in chars:
        if c not in seen:
            unique_chars.append(c)
            seen.add(c)

    summary = f"Track {track_index} {name_part}: {inst_text}"
    if len(devices) > 1:
        summary += f". Chain: {chain_text}"
    if unique_chars:
        summary += f". Knobs that move character: {', '.join(unique_chars[:6])}."
    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def summarise_track_sound(track_index: int) -> dict[str, Any]:
    """Produce a musician-readable summary of one track's sound design surface.

    Reads the device chain via OSC, matches each class_name against the
    schema catalogue and the curated rule set, and assembles a structured
    + human summary.
    """
    try:
        track_name = await _track_name(track_index)
        device_listing = await _list_devices_on_track(track_index)
    except Exception as exc:
        return {
            "status": "error",
            "error": repr(exc),
            "track_index": int(track_index),
        }

    devices = [summarise_device(d) for d in device_listing]
    human = _build_human_summary(track_index, track_name, devices)
    instrument = _instrument_for(devices)

    return {
        "status": "ok",
        "track_index": int(track_index),
        "track_name": track_name,
        "device_count": len(devices),
        "devices": devices,
        "instrument": instrument,
        "summary": human,
        "any_curated_rules": any(d["rules_known"] for d in devices),
    }


async def summarise_all_tracks() -> dict[str, Any]:
    """Scan every audible track and summarise its sound."""
    try:
        n = await _track_count()
    except Exception as exc:
        return {"status": "error", "error": repr(exc)}

    out: list[dict[str, Any]] = []
    for ti in range(n):
        try:
            audible = await _track_audible(ti)
        except Exception:  # pragma: no cover
            audible = True
        if not audible:
            continue
        result = await summarise_track_sound(ti)
        out.append(result)

    return {
        "status": "ok",
        "track_count": n,
        "summaries": out,
    }


def explain_descriptor(class_name: str, descriptor: str) -> dict[str, Any]:
    """Return the curated rule body for (class, descriptor).

    Useful for the LLM when it needs to explain why a particular knob was
    moved (e.g. "I lifted Filter Frequency because brightness on Drift is
    primarily driven by cutoff").
    """
    rules = get_rules(class_name)
    if rules is None:
        return {
            "status": "unsupported_device",
            "class_name": class_name,
            "supported_devices": sorted(DEVICE_RULES.keys()),
        }
    rule_list = rules.get(descriptor.strip().lower(), [])
    if not rule_list:
        return {
            "status": "unknown_descriptor",
            "class_name": class_name,
            "descriptor": descriptor,
            "supported_descriptors": supported_descriptors_for(class_name),
        }
    schema = lookup_schema(class_name)
    return {
        "status": "ok",
        "class_name": class_name,
        "device_display_name": schema.display_name if schema else class_name,
        "descriptor": descriptor.strip().lower(),
        "rules": [
            {
                "param_name": r.param_name,
                "direction": r.direction,
                "weight": r.weight,
                "note": r.note,
            }
            for r in rule_list
        ],
        "rule_count": len(rule_list),
    }


def list_descriptors_for_device(class_name: str) -> dict[str, Any]:
    """List the descriptors that the curated rule set knows for this device."""
    rules = get_rules(class_name)
    if rules is None:
        return {
            "status": "unsupported_device",
            "class_name": class_name,
            "supported_devices": sorted(DEVICE_RULES.keys()),
        }
    schema = lookup_schema(class_name)
    descriptors = supported_descriptors_for(class_name)
    missing = sorted(set(REQUIRED_DESCRIPTORS) - set(descriptors))
    return {
        "status": "ok",
        "class_name": class_name,
        "device_display_name": schema.display_name if schema else class_name,
        "descriptors": descriptors,
        "missing_required": missing,
    }


__all__ = [
    "summarise_track_sound",
    "summarise_all_tracks",
    "summarise_device",
    "explain_descriptor",
    "list_descriptors_for_device",
]
