"""Track input/output routing types & channels (Ext. In, Master, Sends, Resampling, etc.)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..osc_client import get_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def routing_available_inputs(track_index: int) -> dict[str, list[Any]]:
        """List input routing types and channels available for a track."""
        client = await get_client()
        types = (await client.request("/live/track/get/available_input_routing_types", int(track_index)))[1:]
        channels = (await client.request("/live/track/get/available_input_routing_channels", int(track_index)))[1:]
        return {"track": track_index, "types": list(types), "channels": list(channels)}

    @mcp.tool()
    async def routing_available_outputs(track_index: int) -> dict[str, list[Any]]:
        """List output routing types and channels available for a track."""
        client = await get_client()
        types = (await client.request("/live/track/get/available_output_routing_types", int(track_index)))[1:]
        channels = (await client.request("/live/track/get/available_output_routing_channels", int(track_index)))[1:]
        return {"track": track_index, "types": list(types), "channels": list(channels)}

    @mcp.tool()
    async def routing_get(track_index: int) -> dict[str, Any]:
        """Get the current input/output routing for a track."""
        client = await get_client()
        in_t = (await client.request("/live/track/get/input_routing_type", int(track_index)))[1]
        in_c = (await client.request("/live/track/get/input_routing_channel", int(track_index)))[1]
        out_t = (await client.request("/live/track/get/output_routing_type", int(track_index)))[1]
        out_c = (await client.request("/live/track/get/output_routing_channel", int(track_index)))[1]
        return {
            "track": track_index,
            "input_routing_type": in_t,
            "input_routing_channel": in_c,
            "output_routing_type": out_t,
            "output_routing_channel": out_c,
        }

    @mcp.tool()
    async def routing_set_input(track_index: int, type_name: str, channel_name: str | None = None) -> dict[str, Any]:
        """Set a track's input routing by name (must match an available type / channel)."""
        client = await get_client()
        client.send("/live/track/set/input_routing_type", int(track_index), str(type_name))
        if channel_name is not None:
            client.send("/live/track/set/input_routing_channel", int(track_index), str(channel_name))
        return {"track": track_index, "input_routing_type": type_name, "input_routing_channel": channel_name}

    @mcp.tool()
    async def routing_set_output(track_index: int, type_name: str, channel_name: str | None = None) -> dict[str, Any]:
        """Set a track's output routing by name (must match an available type / channel)."""
        client = await get_client()
        client.send("/live/track/set/output_routing_type", int(track_index), str(type_name))
        if channel_name is not None:
            client.send("/live/track/set/output_routing_channel", int(track_index), str(channel_name))
        return {"track": track_index, "output_routing_type": type_name, "output_routing_channel": channel_name}
