"""Tests for ``ableton_mcp.local_automation.als_synth``.

The synthesizer was validated end-to-end against real Live 11.3.43
(see docs/PRACTICE_PACK_WAV_LOAD_DEBUG.md for the smoke-test log) —
these are unit tests for the pure-data parts: input validation,
template parsing, ID renumbering, NextPointeeId bump, Returns-last
ordering. They don't need Live.
"""

from __future__ import annotations

import gzip
import os
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ableton_mcp.local_automation.als_synth import (
    AlsSynthesisError,
    WavSpec,
    _GLOBAL_ID_THRESHOLD,
    _collect_used_ids,
    _renumber_global_ids_in_clone,
    _rewrite_id_references_in_clone,
    _strip_audio_clips_from_track,
    synthesize_als,
)


# ---------------------------------------------------------------------------
# Test fixtures — synthesize a minimal template .als
# ---------------------------------------------------------------------------


def _minimal_template_xml() -> str:
    """Build the smallest valid-ish Ableton XML that ``synthesize_als``
    can use as a template. Real Live Sets are 900+ KB; this is the
    bare minimum the synthesizer needs: ``LiveSet/Tracks`` with one
    AudioTrack containing a populated arrangement clip + the
    NextPointeeId counter the synthesizer bumps on save.

    The XML is intentionally simplified — it would NOT load in Live
    (missing Mixer, Returns, MasterTrack, etc.) but it has every
    structural element the synthesizer reads/mutates.
    """
    return '''<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_11300" SchemaChangeCount="7" Creator="Test">
  <LiveSet>
    <NextPointeeId Value="100" />
    <Tracks>
      <AudioTrack Id="8">
        <LomId Value="0" />
        <Name>
          <EffectiveName Value="3-Reference Track" />
          <UserName Value="" />
        </Name>
        <Color Value="4" />
        <DeviceChain>
          <Mixer>
            <On><AutomationTarget Id="50"><LockEnvelope Value="0" /></AutomationTarget></On>
            <Pointee Id="51" />
          </Mixer>
          <MainSequencer>
            <Sample>
              <ArrangerAutomation>
                <Events>
                  <AudioClip Id="0" Time="0">
                    <LomId Value="0" />
                    <CurrentStart Value="0" />
                    <CurrentEnd Value="100.0" />
                    <Name Value="Reference Clip" />
                    <IsWarped Value="false" />
                    <WarpMode Value="0" />
                    <SampleRef>
                      <FileRef>
                        <RelativePathType Value="0" />
                        <RelativePath Value="" />
                        <Path Value="C:/old/reference.wav" />
                        <Type Value="1" />
                        <LivePackName Value="" />
                        <LivePackId Value="" />
                        <OriginalFileSize Value="123456" />
                      </FileRef>
                    </SampleRef>
                  </AudioClip>
                </Events>
              </ArrangerAutomation>
            </Sample>
          </MainSequencer>
        </DeviceChain>
      </AudioTrack>
      <ReturnTrack Id="2">
        <Name>
          <EffectiveName Value="A-Reverb" />
        </Name>
      </ReturnTrack>
      <ReturnTrack Id="3">
        <Name>
          <EffectiveName Value="B-Delay" />
        </Name>
      </ReturnTrack>
    </Tracks>
  </LiveSet>
</Ableton>'''


@pytest.fixture
def template_als(tmp_path: Path) -> Path:
    """Write a minimal template .als (gzipped XML) and return its path."""
    template = tmp_path / "template.als"
    with gzip.open(template, "wb") as f:
        f.write(_minimal_template_xml().encode("utf-8"))
    return template


@pytest.fixture
def fake_wav(tmp_path: Path) -> Path:
    """Write a valid 1-second silent WAV file. ``soundfile.info`` (which
    the synthesizer uses for duration) needs a real wav header to parse."""
    wav = tmp_path / "fake.wav"
    sr = 44100
    n_frames = sr  # 1 second
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n_frames)
    return wav


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_empty_wavs_raises(template_als: Path, tmp_path: Path) -> None:
    with pytest.raises(AlsSynthesisError, match="empty"):
        synthesize_als(
            [],
            template_als_path=str(template_als),
            output_als_path=str(tmp_path / "out.als"),
        )


def test_missing_wav_raises(template_als: Path, tmp_path: Path) -> None:
    with pytest.raises(AlsSynthesisError, match="not found"):
        synthesize_als(
            [WavSpec(path=str(tmp_path / "definitely-not-here.wav"))],
            template_als_path=str(template_als),
            output_als_path=str(tmp_path / "out.als"),
        )


