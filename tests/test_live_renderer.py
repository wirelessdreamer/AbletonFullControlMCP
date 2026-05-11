"""Tests for ``LiveRenderer`` — real-device capture via Resampling.

The actual render path drives Live via OSC + a bounce, so the tests
mock both the OSC client and the bounce function. We're verifying the
choreography (param push → solo → fire clip → bounce → cleanup) and
the async-vs-sync interface contract — not the audio content of the
output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.sound.renderer import LiveRenderer, Renderer, SynthStubRenderer


# ---------------------------------------------------------------------------
# Renderer ABC: render_async default delegates to render
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_async_default_delegates_to_render_for_sync_renderers() -> None:
    """SynthStubRenderer doesn't override render_async — the default in
    the ABC should just forward to its sync render()."""
    r = SynthStubRenderer(sample_rate=22050, duration_sec=0.05)
    audio_sync = r.render({})
    audio_async = await r.render_async({})
    np.testing.assert_array_equal(audio_sync, audio_async)


def test_sync_render_works_on_synth_stub() -> None:
    """Quick sanity check that the offline renderer still produces audio
    without any async wrapper."""
    r = SynthStubRenderer(sample_rate=22050, duration_sec=0.05)
    audio = r.render({})
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32 or audio.dtype == np.float64
    assert len(audio) > 0


# ---------------------------------------------------------------------------
# LiveRenderer: sync render raises with actionable message
# ---------------------------------------------------------------------------


def test_live_renderer_sync_render_raises_with_actionable_message() -> None:
    r = LiveRenderer(track_index=0, device_index=0)
    with pytest.raises(NotImplementedError) as excinfo:
        r.render({})
    msg = str(excinfo.value)
    assert "async" in msg.lower()
    assert "render_async" in msg


# ---------------------------------------------------------------------------
# LiveRenderer.render_async happy path
# ---------------------------------------------------------------------------


class FakeOSC:
    """Records every send + answers a couple of canned requests."""

    def __init__(self, *, prev_solo: bool = False) -> None:
        self.sent: list[tuple[str, tuple]] = []
        self.prev_solo = prev_solo

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/track/get/solo":
            ti = int(args[0])
            return (ti, 1 if self.prev_solo else 0)
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")

    def send(self, *args: Any) -> None:
        self.sent.append((args[0], args[1:]))


@pytest.mark.asyncio
async def test_live_renderer_render_async_choreography(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The happy-path choreography: param set, solo on, clip create + fire,
    bounce, cleanup (delete clip, restore solo)."""
    osc = FakeOSC(prev_solo=False)

    async def fake_get_client() -> FakeOSC:
        return osc

    # Write a real wav so librosa.load works in the test.
    bounce_wav = tmp_path / "cell.wav"
    test_audio = np.linspace(-0.5, 0.5, 22050, dtype=np.float32)
    sf.write(str(bounce_wav), test_audio, 22050, subtype="PCM_16")

    async def fake_bounce(out_path, *, duration_sec, warmup_sec=0.0, **_):
        # Copy our test wav to the expected output path so librosa can read it.
        import shutil
        shutil.copy(str(bounce_wav), str(out_path))
        return {"copied": True, "output_path": str(out_path)}

    monkeypatch.setattr(
        "ableton_mcp.osc_client.get_client", fake_get_client,
    )
    monkeypatch.setattr(
        "ableton_mcp.bounce.resampling.bounce_song_via_resampling", fake_bounce,
    )

    r = LiveRenderer(
        track_index=2, device_index=1,
        sample_rate=22050, duration_sec=0.5,
        midi_note=60, velocity=100,
        trigger_clip_slot=0,
        bounce_dir=str(tmp_path),
    )
    audio = await r.render_async({"freq": 1000.0, "gain": 0.7})

    # Audio comes back as float32 of the right length.
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert len(audio) > 0

    # Param pushes happened before solo. Verify the OSC traffic shape.
    sent_addrs = [s[0] for s in osc.sent]
    assert "/live/device/set/parameter/value/by_name" in sent_addrs
    # Two param writes (freq, gain) since render received 2 params.
    param_writes = [s for s in osc.sent
                    if s[0] == "/live/device/set/parameter/value/by_name"]
    assert len(param_writes) == 2
    # Solo on + solo restore (off, since prev_solo was False).
    solo_writes = [s for s in osc.sent if s[0] == "/live/track/set/solo"]
    assert len(solo_writes) == 2  # one on, one off
    assert solo_writes[0][1] == (2, 1)  # solo on for track 2
    assert solo_writes[1][1] == (2, 0)  # restore to off
    # Clip create, note add, fire, delete all happened.
    assert "/live/clip_slot/create_clip" in sent_addrs
    assert "/live/clip/add/notes" in sent_addrs
    assert "/live/clip_slot/fire" in sent_addrs
    assert "/live/clip_slot/delete_clip" in sent_addrs


