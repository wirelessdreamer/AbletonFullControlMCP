"""Apply curated descriptor rules to a Live device via OSC.

Given a (track_index, device_index, descriptor, intensity), look up the
device's class_name → :class:`ParamRule` set, fetch current parameter
values, compute the target value for each rule (clamped to the
parameter's [min, max]), and push via OSC. Returns a structured report:
which params changed, which couldn't be matched on the live device, and
the resolved class_name.

The "intensity" knob is a float in ``0..1`` (default 0.5). It scales how
far each rule's direction × weight pushes the parameter toward the
relevant range endpoint. ``intensity=0`` means "no movement";
``intensity=1`` means "go all the way".

For multi-descriptor calls (``apply_descriptors``), per-rule deltas are
summed across descriptors before clamping. If two descriptors disagree
on a parameter (e.g. "warmer" pulls cutoff down, "brighter" pulls up),
the directions cancel — which is the correct behaviour: the LLM asked
for a paradox and gets a near-zero move.

OSC failures don't raise — they return ``{"status": "error", ...}``.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Optional

from ..device_schemas import lookup_schema
from .device_rules import (
    ParamRule,
    get_descriptor_rules,
    get_rules,
    normalize_descriptor,
    supported_descriptors_for,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Live device introspection (mirrors tools/sound_modeling._push_params_via_osc)
# ---------------------------------------------------------------------------


async def _fetch_device_info(track_index: int, device_index: int) -> dict[str, Any]:
    """Fetch class_name + parameter list for one Live device."""
    from ..osc_client import get_client  # local: keeps OSC dep optional at test time

    client = await get_client()

    classes = await client.request(
        "/live/track/get/devices/class_name", int(track_index)
    )
    # Reply: (track_id, class_0, class_1, ...). Strip leading id.
    class_list = list(classes[1:])
    if device_index < 0 or device_index >= len(class_list):
        raise ValueError(
            f"device_index {device_index} out of range (track {track_index} has {len(class_list)} devices)"
        )
    class_name = str(class_list[device_index])

    names = (
        await client.request(
            "/live/device/get/parameters/name", int(track_index), int(device_index)
        )
    )[2:]
    values = (
        await client.request(
            "/live/device/get/parameters/value", int(track_index), int(device_index)
        )
    )[2:]
    mins = (
        await client.request(
            "/live/device/get/parameters/min", int(track_index), int(device_index)
        )
    )[2:]
    maxs = (
        await client.request(
            "/live/device/get/parameters/max", int(track_index), int(device_index)
        )
    )[2:]

    params: dict[str, dict[str, Any]] = {}
    lowered_to_canonical: dict[str, str] = {}
    for i, raw_name in enumerate(names):
        canonical = str(raw_name)
        params[canonical] = {
            "index": i,
            "name": canonical,
            "value": float(values[i]) if i < len(values) else None,
            "min": float(mins[i]) if i < len(mins) else None,
            "max": float(maxs[i]) if i < len(maxs) else None,
        }
        lowered_to_canonical[canonical.strip().lower()] = canonical

    return {
        "class_name": class_name,
        "params": params,
        "lowered_to_canonical": lowered_to_canonical,
    }


# ---------------------------------------------------------------------------
# Delta calculation
# ---------------------------------------------------------------------------


def _compute_target_value(
    rule: ParamRule,
    current_value: Optional[float],
    pmin: Optional[float],
    pmax: Optional[float],
    intensity: float,
) -> Optional[float]:
    """Move ``current_value`` toward the appropriate endpoint.

    ``direction × weight × intensity`` is interpreted as the fraction of the
    distance between ``current`` and the relevant endpoint to move. Returns
    ``None`` if any input is missing.
    """
    if current_value is None or pmin is None or pmax is None or pmax <= pmin:
        return None
    fraction = max(0.0, min(1.0, abs(rule.weight) * abs(intensity)))
    if rule.direction > 0:
        target_endpoint = pmax
    else:
        target_endpoint = pmin
    delta = (target_endpoint - current_value) * fraction
    new_value = current_value + delta
    return float(max(pmin, min(pmax, new_value)))


def _aggregate_rules(
    rules: Iterable[tuple[str, ParamRule, float]],
) -> dict[str, dict[str, Any]]:
    """Sum direction × weight × intensity per param_name across rules.

    Returns ``{ param_name: {"signed_amount": float, "rules": [...]} }``.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for descriptor, rule, intensity in rules:
        amount = float(rule.direction) * float(rule.weight) * float(intensity)
        slot = by_name.setdefault(
            rule.param_name, {"signed_amount": 0.0, "rules": []}
        )
        slot["signed_amount"] += amount
        slot["rules"].append(
            {
                "descriptor": descriptor,
                "direction": rule.direction,
                "weight": rule.weight,
                "intensity": intensity,
                "note": rule.note,
            }
        )
    return by_name