def test_missing_template_raises(tmp_path: Path, fake_wav: Path) -> None:
    # Use a real wav so validation passes through to the template check.
    with pytest.raises(AlsSynthesisError, match="als file not found"):
        synthesize_als(
            [str(fake_wav)],
            template_als_path=str(tmp_path / "no-template.als"),
            output_als_path=str(tmp_path / "out.als"),
        )


def test_template_without_audio_track_raises(tmp_path: Path, fake_wav: Path) -> None:
    """A template with no populated AudioTrack can't be used because the
    synthesizer clones from a reference clip in the template."""
    bad_template = tmp_path / "bad.als"
    with gzip.open(bad_template, "wb") as f:
        f.write(
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Ableton><LiveSet><Tracks></Tracks></LiveSet></Ableton>'
        )
    with pytest.raises(AlsSynthesisError, match="AudioClip"):
        synthesize_als(
            [str(fake_wav)],
            template_als_path=str(bad_template),
            output_als_path=str(tmp_path / "out.als"),
        )


# ---------------------------------------------------------------------------
# Synthesis end-to-end against the in-memory template
# ---------------------------------------------------------------------------


def test_synthesizes_one_audio_track_per_wav(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "out.als"
    result = synthesize_als(
        [
            WavSpec(path=str(fake_wav), track_name="Wav One"),
            WavSpec(path=str(fake_wav), track_name="Wav Two"),
        ],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
    )
    assert result.tracks_added == 2
    assert os.path.exists(result.output_path)

    # Parse the output and verify there are 2 NEW AudioTracks beyond
    # the template's reference one.
    with gzip.open(result.output_path, "rb") as f:
        root = ET.fromstring(f.read())
    tracks = root.findall(".//LiveSet/Tracks/AudioTrack")
    # 1 template AudioTrack + 2 new ones = 3 total
    assert len(tracks) == 3


def test_returns_last_in_track_order(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    """Live requires ReturnTracks at the END of the Tracks container.
    New AudioTracks must be inserted BEFORE the first ReturnTrack."""
    out_path = tmp_path / "out.als"
    synthesize_als(
        [WavSpec(path=str(fake_wav))],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
    )
    with gzip.open(out_path, "rb") as f:
        root = ET.fromstring(f.read())
    tags_in_order = [t.tag for t in root.find(".//LiveSet/Tracks")]
    # Walk left-to-right: once we see ReturnTrack, no AudioTrack may follow.
    seen_return = False
    for tag in tags_in_order:
        if tag == "ReturnTrack":
            seen_return = True
        elif tag == "AudioTrack" and seen_return:
            pytest.fail(f"AudioTrack found after ReturnTrack in order: {tags_in_order}")


def test_audio_clip_points_at_supplied_wav(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "out.als"
    synthesize_als(
        [WavSpec(path=str(fake_wav), track_name="My Wav")],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
    )
    with gzip.open(out_path, "rb") as f:
        root = ET.fromstring(f.read())
    # Find the new AudioTrack (not the reference one).
    new_tracks = [
        t for t in root.findall(".//LiveSet/Tracks/AudioTrack")
        if t.find("Name/EffectiveName").get("Value") == "My Wav"
    ]
    assert len(new_tracks) == 1
    clip = new_tracks[0].find(
        "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    )
    assert clip is not None
    path_el = clip.find("SampleRef/FileRef/Path")
    assert path_el is not None
    # Should be the resolved absolute wav path, with forward slashes.
    expected = str(fake_wav.resolve()).replace("\\", "/")
    assert path_el.get("Value") == expected


def test_existing_clips_stripped_by_default(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    """``strip_existing_clips=True`` is the default — template's
    reference clip should be removed before adding ours."""
    out_path = tmp_path / "out.als"
    synthesize_als(
        [WavSpec(path=str(fake_wav), track_name="My Wav")],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
    )
    with gzip.open(out_path, "rb") as f:
        root = ET.fromstring(f.read())
    # The template's reference track should have NO clip (stripped).
    ref_track = next(
        t for t in root.findall(".//LiveSet/Tracks/AudioTrack")
        if t.find("Name/EffectiveName").get("Value") == "3-Reference Track"
    )
    ref_events = ref_track.find(
        "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events"
    )
    assert ref_events.find("AudioClip") is None


def test_keep_existing_clips_preserves_reference(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "out.als"
    synthesize_als(
        [WavSpec(path=str(fake_wav))],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
        strip_existing_clips=False,
    )
    with gzip.open(out_path, "rb") as f:
        root = ET.fromstring(f.read())
    ref_track = next(
        t for t in root.findall(".//LiveSet/Tracks/AudioTrack")
        if t.find("Name/EffectiveName").get("Value") == "3-Reference Track"
    )
    # Reference clip should still be there.
    assert ref_track.find(
        "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    ) is not None


# ---------------------------------------------------------------------------
# ID uniqueness — the v1→v4 debug saga in one regression test
# ---------------------------------------------------------------------------


def test_global_ids_unique_across_cloned_tracks(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    """v1 of the synthesizer crashed Live with 'non-unique Pointee IDs'
    because cloning re-used the reference's high IDs. This test guards
    that fix."""
    out_path = tmp_path / "out.als"
    synthesize_als(
        [WavSpec(path=str(fake_wav)) for _ in range(3)],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
    )
    with gzip.open(out_path, "rb") as f:
        root = ET.fromstring(f.read())
    high_ids = [
        int(e.get("Id"))
        for e in root.iter()
        if e.get("Id") and e.get("Id").lstrip("-").isdigit()
        and int(e.get("Id")) >= _GLOBAL_ID_THRESHOLD
    ]
    assert len(high_ids) == len(set(high_ids)), (
        f"document-global IDs must be unique; duplicates: "
        f"{[i for i in set(high_ids) if high_ids.count(i) > 1]}"
    )


def test_next_pointee_id_above_max_used(
    template_als: Path, fake_wav: Path, tmp_path: Path,
) -> None:
    """v2 of the synthesizer triggered 'NextPointeeId is too low' because
    we generated IDs above the counter but didn't bump it. Guards the fix."""
    out_path = tmp_path / "out.als"
    synthesize_als(
        [WavSpec(path=str(fake_wav)) for _ in range(3)],
        template_als_path=str(template_als),
        output_als_path=str(out_path),
    )
    with gzip.open(out_path, "rb") as f:
        root = ET.fromstring(f.read())
    next_id_el = root.find(".//NextPointeeId")
    assert next_id_el is not None
    next_id = int(next_id_el.get("Value"))
    max_id_used = max(
        int(e.get("Id")) for e in root.iter()
        if e.get("Id") and e.get("Id").lstrip("-").isdigit()
    )
    assert next_id > max_id_used, (
        f"NextPointeeId ({next_id}) must be > max ID actually used ({max_id_used})"
    )


# ---------------------------------------------------------------------------
# Direct helper-function tests (cheaper than full synth)
# ---------------------------------------------------------------------------


def test_collect_used_ids_returns_only_numeric_ids() -> None:
    root = ET.fromstring(
        '<root>'
        '<a Id="10" />'
        '<b Id="not-a-number" />'
        '<c Id="20" />'
        '<d />'
        '</root>'
    )
    used = _collect_used_ids(root)
    assert used == {10, 20}


def test_strip_audio_clips_from_track_clears_events() -> None:
    track = ET.fromstring(
        '<AudioTrack>'
        '  <DeviceChain><MainSequencer><Sample><ArrangerAutomation>'
        '    <Events>'
        '      <AudioClip Id="0" />'
        '      <AudioClip Id="1" />'
        '    </Events>'
        '  </ArrangerAutomation></Sample></MainSequencer></DeviceChain>'
        '</AudioTrack>'
    )
    _strip_audio_clips_from_track(track)
    events = track.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    assert events.find("AudioClip") is None


def test_renumber_global_ids_skips_low_ids() -> None:
    """Low IDs (ClipSlot index, send index, etc.) are track-local and
    must NOT be renumbered."""
    clone = ET.fromstring(
        '<root>'
        '<a Id="3" />'          # below threshold → keep
        '<b Id="8000" />'       # above threshold → renumber
        '<c Id="8001" />'       # above threshold → renumber
        '</root>'
    )
    counter = iter(range(99000, 99010))
    mapping = _renumber_global_ids_in_clone(clone, lambda: next(counter))
    assert clone.find("a").get("Id") == "3"  # unchanged
    assert clone.find("b").get("Id") != "8000"
    assert clone.find("c").get("Id") != "8001"
    # Mapping captures the rewrites for downstream reference updates.
    assert mapping == {8000: 99000, 8001: 99001}


def test_rewrite_id_references_updates_value_attrs() -> None:
    """Pointee Value attributes that referenced an old high ID should
    be rewritten to the new mapped ID."""
    clone = ET.fromstring(
        '<root>'
        '<Pointee Value="8000" />'
        '<UnrelatedValue Value="42" />'   # below mapping → untouched
        '<AnotherPointee Value="8001" />'
        '</root>'
    )
    _rewrite_id_references_in_clone(clone, {8000: 99000, 8001: 99001})
    assert clone.find("Pointee").get("Value") == "99000"
    assert clone.find("UnrelatedValue").get("Value") == "42"
    assert clone.find("AnotherPointee").get("Value") == "99001"