@pytest.mark.asyncio
async def test_live_renderer_restores_solo_to_true_when_track_was_soloed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If the user had the track already soloed, restore should put it
    back to soloed (not silently un-solo)."""
    osc = FakeOSC(prev_solo=True)  # was already soloed

    async def fake_get_client() -> FakeOSC:
        return osc

    bounce_wav = tmp_path / "cell.wav"
    sf.write(str(bounce_wav), np.zeros(1000, dtype=np.float32), 22050,
             subtype="PCM_16")

    async def fake_bounce(out_path, *, duration_sec, warmup_sec=0.0, **_):
        import shutil
        shutil.copy(str(bounce_wav), str(out_path))
        return {"copied": True, "output_path": str(out_path)}

    monkeypatch.setattr("ableton_mcp.osc_client.get_client", fake_get_client)
    monkeypatch.setattr(
        "ableton_mcp.bounce.resampling.bounce_song_via_resampling", fake_bounce,
    )

    r = LiveRenderer(track_index=0, device_index=0, sample_rate=22050,
                     duration_sec=0.05, bounce_dir=str(tmp_path))
    await r.render_async({})
    solo_writes = [s for s in osc.sent if s[0] == "/live/track/set/solo"]
    # First write: solo on (1). Second write: restore (1 because was soloed).
    assert solo_writes[0][1] == (0, 1)
    assert solo_writes[1][1] == (0, 1)


@pytest.mark.asyncio
async def test_live_renderer_cleans_up_temp_clip_even_on_bounce_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A failed bounce should still trigger cleanup (clip delete + solo
    restore) and re-raise the original error so the caller sees it."""
    osc = FakeOSC(prev_solo=False)

    async def fake_get_client() -> FakeOSC:
        return osc

    async def fake_bounce(*_args, **_kwargs):
        return {"copied": False, "error": "simulated bounce failure"}

    monkeypatch.setattr("ableton_mcp.osc_client.get_client", fake_get_client)
    monkeypatch.setattr(
        "ableton_mcp.bounce.resampling.bounce_song_via_resampling", fake_bounce,
    )

    r = LiveRenderer(track_index=0, device_index=0, sample_rate=22050,
                     duration_sec=0.05, bounce_dir=str(tmp_path))
    with pytest.raises(RuntimeError) as excinfo:
        await r.render_async({})
    assert "bounce failed" in str(excinfo.value)
    # Cleanup still happened.
    sent_addrs = [s[0] for s in osc.sent]
    assert "/live/clip_slot/delete_clip" in sent_addrs
    assert "/live/track/set/solo" in sent_addrs


# ---------------------------------------------------------------------------
# Module-level wiring sanity
# ---------------------------------------------------------------------------


def test_live_renderer_is_a_renderer_subclass() -> None:
    """The class should still be a Renderer so the planner / matcher
    can accept it via the ABC. Sanity check after the render_async
    refactor."""
    assert issubclass(LiveRenderer, Renderer)


def test_live_renderer_constructor_accepts_documented_params() -> None:
    """Smoke that the public constructor signature didn't break."""
    r = LiveRenderer(
        track_index=3, device_index=1,
        sample_rate=44100, duration_sec=2.0,
        midi_note=72, velocity=110,
        trigger_clip_slot=5,
    )
    assert r.track_index == 3
    assert r.device_index == 1
    assert r.sample_rate == 44100
    assert r.duration_sec == 2.0
    assert r.midi_note == 72
    assert r.velocity == 110
    assert r.trigger_clip_slot == 5
