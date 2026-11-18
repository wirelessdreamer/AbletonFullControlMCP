"""Isolated import worker, including model inference and Live verification."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from .pipeline import prepare_project, write_json


def main() -> None:
    folder = Path(sys.argv[1]).resolve()
    request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    lock = threading.Lock()
    finished = threading.Event()
    state = {"job_id": folder.name, "state": "running", "stage": "starting", "pid": os.getpid()}

    def save(stage: str | None = None) -> None:
        with lock:
            if stage:
                state["stage"] = stage
            state["updated_at"] = time.time()
            write_json(folder / "job.json", state)

    def monitor() -> None:
        while not finished.wait(1):
            if (folder / "cancel").exists():
                if state.get("stage") == "verifying_live":
                    # Leave the app stopped and restore any isolated MIDI preview
                    # before terminating this process. Never stop an unrelated set.
                    async def stop_owned_project():
                        from ..bridge_client import AbletonBridgeClient
                        bridge = AbletonBridgeClient()
                        expected = str(Path(request["destination"]).expanduser().resolve() / "Song.als")
                        snapshot = await bridge.call("project.import_snapshot", timeout=2,
                                                     expected_set_path=expected)
                        current = snapshot.get("file_path")
                        if current and Path(current).resolve().is_relative_to(
                            Path(request["destination"]).expanduser().resolve()
                        ):
                            await bridge.call("project.import_stop", timeout=2, expected_set_path=expected)
                            await bridge.call("project.import_restore", timeout=2, expected_set_path=expected)
                    try:
                        asyncio.run(stop_owned_project())
                    except Exception:
                        pass
                    from ..paths import data_dir
                    live_lock = data_dir("project_import_jobs", "live-verification.lock")
                    if live_lock.is_file() and live_lock.read_text() == str(os.getpid()):
                        live_lock.unlink()
                with lock:
                    state.update(state="cancelled", stage="cancelled", updated_at=time.time())
                    write_json(folder / "job.json", state)
                # Inference is native code in this process. Exiting stops its threads and writes.
                # Never signal another process using a possibly recycled PID.
                os._exit(130)
            save()

    save()
    threading.Thread(target=monitor, daemon=True).start()
    try:
        verify_live = request.pop("verify_live", True)
        result = prepare_project(**request, progress=save)
        if verify_live and result["status"] != "inspected":
            from .verify import verify_project
            save("verifying_live")
            result = asyncio.run(verify_project(result["project_path"]))
        with lock:
            state.update(state="done", stage="done", result=result, updated_at=time.time())
            write_json(folder / "job.json", state)
    except Exception as exc:
        traceback.print_exc()
        with lock:
            state.update(state="failed", stage="failed", error=str(exc), updated_at=time.time())
            write_json(folder / "job.json", state)
    finally:
        finished.set()


if __name__ == "__main__":
    main()
