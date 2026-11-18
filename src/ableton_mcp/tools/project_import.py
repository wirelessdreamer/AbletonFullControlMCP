"""Prepared-stem and Demucs-recording project workflows."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..paths import data_dir
from ..project_import import jobs
from ..project_import.pipeline import prepare_project
from ..project_import.suno_export import ExportFile, stage_export


async def _job_result(started: dict, background: bool) -> dict:
    if background:
        return started
    while True:
        value = jobs.status(started["job_id"])
        if value["state"] not in {"queued", "running"}:
            return value.get("result", value)
        await asyncio.sleep(1)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def song_use_import_template(template_als_path: str) -> dict[str, Any]:
        """Use a natively saved Live 11 set as local scaffolding for future imports.

        The file must be the currently loaded set, saved by this running build.
        Caches a local copy with its genuine schema/edition metadata. Generated
        tracks discard source clips/devices. Does not modify or save Live edits.
        """
        from ..bridge_client import AbletonBridgeClient
        from ..project_import.templates import remember_saved_template
        try:
            bridge = AbletonBridgeClient()
            identity = await bridge.call("project.get_file_path")
            info = await bridge.call("project.info")
            path = Path(template_als_path).expanduser().resolve()
            if identity.get("path_evidence") != "native" or Path(identity.get("file_path") or "").resolve() != path:
                raise ValueError("Select the currently loaded, natively saved set")
            result = await asyncio.to_thread(remember_saved_template, path, info["live_version"])
            return {"status": "selected", **result, "screen_control": False}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    @mcp.tool()
    async def song_prepare_project(
        source_paths: list[str], destination: str, source_mode: str = "auto",
        bpm: float | None = None, time_signature: list[int] | None = None,
        downbeat_sec: float | None = None, tempo_map: list[list[float]] | None = None,
        reference_path: str | None = None, offsets_seconds: dict[str, float] | None = None,
        midi_pairings: dict[str, str] | None = None, template_als_path: str | None = None,
        audio_roles: dict[str, str] | None = None,
        inspect_only: bool = False, verify_live: bool = True, background: bool = True,
        require_midi: bool = False,
    ) -> dict[str, Any]:
        """Create a portable Arrangement project from stems or one recording.

        A single audio file in auto mode runs Demucs htdemucs_6s; a ZIP/folder
        imports prepared stems. Use prepared_stems explicitly for a single stem.
        Existing destinations are never overwritten. Supplied BPM/meter/downbeat
        help establish the grid. MIDI and reference mix are muted on first playback.
        Returns started/job_id by default; poll song_import_job_status. A static
        build or uncertain alignment returns needs_review, never a false ready.
        Set require_midi when the requested deliverable must include MIDI notes.
        """
        request = dict(source_paths=source_paths, destination=destination, source_mode=source_mode,
                       bpm=bpm, time_signature=time_signature, downbeat_sec=downbeat_sec,
                       tempo_map=tempo_map, reference_path=reference_path,
                       offsets_seconds=offsets_seconds, midi_pairings=midi_pairings,
                       template_als_path=template_als_path, audio_roles=audio_roles, inspect_only=inspect_only,
                       verify_live=verify_live, require_midi=require_midi)
        try:
            if inspect_only:
                request.pop("verify_live")
                return await asyncio.to_thread(prepare_project, **request)
            return await _job_result(jobs.start(request), background)
        except (ValueError, OSError) as exc:
            return {"status": "failed", "error": str(exc)}

    @mcp.tool()
    async def suno_prepare_project(
        source_paths: list[str], destination: str, bpm: float | None = None,
        time_signature: list[int] | None = None, downbeat_sec: float | None = None,
        reference_path: str | None = None, midi_pairings: dict[str, str] | None = None,
        template_als_path: str | None = None, background: bool = True, require_midi: bool = False,
    ) -> dict[str, Any]:
        """Import an already separated Suno download; never runs Demucs again.

        Set require_midi when companion MIDI is expected. Use song_prepare_project
        for offsets, tempo maps or inspection options.
        """
        return await song_prepare_project(
            source_paths, destination, source_mode="prepared_stems", bpm=bpm,
            time_signature=time_signature, downbeat_sec=downbeat_sec, reference_path=reference_path,
            midi_pairings=midi_pairings, template_als_path=template_als_path, background=background,
            require_midi=require_midi)

    @mcp.tool()
    async def suno_import_mcp_export(
        export_files: list[ExportFile], destination: str, require_midi: bool = True,
        bpm: float | None = None, time_signature: list[int] | None = None,
        downbeat_sec: float | None = None, midi_pairings: dict[str, str] | None = None,
        offsets_seconds: dict[str, float] | None = None, reference_path: str | None = None,
        verify_live: bool = True, background: bool = True,
    ) -> dict[str, Any]:
        """Pass a completed stem/MIDI export from Suno MCP to Ableton.

        Use Suno MCP in the same client to get actual downloadable export files,
        then pass their direct HTTPS Suno URLs and filenames here. Accepts a ZIP
        or loose stem/MIDI files. Song/share pages are not export files. If the
        host already saved files locally, call suno_prepare_project instead.
        No Suno sign-in or credit-spending action happens in this tool. MIDI is
        required by default; set require_midi=false only for an audio-only import.
        Downloads finish before the job ID is returned; retries reuse local files.
        """
        target = Path(destination).expanduser().resolve()
        try:
            if target.exists():
                raise ValueError(f"Destination exists; choose a new project folder: {target}")
            root = data_dir("project_import_jobs").resolve()
            if target.is_relative_to(root):
                raise ValueError("Choose a destination outside the import jobs directory")
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".suno-export-", dir=root) as temporary:
                stage = Path(temporary) / "files"
                stage.mkdir()
                names = await asyncio.to_thread(stage_export, export_files, stage)
                started = jobs.start(dict(destination=str(target), source_mode="prepared_stems",
                                          require_midi=require_midi, bpm=bpm, time_signature=time_signature,
                                          downbeat_sec=downbeat_sec, midi_pairings=midi_pairings,
                                          offsets_seconds=offsets_seconds, reference_path=reference_path,
                                          verify_live=verify_live), staged_sources=(stage, names))
            return await _job_result(started, background)
        except (ValueError, OSError) as exc:
            return {"status": "failed", "error": str(exc)}

    @mcp.tool()
    async def song_import_job_status(job_id: str) -> dict[str, Any]:
        """Read persisted import progress/result, including after an MCP reconnect."""
        try:
            return jobs.status(job_id)
        except (ValueError, OSError) as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    async def song_import_job_cancel(job_id: str) -> dict[str, Any]:
        """Request worker exit, stopping separation and retaining incomplete staging for diagnosis."""
        try:
            return jobs.cancel(job_id)
        except (ValueError, OSError) as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    async def song_import_job_resume(job_id: str, destination: str | None = None) -> dict[str, Any]:
        """Retry interrupted/failed work with validated separation cache; preserve existing projects."""
        try:
            return jobs.resume(job_id, destination)
        except (ValueError, OSError) as exc:
            return {"status": "error", "error": str(exc)}

    @mcp.tool()
    async def song_verify_imported_project(project_path: str) -> dict[str, Any]:
        """Open, inspect, audition, checkpoint/reopen and relocate an imported project in Live.

        Requires the updated bridge. Refuses to replace a set with undoable edits.
        Writes portable artifacts directly; Live 11 native save is unsupported.
        Uses no screen control.
        """
        from ..project_import.verify import verify_project
        return await verify_project(project_path)
