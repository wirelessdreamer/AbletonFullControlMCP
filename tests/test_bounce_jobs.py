"""Tests for background bounce jobs (bounce/jobs.py + background=True tools).

The engine functions are monkeypatched with controllable async fakes — no
Live, no audio. Fakes block on an asyncio.Event so tests can observe the
running state, then release or cancel deterministically.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from ableton_mcp.bounce import jobs
from ableton_mcp.tools import bounce as bounce_tools


async def _invoke(mcp: FastMCP, name: str, **args: Any) -> Any:
    """Call a registered MCP tool and unwrap the structured content."""
    result = await mcp.call_tool(name, args)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, list):
        return result[-1] if result else {}
    return result


@pytest.fixture(autouse=True)
def _clean_registry():
    jobs.reset_jobs()
    yield
    jobs.reset_jobs()


@pytest.fixture
def mcp() -> FastMCP:
    m = FastMCP("t")
    bounce_tools.register(m)
    return m


class GatedEngine:
    """Async fake engine: waits on an event, reports progress, returns."""

    def __init__(self, result: dict[str, Any] | None = None):
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.calls: list[dict[str, Any]] = []
        self.result = result if result is not None else {"copied": True, "output_path": "out.wav"}

    async def __call__(self, *args: Any, progress_callback=None, **kwargs: Any):
        self.calls.append({"args": args, "kwargs": kwargs})
        self.started.set()
        if progress_callback is not None:
            # Mirror the real engine's _report contract: a failing notifier
            # (e.g. ctx.report_progress outside a live request) never
            # disrupts the bounce.
            try:
                await progress_callback(0.5, "recording 30.0/60.0 s")
            except Exception:
                pass
        await self.gate.wait()
        return dict(self.result)


async def _wait_for_state(job_id: str, state: str, timeout: float = 2.0) -> dict[str, Any]:
    """Poll the registry until the job reaches ``state`` (or fail)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        snap = jobs.get_job(job_id).snapshot()
        if snap["state"] == state:
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached state {state!r}: {snap}")


# ---------------------------------------------------------------------------
# Registry unit tests
# ---------------------------------------------------------------------------


async def test_job_lifecycle_done() -> None:
    async def runner(progress_cb):
        await progress_cb(0.25, "warming up")
        return {"status": "ok", "wav": "x.wav"}

    job = jobs.start_job("bounce_song", runner)
    snap = await _wait_for_state(job.job_id, "done")
    assert snap["progress"] == 1.0
    assert snap["result"] == {"status": "ok", "wav": "x.wav"}
    assert snap["error"] is None


async def test_job_error_from_exception() -> None:
    async def runner(progress_cb):
        raise RuntimeError("Live exploded")

    job = jobs.start_job("bounce_song", runner)
    snap = await _wait_for_state(job.job_id, "error")
    assert "Live exploded" in snap["error"]


async def test_job_error_from_error_status_result() -> None:
    async def runner(progress_cb):
        return {"status": "error", "error": "no clip captured"}

    job = jobs.start_job("bounce_song", runner)
    snap = await _wait_for_state(job.job_id, "error")
    assert snap["error"] == "no clip captured"
    assert snap["result"]["status"] == "error"


async def test_job_cancel() -> None:
    gate = asyncio.Event()

    async def runner(progress_cb):
        await gate.wait()
        return {"status": "ok"}

    job = jobs.start_job("bounce_song", runner)
    await asyncio.sleep(0.01)
    cancelled = await jobs.cancel_job(job.job_id)
    assert cancelled.state == "cancelled"
    assert "Live state restored" in cancelled.message


async def test_single_flight_guard() -> None:
    gate = asyncio.Event()

    async def runner(progress_cb):
        await gate.wait()
        return {"status": "ok"}

    first = jobs.start_job("bounce_song", runner)
    with pytest.raises(jobs.BounceJobError, match=first.job_id):
        jobs.start_job("bounce_tracks", runner)
    gate.set()
    await _wait_for_state(first.job_id, "done")
    # After completion a new job may start.
    second = jobs.start_job("bounce_tracks", runner)
    gate.set()
    await _wait_for_state(second.job_id, "done")


async def test_unknown_job_id() -> None:
    with pytest.raises(jobs.BounceJobError, match="unknown bounce job"):
        jobs.get_job("nope")


