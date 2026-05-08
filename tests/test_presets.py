"""Tests for the curated + discovered preset library."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ableton_mcp.presets import LIBRARY, Preset
from ableton_mcp.presets.clusterer import discover_presets_from_dataset
from ableton_mcp.presets.library import by_name
from ableton_mcp.presets.storage import (
    add_preset,
    all_presets,
    clear_all,
    device_classes,
    find_by_name,
    list_presets,
    search_by_tag,
    search_by_text,
    seed_curated,
)
from ableton_mcp.sound import (
    ProbeDataset,
    SweepPlanner,
    SynthStubRenderer,
    extract_features,
)


# ---------- library shape -----------------------------------------------------


def test_library_has_at_least_30_curated_presets() -> None:
    assert len(LIBRARY) >= 30, f"only {len(LIBRARY)} curated presets — need ≥30"


def test_library_entries_are_well_formed() -> None:
    seen_names: set[str] = set()
    for p in LIBRARY:
        assert isinstance(p, Preset)
        assert p.name and p.name not in seen_names, f"duplicate or empty: {p.name}"
        seen_names.add(p.name)
        assert p.device_class
        assert p.params, f"no params on {p.name}"
        assert p.tags, f"no tags on {p.name}"
        assert p.source == "curated"
        # Every param value is a finite float.
        for k, v in p.params.items():
            assert isinstance(v, float), f"{p.name}.{k} is not float"
            assert np.isfinite(v), f"{p.name}.{k} not finite"


def test_library_includes_synth_stub_presets() -> None:
    stub = [p for p in LIBRARY if p.device_class == "synth_stub"]
    assert len(stub) >= 20, "expected at least 20 synth_stub curated presets"


def test_by_name_is_case_insensitive() -> None:
    p = by_name("warm saw pad")
    assert p is not None and p.name == "Warm Saw Pad"


# ---------- sqlite storage ----------------------------------------------------


def test_seed_curated_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    inserted_first = seed_curated(db_path=db)
    assert inserted_first == len(LIBRARY)
    inserted_second = seed_curated(db_path=db)
    assert inserted_second == 0, "second seed must not duplicate rows"
    rows = all_presets(db_path=db)
    # Names are unique, count matches the curated library.
    assert len(rows) == len(LIBRARY)
    names = [r.name for r in rows]
    assert len(set(names)) == len(names)


def test_list_presets_filters(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)

    stub_only = list_presets(device_class="synth_stub", db_path=db)
    assert stub_only and all(p.device_class == "synth_stub" for p in stub_only)

    pads = list_presets(tag="pad", db_path=db)
    assert pads, "expected at least one pad-tagged preset"
    for p in pads:
        assert any(t.lower() == "pad" for t in p.tags)


def test_find_by_name_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    p = find_by_name("Sub Bass", db_path=db)
    assert p is not None
    assert p.name == "Sub Bass"
    assert "bass" in [t.lower() for t in p.tags]
    # Case-insensitive lookup.
    p2 = find_by_name("sub bass", db_path=db)
    assert p2 is not None and p2.name == "Sub Bass"


def test_search_by_tag_returns_matches(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    pluck = search_by_tag("pluck", db_path=db)
    assert pluck, "no plucks?"
    assert any("pluck" in p.name.lower() or "pluck" in [t.lower() for t in p.tags] for p in pluck)


def test_search_by_text_partial_matches_warm_pad(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    rows = search_by_text("warm pad", db_path=db)
    assert rows, "fuzzy search for 'warm pad' returned nothing"
    # The exact name should be top — both tokens hit it.
    assert rows[0].name == "Warm Saw Pad", f"expected Warm Saw Pad first, got {rows[0].name}"


def test_search_by_text_partial_matches_warm(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    rows = search_by_text("warm", db_path=db)
    assert any(p.name == "Warm Saw Pad" for p in rows), (
        "single-token 'warm' should still find 'Warm Saw Pad'"
    )


def test_add_preset_then_find(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    custom = Preset(
        name="Custom Test Pad",
        device_class="synth_stub",
        params={"freq": 220.0, "cutoff": 1000.0},
        tags=["custom", "test"],
        description="a test row",
        source="discovered",
    )
    rid = add_preset(custom, db_path=db)
    assert rid > 0
    fetched = find_by_name("Custom Test Pad", db_path=db)
    assert fetched is not None
    assert fetched.source == "discovered"
    assert fetched.params["freq"] == pytest.approx(220.0)


def test_clear_all_then_seed(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    assert clear_all(db_path=db) == len(LIBRARY)
    assert all_presets(db_path=db) == []
    seed_curated(db_path=db)
    assert len(all_presets(db_path=db)) == len(LIBRARY)


# ---------- clusterer ---------------------------------------------------------


def _build_synth_stub_probe(tmp_path: Path) -> Path:
    """Render a small probe dataset suitable for clustering."""
    renderer = SynthStubRenderer(sample_rate=22050, duration_sec=0.4)
    ranges = {
        "freq": (110.0, 880.0),
        "cutoff": (400.0, 6000.0),
        "noise_amount": (0.0, 0.4),
    }
    db = tmp_path / "stub_probe.sqlite"
    with ProbeDataset(db, device_id="synth_stub") as ds:
        for cell in SweepPlanner(ranges, steps_per_param=3, strategy="grid"):
            audio = renderer.render(cell)
            feats = extract_features(audio, sr=22050)
            ds.append(cell, feats)
    return db


def test_discover_presets_produces_k_with_auto_names(tmp_path: Path) -> None:
    probe_path = _build_synth_stub_probe(tmp_path)
    preset_db = tmp_path / "presets.sqlite"
    seed_curated(db_path=preset_db)

    discovered = discover_presets_from_dataset(
        probe_path, k=4, db_path=preset_db, seed=0
    )
    assert len(discovered) == 4
    for p in discovered:
        assert p.source == "discovered"
        assert p.name.startswith("Cluster"), f"unexpected auto-name: {p.name}"
        assert p.tags, f"discovered preset {p.name} has no tags"
        assert p.device_class == "synth_stub"
        # Discovered presets should land in the DB.
        assert find_by_name(p.name, db_path=preset_db) is not None

    # Discovered presets are filterable by source.
    in_db = list_presets(source="discovered", db_path=preset_db)
    assert len(in_db) >= 4


def test_discover_presets_persist_false_does_not_write(tmp_path: Path) -> None:
    probe_path = _build_synth_stub_probe(tmp_path)
    preset_db = tmp_path / "presets.sqlite"
    seed_curated(db_path=preset_db)
    before = len(all_presets(db_path=preset_db))
    discovered = discover_presets_from_dataset(
        probe_path, k=3, db_path=preset_db, persist=False, seed=0
    )
    assert len(discovered) == 3
    after = len(all_presets(db_path=preset_db))
    assert after == before, "persist=False must not write to the preset DB"


def test_discover_presets_caps_k_to_dataset_size(tmp_path: Path) -> None:
    """Tiny dataset (1 row) must not crash KMeans — k is clamped."""
    db = tmp_path / "tiny_probe.sqlite"
    with ProbeDataset(db, device_id="synth_stub") as ds:
        renderer = SynthStubRenderer(sample_rate=22050, duration_sec=0.2)
        feats = extract_features(renderer.render({"freq": 220.0}), sr=22050)
        ds.append({"freq": 220.0}, feats)
    preset_db = tmp_path / "presets.sqlite"
    seed_curated(db_path=preset_db)
    discovered = discover_presets_from_dataset(
        db, k=20, db_path=preset_db, seed=0
    )
    assert len(discovered) == 1
    assert discovered[0].source == "discovered"


def test_discover_presets_missing_dataset_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_presets_from_dataset(tmp_path / "nope.sqlite", k=2)


# ---------- device_classes helper --------------------------------------------


def test_device_classes_includes_synth_stub(tmp_path: Path) -> None:
    db = tmp_path / "presets.sqlite"
    seed_curated(db_path=db)
    classes = device_classes(db_path=db)
    assert "synth_stub" in classes


# ---------- MCP tool registration --------------------------------------------


def test_preset_tool_module_imports() -> None:
    """Importing the tool module must not raise."""
    from ableton_mcp.tools import presets as presets_tools  # noqa: F401

    assert hasattr(presets_tools, "register")
