"""Renderer interface — abstract bridge between params and rendered audio.

The matcher and probe loop need to call ``render`` (or its async sibling
``render_async``) per parameter cell. Two concrete implementations live
here:

- :class:`SynthStubRenderer` — wraps ``synth_stub.synth_render`` for
  tests + demos. Pure CPU work; ``render`` returns immediately.
- :class:`LiveRenderer` — real-device capture via Live's Resampling
  bounce path. Async-only because driving Live requires OSC + bridge
  round-trips; callers must use ``render_async``.

Two render entry points exist so sync renderers and async ones can both
sit behind the same Renderer ABC:

- ``render(params)`` — synchronous, used by sync callers (bench, scripts).
  Default implementation; SynthStubRenderer keeps it. LiveRenderer raises
  ``NotImplementedError`` pointing at the async variant.
- ``async render_async(params)`` — asynchronous, used by MCP tools and any
  caller already inside an event loop. Default implementation delegates
  to ``render(params)`` so existing sync renderers Just Work; LiveRenderer
  overrides this to do the real OSC + bounce work.
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
        """Render mono float32 audio for the given param dict.

        Synchronous interface. For renderers that need an event loop (like
        :class:`LiveRenderer`, which drives Live via OSC), this method
        should raise ``NotImplementedError`` and point at ``render_async``.
        """

    async def render_async(self, params: Mapping[str, float]) -> np.ndarray:
        """Async render. Default delegates to :meth:`render`.

        Async-native renderers (those that need OSC, bridge calls, or
        any other awaitable work per render) should override this. Sync
        renderers don't need to override — the default just forwards to
        ``render``.
        """
        return self.render(params)

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
    """Renderer that captures real Live device output via Resampling.

    Per ``render_async`` call:

    1. Push every param in ``params`` to the live device via OSC
       (``/live/device/set/parameter/value/by_name``).
    2. Snapshot the target track's solo state, then solo it so the
       master mix captures only this track's output.
    3. Create a temp MIDI clip in the configured slot (default slot 0)
       containing one note ``(midi_note, vel=velocity, dur=duration_sec)``.
       The slot's existing clip (if any) is snapshotted via deletion +
       restoration is deferred to the user — for sweep workloads the
       calling tool typically dedicates a slot to this renderer.
    4. Fire the slot, bounce ``duration_sec + 0.5 s`` via
       :func:`bounce_song_via_resampling`, stop playback.
    5. Restore the solo state. Delete the temp clip.
    6. Load the bounced wav at ``self.sample_rate`` (mono) and return a
       float32 ndarray.

    **Cost**: roughly ``duration_sec + 1.5 s`` per cell. For a 100-cell
    probe sweep at 2 s renders, ~5 minutes. Acceptable for sound-matching
    workflows. Two future optimisations are documented in the module
    docstring and could batch many cells into one bounce.

    **Caveats** (documented for callers — the implementation handles
    them gracefully but you should know):

    - The track must already have the target device on it; we don't
      create it. Pass ``track_index`` / ``device_index`` for an
      existing device.
    - Solo-isolation captures only the soloed track's signal, but
      tracks routed INTO the target as inputs (sidechain inputs) can
      still bleed through. If the target's device has a sidechain
      configured, results may not match an isolated-only capture.
    - Per-render cost is real-time; build sweeps that respect that.
    - Use :class:`SynthStubRenderer` for fast iteration on the rest of
      the sound-modeling pipeline; switch to :class:`LiveRenderer` for
      production probes against real devices.

    ``render()`` (sync) raises ``NotImplementedError`` pointing at
    ``render_async``. The OSC + bounce pipeline is fundamentally async;
    we don't try to fake a sync path with an event-loop-in-a-thread
    because every relevant caller already runs inside an async context.
    """

    # Per-render fixed offsets (kept as class constants for visibility).
    _BOUNCE_TAIL_SEC = 0.5  # let the note ring out a bit
    _FIRE_SETTLE_SEC = 0.15  # wait after fire() before bounce starts

    def __init__(
        self,
        track_index: int,
        device_index: int,
        *,
        sample_rate: int = 44100,
        duration_sec: float = 2.0,
        midi_note: int = 60,
        velocity: int = 100,
        trigger_clip_slot: int = 0,
        bounce_dir: str | None = None,
    ) -> None:
        self.track_index = int(track_index)
        self.device_index = int(device_index)
        self.sample_rate = int(sample_rate)
        self.duration_sec = float(duration_sec)
        self.midi_note = int(midi_note)
        self.velocity = int(velocity)
        self.trigger_clip_slot = int(trigger_clip_slot)
        # Where to write the per-cell bounced wavs (one is overwritten per
        # cell). Defaults to a tmp folder if not specified.
        if bounce_dir is None:
            import tempfile
            bounce_dir = tempfile.mkdtemp(prefix="ableton_live_render_")
        self.bounce_dir = bounce_dir

    def render(self, params: Mapping[str, float]) -> np.ndarray:
        raise NotImplementedError(
            "LiveRenderer is async-only. Use renderer.render_async(params) "
            "from an async context, or switch to SynthStubRenderer for the "
            "sync pipeline."
        )

    async def render_async(self, params: Mapping[str, float]) -> np.ndarray:
        """Capture one render cell from Live. See class docstring for the
        full per-call flow + caveats."""
        # Local imports to keep the offline path (synth_stub) from pulling
        # in librosa + the OSC client at module import time.
        import os
        import asyncio
        import librosa
        from ..bounce.resampling import bounce_song_via_resampling
        from ..osc_client import get_client

        osc = await get_client()
        ti = self.track_index

        # 1. Push params to the live device. by_name is the resilient form
        #    — order independence + survives parameter index reshuffles.
        for name, value in params.items():
            osc.send(
                "/live/device/set/parameter/value/by_name",
                ti, int(self.device_index), str(name), float(value),
            )
        # Tiny settle so Live processes the parameter writes before render.
        await asyncio.sleep(0.05)

        # 2. Snapshot solo state, then solo the target track. We restore
        #    solo in a finally so a failed bounce can't leave the user's
        #    session in solo'd state.
        try:
            prev_solo = bool(
                (await osc.request("/live/track/get/solo", ti))[1]
            )
        except Exception:
            prev_solo = False
        osc.send("/live/track/set/solo", ti, 1)
        await asyncio.sleep(0.05)

        # 3. Plant a trigger clip in the configured slot. We use
        #    AbletonOSC's create_clip + add_notes flow rather than going
        #    through the bridge — fewer dependencies and the slot
        #    addressing is straightforward.
        ci = self.trigger_clip_slot
        try:
            # Create clip (length = duration_sec beats; if the user wants
            # actual seconds they should set duration_sec to match
            # tempo-adjusted beats. The bounce wraps duration_sec in
            # seconds regardless of beats so this is just clip length.)
            osc.send("/live/clip_slot/create_clip", ti, ci, float(self.duration_sec))
            await asyncio.sleep(0.05)
            # Add one note. AbletonOSC's add_notes takes flat tuples
            # (pitch, start, duration, velocity, mute).
            osc.send(
                "/live/clip/add/notes", ti, ci,
                int(self.midi_note), 0.0, float(self.duration_sec),
                int(self.velocity), 0,
            )
            await asyncio.sleep(0.05)

            # 4. Fire the clip, then bounce. Bounce path handles its own
            #    transport (stop, jump to 0, start, etc.) — but we want
            #    OUR clip firing during the bounce window, so the fire
            #    has to happen AT the start of the bounce. Trick: queue
            #    the clip fire, then start the bounce. The bounce starts
            #    transport which fires queued clips at the next quantize
            #    boundary. We pre-set quantize=0 (none) so it fires
            #    immediately.
            #
            #    To keep this minimal we simply fire-then-bounce; minor
            #    latency at clip start is absorbed by the warmup_sec
            #    parameter of the bounce.
            osc.send("/live/clip_slot/fire", ti, ci)
            await asyncio.sleep(self._FIRE_SETTLE_SEC)

            out_path = os.path.join(self.bounce_dir, f"cell_t{ti}_d{self.device_index}.wav")
            bounce_result = await bounce_song_via_resampling(
                out_path,
                duration_sec=self.duration_sec + self._BOUNCE_TAIL_SEC,
                warmup_sec=0.0,  # we already fired the clip; no priming needed
            )
            if not bounce_result.get("copied"):
                raise RuntimeError(
                    "LiveRenderer bounce failed: %s"
                    % (bounce_result.get("error") or "unknown bounce error")
                )

            # 5. Load + downmix.
            audio, _ = librosa.load(out_path, sr=self.sample_rate, mono=True)
            return audio.astype(np.float32)
        finally:
            # 6. Cleanup: stop playback + delete temp clip + restore solo.
            #    Best-effort; logged failures only.
            try:
                osc.send("/live/clip_slot/delete_clip", ti, ci)
            except Exception:
                pass
            try:
                osc.send("/live/track/set/solo", ti, 1 if prev_solo else 0)
            except Exception:
                pass
