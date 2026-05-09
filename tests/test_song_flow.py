"""Unit tests for the song-flow surface.

These exercise the pure-Python orchestration paths with the OSC client and
bridge client both mocked. The bridge handlers themselves run inside Live
and can only be exercised by the manual end-to-end test described in the
plan; here we verify that the Python side calls the right handlers in the
right order with the right snapshots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.bounce.mix import mix_stems_to_master
from ableton_mcp.song_flow import (
    PITCH_CLASS,
    make_variations,
    semitone_delta,
)
from ableton_mcp.song_flow.key import normalize_key


# ---------------------------------------------------------------------------
# key.py — pure math, no I/O
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,tgt,direction,expected",
    [
        ("C", "C", "auto", 0),
        ("C", "F#", "auto", -6),      # tritone: ties go DOWN by docstring policy
        ("C", "G", "auto", -5),       # 5 down vs 7 up → down wins (shorter)
        ("C", "F", "auto", 5),        # 5 up vs 7 down → up wins
        ("C", "F#", "up", 6),
        ("C", "F#", "down", -6),
        ("C", "G", "up", 7),
        ("C", "G", "down", -5),
        ("c", "f#", "auto", -6),      # case-insensitive
        ("Bb", "A#", "auto", 0),      # enharmonic equivalents
        ("Bb minor", "F", "auto", -5),  # mode suffix stripped
        ("E", "A", "up", 5),
        ("A", "E", "auto", -5),
    ],
)
def test_semitone_delta(src: str, tgt: str, direction: str, expected: int) -> None:
    assert semitone_delta(src, tgt, direction) == expected  # type: ignore[arg-type]


def test_semitone_delta_tritone_goes_down() -> None:
    """Tritone (raw==6) resolves to the negative direction in 'auto' — the
    docstring says ties go down to keep vocals in a more singable range."""
    assert semitone_delta("C", "F#", "auto") == -6
    assert semitone_delta("F#", "C", "auto") == -6  # symmetric: shortest signed always negative-leaning


def test_normalize_key_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_key("XYZ")
    with pytest.raises(ValueError):
        normalize_key("")


def test_pitch_class_table_covers_12_pcs() -> None:
    # Includes both sharps and flats; assert 12 distinct pitch classes.
    assert set(PITCH_CLASS.values()) == set(range(12))


# ---------------------------------------------------------------------------
# mix_stems_to_master with gains_db
# ---------------------------------------------------------------------------


def _write_synthetic_stem(path: Path, *, freq: float, amp: float = 0.3,
                          duration_sec: float = 0.5, sr: int = 22050) -> None:
    n = int(sr * duration_sec)
    t = np.arange(n, dtype=np.float32) / sr
    y = (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), y, sr, subtype="PCM_24")


def test_mix_stems_with_gains_applies_per_stem_db(tmp_path: Path) -> None:
    """A stem at +6 dB sums roughly 2× as loud as one at unity."""
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_synthetic_stem(a, freq=440.0, amp=0.3)
    _write_synthetic_stem(b, freq=660.0, amp=0.3)

    out_unity = tmp_path / "unity.wav"
    out_boost = tmp_path / "boost.wav"

    info_unity = mix_stems_to_master(
        [a, b], out_unity, normalize=False,
    )
    info_boost = mix_stems_to_master(
        [a, b], out_boost, normalize=False,
        gains_db=[6.0, -120.0],  # 'a' boosted, 'b' effectively muted
    )

    # With b muted, peak ≈ 0.3 * 10**(6/20) ≈ 0.598. Without normalize.
    assert info_boost["peak_dbfs"] < 0.0
    # And the boosted-a peak is louder than 'a' alone at unity (which would
    # peak at ~0.3 = -10.5 dBFS).
    assert info_boost["peak_dbfs"] > -7.0
    # Sanity: the unity sum has a higher (less negative) peak than just-a.
    # Both stems summed at unity peak around 0.3 + 0.3 = 0.6 → -4.4 dBFS.
    assert info_unity["peak_dbfs"] > -5.0


def test_mix_stems_gains_length_must_match(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_synthetic_stem(a, freq=440.0)
    _write_synthetic_stem(b, freq=660.0)
    with pytest.raises(ValueError, match="gains_db length"):
        mix_stems_to_master([a, b], tmp_path / "out.wav", gains_db=[6.0])


# ---------------------------------------------------------------------------
# make_variations — file-on-disk math
# ---------------------------------------------------------------------------


def _six_stems(tmp_path: Path) -> list[dict[str, Any]]:
    names = ["drums", "bass", "other", "vocals", "guitar", "piano"]
    out = []
    for i, name in enumerate(names):
        p = tmp_path / f"{name}.wav"
        _write_synthetic_stem(p, freq=200.0 + 60 * i, amp=0.15)
        out.append({"name": name, "path": str(p)})
    return out


def test_make_variations_count_for_6_stem_split(tmp_path: Path) -> None:
    """6-stem split → 1 (original) + 1 (instrumental) + 6 (instrument-up) = 8 wavs."""
    stems = _six_stems(tmp_path)
    out_dir = tmp_path / "variations"
    result = make_variations(stems, out_dir, encode_mp3=False)
    assert result["status"] == "ok"
    assert result["n_variations"] == 8
    labels = sorted(v["label"] for v in result["variations"])
    assert labels == sorted([
        "original", "instrumental",
        "drums_up", "bass_up", "other_up", "vocals_up", "guitar_up", "piano_up",
    ])


def test_make_variations_skips_instrumental_when_no_vocal_stem(tmp_path: Path) -> None:
    stems = [
        {"name": "drums", "path": str(tmp_path / "drums.wav")},
        {"name": "bass", "path": str(tmp_path / "bass.wav")},
    ]
    _write_synthetic_stem(Path(stems[0]["path"]), freq=200.0)
    _write_synthetic_stem(Path(stems[1]["path"]), freq=300.0)
    out_dir = tmp_path / "var"
    result = make_variations(stems, out_dir, encode_mp3=False)
    assert result["status"] == "ok"
    # 1 original + 0 instrumental (no vocals to drop) + 2 instrument-up = 3
    labels = [v["label"] for v in result["variations"]]
    assert "instrumental" not in labels
    assert result["n_variations"] == 3


def test_make_variations_writes_actual_files(tmp_path: Path) -> None:
    stems = _six_stems(tmp_path)
    out_dir = tmp_path / "vars"
    result = make_variations(stems, out_dir, encode_mp3=False)
    for v in result["variations"]:
        assert Path(v["wav_path"]).exists(), f"missing wav: {v['wav_path']}"


def test_make_variations_empty_list_errors() -> None:
    out = make_variations([], "/tmp/whatever", encode_mp3=False)
    assert out["status"] == "error"


def test_make_variations_practice_pack_count_for_6_stem(tmp_path: Path) -> None:
    """6-stem split, practice_pack mode → 1 (no_vocals) + 5 instrument stems
    × 2 variants (with/without vocals) = 11 wavs. Vocals stem gets no boost
    track of its own."""
    stems = _six_stems(tmp_path)  # drums/bass/other/vocals/guitar/piano
    out_dir = tmp_path / "pp_out"
    result = make_variations(stems, out_dir,
                             output_set="practice_pack", encode_mp3=False)
    assert result["status"] == "ok"
    assert result["output_set"] == "practice_pack"
    assert result["n_variations"] == 11
    labels = sorted(v["label"] for v in result["variations"])
    expected = sorted([
        "no_vocals",
        "drums_boost_no_vocals", "drums_boost_with_vocals",
        "bass_boost_no_vocals", "bass_boost_with_vocals",
        "other_boost_no_vocals", "other_boost_with_vocals",
        "guitar_boost_no_vocals", "guitar_boost_with_vocals",
        "piano_boost_no_vocals", "piano_boost_with_vocals",
    ])
    assert labels == expected


def test_make_variations_practice_pack_writes_files(tmp_path: Path) -> None:
    stems = _six_stems(tmp_path)
    out_dir = tmp_path / "pp_files"
    result = make_variations(stems, out_dir,
                             output_set="practice_pack", encode_mp3=False)
    for v in result["variations"]:
        assert Path(v["wav_path"]).exists(), f"missing wav: {v['wav_path']}"


def test_make_variations_practice_pack_name_prefix(tmp_path: Path) -> None:
    """name_prefix should land on every output filename so multiple songs
    can share an output dir without collisions."""
    stems = _six_stems(tmp_path)
    out_dir = tmp_path / "pp_prefix"
    result = make_variations(
        stems, out_dir,
        output_set="practice_pack", name_prefix="Reasons - ",
        encode_mp3=False,
    )
    # Prefix is prepended verbatim (preserved as-is, NOT run through
    # _safe_filename — the caller chose the format on purpose).
    for v in result["variations"]:
        assert Path(v["wav_path"]).name.startswith("Reasons - "), (
            f"missing prefix: {v['wav_path']}"
        )


def test_make_variations_practice_pack_errors_when_only_vocals(tmp_path: Path) -> None:
    """practice_pack requires at least one non-vocal stem (a no_vocals mix
    of nothing isn't useful)."""
    stems = [
        {"name": "vocals", "path": str(tmp_path / "vocals.wav")},
    ]
    _write_synthetic_stem(Path(stems[0]["path"]), freq=400.0)
    out = make_variations(stems, tmp_path / "v",
                          output_set="practice_pack", encode_mp3=False)
    assert out["status"] == "error"
    assert "non-vocal" in out["error"]


def test_make_variations_unknown_output_set_errors(tmp_path: Path) -> None:
    stems = _six_stems(tmp_path)
    out = make_variations(stems, tmp_path / "v",
                          output_set="bogus_mode", encode_mp3=False)
    assert out["status"] == "error"
    assert "output_set" in out["error"]


def test_make_variations_practice_pack_skips_vocal_stems_with_substring_match(
    tmp_path: Path,
) -> None:
    """Stems named 'Backing Vocals' / 'Lead Vocal' should be classified as
    vocals (substring match) and not get their own boost tracks."""
    names = ["drums", "bass", "guitar", "Lead Vocals", "Backing Vocals"]
    stems = []
    for i, name in enumerate(names):
        p = tmp_path / f"{name.replace(' ', '_')}.wav"
        _write_synthetic_stem(p, freq=200.0 + 50 * i)
        stems.append({"name": name, "path": str(p)})
    result = make_variations(stems, tmp_path / "pp_subst",
                             output_set="practice_pack", encode_mp3=False)
    assert result["status"] == "ok"
    # 1 no_vocals + 3 non-vocal stems × 2 = 7
    assert result["n_variations"] == 7
    labels = {v["label"] for v in result["variations"]}
    # No boost track should start with the name of a vocal stem. (Can't match
    # on substring "vocal" alone — the variant suffix "_no_vocals" /
    # "_with_vocals" contains it by design.)
    vocal_stem_names = ["Lead Vocals", "Backing Vocals"]
    for lbl in labels:
        for vsn in vocal_stem_names:
            assert not lbl.startswith(f"{vsn}_boost_"), (
                f"vocal stem {vsn!r} got its own boost track: {lbl}"
            )


# ---------------------------------------------------------------------------
# transpose_song — orchestration with mocked bridge + OSC
# ---------------------------------------------------------------------------


class _FakeOSCClient:
    """Returns canned replies for the addresses transpose_song reads."""

    def __init__(self, *, num_tracks: int, arrangement_clips_per_track: list[int],
                 tempo: float = 120.0, song_length_beats: float = 32.0) -> None:
        self.num_tracks = num_tracks
        self.arrangement_clips_per_track = arrangement_clips_per_track
        self.tempo = tempo
        self.song_length_beats = song_length_beats
        self.sent: list[tuple[str, tuple]] = []

    async def request(self, addr: str, *args: Any) -> tuple:
        if addr == "/live/song/get/num_tracks":
            return (self.num_tracks,)
        if addr == "/live/song/get/tempo":
            return (self.tempo,)
        if addr == "/live/song/get/song_length":
            return (self.song_length_beats,)
        # NOTE: /live/track/get/arrangement_clips/length is NOT mocked here.
        # ``_arrangement_clip_count`` was migrated to the bridge handler
        # ``clip.list_arrangement_clips`` because AbletonOSC's reply for the
        # OSC equivalent is unreliable (caches via listeners; misses
        # clips dragged in after startup). The fake bridge below provides
        # the per-track clip count.
        raise AssertionError(f"unexpected OSC request: {addr} {args!r}")

    def send(self, *_args: Any, **_kwargs: Any) -> None:
        self.sent.append(_args)


class _FakeBridgeClient:
    """Records every call. ``state`` simulates what the real bridge would do."""

    def __init__(self, audio_clips: list[tuple[int, int]],
                 midi_clips: list[tuple[int, int]]) -> None:
        self.audio_clips = set(audio_clips)
        self.midi_clips = set(midi_clips)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, op: str, **kwargs: Any) -> Any:
        self.calls.append((op, dict(kwargs)))
        ti = int(kwargs.get("track_index", 0))
        ci = int(kwargs.get("clip_index", 0))
        if op == "clip.list_arrangement_clips":
            # Direct-LOM clip enumeration (replaces the old OSC-based count
            # because AbletonOSC's reply is unreliable on user-drag clips).
            clips = []
            all_in_track = sorted(
                [c for (t, c) in (self.audio_clips | self.midi_clips) if t == ti]
            )
            for c_idx in all_in_track:
                clips.append({
                    "clip_index": c_idx,
                    "name": f"clip-{ti}-{c_idx}",
                    "length": 4.0,
                    "start_time": 0.0,
                    "is_midi_clip": (ti, c_idx) in self.midi_clips,
                    "is_audio_clip": (ti, c_idx) in self.audio_clips,
                })
            return {"track_index": ti, "clips": clips}
        if op == "clip.get_arrangement_pitch_state":
            is_midi = (ti, ci) in self.midi_clips
            return {
                "track_index": ti, "clip_index": ci,
                "is_midi_clip": is_midi,
                "warping": False if is_midi else True,
                "warp_mode": 0 if is_midi else 4,
                "pitch_coarse": 0 if is_midi else 0,
                "pitch_fine": 0,
            }
        if op == "clip.get_arrangement_notes":
            return {"notes": [
                {"pitch": 60, "start": 0.0, "duration": 1.0, "velocity": 100, "mute": False},
                {"pitch": 64, "start": 1.0, "duration": 1.0, "velocity": 100, "mute": False},
            ]}
        # Any setter just succeeds.
        return {"ok": True}


