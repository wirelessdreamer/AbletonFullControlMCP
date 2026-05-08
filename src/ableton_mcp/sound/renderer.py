"""Renderer interface — abstract bridge between params and rendered audio.

The matcher and probe loop only need ``render(params) -> np.ndarray`` so they
can be exercised with the in-process ``SynthStubRenderer`` today and swapped
for the real ``LiveRenderer`` once Phase 2 lands.

Two concrete implementations live here:

- :class:`SynthStubRenderer` — wraps ``synth_stub.synth_render`` for tests + demos.
- :class:`LiveRenderer` — placeholder that records the targeted track/device
  but raises ``NotImplementedError`` because audio capture from Live needs the
  Phase 2 render-in-the-loop pipeline (out of scope for this task).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping

import numpy as np

from .synth_stub import SYNTH_STUB_DEFAULTS, SYNTH_STUB_PARAM_RANGES, synth_render


class Renderer(ABC):
    """Abstract render interface.

    Implementations should be deterministic for a given param dict so that the
    feature distance landscape used by the optimiser is well-defined.
    """

    sample_rate: int = 22050
    duration_sec: float = 2.0

    @abstractmethod
    def render(self, params: Mapping[str, float]) -> np.ndarray:
        """Render mono float32 audio for the given param dict."""

    @property
    def param_ranges(self) -> dict[str, tuple[float, float]]:
        """Optional: expose the legal range for each param the renderer accepts."""
        return {}

    @property
    def default_params(self) -> dict[str, float]:
        """Optional: a sensible starting cell for refinement."""
        return {}


@dataclass
class SynthStubRenderer(Renderer):
    """Renders the in-process numpy synth from ``synth_stub``."""

    sample_rate: int = 22050
    duration_sec: float = 2.0
    seed: int | None = 0

    def render(self, params: Mapping[str, float]) -> np.ndarray:
        return synth_render(
            params,
            sr=self.sample_rate,
            dur=self.duration_sec,
            seed=self.seed,
        )

    @property
    def param_ranges(self) -> dict[str, tuple[float, float]]:
        return dict(SYNTH_STUB_PARAM_RANGES)

    @property
    def default_params(self) -> dict[str, float]:
        return dict(SYNTH_STUB_DEFAULTS)


class LiveRenderer(Renderer):
    """Renderer that drives a real Live device through the capture pipeline.

    Render pipeline (per ``render(params)``):

      1. Push each named param to ``(track_index, device_index)`` via AbletonOSC,
         matching the param's name case-insensitively against the device's
         reported parameter list.
      2. Trigger ``midi_note`` (best-effort via AbletonOSC) so the instrument
         actually sounds.
      3. Record ``duration_sec`` of audio to a temp wav using the configured
         capture backend (Max for Live tape device on UDP/11003 by default,
         or sounddevice loopback if the user has configured that).
      4. Load the wav with ``soundfile``, downmix to mono float32, return.

    The capture backend is resolved per-call via
    :func:`ableton_mcp.tape.pick_capture_backend` so the user can switch
    between the M4L tape device and loopback at runtime.
    """

    def __init__(
        self,
        track_index: int,
        device_index: int,
        *,
        sample_rate: int = 44100,
        duration_sec: float = 2.0,
        midi_note: int = 60,
        velocity: int = 100,
        capture_config: object | None = None,
        osc_config: object | None = None,
        tmp_dir: str | None = None,
    ) -> None:
        self.track_index = int(track_index)
        self.device_index = int(device_index)
        self.sample_rate = int(sample_rate)
        self.duration_sec = float(duration_sec)
        self.midi_note = int(midi_note)
        self.velocity = int(velocity)
        # Optional injection points (used by tests):
        self._capture_config = capture_config
        self._osc_config = osc_config
        self._tmp_dir = tmp_dir

    # ------------------------------------------------------------------
    # Async helpers — every IO step is an awaitable so the whole render
    # pipeline can run in a single fresh asyncio loop. Tests can also call
    # ``arender(params)`` directly from inside their own loop.
    # ------------------------------------------------------------------

    async def _push_params(self, osc_client: object, params: Mapping[str, float]) -> dict:
        """Push named params via the supplied OSC client. Returns
        ``{"applied": [...], "unmatched": [...]}``.
        """
        client = osc_client  # type: ignore[assignment]
        names_reply = await client.request(  # type: ignore[attr-defined]
            "/live/device/get/parameters/name",
            int(self.track_index), int(self.device_index),
        )
        # AbletonOSC echoes (track_id, device_id, *names) — strip the selector.
        names = list(names_reply[2:]) if len(names_reply) > 2 else []
        applied: list[dict] = []
        unmatched: list[str] = []
        lowered = [str(n).strip().lower() for n in names]
        for name, value in params.items():
            try:
                idx = lowered.index(str(name).strip().lower())
            except ValueError:
                unmatched.append(str(name))
                continue
            client.send(  # type: ignore[attr-defined]
                "/live/device/set/parameter/value",
                int(self.track_index), int(self.device_index),
                int(idx), float(value),
            )
            applied.append({"name": str(names[idx]), "index": idx, "value": float(value)})
        return {"applied": applied, "unmatched": unmatched}

    async def _trigger_midi_note(self, osc_client: object) -> None:
        """Best-effort one-shot note-on. Silently no-ops on failure so we
        still attempt the recording."""
        try:
            osc_client.send(  # type: ignore[attr-defined]
                "/live/track/play_note",
                int(self.track_index), int(self.midi_note), int(self.velocity),
                int(self.duration_sec * 1000),
            )
        except Exception:
            return

    def _load_wav(self, path: str) -> np.ndarray:
        """Load a wav and return mono float32 audio."""
        import soundfile as sf

        audio, _sr = sf.read(str(path), dtype="float32", always_2d=False)
        arr = np.asarray(audio, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr.mean(axis=1).astype(np.float32)
        return arr

    async def arender(self, params: Mapping[str, float]) -> np.ndarray:
        """Async variant of :meth:`render` for callers inside an asyncio loop.

        Uses fresh per-call OSC + tape clients (bound to the current loop)
        rather than process-wide singletons. This is safe because the OSC
        client's UDP server is cheap to start/stop.
        """
        import os
        import tempfile

        from ..config import Config as OSCConfig
        from ..osc_client import AbletonOSCClient
        from ..tape import pick_capture_backend

        tmp_dir = self._tmp_dir or tempfile.gettempdir()
        os.makedirs(tmp_dir, exist_ok=True)
        out_path = os.path.join(
            tmp_dir, f"liverenderer_{os.getpid()}_{int.from_bytes(os.urandom(4), 'big')}.wav"
        )

        osc_cfg = self._osc_config or OSCConfig.from_env()
        osc_client = AbletonOSCClient(osc_cfg)  # type: ignore[arg-type]
        await osc_client.start()
        try:
            await self._push_params(osc_client, params)
            await self._trigger_midi_note(osc_client)

            backend = pick_capture_backend(self._capture_config)  # type: ignore[arg-type]
            if hasattr(backend, "start"):
                await backend.start()
            try:
                kwargs = (
                    {"track_index": self.track_index}
                    if backend.__class__.__name__ == "TapeClient" else {}
                )
                result = await backend.record(out_path, self.duration_sec, **kwargs)
            finally:
                if hasattr(backend, "stop"):
                    try:
                        await backend.stop()
                    except Exception:
                        pass

            wav_path = (
                str(result.get("path", out_path)) if isinstance(result, dict) else str(out_path)
            )
            return self._load_wav(wav_path)
        finally:
            try:
                await osc_client.stop()
            except Exception:
                pass

    def render(self, params: Mapping[str, float]) -> np.ndarray:
        """Synchronous render entry point.

        If called from outside an asyncio loop, creates one with
        :func:`asyncio.run`. If called from inside a running loop, runs the
        whole pipeline in a fresh background thread + loop so we don't block
        the caller's loop.
        """
        import asyncio

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is None:
            return asyncio.run(self.arender(params))

        import concurrent.futures

        def _runner() -> np.ndarray:
            return asyncio.run(self.arender(params))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result()
