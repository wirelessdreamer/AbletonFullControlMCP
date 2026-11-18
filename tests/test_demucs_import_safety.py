"""Exercise separation I/O with real tensors and a deterministic lightweight model."""

from __future__ import annotations

import shutil
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.stems.demucs import StemsError, _separate_sync


def test_float_output_content_cache_and_corruption_recovery(tmp_path, monkeypatch):
    import torch
    import demucs.apply
    import demucs.pretrained

    calls = []
    class Model:
        sources = ["drums", "bass", "other", "vocals", "guitar", "piano"]
        samplerate = 44100
        def eval(self):
            return self
    def model(name):
        calls.append(name)
        return Model()
    def apply(model, audio, **kwargs):
        return torch.stack([audio * 4 for _ in model.sources], dim=1)
    monkeypatch.setattr(demucs.pretrained, "get_model", model)
    monkeypatch.setattr(demucs.apply, "apply_model", apply)
    source = tmp_path / "recording.wav"
    sf.write(source, np.sin(np.arange(4410) * 0.1) * 0.8, 44100, subtype="FLOAT")
    first = _separate_sync(str(source), "htdemucs_6s", tmp_path / "cache")
    assert len(first) == 6
    audio, _ = sf.read(first[0].path, always_2d=True)
    assert audio.shape == (4410, 2)
    assert sf.info(first[0].path).subtype == "FLOAT"
    assert np.max(np.abs(audio)) > 1  # Integer encoding would have destroyed these samples.
    renamed = tmp_path / "same_content_different_name.wav"
    shutil.copy2(source, renamed)
    second = _separate_sync(str(renamed), "htdemucs_6s", tmp_path / "cache")
    assert [s.path for s in first] == [s.path for s in second]
    assert len(calls) == 1
    marker = Path(first[0].path).parent / "separation.json"
    metadata = marker.read_text()
    marker.write_text(json.dumps({"sources": []}))
    rebuilt = _separate_sync(str(source), "htdemucs_6s", tmp_path / "cache")
    assert len(rebuilt) == 6 and len(calls) == 2
    marker.write_text(metadata)
    sf.write(first[0].path, np.zeros_like(audio), 44100, subtype="FLOAT")
    recovered = _separate_sync(str(source), "htdemucs_6s", tmp_path / "cache")
    assert len(calls) == 3
    assert recovered[0].path != first[0].path
    assert sf.info(recovered[0].path).frames == 4410


def test_more_than_stereo_is_rejected_without_discarding_channels(tmp_path, monkeypatch):
    import demucs.pretrained
    class Model:
        sources = ["drums", "bass", "other", "vocals", "guitar", "piano"]
        samplerate = 44100
        def eval(self):
            return self
    monkeypatch.setattr(demucs.pretrained, "get_model", lambda name: Model())
    source = tmp_path / "surround.wav"
    sf.write(source, np.ones((4410, 4), np.float32), 44100)
    with pytest.raises(StemsError, match="channels were not discarded"):
        _separate_sync(str(source), "htdemucs_6s", tmp_path / "cache")