@pytest.mark.asyncio
async def test_transpose_snapshots_and_restores(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    """For a 2-track / 3-clip arrangement, every clip is snapshotted before
    mutation and every snapshot is replayed in the finally block.
    """
    # Set up: track 0 has 2 audio clips, track 1 has 1 MIDI clip.
    osc = _FakeOSCClient(num_tracks=2, arrangement_clips_per_track=[2, 1])
    bridge = _FakeBridgeClient(
        audio_clips=[(0, 0), (0, 1)],
        midi_clips=[(1, 0)],
    )

    async def fake_get_client():
        return osc

    def fake_get_bridge_client():
        return bridge

    # Make the bounce a no-op that returns a "copied" result.
    async def fake_bounce(output_path, duration_sec, **_kw):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"")  # truthy file
        return {"copied": True, "output_path": str(output_path), "duration_sec": duration_sec}

    monkeypatch.setattr("ableton_mcp.song_flow.transpose.get_client", fake_get_client)
    monkeypatch.setattr("ableton_mcp.song_flow.transpose.get_bridge_client", fake_get_bridge_client)
    monkeypatch.setattr("ableton_mcp.song_flow.transpose.bounce_song_via_resampling", fake_bounce)

    from ableton_mcp.song_flow import transpose_song

    out_path = tmp_path / "transposed.wav"
    result = await transpose_song(
        target_key="F#",
        source_key="C",          # explicit so we don't trigger analyze
        direction="auto",
        output_path=str(out_path),
    )

    assert result["status"] == "ok"
    assert result["semitone_delta"] == -6   # tritone resolves down in auto mode
    assert result["audio_clips_transposed"] == 2
    assert result["midi_clips_transposed"] == 1
    assert result["restore_errors"] == []

    ops = [c[0] for c in bridge.calls]
    # Each audio clip: get_state, set_warp, set_warp_mode, set_pitch (×2 for restore)
    # Each MIDI clip: get_notes, set_notes (+ set_notes for restore)
    # Snapshot first, then mutate, then restore — assert ordering.
    assert ops.count("clip.get_arrangement_pitch_state") == 3   # 2 audio + 1 midi
    assert ops.count("clip.get_arrangement_notes") == 1         # MIDI snapshot
    assert ops.count("clip.set_arrangement_warp") == 4          # 2 mutate + 2 restore
    assert ops.count("clip.set_arrangement_warp_mode") == 4     # 2 mutate + 2 restore
    assert ops.count("clip.set_arrangement_pitch") == 4         # 2 mutate + 2 restore
    assert ops.count("clip.set_arrangement_notes") == 2         # 1 mutate + 1 restore