# ---------------------------------------------------------------------------
# Public application functions (async)
# ---------------------------------------------------------------------------


async def apply_descriptor(
    track_index: int,
    device_index: int,
    descriptor: str,
    intensity: float = 0.5,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a single descriptor's rules to a Live device.

    Returns a report including ``status``, ``class_name``, ``descriptor``,
    ``applied`` (list of param updates pushed), ``unmatched_rules`` (rules
    whose param_name wasn't on the live device), and ``unsupported_device``
    if the class_name has no rule set.
    """
    return await apply_descriptors(
        track_index=track_index,
        device_index=device_index,
        descriptors=[descriptor],
        intensity=intensity,
        dry_run=dry_run,
    )


async def apply_descriptors(
    track_index: int,
    device_index: int,
    descriptors: list[str],
    intensity: float = 0.5,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply multiple descriptors' rules to one Live device.

    Per-rule deltas combine additively across descriptors. Opposing
    descriptors cancel by design.
    """
    if not descriptors:
        return {
            "status": "error",
            "error": "no descriptors provided",
            "track_index": int(track_index),
            "device_index": int(device_index),
        }

    try:
        info = await _fetch_device_info(int(track_index), int(device_index))
    except Exception as exc:  # OSC failure / bad index
        log.info("apply_descriptors: device introspection failed: %r", exc)
        return {
            "status": "error",
            "error": repr(exc),
            "track_index": int(track_index),
            "device_index": int(device_index),
        }

    class_name = info["class_name"]
    device_params = info["params"]
    lowered_to_canonical: dict[str, str] = info["lowered_to_canonical"]

    rules_dict = get_rules(class_name)
    requested = [normalize_descriptor(d) for d in descriptors]
    if rules_dict is None:
        return {
            "status": "unsupported_device",
            "class_name": class_name,
            "track_index": int(track_index),
            "device_index": int(device_index),
            "descriptors": requested,
            "supported_devices_hint": (
                "No curated rule set for this device class. "
                "Try shape_apply (probe-dataset path) instead."
            ),
        }

    # Collect all (descriptor, rule, intensity) triples.
    triples: list[tuple[str, ParamRule, float]] = []
    descriptors_unknown: list[str] = []
    descriptors_applied: list[str] = []
    for desc in requested:
        rules = rules_dict.get(desc, [])
        if not rules:
            descriptors_unknown.append(desc)
            continue
        descriptors_applied.append(desc)
        for r in rules:
            triples.append((desc, r, float(intensity)))

    aggregated = _aggregate_rules(triples)

    # Resolve param names against live device's actual parameter list.
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unmatched_rules: list[dict[str, Any]] = []

    # Used for OSC push.
    from ..osc_client import get_client  # local

    push_client = None
    if not dry_run and aggregated:
        try:
            push_client = await get_client()
        except Exception as exc:  # OSC unreachable
            log.info("apply_descriptors: OSC unreachable: %r", exc)
            return {
                "status": "error",
                "error": repr(exc),
                "class_name": class_name,
                "track_index": int(track_index),
                "device_index": int(device_index),
                "descriptors": requested,
                "planned": [
                    {
                        "param_name": name,
                        "signed_amount": slot["signed_amount"],
                        "rules": slot["rules"],
                    }
                    for name, slot in aggregated.items()
                ],
            }

    for param_name, slot in aggregated.items():
        signed = float(slot["signed_amount"])
        if abs(signed) < 1e-6:
            skipped.append(
                {
                    "param_name": param_name,
                    "reason": "rules cancelled out (opposing descriptors)",
                    "rules": slot["rules"],
                }
            )
            continue

        canonical = lowered_to_canonical.get(param_name.strip().lower())
        if canonical is None:
            unmatched_rules.append(
                {
                    "param_name": param_name,
                    "reason": "param not on live device — schema/UI name mismatch",
                    "rules": slot["rules"],
                }
            )
            continue

        meta = device_params[canonical]
        current = meta.get("value")
        pmin = meta.get("min")
        pmax = meta.get("max")
        if current is None or pmin is None or pmax is None or pmax <= pmin:
            unmatched_rules.append(
                {
                    "param_name": canonical,
                    "reason": "missing or zero-range bounds",
                    "rules": slot["rules"],
                }
            )
            continue

        # Build a synthetic rule from the aggregate signed amount: pick
        # direction from sign, magnitude from |signed| capped to 1.
        direction = +1 if signed > 0 else -1
        magnitude = min(1.0, abs(signed))
        synth_rule = ParamRule(
            param_name=canonical,
            direction=direction,
            weight=magnitude,
            note="aggregate of " + ", ".join(r["descriptor"] for r in slot["rules"]),
        )
        target = _compute_target_value(
            synth_rule, current, pmin, pmax, intensity=1.0
        )
        if target is None:
            unmatched_rules.append(
                {"param_name": canonical, "reason": "could not compute target"}
            )
            continue

        change = {
            "param_name": canonical,
            "param_index": meta["index"],
            "from_value": float(current),
            "to_value": float(target),
            "delta": float(target - current),
            "min": float(pmin),
            "max": float(pmax),
            "signed_amount": signed,
            "rules": slot["rules"],
        }

        if not dry_run and push_client is not None:
            push_client.send(
                "/live/device/set/parameter/value",
                int(track_index),
                int(device_index),
                int(meta["index"]),
                float(target),
            )
        applied.append(change)

    schema = lookup_schema(class_name)
    return {
        "status": "ok" if not unmatched_rules else "partial",
        "dry_run": bool(dry_run),
        "class_name": class_name,
        "device_display_name": schema.display_name if schema else class_name,
        "track_index": int(track_index),
        "device_index": int(device_index),
        "intensity": float(intensity),
        "descriptors_applied": descriptors_applied,
        "descriptors_unknown": descriptors_unknown,
        "descriptors_supported_for_device": supported_descriptors_for(class_name),
        "applied": applied,
        "skipped": skipped,
        "unmatched_rules": unmatched_rules,
    }


# ---------------------------------------------------------------------------
# Multi-device walk
# ---------------------------------------------------------------------------


async def apply_descriptors_to_track(
    track_index: int,
    descriptors: list[str],
    intensity: float = 0.5,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a list of descriptors across every device on a track.

    Devices without a rule set are noted but don't fail the call.
    """
    from ..osc_client import get_client  # local

    try:
        client = await get_client()
        n_args = await client.request("/live/track/get/num_devices", int(track_index))
    except Exception as exc:
        return {
            "status": "error",
            "error": repr(exc),
            "track_index": int(track_index),
            "descriptors": descriptors,
        }

    n = int(n_args[1])
    per_device: list[dict[str, Any]] = []
    for di in range(n):
        result = await apply_descriptors(
            track_index=track_index,
            device_index=di,
            descriptors=descriptors,
            intensity=intensity,
            dry_run=dry_run,
        )
        per_device.append(result)

    return {
        "status": "ok",
        "track_index": int(track_index),
        "device_count": n,
        "descriptors": [normalize_descriptor(d) for d in descriptors],
        "intensity": float(intensity),
        "per_device": per_device,
    }


__all__ = [
    "apply_descriptor",
    "apply_descriptors",
    "apply_descriptors_to_track",
]
