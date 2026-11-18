"""Disk-backed jobs. Workers survive MCP reconnects and own their output state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..paths import data_dir
from .pipeline import write_json


def job_folder(job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid import job ID")
    return data_dir("project_import_jobs", job_id)


def start(request: dict[str, Any], staged_sources: tuple[Path, list[str]] | None = None) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    folder = job_folder(job_id)
    folder.mkdir(parents=True)
    if staged_sources is not None:
        staging, names = staged_sources
        sources = folder.resolve() / "suno_export"
        staging.replace(sources)
        request = {**request, "source_paths": [str(sources / name) for name in names]}
    write_json(folder / "request.json", request)
    write_json(folder / "job.json", {"job_id": job_id, "state": "queued",
                                      "stage": "queued", "updated_at": time.time()})
    with (folder / "worker.log").open("ab") as log:
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        # Explicit cwd/PYTHONPATH makes source-checkout launches independent of MCP host cwd.
        env = dict(os.environ)
        package_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
        subprocess.Popen([sys.executable, "-m", "ableton_mcp.project_import.worker", str(folder)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, **options)
    return {"status": "started", "job_id": job_id,
            "hint": "Poll song_import_job_status; cancellation uses song_import_job_cancel"}


def status(job_id: str) -> dict[str, Any]:
    folder = job_folder(job_id)
    path = folder / "job.json"
    if not path.is_file():
        raise ValueError("Import job not found")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["state"] in {"running", "queued"} and time.time() - value["updated_at"] > 60:
        value["state"] = "interrupted"
        value["hint"] = "Worker heartbeat expired; resume creates a new job using validated cached stems"
    value["log_path"] = str(folder / "worker.log")
    return value


def cancel(job_id: str) -> dict[str, Any]:
    value = status(job_id)
    if value["state"] not in {"running", "queued"}:
        return value
    (job_folder(job_id) / "cancel").touch()
    return {"job_id": job_id, "state": "cancel_requested",
            "hint": "Worker will stop within its next heartbeat; poll for cancelled"}


def resume(job_id: str, destination: str | None = None) -> dict[str, Any]:
    value = status(job_id)
    if value["state"] in {"running", "queued"}:
        raise ValueError("Job is still active")
    request = json.loads((job_folder(job_id) / "request.json").read_text(encoding="utf-8"))
    if destination:
        request["destination"] = destination
    if Path(request["destination"]).exists():
        raise ValueError("A project already exists at the destination; verify it or resume to a new folder")
    return start(request)
