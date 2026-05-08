"""Sounddevice-based loopback capture, the fallback when no M4L tape device exists.

The user routes Live's master (or a track's output) through a virtual loopback
audio device (Windows: VB-Audio Cable / VB-CABLE; macOS: BlackHole; Linux:
PulseAudio loopback / pipewire). We record from that device for a given
duration and write a wav.

This module imports :mod:`sounddevice` and :mod:`soundfile` lazily so that the
package can be imported without those deps; we only complain when someone
calls :meth:`LoopbackCapture.record`.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from .config import CaptureConfig

log = logging.getLogger(__name__)


class LoopbackNotAvailable(RuntimeError):
    """Raised when the optional ``sounddevice`` extra is missing."""


_INSTALL_HINT = (
    "Install the optional capture extra: `pip install sounddevice soundfile` "
    "(or `pip install -e .[capture]` once it ships). On Windows you also need "
    "VB-Audio Cable; on macOS, BlackHole. Or use the Max for Live tape device "
    "via ABLETON_MCP_CAPTURE_BACKEND=tape."
)


def _import_sounddevice() -> Any:
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception as exc:
        raise LoopbackNotAvailable(f"sounddevice not importable: {exc}. {_INSTALL_HINT}") from exc
    return sd


def _import_soundfile() -> Any:
    try:
        import soundfile as sf  # type: ignore[import-not-found]
    except Exception as exc:
        raise LoopbackNotAvailable(f"soundfile not importable: {exc}. {_INSTALL_HINT}") from exc
    return sf


def _platform_recommendation() -> str:
    if sys.platform == "win32":
        return (
            "On Windows, install VB-Audio Cable (https://vb-audio.com/Cable/), set Live's "
            "master Audio Output to 'CABLE Input', and pass the matching 'CABLE Output' "
            "as device_name (e.g. 'CABLE Output'). WASAPI loopback hosts are preferred "
            "when sounddevice exposes them."
        )
    if sys.platform == "darwin":
        return (
            "On macOS, install BlackHole (https://existential.audio/blackhole/), create a "
            "Multi-Output Device that includes BlackHole + your speakers, set Live's master "
            "to that aggregate, and pass 'BlackHole 2ch' (or similar) as device_name."
        )
    return (
        "On Linux, create a pulseaudio/pipewire null-sink + loopback and pass its monitor "
        "name as device_name."
    )


class LoopbackCapture:
    """Records from a configured input device for N seconds, returns wav path."""

    def __init__(self, cfg: CaptureConfig | None = None) -> None:
        self._cfg = cfg or CaptureConfig.from_env()

    @property
    def config(self) -> CaptureConfig:
        return self._cfg

    # The TapeClient/LoopbackCapture surface is intentionally small + parallel.
    async def start(self) -> None:  # pragma: no cover — nothing to do
        pass

    async def stop(self) -> None:  # pragma: no cover — nothing to do
        pass

    def list_devices(self) -> list[dict[str, Any]]:
        """List sounddevice input devices. Raises LoopbackNotAvailable if missing."""
        sd = _import_sounddevice()
        out: list[dict[str, Any]] = []
        for i, dev in enumerate(sd.query_devices()):
            try:
                hostapi_name = sd.query_hostapis(int(dev.get("hostapi", 0)))["name"]
            except Exception:
                hostapi_name = ""
            entry = {
                "index": i,
                "name": dev.get("name", ""),
                "max_input_channels": int(dev.get("max_input_channels", 0)),
                "max_output_channels": int(dev.get("max_output_channels", 0)),
                "default_samplerate": float(dev.get("default_samplerate", 0)),
                "hostapi": hostapi_name,
            }
            out.append(entry)
        return out

    def _resolve_device(self, device_name: str | None) -> str | int | None:
        """Pick a device by substring match. None means sounddevice default."""
        sd = _import_sounddevice()
        wanted = (device_name or self._cfg.loopback_device or "").strip()
        if not wanted:
            return None
        # Prefer WASAPI hosts on Windows when ambiguous.
        candidates: list[tuple[int, dict[str, Any]]] = []
        for i, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_input_channels", 0)) <= 0:
                continue
            if wanted.lower() in str(dev.get("name", "")).lower():
                candidates.append((i, dev))
        if not candidates:
            raise LoopbackNotAvailable(
                f"No input device matched {wanted!r}. Available: "
                f"{[d['name'] for d in self.list_devices() if d['max_input_channels'] > 0]}. "
                + _platform_recommendation()
            )
        if sys.platform == "win32":
            # Sort to favour WASAPI when present.
            def _wasapi_score(item: tuple[int, dict[str, Any]]) -> int:
                _, dev = item
                try:
                    name = sd.query_hostapis(int(dev.get("hostapi", 0)))["name"]
                except Exception:
                    name = ""
                return 0 if "WASAPI" in name else 1
            candidates.sort(key=_wasapi_score)
        return int(candidates[0][0])

    async def record(
        self,
        path: str,
        duration_sec: float,
        device_name: str | None = None,
        samplerate: int | None = None,
        channels: int = 2,
    ) -> dict[str, Any]:
        """Block on the asyncio loop while sounddevice fills a buffer.

        Returns ``{"path": str, "duration_actual": float, "samplerate": int}``.
        """
        sd = _import_sounddevice()
        sf = _import_soundfile()

        sr = int(samplerate or self._cfg.sample_rate)
        device = self._resolve_device(device_name)
        n_frames = int(round(float(duration_sec) * sr))

        loop = asyncio.get_running_loop()

        def _do_record() -> Any:
            log.info("loopback recording %d frames @ %d Hz from %r", n_frames, sr, device)
            return sd.rec(n_frames, samplerate=sr, channels=channels, device=device, dtype="float32")

        rec = await loop.run_in_executor(None, _do_record)
        await loop.run_in_executor(None, sd.wait)

        sf.write(str(path), rec, sr)
        log.info("loopback wrote %s (%d frames)", path, n_frames)
        return {
            "path": str(path),
            "duration_actual": float(n_frames) / float(sr),
            "samplerate": sr,
        }

    async def ping(self, timeout: float | None = None) -> bool:  # noqa: ARG002
        """Loopback has no out-of-band liveness check; report whether the
        sounddevice import + device resolution work.
        """
        try:
            _import_sounddevice()
            _import_soundfile()
            self._resolve_device(None)
            return True
        except LoopbackNotAvailable:
            return False
