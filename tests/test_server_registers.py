"""Sanity test: the FastMCP server boots and registers tools without touching Ableton."""

from __future__ import annotations

import pytest

from ableton_mcp.server import build_server


@pytest.mark.asyncio
async def test_server_builds_and_registers_tools() -> None:
    mcp = build_server()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        # Transport
        "live_ping", "live_get_state", "live_play", "live_stop", "live_set_tempo",
        "live_tap_tempo", "live_set_time_signature", "live_capture_midi",
        "live_session_record", "live_set_punch", "live_set_groove_amount",
        "live_set_clip_trigger_quantization",
        # Tracks
        "track_list", "track_get", "track_get_meters", "track_create_midi",
        "track_create_audio", "track_create_return", "track_set_volume",
        "track_set_color_index", "track_set_fold", "track_set_monitoring",
        # Clips
        "clip_list", "clip_get", "clip_create_midi", "clip_fire", "clip_stop",
        "clip_set_loop", "clip_set_markers", "clip_set_warp_mode", "clip_set_launch_mode",
        "clip_set_pitch", "clip_set_gain", "clip_get_notes", "clip_add_notes",
        "clip_remove_notes", "clip_duplicate_loop",
        # Clip slots
        "clip_slot_has_clip", "clip_slot_set_stop_button", "clip_slot_duplicate_to",
        # Scenes
        "scene_list", "scene_create", "scene_fire", "scene_set_tempo", "scene_set_time_signature",
        # Cue points
        "cue_points_list", "cue_point_jump", "cue_point_jump_next",
        # View
        "view_get_selection", "view_select_track", "view_select_clip", "view_select_device",
        # Arrangement
        "arrangement_clips_list", "arrangement_summary",
        # Routing
        "routing_available_inputs", "routing_get", "routing_set_output",
        # Devices
        "device_list", "device_get_parameters", "device_set_parameter_by_name",
        # MIDI mapping
        "midi_map_cc",
        # MIDI files
        "midi_file_summary", "midi_file_load_into_clip", "midi_file_export_from_clip",
        # Audio analysis
        "audio_analyze", "audio_compare",
        # Listeners
        "listen_song", "listen_track", "listen_view", "listen_clip_playing_position",
        "listen_device_parameter", "listen_poll", "listen_stop", "listen_list",
        # High-level stubs
        "op_group_tracks", "op_freeze_track", "op_consolidate_clip",
        # Future-phase stubs
        "browser_search", "render_master", "sound_probe_device",
        "ableton_search_docs", "suno_generate",
    }
    missing = expected - names
    assert not missing, f"missing tools: {sorted(missing)}"
    # Sanity floor on total registered tools.
    assert len(names) >= 120, f"expected ≥120 tools, got {len(names)}"
