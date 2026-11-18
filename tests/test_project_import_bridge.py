"""Exercise the actual Remote Script handlers with a bounded fake Live API."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bridge_project(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "live_remote_script/AbletonFullControlBridge/handlers/project.py"
    spec = importlib.util.spec_from_file_location("test_live_project", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    song = SimpleNamespace(can_undo=False, is_playing=False, record_mode=False)
    app = SimpleNamespace(get_document=lambda: song, open_dialog_count=0)
    monkeypatch.setitem(sys.modules, "Live", SimpleNamespace(Application=SimpleNamespace(get_application=lambda: app)))
    return module, song, app


def test_missing_native_save_returns_unsupported_without_ui(bridge_project):
    module, _, _ = bridge_project
    result = module.save(None)
    assert result["saved"] is False and result["supported"] is False
    assert module.capabilities(None)["screen_control"] is False


def test_open_refuses_edited_set_before_os_request(bridge_project, tmp_path, monkeypatch):
    module, song, _ = bridge_project
    path = tmp_path / "Song.als"
    path.write_bytes(b"test")
    song.can_undo = True
    def forbidden(*args):
        raise AssertionError("must not open an edited set")
    monkeypatch.setattr(module.os, "startfile", forbidden, raising=False)
    with pytest.raises(RuntimeError, match="undoable edits"):
        module.open_set(None, str(path))


def test_user_saved_set_can_keep_undo_history_without_discarding_anything(bridge_project, tmp_path, monkeypatch):
    module, song, app = bridge_project
    preserved = tmp_path / "User edit.als"
    target = tmp_path / "Import.als"
    preserved.write_bytes(b"saved edits")
    target.write_bytes(b"import")
    song.file_path = str(preserved)
    song.can_undo = True
    opened = []
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", path=module.os.path,
                                                    startfile=opened.append))
    result = module.open_set(None, str(target), preserved_set_path=str(preserved))
    assert opened == [str(target)]
    assert "untouched" in result["pending"]
    assert preserved.read_bytes() == b"saved edits"
    with pytest.raises(RuntimeError, match="does not identify"):
        module.open_set(None, str(target), preserved_set_path=str(target))
    app.open_dialog_count = 1
    with pytest.raises(RuntimeError, match="dialogs"):
        module.open_set(None, str(target), preserved_set_path=str(preserved))


def test_checkpoint_refuses_modified_generated_artifact(bridge_project, tmp_path, monkeypatch):
    module, _, _ = bridge_project
    monkeypatch.setattr(module, "_require_import_set", lambda *args: None)
    path = tmp_path / "Song.als"
    path.write_bytes(b"original")
    (tmp_path / "import_manifest.json").write_text(json.dumps({"set_sha256": hashlib.sha256(b"original").hexdigest()}))
    path.write_bytes(b"edited")
    with pytest.raises(RuntimeError, match="edited after import"):
        module.import_checkpoint(None, str(path))



def test_checkpoint_does_not_require_embedded_xml_parser(bridge_project, tmp_path, monkeypatch):
    import builtins
    import gzip
    module, _, _ = bridge_project
    monkeypatch.setattr(module, "_require_import_set", lambda *args: None)
    path = tmp_path / "Song.als"
    data = gzip.compress(b"<Ableton/>")
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    (tmp_path / "import_manifest.json").write_text(json.dumps({"set_sha256": sha}))
    original = builtins.__import__
    def embedded_import(name, *args, **kwargs):
        if name.startswith("xml") or name in {"pyexpat", "expat"}:
            raise ImportError("No module named expat")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", embedded_import)
    result = module.import_checkpoint(None, str(path))
    assert result["checkpointed"] and result["set_sha256"] == sha
    assert result["native_save"] is False


def test_core_reload_preserves_listener_and_updates_factory(bridge_project, monkeypatch):
    import importlib
    _, _, app = bridge_project
    root = Path(__file__).resolve().parents[1] / "live_remote_script"
    monkeypatch.syspath_prepend(str(root))
    package = importlib.import_module("AbletonFullControlBridge")
    server_module = importlib.import_module("AbletonFullControlBridge.bridge_server")
    project = importlib.import_module("AbletonFullControlBridge.handlers.project")
    owner = SimpleNamespace(show_message=lambda msg: None)
    server = server_module.BridgeServer(owner, port=0)
    app.control_surfaces = [server]
    listener = server._listener
    try:
        result = project.reload_bridge(owner)
        assert result["listener_preserved"] and result["refresh_state"]
        assert server._listener is listener
        assert type(server) is package.BridgeServer
        assert server.refresh_state() is None
        assert "project.import_snapshot" in server._handlers
    finally:
        server.disconnect()



@pytest.mark.parametrize("change", [None, "controls", "expired", "edited", "file", "dialog", "no_lease"])
def test_native_switch_requires_restored_state_and_owned_artifact(bridge_project, tmp_path, monkeypatch, change):
    import time
    module, song, app = bridge_project
    song.name = "Song"
    path = tmp_path / "Song.als"
    path.write_bytes(b"checked-artifact")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "_require_import_set", lambda *args: None)
    monkeypatch.setattr(module, "_switch_fingerprint", lambda *args: "changed" if change == "controls" else "baseline")
    app.current_dialog_message = 'Save changes to "Song" before closing?'
    app.current_dialog_button_count = 3
    pressed = []
    app.press_current_dialog_button = pressed.append
    module._IMPORT_SWITCH = (song, str(path), sha, "baseline", time.monotonic() - (121 if change == "expired" else 0))
    if change == "edited":
        song.can_undo = True
    if change == "file":
        path.write_bytes(b"edited-artifact")
    if change == "dialog":
        app.current_dialog_message = "Repair corrupt set?"
    if change == "no_lease":
        module._IMPORT_SWITCH = None
    if change is None:
        result = module.import_accept_switch(None, str(path))
        assert pressed == [1] and result["screen_control"] is False
        with pytest.raises(RuntimeError):
            module.import_accept_switch(None, str(path))
        assert pressed == [1]
    else:
        with pytest.raises(RuntimeError):
            module.import_accept_switch(None, str(path))
        assert pressed == []