@pytest.mark.asyncio
async def test_transpose_restores_even_on_bounce_failure(monkeypatch: pytest.MonkeyPatch,
                                                          tmp_path: Path) -> None:
    """Bounce raises mid-flight → transpose still runs the restore pass."""
    osc = _FakeOSCClient(num_tracks=1, arrangement_clips_per_track=[1])
    bridge = _FakeBridgeClient(audio_clips=[(0, 0)], midi_clips=[])

    async def fake_get_client():
        return osc

    def fake_get_bridge_client():
        return bridge

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("ableton_mcp.song_flow.transpose.get_client", fake_get_client)
    monkeypatch.setattr("ableton_mcp.song_flow.transpose.get_bridge_client", fake_get_bridge_client)
    monkeypatch.setattr("ableton_mcp.song_flow.transpose.bounce_song_via_resampling", boom)

    from ableton_mcp.song_flow import transpose_song

    result = await transpose_song(
        target_key="D",
        source_key="C",
        output_path=str(tmp_path / "x.wav"),
    )

    assert result["status"] == "error"
    assert result["stage"] == "transpose_or_bounce"
    # Audio restore still ran: set_warp/warp_mode/pitch each appear 2x
    # (1 for mutate, 1 for restore).
    ops = [c[0] for c in bridge.calls]
    assert ops.count("clip.set_arrangement_warp") == 2
    assert ops.count("clip.set_arrangement_pitch") == 2


