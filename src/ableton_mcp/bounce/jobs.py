"""In-process job registry for background bounces.

Why this exists: a resampling bounce is realtime — a 6-minute piece is a
6-minute tool call — and MCP clients treat long calls specially (Claude
Code auto-backgrounds calls at 2 minutes; other hosts enforce hard
timeouts). For unattended batch work ("render these 100 songs") the tools
accept ``background=True``: the call returns a ``job_id`` in milliseconds
while the render continues inside the server process, and the caller polls
``bounce_job_status`` between other work.

Single-flight by design: Live has one transport, so only one bounce job may
run at a time — ``start_job`` refuses while another job is running. Batch
orchestration is therefore poll-until-done, then start the next song.

The registry is process-local (lost if the MCP server restarts) and holds
the last :data:`MAX_FINISHED_JOBS` finished jobs for post-hoc inspection.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# runner(progress_callback) -> result dict. The callback signature matches
# resampling.ProgressCallback: (progress 0..1, message) -> Awaitable[None].
JobRunner = Callable[[Callable[[float, str], Awaitable[None]]], Awaitable[dict[str, Any]]]

MAX_FINISHED_JOBS = 20

_VALID_STATES = ("running", "done", "error", "cancelled")


class BounceJobError(RuntimeError):
    """Job registry refusal (busy, unknown id, ...)."""


# Creation-order tiebreaker: time.time() has ~16 ms granularity on Windows,
# so jobs started back-to-back tie on created_at.
_SEQ = itertools.count()


@dataclass
class BounceJob:
    job_id: str
    operation: str
    state: str = "running"
    exclusive: bool = True  # needs Live's transport to itself
    seq: int = field(default_factory=lambda: next(_SEQ))
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    progress: float = 0.0
    message: str = "starting"
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe view (no task handle)."""
        elapsed = (self.finished_at or time.time()) - self.created_at
        return {
            "job_id": self.job_id,
            "operation": self.operation,
            "state": self.state,
            "exclusive": self.exclusive,
            "progress": round(self.progress, 4),
            "message": self.message,
            "elapsed_sec": round(elapsed, 1),
            "result": self.result,
            "error": self.error,
        }


_JOBS: dict[str, BounceJob] = {}


def reset_jobs() -> None:
    """Test hook: forget all jobs (does not cancel running tasks)."""
    _JOBS.clear()


def active_job() -> BounceJob | None:
    """Any running job (newest-first is not guaranteed; used for reporting)."""
    for job in _JOBS.values():
        if job.state == "running":
            return job
    return None


def active_exclusive_job() -> BounceJob | None:
    """The running job that holds Live's transport, if any.

    Only exclusive jobs conflict. Pure file-math jobs (mixdowns, encodes)
    run concurrently with a bounce and with each other.
    """
    for job in _JOBS.values():
        if job.state == "running" and job.exclusive:
            return job
    return None


def _prune_finished() -> None:
    finished = [j for j in _JOBS.values() if j.state != "running"]
    finished.sort(key=lambda j: j.seq)
    for job in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
        del _JOBS[job.job_id]


def start_job(
    operation: str, runner: JobRunner, *, exclusive: bool = True
) -> BounceJob:
    """Launch ``runner`` as a background task and track it.

    ``exclusive=True`` (default) means the job drives Live's transport:
    only one such job runs at a time, and starting another raises
    :class:`BounceJobError` (two overlapping bounces would corrupt each
    other's audio). Pass ``exclusive=False`` for pure file-math jobs
    (stem mixdowns, mp3 encodes) — they touch no Live state, so they run
    concurrently with a bounce and with each other.
    """
    if exclusive:
        active = active_exclusive_job()
        if active is not None:
            raise BounceJobError(
                f"bounce job {active.job_id} ({active.operation}) is still running "
                f"— Live can only bounce one thing at a time; poll "
                f"bounce_job_status({active.job_id!r}) and retry when it finishes"
            )

    job = BounceJob(
        job_id=uuid.uuid4().hex[:12], operation=operation, exclusive=exclusive
    )

    async def _on_progress(progress: float, message: str) -> None:
        job.progress = float(progress)
        job.message = str(message)

    async def _run() -> None:
        try:
            result = await runner(_on_progress)
            job.result = result
            if isinstance(result, dict) and result.get("status") == "error":
                job.state = "error"
                job.error = str(result.get("error") or "bounce reported an error")
                job.message = "failed"
            else:
                job.state = "done"
                job.progress = 1.0
                job.message = "complete"
        except asyncio.CancelledError:
            # The engine's cancellation contract already restored Live's
            # state (transport stopped, temp tracks deleted) on the way up.
            job.state = "cancelled"
            job.message = "cancelled; Live state restored"
        except Exception as exc:  # noqa: BLE001 — job boundary
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "failed"
            log.exception("bounce job %s (%s) failed", job.job_id, job.operation)
        finally:
            job.finished_at = time.time()
            _prune_finished()

    job.task = asyncio.create_task(_run())
    _JOBS[job.job_id] = job
    return job


def get_job(job_id: str) -> BounceJob:
    job = _JOBS.get(str(job_id))
    if job is None:
        known = [j.job_id for j in _JOBS.values()]
        raise BounceJobError(
            f"unknown bounce job {job_id!r}; known jobs: {known or 'none'} "
            f"(the registry is process-local and empties on server restart)"
        )
    return job


async def cancel_job(job_id: str) -> BounceJob:
    """Cancel a running job and wait for its cleanup to finish."""
    job = get_job(job_id)
    if job.state != "running" or job.task is None:
        return job
    job.task.cancel()
    try:
        await job.task
    except asyncio.CancelledError:  # pragma: no cover — defensive
        pass
    return job


def list_jobs() -> list[dict[str, Any]]:
    """Snapshots, newest first."""
    return [
        j.snapshot()
        for j in sorted(_JOBS.values(), key=lambda j: j.seq, reverse=True)
    ]
