"""FastMCP server entry — registers all tools and runs over stdio."""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .config import Config
from .tools import (
    arrangement,
    audio_analysis,
    bench,
    bounce,
    browser,
    clip_slots,
    clips,
    cue_points,
    device_schemas,
    devices,
    high_level,
    inventory,
    knowledge,
    listeners,
    midi_files,
    midi_mapping,
    mix,
    presets,
    project,
    render,
    routing,
    scenes,
    semantics,
    sound_design,
    song_flow,
    sound_modeling,
    sound_shaping,
    structure,
    suno,
    tracks,
    transport,
    view,
)

log = logging.getLogger("ableton_mcp")


def build_server() -> FastMCP:
    cfg = Config.from_env()
    logging.basicConfig(
        level=cfg.log_level,
        stream=sys.stderr,  # MCP stdio uses stdout; logs MUST go to stderr
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp = FastMCP(
        "ableton-mcp",
        instructions=(
            "Tools for controlling Ableton Live 11 via AbletonOSC. "
            "Indices are 0-based; time is in beats unless noted. "
            "Categories: live_* transport, track_* mixer, clip_* session clips, "
            "clip_slot_* slot ops, scene_* scenes, cue_point_* locators, "
            "arrangement_* timeline view, view_* selection, routing_* I/O, "
            "device_* parameters, midi_map_* mapping, midi_file_* offline .mid editing, "
            "audio_* feature extraction, listen_* event subscriptions, op_* high-level ops, "
            "browser_*, render_*, sound_*, mix_* (multi-track masking analysis + "
            "propose/apply/verify loop driven by intent words like 'cuts_through', "
            "'muddy', 'harsh'), ableton_* (Phase 2-4 stubs)."
        ),
    )

    transport.register(mcp)
    tracks.register(mcp)
    clips.register(mcp)
    clip_slots.register(mcp)
    scenes.register(mcp)
    cue_points.register(mcp)
    view.register(mcp)
    arrangement.register(mcp)
    routing.register(mcp)
    devices.register(mcp)
    midi_mapping.register(mcp)
    midi_files.register(mcp)
    audio_analysis.register(mcp)
    listeners.register(mcp)
    high_level.register(mcp)
    browser.register(mcp)
    render.register(mcp)
    sound_modeling.register(mcp)
    knowledge.register(mcp)
    suno.register(mcp)
    # Sound understanding stack — device schemas, semantic vocabulary,
    # NL shaping engine, in-process synth bench, preset library.
    device_schemas.register(mcp)
    semantics.register(mcp)
    sound_shaping.register(mcp)
    bench.register(mcp)
    presets.register(mcp)
    # Live integration — inventory of installed instruments.
    inventory.register(mcp)
    # Bounce / export pipeline — wav + mp3, full mix and stems.
    bounce.register(mcp)
    # Song structure — bar-counted dialect, sections, edit/loop/jump.
    structure.register(mcp)
    # Sound design — per-device curated rules: NL descriptor → which knobs.
    sound_design.register(mcp)
    # Song flow — analyze, transpose, instrument-up variations, bulk import.
    song_flow.register(mcp)
    # One-call session snapshot for question-answering ("what's on track 3?").
    project.register(mcp)
    # Mix-aware shaping — multi-track masking analysis, propose/apply/verify
    # loop driven by intent words ("cuts_through", "muddy", "harsh", ...).
    mix.register(mcp)

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
