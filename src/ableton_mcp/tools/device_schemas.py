"""MCP tools that expose the canonical Live device-schema library.

These tools let the LLM client ask:
- "what built-in devices do you know about?"  → ``device_schema_list``
- "what knobs does Operator have?"            → ``device_schema_get``
- "what's on track 0, slot 0, and what can I sweep?"
                                               → ``device_schema_for_track_device``
- "which params should I sweep on this device?"
                                               → ``device_schema_recommended_sweep_params``
- "find the device that does pitch shifting"   → ``device_schema_search``

All schemas are stored offline (no Live dependency) — only
``device_schema_for_track_device`` calls AbletonOSC to figure out which
class_name is on the track first.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from ..device_schemas import (
    DEVICE_SCHEMAS,
    DeviceSchema,
    closest_class_name,
    lookup_schema,
)
from ..osc_client import get_client


def _summary_row(s: DeviceSchema) -> dict[str, Any]:
    return {
        "class_name": s.class_name,
        "display_name": s.display_name,
        "device_type": s.device_type,
        "num_params": len(s.parameters),
        "categories": list(s.categories),
    }


def _search_score(query: str, schema: DeviceSchema) -> float:
    """Cheap relevance score: weighted matches across name / description / categories / param names."""
    q = query.strip().lower()
    if not q:
        return 0.0
    name_text = (schema.display_name + " " + schema.class_name).lower()
    cat_text = " ".join(schema.categories).lower()
    desc_text = schema.description.lower()

    score = 0.0
    tokens = [t for t in q.split() if t]

    # Strongest signal: query token shows up in the device name itself.
    for t in tokens or [q]:
        if t in name_text:
            score += 3.0
        if t in cat_text:
            score += 1.5
        if t in desc_text:
            score += 0.5

    # Whole-query substring boosts.
    if q in name_text:
        score += 2.0
    if q in cat_text:
        score += 1.0

    # Per-parameter signal — tokenwise on parameter names.
    for p in schema.parameters:
        pname = p.name.lower()
        for t in tokens or [q]:
            if t in pname:
                score += 0.4
                break
    return score


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def device_schema_list(device_type: Optional[str] = None) -> list[dict[str, Any]]:
        """List catalogued devices. ``device_type`` filters by 'instrument' / 'audio_effect' / 'midi_effect' / 'drum' / 'utility' / 'rack'."""
        rows: list[dict[str, Any]] = []
        for s in DEVICE_SCHEMAS:
            if device_type and s.device_type != device_type:
                continue
            rows.append(_summary_row(s))
        return rows

    @mcp.tool()
    async def device_schema_get(class_name: str) -> dict[str, Any]:
        """Return the full canonical schema for a device by its LOM ``class_name``."""
        s = lookup_schema(class_name)
        if s is None:
            suggestion = closest_class_name(class_name)
            return {
                "error": f"no schema for class_name {class_name!r}",
                "closest_match": suggestion,
            }
        return s.to_dict()

    @mcp.tool()
    async def device_schema_for_track_device(
        track_index: int, device_index: int
    ) -> dict[str, Any]:
        """Fetch the device class_name from Live and return the matching schema (or a helpful 'no schema' message)."""
        client = await get_client()
        # AbletonOSC's reply: [track_id, name0, name1, ...] for the track's whole device list.
        reply = await client.request(
            "/live/track/get/devices/class_name", int(track_index)
        )
        classes = list(reply[1:])
        if device_index < 0 or device_index >= len(classes):
            return {
                "error": f"device index {device_index} out of range (track has {len(classes)} devices)",
                "track_index": track_index,
                "available": classes,
            }
        class_name = str(classes[device_index])
        schema = lookup_schema(class_name)
        if schema is None:
            return {
                "track_index": track_index,
                "device_index": device_index,
                "class_name": class_name,
                "error": f"no schema for {class_name!r}",
                "closest_match": closest_class_name(class_name),
            }
        out = schema.to_dict()
        out["track_index"] = track_index
        out["device_index"] = device_index
        return out

    @mcp.tool()
    async def device_schema_recommended_sweep_params(class_name: str) -> dict[str, Any]:
        """Just the params we recommend for sweeping on a given device."""
        s = lookup_schema(class_name)
        if s is None:
            return {
                "error": f"no schema for class_name {class_name!r}",
                "closest_match": closest_class_name(class_name),
            }
        params = [p.to_dict() for p in s.recommended_sweep_params()]
        return {
            "class_name": s.class_name,
            "display_name": s.display_name,
            "count": len(params),
            "parameters": params,
        }

    @mcp.tool()
    async def device_schema_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fuzzy search the catalogue by name / description / category / parameter name."""
        scored = [
            (_search_score(query, s), s) for s in DEVICE_SCHEMAS
        ]
        scored = [(score, s) for (score, s) in scored if score > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {**_summary_row(s), "score": round(score, 3)}
            for score, s in scored[: max(1, int(limit))]
        ]