@pytest.mark.asyncio
async def test_transpose_noop_when_keys_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """source_key == target_key → bypass everything, return noop."""
    from ableton_mcp.song_flow import transpose_song

    result = await transpose_song(target_key="C", source_key="C")
    assert result["status"] == "noop"
    assert result["semitone_delta"] == 0


# ---------------------------------------------------------------------------
# import_variations_to_live — bulk track + clip create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_variations_creates_one_track_per_variation(monkeypatch: pytest.MonkeyPatch) -> None:
    created_tracks: list[str] = []
    bridge_calls: list[tuple[str, dict[str, Any]]] = []

    class FakeOSC:
        async def request(self, addr: str, *args: Any) -> tuple:
            if addr == "/live/song/get/num_tracks":
                # Each create bumps the count by 1; tests don't care about the
                # exact value, only that the helper computes new_index from it.
                return (len(created_tracks),)
            raise AssertionError(addr)

        def send(self, *args: Any) -> None:
            if args and args[0] == "/live/song/create_audio_track":
                created_tracks.append("created")
            elif args and args[0] == "/live/track/set/name":
                # name is set after create — just record
                pass

    class FakeBridge:
        async def call(self, op: str, **kwargs: Any) -> Any:
            bridge_calls.append((op, dict(kwargs)))
            return {"ok": True}

    async def fake_get_client():
        return FakeOSC()

    def fake_get_bridge_client():
        return FakeBridge()

    monkeypatch.setattr("ableton_mcp.song_flow.import_to_live.get_client", fake_get_client)
    monkeypatch.setattr(
        "ableton_mcp.song_flow.import_to_live.get_bridge_client",
        fake_get_bridge_client,
    )

    from ableton_mcp.song_flow import import_variations_to_live

    variations = [
        {"label": "drums_up", "wav_path": "/tmp/drums_up.wav"},
        {"label": "bass_up", "wav_path": "/tmp/bass_up.wav"},
    ]
    result = await import_variations_to_live(variations)
    assert result["status"] == "ok"
    assert len(result["tracks"]) == 2
    assert all("error" not in t for t in result["tracks"])
    # Each variation triggers exactly one bridge load_sample call.
    load_calls = [c for c in bridge_calls if c[0] == "browser.load_sample"]
    assert len(load_calls) == 2
    assert load_calls[0][1]["path"] == "/tmp/drums_up.wav"


