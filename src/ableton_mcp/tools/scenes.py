"""Scene control: launch, name, color, per-scene tempo and time signature, lifecycle."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def scene_list() -> list[dict[str, Any]]:
        """Return every scene with name, color, is_empty, is_triggered, optional tempo/signature overrides."""
        client = await get_client()
        n = int((await client.request("/live/song/get/num_scenes"))[0])
        out: list[dict[str, Any]] = []
        for i in range(n):
            name = (await client.request("/live/scene/get/name", i))[1]
            color = (await client.request("/live/scene/get/color_index", i))[1]
            empty = (await client.request("/live/scene/get/is_empty", i))[1]
            triggered = (await client.request("/live/scene/get/is_triggered", i))[1]
            tempo_enabled = (await client.request("/live/scene/get/tempo_enabled", i))[1]
            ts_enabled = (await client.request("/live/scene/get/time_signature_enabled", i))[1]
            entry: dict[str, Any] = {
                "index": i,
                "name": name,
                "color_index": int(color),
                "is_empty": bool(empty),
                "is_triggered": bool(triggered),
                "tempo_enabled": bool(tempo_enabled),
                "time_signature_enabled": bool(ts_enabled),
            }
            if tempo_enabled:
                entry["tempo"] = (await client.request("/live/scene/get/tempo", i))[1]
            if ts_enabled:
                num = (await client.request("/live/scene/get/time_signature_numerator", i))[1]
                den = (await client.request("/live/scene/get/time_signature_denominator", i))[1]
                entry["time_signature"] = f"{num}/{den}"
            out.append(entry)
        return out

    @mcp.tool()
    async def scene_create(at_index: int = -1, name: str | None = None) -> dict[str, Any]:
        """Create a new scene. at_index=-1 appends."""
        client = await get_client()
        client.send("/live/song/create_scene", int(at_index))
        n = int((await client.request("/live/song/get/num_scenes"))[0])
        new_index = n - 1 if at_index < 0 else at_index
        if name:
            client.send("/live/scene/set/name", new_index, name)
        return {"index": new_index, "name": name}

    @mcp.tool()
    async def scene_delete(scene_index: int) -> dict[str, int]:
        (await get_client()).send("/live/song/delete_scene", int(scene_index))
        return {"deleted_index": scene_index}

    @mcp.tool()
    async def scene_duplicate(scene_index: int) -> dict[str, int]:
        (await get_client()).send("/live/song/duplicate_scene", int(scene_index))
        return {"duplicated_from": scene_index}

    @mcp.tool()
    async def scene_fire(scene_index: int) -> dict[str, int]:
        """Launch every clip on a scene."""
        (await get_client()).send("/live/scene/fire", int(scene_index))
        return {"scene": scene_index, "status": "fired"}

    @mcp.tool()
    async def scene_fire_selected() -> dict[str, str]:
        """Launch the currently selected scene."""
        (await get_client()).send("/live/scene/fire_selected")
        return {"status": "selected_scene_fired"}

    @mcp.tool()
    async def scene_fire_as_selected(scene_index: int) -> dict[str, int]:
        """Select a scene and launch it (advances the selection per Live's setting)."""
        (await get_client()).send("/live/scene/fire_as_selected", int(scene_index))
        return {"scene": scene_index, "status": "fired_as_selected"}

    @mcp.tool()
    async def scene_set_name(scene_index: int, name: str) -> dict[str, Any]:
        (await get_client()).send("/live/scene/set/name", int(scene_index), str(name))
        return {"scene": scene_index, "name": name}

    @mcp.tool()
    async def scene_set_color_index(scene_index: int, color_index: int) -> dict[str, Any]:
        (await get_client()).send(
            "/live/scene/set/color_index", int(scene_index), int(color_index)
        )
        return {"scene": scene_index, "color_index": color_index}

    @mcp.tool()
    async def scene_set_tempo(scene_index: int, bpm: float, enabled: bool = True) -> dict[str, Any]:
        """Set scene tempo override."""
        client = await get_client()
        client.send("/live/scene/set/tempo", int(scene_index), float(bpm))
        client.send("/live/scene/set/tempo_enabled", int(scene_index), 1 if enabled else 0)
        return {"scene": scene_index, "tempo": bpm, "enabled": enabled}

    @mcp.tool()
    async def scene_set_time_signature(
        scene_index: int, numerator: int, denominator: int, enabled: bool = True
    ) -> dict[str, Any]:
        """Set scene time signature override."""
        client = await get_client()
        client.send("/live/scene/set/time_signature_numerator", int(scene_index), int(numerator))
        client.send("/live/scene/set/time_signature_denominator", int(scene_index), int(denominator))
        client.send("/live/scene/set/time_signature_enabled", int(scene_index), 1 if enabled else 0)
        return {
            "scene": scene_index,
            "time_signature": f"{numerator}/{denominator}",
            "enabled": enabled,
        }
