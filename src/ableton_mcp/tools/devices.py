"""Device introspection and parameter editing."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def device_list(track_index: int) -> list[dict[str, Any]]:
        """List devices on a track: index, name, class_name, type."""
        client = await get_client()
        n_args = await client.request("/live/track/get/num_devices", int(track_index))
        n = int(n_args[1])
        names = await client.request("/live/track/get/devices/name", int(track_index))
        types = await client.request("/live/track/get/devices/type", int(track_index))
        classes = await client.request("/live/track/get/devices/class_name", int(track_index))

        # Replies are [track_id, name0, name1, ...]
        name_list = list(names[1:])
        type_list = list(types[1:])
        class_list = list(classes[1:])
        out: list[dict[str, Any]] = []
        for i in range(n):
            out.append(
                {
                    "track_index": track_index,
                    "device_index": i,
                    "name": name_list[i] if i < len(name_list) else None,
                    "type": type_list[i] if i < len(type_list) else None,
                    "class_name": class_list[i] if i < len(class_list) else None,
                }
            )
        return out

    @mcp.tool()
    async def device_get_parameters(track_index: int, device_index: int) -> list[dict[str, Any]]:
        """Return all parameters of a device with current value, min, max, quantized flag."""
        client = await get_client()
        n_args = await client.request(
            "/live/device/get/num_parameters", int(track_index), int(device_index)
        )
        n = int(n_args[2])
        names = (await client.request(
            "/live/device/get/parameters/name", int(track_index), int(device_index)
        ))[2:]
        values = (await client.request(
            "/live/device/get/parameters/value", int(track_index), int(device_index)
        ))[2:]
        mins = (await client.request(
            "/live/device/get/parameters/min", int(track_index), int(device_index)
        ))[2:]
        maxs = (await client.request(
            "/live/device/get/parameters/max", int(track_index), int(device_index)
        ))[2:]
        quant = (await client.request(
            "/live/device/get/parameters/is_quantized", int(track_index), int(device_index)
        ))[2:]
        out: list[dict[str, Any]] = []
        for i in range(n):
            out.append(
                {
                    "index": i,
                    "name": names[i] if i < len(names) else None,
                    "value": float(values[i]) if i < len(values) else None,
                    "min": float(mins[i]) if i < len(mins) else None,
                    "max": float(maxs[i]) if i < len(maxs) else None,
                    "quantized": bool(quant[i]) if i < len(quant) else False,
                }
            )
        return out

    @mcp.tool()
    async def device_set_parameter(
        track_index: int, device_index: int, parameter_index: int, value: float
    ) -> dict[str, Any]:
        """Set a single device parameter by index."""
        (await get_client()).send(
            "/live/device/set/parameter/value",
            int(track_index),
            int(device_index),
            int(parameter_index),
            float(value),
        )
        return {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "value": value,
        }

    @mcp.tool()
    async def device_set_parameter_by_name(
        track_index: int, device_index: int, parameter_name: str, value: float
    ) -> dict[str, Any]:
        """Set a device parameter by case-insensitive name match."""
        client = await get_client()
        names = (await client.request(
            "/live/device/get/parameters/name", int(track_index), int(device_index)
        ))[2:]
        target = parameter_name.strip().lower()
        for i, n in enumerate(names):
            if str(n).strip().lower() == target:
                client.send(
                    "/live/device/set/parameter/value",
                    int(track_index),
                    int(device_index),
                    i,
                    float(value),
                )
                return {
                    "track_index": track_index,
                    "device_index": device_index,
                    "parameter_index": i,
                    "name": str(n),
                    "value": value,
                }
        return {"error": f"no parameter named {parameter_name!r}", "available": list(names)}

    @mcp.tool()
    async def device_get_parameter_string(
        track_index: int, device_index: int, parameter_index: int
    ) -> dict[str, Any]:
        """Get a parameter's display string (e.g. '440 Hz' instead of raw float)."""
        client = await get_client()
        args = await client.request(
            "/live/device/get/parameter/value_string",
            int(track_index),
            int(device_index),
            int(parameter_index),
        )
        return {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "value_string": args[3] if len(args) > 3 else None,
        }
