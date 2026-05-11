"""Non-blocking JSON-over-TCP server for AbletonFullControlBridge.

Live runs Remote Scripts on its single audio/UI thread. Anything that blocks here
freezes Live's UI and can drop out audio. So:

  * We use non-blocking sockets (`setblocking(False)`) and never `recv()` more
    than is currently buffered.
  * `poll()` is driven by Live's `update_display` callback (~60 Hz). On each
    tick we accept new connections, drain ready bytes, dispatch any complete
    request line, and write replies back without blocking.
  * Each connection is single-shot: one request line, one response line, then
    we close. This keeps the state machine trivial.

The on-the-wire protocol is line-delimited JSON:

    {"id": 7, "op": "browser.tree", "args": {}}\\n
    {"id": 7, "ok": true, "result": {...}}\\n

If a handler raises, we return `{"ok": false, "error": "<repr>"}`.
"""

from __future__ import absolute_import

import errno
import json
import socket
import sys
import traceback

from .handlers import browser as browser_handlers
from .handlers import clips as clip_handlers
from .handlers import project as project_handlers
from .handlers import tracks as track_handlers


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11002
MAX_LINE_BYTES = 1 << 20  # 1 MiB — browser tree dumps can be large


def _log(msg):
    # Live captures stdout from Remote Scripts to its Log.txt, so a plain
    # print() is the standard idiom here.
    sys.stdout.write("[AbletonFullControlBridge] " + str(msg) + "\n")
    try:
        sys.stdout.flush()
    except Exception:
        pass


class _ClientState(object):
    __slots__ = ("sock", "addr", "rx_buf", "tx_buf", "closed")

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.rx_buf = b""
        self.tx_buf = b""
        self.closed = False