async def test_list_jobs_newest_first_and_pruned() -> None:
    async def runner(progress_cb):
        return {"status": "ok"}

    ids = []
    for _ in range(jobs.MAX_FINISHED_JOBS + 5):
        job = jobs.start_job("bounce_song", runner)
        await _wait_for_state(job.job_id, "done")
        ids.append(job.job_id)
    listed = jobs.list_jobs()
    assert len(listed) == jobs.MAX_FINISHED_JOBS
    # Newest first; the oldest five were pruned.
    assert listed[0]["job_id"] == ids[-1]
    assert all(i not in {j["job_id"] for j in listed} for i in ids[:5])


# ---------------------------------------------------------------------------
# Tool-level: background=True on bounce_song
# ---------------------------------------------------------------------------


async def test_bounce_song_background_returns_job_id(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine()
    monkeypatch.setattr(bounce_tools, "bounce_song_via_resampling", engine)

    out = await _invoke(
        mcp, "bounce_song",
        output_path="song.wav", duration_sec=354.0,
        encode_mp3=False, background=True,
    )
    assert out["status"] == "started"
    job_id = out["job_id"]

    await asyncio.wait_for(engine.started.wait(), timeout=2.0)
    status = await _invoke(mcp, "bounce_job_status", job_id=job_id)
    assert status["job"]["state"] == "running"
    assert status["job"]["progress"] == pytest.approx(0.5)
    assert "recording" in status["job"]["message"]

    engine.gate.set()
    await _wait_for_state(job_id, "done")
    status = await _invoke(mcp, "bounce_job_status", job_id=job_id)
    assert status["job"]["result"]["status"] == "ok"
    assert status["job"]["result"]["wav"]["copied"] is True


async def test_bounce_song_background_busy_rejection(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine()
    monkeypatch.setattr(bounce_tools, "bounce_song_via_resampling", engine)

    first = await _invoke(
        mcp, "bounce_song", output_path="a.wav", duration_sec=60.0,
        encode_mp3=False, background=True,
    )
    await asyncio.wait_for(engine.started.wait(), timeout=2.0)
    second = await _invoke(
        mcp, "bounce_song", output_path="b.wav", duration_sec=60.0,
        encode_mp3=False, background=True,
    )
    assert second["status"] == "busy"
    assert second["active_job"]["job_id"] == first["job_id"]
    engine.gate.set()
    await _wait_for_state(first["job_id"], "done")


async def test_bounce_song_background_cancel_via_tool(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine()
    monkeypatch.setattr(bounce_tools, "bounce_song_via_resampling", engine)

    out = await _invoke(
        mcp, "bounce_song", output_path="song.wav", duration_sec=300.0,
        encode_mp3=False, background=True,
    )
    await asyncio.wait_for(engine.started.wait(), timeout=2.0)
    cancelled = await _invoke(mcp, "bounce_job_cancel", job_id=out["job_id"])
    assert cancelled["job"]["state"] == "cancelled"

    # Registry is free again.
    engine2 = GatedEngine()
    monkeypatch.setattr(bounce_tools, "bounce_song_via_resampling", engine2)
    again = await _invoke(
        mcp, "bounce_song", output_path="song2.wav", duration_sec=1.0,
        encode_mp3=False, background=True,
    )
    assert again["status"] == "started"
    engine2.gate.set()
    await _wait_for_state(again["job_id"], "done")


async def test_bounce_song_foreground_unchanged(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine()
    engine.gate.set()  # complete immediately
    monkeypatch.setattr(bounce_tools, "bounce_song_via_resampling", engine)
    out = await _invoke(
        mcp, "bounce_song", output_path="song.wav", duration_sec=5.0,
        encode_mp3=False,
    )
    assert out["status"] == "ok"
    assert out["wav"]["copied"] is True
    assert jobs.list_jobs() == []  # no job created for foreground calls


# ---------------------------------------------------------------------------
# Tool-level: background on the other realtime tools
# ---------------------------------------------------------------------------


async def test_bounce_tracks_background(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine(result={"stems": [], "status": "ok"})

    async def fake_tracks(*args: Any, **kwargs: Any):
        return await engine(*args, **kwargs)

    monkeypatch.setattr(bounce_tools, "bounce_tracks_via_resampling", fake_tracks)
    out = await _invoke(
        mcp, "bounce_tracks", track_indices=[0, 1], output_dir="d",
        duration_sec=200.0, encode_mp3=False, background=True,
    )
    assert out["status"] == "started"
    engine.gate.set()
    snap = await _wait_for_state(out["job_id"], "done")
    assert snap["result"]["status"] == "ok"


async def test_bounce_region_background_gets_engine_progress(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine(result={"kind": "region_master", "output_path": "r.wav", "copied": True})
    monkeypatch.setattr(bounce_tools, "bounce_region_via_resampling", engine)
    out = await _invoke(
        mcp, "bounce_region", output_dir="d", start_beats=0.0, end_beats=64.0,
        encode_mp3=False, background=True,
    )
    assert out["status"] == "started"
    await asyncio.wait_for(engine.started.wait(), timeout=2.0)
    status = await _invoke(mcp, "bounce_job_status", job_id=out["job_id"])
    # The engine's own progress callback reached the job record.
    assert status["job"]["progress"] == pytest.approx(0.5)
    engine.gate.set()
    await _wait_for_state(out["job_id"], "done")


async def test_bounce_job_status_unknown_id(mcp: FastMCP) -> None:
    out = await _invoke(mcp, "bounce_job_status", job_id="doesnotexist")
    assert out["status"] == "error"
    assert "unknown bounce job" in out["error"]


async def test_bounce_job_list_tool(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = GatedEngine()
    engine.gate.set()
    monkeypatch.setattr(bounce_tools, "bounce_song_via_resampling", engine)
    started = await _invoke(
        mcp, "bounce_song", output_path="x.wav", duration_sec=1.0,
        encode_mp3=False, background=True,
    )
    await _wait_for_state(started["job_id"], "done")
    listed = await _invoke(mcp, "bounce_job_list")
    assert listed["count"] == 1
    assert listed["jobs"][0]["job_id"] == started["job_id"]


# ---------------------------------------------------------------------------
# Non-exclusive jobs — pure file-math work must not queue behind a bounce
# ---------------------------------------------------------------------------


async def test_non_exclusive_job_runs_alongside_bounce() -> None:
    gate = asyncio.Event()

    async def blocking(progress_cb):
        await gate.wait()
        return {"status": "ok"}

    async def quick(progress_cb):
        return {"status": "ok", "variations": []}

    bounce = jobs.start_job("bounce_song", blocking)             # exclusive
    mixdown = jobs.start_job("song_make_variations", quick,      # non-exclusive
                             exclusive=False)
    snap = await _wait_for_state(mixdown.job_id, "done")
    assert snap["exclusive"] is False
    assert jobs.get_job(bounce.job_id).state == "running"
    gate.set()
    await _wait_for_state(bounce.job_id, "done")


async def test_non_exclusive_jobs_run_concurrently() -> None:
    async def quick(progress_cb):
        return {"status": "ok"}

    a = jobs.start_job("song_make_variations", quick, exclusive=False)
    b = jobs.start_job("song_make_variations", quick, exclusive=False)
    await _wait_for_state(a.job_id, "done")
    await _wait_for_state(b.job_id, "done")


async def test_exclusive_job_still_blocked_by_exclusive() -> None:
    gate = asyncio.Event()

    async def blocking(progress_cb):
        await gate.wait()
        return {"status": "ok"}

    first = jobs.start_job("bounce_song", blocking)
    with pytest.raises(jobs.BounceJobError, match=first.job_id):
        jobs.start_job("bounce_region", blocking)
    gate.set()
    await _wait_for_state(first.job_id, "done")


async def test_variations_background_returns_job_id(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """song_make_variations(background=True) returns immediately with a job."""
    from mcp.server.fastmcp import FastMCP as _F  # noqa: F401
    from ableton_mcp.tools import song_flow as song_flow_tools

    m = _F("t")
    song_flow_tools.register(m)

    def fake_make_variations(stems, output_dir, **kw):
        return {"status": "ok", "n_variations": 12, "variations": []}

    monkeypatch.setattr(song_flow_tools, "make_variations", fake_make_variations)
    out = await _invoke(
        m, "song_make_variations",
        stems=[{"name": "drums", "path": "d.wav"}],
        output_dir=str(tmp_path), output_set="practice_pack", background=True,
    )
    assert out["status"] == "started"
    snap = await _wait_for_state(out["job_id"], "done")
    assert snap["exclusive"] is False
    assert snap["result"]["n_variations"] == 12
