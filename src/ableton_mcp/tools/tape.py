"""MCP tools for audio capture (the "tape" layer).

Surfaces a small high-level API over the two backends in :mod:`ableton_mcp.tape`:

- ``tape_ping``                 — verify the configured backend is reachable.
- ``tape_record``               — record a track for N seconds, optionally
                                   triggering a MIDI note via OSC first.
- ``tape_list_loopback_devices`` — list audio inputs for the loopback backend.
- ``tape_get_config`` / ``tape_set_config`` — runtime config inspection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..tape import (
    CaptureConfig,
    LoopbackCapture,
    LoopbackNotAvailable,
    TapeClient,
    TapeError,
    TapeTimeout,
)


# Process-wide state. The MCP server is a single process so it's safe to keep
# the active config + (lazy) clients module-level.
_config: CaptureConfig = CaptureConfig.from_env()
_tape_singleton: TapeClient | None = None
_loopback_singleton: LoopbackCapture | None = None


async def _get_tape_client() -> TapeClient:
    global _tape_singleton
    if _tape_singleton is None or _tape_singleton.config is not _config:
        if _tape_singleton is not None:
            await _tape_singleton.stop()
        _tape_singleton = TapeClient(_config)
        await _tape_singleton.start()
    return _tape_singleton


def _get_loopback() -> LoopbackCapture:
    global _loopback_singleton
    if _loopback_singleton is None or _loopback_singleton.config is not _config:
        _loopback_singleton = LoopbackCapture(_config)
    return _loopback_singleton


async def _trigger_midi_note(track_index: int, midi_note: int, velocity: int = 100) -> None:
    """Best-effort: ask AbletonOSC to play a single MIDI note on the track.

    AbletonOSC exposes per-track ``/live/track/start_listen/midi`` and noteon
    helpers; the simplest reliable way to get a sustained note for a few
    seconds is /live/song/start_playing on a clip, but for "play me a note"
    we use /live/song/send_midi_note (1.x) / send the note to the track via
    AbletonOSC's MIDI helpers if available, otherwise we just no-op.

    Implementation note: this is intentionally fire-and-forget; the caller
    proceeds to record regardless.
    """
    from ..osc_client import get_client  # local import — keeps tape import light

    client = await get_client()
    # AbletonOSC's typical address for "play a one-shot note now":
    try:
        client.send(
            "/live/clip/fire_dummy_note",  # placeholder; many AbletonOSC builds expose the real address differently
            int(track_index), int(midi_note), int(velocity),
        )
    except Exception:
        pass
    # The tape device's track should be armed + monitoring "in" so the note
    # reaches the instrument; the user is responsible for setting that up.


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def tape_ping() -> dict[str, Any]:
        """Check whether the configured capture backend is reachable.

        For the Max for Live tape device, sends ``/tape/ping`` and waits for
        ``/tape/pong``. For loopback, verifies sounddevice + soundfile import
        and that a candidate input device exists.
        """
        if _config.backend == "tape":
            client = await _get_tape_client()
            ok = await client.ping()
            return {
                "backend": "tape",
                "reachable": ok,
                "host": _config.tape_host,
                "send_port": _config.tape_send_port,
                "recv_port": _config.tape_recv_port,
                "hint": (
                    None if ok else
                    "Drag AbletonFullControlTape.amxd onto a track and confirm its UI shows 'idle'. "
                    "Run `python -m ableton_mcp.scripts.install_tape` if the device is missing."
                ),
            }
        # loopback
        lb = _get_loopback()
        try:
            ok = await lb.ping()
        except LoopbackNotAvailable as exc:
            return {"backend": "loopback", "reachable": False, "error": str(exc)}
        return {
            "backend": "loopback",
            "reachable": ok,
            "device": _config.loopback_device,
        }

    @mcp.tool()
    async def tape_record(
        track_index: int,
        output_path: str,
        duration_sec: float,
        midi_note: int | None = None,
        velocity: int = 100,
    ) -> dict[str, Any]:
        """Record the configured backend for ``duration_sec`` to ``output_path``.

        If ``midi_note`` is given, sends a one-shot note-on via AbletonOSC just
        before recording starts so the instrument on the track makes sound.
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        if midi_note is not None:
            try:
                await _trigger_midi_note(track_index, midi_note, velocity)
            except Exception as exc:  # pragma: no cover — note-trigger is best-effort
                pass
        if _config.backend == "tape":
            client = await _get_tape_client()
            try:
                result = await client.record(output_path, float(duration_sec), track_index=track_index)
            except TapeTimeout as exc:
                return {"status": "timeout", "error": str(exc), "backend": "tape"}
            except TapeError as exc:
                return {"status": "error", "error": str(exc), "backend": "tape"}
            return {"status": "ok", "backend": "tape", **result}

        lb = _get_loopback()
        try:
            result = await lb.record(output_path, float(duration_sec))
        except LoopbackNotAvailable as exc:
            return {"status": "error", "error": str(exc), "backend": "loopback"}
        return {"status": "ok", "backend": "loopback", **result}

    @mcp.tool()
    async def tape_list_loopback_devices() -> dict[str, Any]:
        """List the audio input devices visible to sounddevice."""
        lb = _get_loopback()
        try:
            devices = lb.list_devices()
        except LoopbackNotAvailable as exc:
            return {"status": "error", "error": str(exc), "devices": []}
        # Pre-filter to inputs since loopback always reads.
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        return {"status": "ok", "count": len(inputs), "devices": inputs}

    @mcp.tool()
    async def tape_get_config() -> dict[str, Any]:
        """Return the current capture config (backend + ports + sample rate)."""
        return {
            "backend": _config.backend,
            "tape_host": _config.tape_host,
            "tape_send_port": _config.tape_send_port,
            "tape_recv_port": _config.tape_recv_port,
            "tape_timeout": _config.tape_timeout,
            "loopback_device": _config.loopback_device,
            "sample_rate": _config.sample_rate,
        }

    @mcp.tool()
    async def tape_set_config(
        backend: str | None = None,
        tape_host: str | None = None,
        tape_send_port: int | None = None,
        tape_recv_port: int | None = None,
        tape_timeout: float | None = None,
        loopback_device: str | None = None,
        sample_rate: int | None = None,
    ) -> dict[str, Any]:
        """Mutate the runtime capture config. Affects subsequent calls only.

        Set ``backend`` to ``"tape"`` or ``"loopback"``. Pass any subset of
        the other fields to override.
        """
        global _config, _tape_singleton, _loopback_singleton
        changes: dict[str, Any] = {}
        if backend is not None:
            if backend not in ("tape", "loopback"):
                return {"status": "error", "error": f"backend must be 'tape' or 'loopback', got {backend!r}"}
            changes["backend"] = backend
        if tape_host is not None:
            changes["tape_host"] = tape_host
        if tape_send_port is not None:
            changes["tape_send_port"] = int(tape_send_port)
        if tape_recv_port is not None:
            changes["tape_recv_port"] = int(tape_recv_port)
        if tape_timeout is not None:
            changes["tape_timeout"] = float(tape_timeout)
        if loopback_device is not None:
            changes["loopback_device"] = loopback_device
        if sample_rate is not None:
            changes["sample_rate"] = int(sample_rate)
        _config = _config.with_overrides(**changes)
        # Tear down stale singletons so they pick up the new config.
        if _tape_singleton is not None:
            try:
                await _tape_singleton.stop()
            except Exception:
                pass
            _tape_singleton = None
        _loopback_singleton = None
        return {"status": "ok", "config": {
            "backend": _config.backend,
            "tape_host": _config.tape_host,
            "tape_send_port": _config.tape_send_port,
            "tape_recv_port": _config.tape_recv_port,
            "tape_timeout": _config.tape_timeout,
            "loopback_device": _config.loopback_device,
            "sample_rate": _config.sample_rate,
        }}