class BridgeServer(object):
    """Lifetime: one instance per Live session.

    Live calls:
        update_display() ~60 Hz on main thread     → drives our poll loop
        disconnect()     when control surface unloaded → tears down socket
    """

    def __init__(self, c_instance, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self._c_instance = c_instance
        self._host = host
        self._port = port
        self._listener = None
        self._clients = []  # list[_ClientState]
        self._handlers = self._build_handler_table()
        try:
            self._open_listener()
            _log("listening on %s:%d (%d handlers)" % (host, port, len(self._handlers)))
            self._show_message("AbletonFullControlBridge listening on %d" % port)
        except Exception as exc:
            _log("FATAL: could not bind listener: %r" % (exc,))

    # ----- Live remote-script lifecycle -----

    def disconnect(self):
        _log("disconnecting")
        for c in self._clients:
            self._close_client(c)
        self._clients = []
        if self._listener is not None:
            try:
                self._listener.close()
            except Exception:
                pass
            self._listener = None

    def can_lock_to_devices(self):  # noqa: D401 — required surface stub
        return False

    def lock_to_device(self, device):  # pragma: no cover — required stub
        pass

    def unlock_from_device(self, device):  # pragma: no cover — required stub
        pass

    def suggest_input_port(self):
        return ""

    def suggest_output_port(self):
        return ""

    def suggest_map_mode(self, cc_no, channel):  # pragma: no cover
        return -1

    def restore_bank(self, bank):  # pragma: no cover
        pass

    def show_message(self, message):  # pragma: no cover
        self._show_message(message)

    def connect_script_instances(self, instantiated_scripts):  # pragma: no cover
        pass

    def request_rebuild_midi_map(self):  # pragma: no cover
        pass

    def update_display(self):
        """Heartbeat — Live invokes this ~60×/sec on the main thread."""
        try:
            self._poll()
        except Exception as exc:  # never let an exception bubble back to Live
            _log("poll error: %r\n%s" % (exc, traceback.format_exc()))

    def build_midi_map(self, midi_map_handle):  # pragma: no cover
        pass

    def receive_midi(self, midi_bytes):  # pragma: no cover
        pass

    # ----- Internals -----

    def _show_message(self, msg):
        try:
            self._c_instance.show_message(msg)
        except Exception:
            pass

    def _open_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setblocking(False)
        s.bind((self._host, self._port))
        s.listen(8)
        self._listener = s

    def _poll(self):
        if self._listener is None:
            return
        # Accept as many pending connections as we can without blocking.
        while True:
            try:
                conn, addr = self._listener.accept()
            except socket.error as exc:
                err = exc.args[0] if exc.args else None
                if err in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                _log("accept error: %r" % (exc,))
                break
            conn.setblocking(False)
            self._clients.append(_ClientState(conn, addr))

        # Service each existing client.
        survivors = []
        for c in self._clients:
            self._service(c)
            if not c.closed:
                survivors.append(c)
        self._clients = survivors

    def _service(self, c):
        # Read whatever is buffered.
        if not c.tx_buf:
            try:
                chunk = c.sock.recv(4096)
            except socket.error as exc:
                err = exc.args[0] if exc.args else None
                if err in (errno.EAGAIN, errno.EWOULDBLOCK):
                    chunk = None
                else:
                    _log("recv error: %r" % (exc,))
                    self._close_client(c)
                    return
            else:
                if chunk == b"":
                    self._close_client(c)
                    return
            if chunk:
                c.rx_buf += chunk
                if len(c.rx_buf) > MAX_LINE_BYTES:
                    self._reply(c, {"id": None, "ok": False, "error": "request too large"})
                    return
                nl = c.rx_buf.find(b"\n")
                if nl >= 0:
                    line = c.rx_buf[:nl]
                    c.rx_buf = c.rx_buf[nl + 1:]
                    self._handle_line(c, line)

        # Drain any pending writes.
        if c.tx_buf:
            try:
                sent = c.sock.send(c.tx_buf)
            except socket.error as exc:
                err = exc.args[0] if exc.args else None
                if err in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                _log("send error: %r" % (exc,))
                self._close_client(c)
                return
            c.tx_buf = c.tx_buf[sent:]
            if not c.tx_buf:
                # Single-shot: response written, close the connection.
                self._close_client(c)

    def _close_client(self, c):
        if c.closed:
            return
        c.closed = True
        try:
            c.sock.close()
        except Exception:
            pass

    def _reply(self, c, obj):
        try:
            data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            data = json.dumps(
                {"id": obj.get("id"), "ok": False, "error": "non-serialisable result: %r" % (exc,)},
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
        c.tx_buf += data

    def _handle_line(self, c, line):
        try:
            req = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._reply(c, {"id": None, "ok": False, "error": "malformed JSON: %r" % (exc,)})
            return
        if not isinstance(req, dict):
            self._reply(c, {"id": None, "ok": False, "error": "request must be a JSON object"})
            return
        req_id = req.get("id")
        op = req.get("op")
        args = req.get("args") or {}
        if not isinstance(op, str):
            self._reply(c, {"id": req_id, "ok": False, "error": "missing string field 'op'"})
            return
        if not isinstance(args, dict):
            self._reply(c, {"id": req_id, "ok": False, "error": "field 'args' must be an object"})
            return
        handler = self._handlers.get(op)
        if handler is None:
            self._reply(c, {"id": req_id, "ok": False, "error": "unknown op: %s" % op})
            return
        try:
            result = handler(self._c_instance, **args)
            self._reply(c, {"id": req_id, "ok": True, "result": result})
        except TypeError as exc:
            self._reply(c, {"id": req_id, "ok": False, "error": "bad args for %s: %r" % (op, exc)})
        except Exception as exc:
            tb = traceback.format_exc()
            _log("op %r failed: %s" % (op, tb))
            self._reply(c, {"id": req_id, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})

    def _build_handler_table(self):
        table = {
            "system.ping": _system_ping,
            "system.version": self._system_version,
            "system.reload": self._system_reload,
        }
        for mod, prefix in (
            (browser_handlers, "browser."),
            (track_handlers, "track."),
            (clip_handlers, "clip."),
            (project_handlers, "project."),
        ):
            for name in getattr(mod, "EXPORTS"):
                table[prefix + name] = getattr(mod, name)
        return table

    def _system_version(self, c_instance, **_):
        """Return Live + bridge versions + the live handler list.

        Method form so the handler table is in scope. See the module-level
        ``_system_version`` doc for protocol intent.
        """
        return _system_version(c_instance, _handler_table=self._handlers)

    def _system_reload(self, c_instance, **_):
        """Reload handler modules from disk and rebuild the dispatch table.

        This lets us pick up edits to `handlers/*.py` without restarting
        Live or toggling the Control Surface. The bridge_server module
        itself is NOT reloaded (you would still need a Live restart for
        edits to this file).
        """
        try:
            import importlib
        except ImportError as exc:  # pragma: no cover — stdlib always present
            return {"ok": False, "error": "importlib unavailable: %r" % (exc,)}
        reloaded = []
        errors = {}
        for mod in (browser_handlers, track_handlers, clip_handlers, project_handlers):
            try:
                importlib.reload(mod)
                reloaded.append(mod.__name__)
            except Exception as exc:
                errors[mod.__name__] = repr(exc)
        # Rebuild table so newly-added handlers in EXPORTS become reachable.
        self._handlers = self._build_handler_table()
        return {"ok": True, "reloaded": reloaded, "errors": errors,
                "handler_count": len(self._handlers)}


def _system_ping(c_instance, **_):
    return {"ok": True, "service": "AbletonFullControlBridge"}


# Semver for the bridge wire protocol + handler surface.
# Bump MINOR when adding handlers (backwards-compatible additions).
# Bump MAJOR when removing/renaming handlers (breaking changes).
# Bump PATCH for behaviour fixes that don't change the surface.
#
# History:
#   1.0.0 — initial public version (project.save/info, track group/ungroup/
#           freeze/flatten/delete_device/list_devices, clip arrangement ops,
#           browser ops)
#   1.1.0 — added track.is_frozen, track.unfreeze, project.get_file_path,
#           project.list_freezing_dir (PR #10, freeze-mode stem bouncing)
#   1.2.0 — added session-clip handlers: clip.get_session_pitch_state,
#           set_session_warp, set_session_warp_mode, set_session_pitch,
#           get_session_notes, set_session_notes, list_session_clip_slots
#           (session-view clip transposition)
#   1.3.0 — clip.reverse now implements MIDI clip reverse via note
#           manipulation (was audio-only-with-no-LOM-method previously,
#           so it always returned supported=false). Audio clip reverse
#           still returns supported=false (no LOM method exists).
#   1.4.0 — arrangement editing: clip.create_arrangement_midi_clip
#           (Track.create_midi_clip wrapper) and clip.move_arrangement_clip
#           (Clip.move wrapper). Enables "insert MIDI clip at bar N" and
#           "move clip to bar M" workflows without manual UI drag.
BRIDGE_VERSION = "1.4.0"


def _system_version(c_instance, _handler_table=None, **_):
    """Return both Live's version AND the bridge's own version + handler list.

    The bridge version + handler list let the Python client detect when the
    user has upgraded the Python package but forgotten to reinstall the
    Live Remote Script — without that signal, calls to new handlers fail
    with cryptic "unknown op" errors. With it, the client can warn at
    startup with an actionable "reinstall the bridge" message.
    """
    out = {
        "bridge_version": BRIDGE_VERSION,
        "handlers": sorted(_handler_table.keys()) if _handler_table else [],
    }
    try:
        import Live  # type: ignore  # only available inside Live
        app = Live.Application.get_application()
        out["live_version"] = "%d.%d.%d" % (
            app.get_major_version(),
            app.get_minor_version(),
            app.get_bugfix_version(),
        )
    except Exception as exc:
        out["live_version"] = None
        out["live_version_error"] = repr(exc)
    return out