@pytest.mark.asyncio
async def test_import_variations_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    n = {"count": 0}

    class FakeOSC:
        async def request(self, addr: str, *args: Any) -> tuple:
            if addr == "/live/song/get/num_tracks":
                return (n["count"],)
            raise AssertionError(addr)

        def send(self, *args: Any) -> None:
            if args and args[0] == "/live/song/create_audio_track":
                n["count"] += 1

    class FakeBridge:
        def __init__(self):
            self.idx = 0

        async def call(self, op: str, **kwargs: Any) -> Any:
            self.idx += 1
            if self.idx == 2:
                raise RuntimeError("simulated load failure")
            return {"ok": True}

    async def fake_get_client():
        return FakeOSC()

    def fake_get_bridge_client():
        return FakeBridge()

    monkeypatch.setattr("ableton_mcp.song_flow.import_to_live.get_client", fake_get_client)
    monkeypatch.setattr(
        "ableton_mcp.song_flow.import_to_live.get_bridge_client",
        fake_get_bridge_client,
    )

    from ableton_mcp.song_flow import import_variations_to_live

    variations = [
        {"label": "a", "wav_path": "/tmp/a.wav"},
        {"label": "b", "wav_path": "/tmp/b.wav"},
        {"label": "c", "wav_path": "/tmp/c.wav"},
    ]
    result = await import_variations_to_live(variations)
    assert result["status"] == "partial"
    assert result["n_imported"] == 2  # 1 ok + 1 failed (b) + 1 ok
    failed = [t for t in result["tracks"] if "error" in t]
    assert len(failed) == 1
    assert failed[0]["label"] == "b"


# ---------------------------------------------------------------------------
# load_wav_to_arrangement — bridge call + cleanup behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_wav_to_arrangement_success(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    wav = tmp_path / "song.wav"
    _write_synthetic_stem(wav, freq=220.0, duration_sec=0.2)
    sent_osc: list[tuple] = []

    class FakeOSC:
        async def request(self, addr, *args):
            if addr == "/live/song/get/num_tracks":
                return (1,)
            raise AssertionError(addr)

        def send(self, *args):
            sent_osc.append(args)

    bridge_calls: list[tuple] = []

    class FakeBridge:
        async def call(self, op, **kwargs):
            bridge_calls.append((op, kwargs))
            return {
                "loaded": True, "supported": True,
                "via": "create_audio_clip(file_path, position)",
                "clip_file_path": str(wav.resolve()),
                "length": 4.0,
            }

    async def fake_get_client():
        return FakeOSC()

    def fake_get_bridge():
        return FakeBridge()

    monkeypatch.setattr("ableton_mcp.song_flow.load_to_arrangement.get_client", fake_get_client)
    monkeypatch.setattr("ableton_mcp.song_flow.load_to_arrangement.get_bridge_client", fake_get_bridge)

    from ableton_mcp.song_flow import load_wav_to_arrangement
    result = await load_wav_to_arrangement(wav)

    assert result["status"] == "ok"
    assert result["loaded"] is True
    # Should have created a track + named it via OSC.
    assert ("/live/song/create_audio_track", -1) in sent_osc
    # Bridge was called with the new track index and the wav path.
    assert bridge_calls[0][0] == "clip.create_arrangement_audio_clip"
    assert bridge_calls[0][1]["file_path"] == str(wav.resolve())


@pytest.mark.asyncio
async def test_load_wav_to_arrangement_cleans_up_on_unsupported(monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: Path) -> None:
    """When the bridge reports the LOM can't load the wav, the empty temp
    track must be deleted so the user's session isn't littered."""
    wav = tmp_path / "song.wav"
    _write_synthetic_stem(wav, freq=220.0, duration_sec=0.2)
    sent_osc: list[tuple] = []

    class FakeOSC:
        async def request(self, addr, *args):
            if addr == "/live/song/get/num_tracks":
                return (1,)
            raise AssertionError(addr)

        def send(self, *args):
            sent_osc.append(args)

    class FakeBridge:
        async def call(self, op, **kwargs):
            return {
                "loaded": False, "supported": False,
                "attempt_errors": ["create_audio_clip(file_path, position) -> TypeError: bad args"],
                "workaround": "Drag manually.",
            }

    async def fake_get_client():
        return FakeOSC()

    def fake_get_bridge():
        return FakeBridge()

    monkeypatch.setattr("ableton_mcp.song_flow.load_to_arrangement.get_client", fake_get_client)
    monkeypatch.setattr("ableton_mcp.song_flow.load_to_arrangement.get_bridge_client", fake_get_bridge)

    from ableton_mcp.song_flow import load_wav_to_arrangement
    result = await load_wav_to_arrangement(wav)

    assert result["status"] == "not_supported"
    assert result["loaded"] is False
    # Track deleted: the create AND a delete should both be in sent_osc.
    delete_calls = [s for s in sent_osc if s and s[0] == "/live/song/delete_track"]
    assert len(delete_calls) == 1, f"expected exactly one delete_track, got {delete_calls!r}"


@pytest.mark.asyncio
async def test_load_wav_to_arrangement_missing_file(tmp_path: Path) -> None:
    from ableton_mcp.song_flow import load_wav_to_arrangement
    result = await load_wav_to_arrangement(tmp_path / "nope.wav")
    assert result["status"] == "error"
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# Server registration smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_song_flow_tools_registered() -> None:
    from ableton_mcp.server import build_server

    mcp = build_server()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    for expected in (
        "song_analyze",
        "song_transpose",
        "song_make_variations",
        "song_import_variations_to_live",
        "song_load_wav_to_arrangement",
    ):
        assert expected in names, f"missing tool: {expected}"
