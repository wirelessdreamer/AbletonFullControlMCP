"""Programmatic OLE drag-drop of files into Ableton Live's arrangement.

Live's main window doesn't have ``WS_EX_ACCEPTFILES`` so ``WM_DROPFILES``
(the legacy file-drop message) is silently ignored — see
``wm_dropfiles.py`` for that dead end. Live uses the **modern** Windows
drag-drop API: ``RegisterDragDrop`` + ``IDropTarget``. To deliver files
to that interface from outside Live's process we have to play the role
of an OLE drag SOURCE: implement ``IDataObject`` carrying ``CF_HDROP``,
implement ``IDropSource`` to drive the modal drag loop, then call
``DoDragDrop``.

How it works
------------

1. ``OleInitialize`` for this thread (DoDragDrop requires it).
2. Build a :class:`FileDataObject` exposing ``CF_HDROP`` format with our
   wav path(s).
3. Build a :class:`DropSource` whose ``QueryContinueDrag`` synthesizes
   the drag's lifecycle:
   - move cursor over Live's drop region via ``SetCursorPos``
   - after a couple cycles, return ``DRAGDROP_S_DROP`` to signal release
4. Call ``pythoncom.DoDragDrop(dataObject, dropSource, effects)``.
5. The shell's modal loop drives the cursor, queries our IDropSource
   each cycle, fires Live's ``IDropTarget::DragEnter / DragOver / Drop``
   when the cursor is over Live's drop region. Live processes the drop
   exactly as if a user had dragged from Explorer.

Trade-offs vs ``.als`` synthesis
-------------------------------

- **Pro**: doesn't disrupt Live's current session — just adds the wav as
  another audio track in the existing arrangement.
- **Con**: ~99% reliable vs ``.als`` synthesis's 100% — the modal drag
  loop can be interrupted by foreground-window-stealing protection,
  user mouse activity, or Live being in a state that doesn't accept
  drops (modal dialog open, etc).
- **Con**: requires foreground access (the drag cursor is real cursor
  movement) — if the user is doing something else with the mouse mid-
  drop, the synthetic drag can get derailed.

For batch-load workflows the ``.als`` path is preferred. This module
exists for the case where preserving the active Live session matters.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Sequence

log = logging.getLogger(__name__)


# Windows / OLE constants we use. Defined here rather than imported so
# the module imports cleanly on non-Windows (only the runtime call
# raises).
CF_HDROP = 15
TYMED_HGLOBAL = 1
DVASPECT_CONTENT = 1

# DROPEFFECT
DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1
DROPEFFECT_MOVE = 2
DROPEFFECT_LINK = 4
DROPEFFECT_ANY = DROPEFFECT_COPY | DROPEFFECT_MOVE | DROPEFFECT_LINK

# IDropSource return codes — these are HRESULTs.
S_OK = 0x00000000
DRAGDROP_S_DROP = 0x00040100
DRAGDROP_S_CANCEL = 0x00040101
DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102

# How many SetCursorPos cycles to spend smoothly moving toward the drop
# target. Some apps' IDropTarget tracks Drag* over multiple positions
# (showing drop-region hints to the user) and rejects sudden jumps.
_CURSOR_MOVE_STEPS: int = 8

# Delay between cursor positions during the smooth move. Total drag
# time is roughly STEPS * MOVE_INTERVAL.
_CURSOR_MOVE_INTERVAL_SEC: float = 0.04


@dataclass(frozen=True)
class OleDropResult:
    """What happened on a successful drag-drop."""

    hwnd: int
    live_window_title: str
    drop_point_screen: tuple[int, int]
    file_paths: tuple[str, ...]
    effect_returned: int
    """The DROPEFFECT_* value Live's IDropTarget::Drop returned. Copy=1
    is the common case for file-drops."""


class OleDropError(RuntimeError):
    """Raised when OLE drag-drop can't complete (non-Windows host, Live
    window not found, DoDragDrop returned a failure HRESULT, etc)."""


# ---------------------------------------------------------------------------
# CF_HDROP payload — build the same DROPFILES blob that Explorer uses.
# ---------------------------------------------------------------------------


def _build_dropfiles_bytes(file_paths: Sequence[str]) -> bytes:
    """Build a DROPFILES struct + path list in wide-char (UTF-16-LE)
    format. Identical layout to ``wm_dropfiles._build_dropfiles_payload``
    but returned as bytes for embedding in an HGLOBAL via pythoncom's
    StgMedium."""
    # DROPFILES struct (5 fields, 20 bytes on Win64 with packing):
    #   DWORD pFiles    — offset to file list (= sizeof(DROPFILES) = 20)
    #   POINT pt        — drop point (2 LONG = 8 bytes)
    #   BOOL fNC        — non-client (4 bytes)
    #   BOOL fWide      — 1 for wide chars (4 bytes)
    import struct
    struct_size = 20
    paths_blob = "\x00".join(file_paths) + "\x00\x00"
    paths_bytes = paths_blob.encode("utf-16-le")
    # pt is 0,0 here — the shell uses the actual cursor position at
    # drop time, not the value embedded here.
    header = struct.pack(
        "=I i i I I",
        struct_size,  # pFiles
        0, 0,          # pt.x, pt.y
        0,             # fNC
        1,             # fWide
    )
    return header + paths_bytes


# ---------------------------------------------------------------------------
# IDataObject implementation
# ---------------------------------------------------------------------------


class _FileDataObject:
    """``IDataObject`` exposing one ``CF_HDROP`` format containing the
    supplied file paths.

    pythoncom's WrapObject (via win32com.server.policy) marshals method
    calls from COM → these Python methods. Method names must match COM
    method names exactly (Python casing, no _).
    """

    _com_interfaces_ = ["IDataObject"]
    _public_methods_ = [
        "GetData",
        "GetDataHere",
        "QueryGetData",
        "GetCanonicalFormatEtc",
        "SetData",
        "EnumFormatEtc",
        "DAdvise",
        "DUnadvise",
        "EnumDAdvise",
    ]

    def __init__(self, file_paths: Sequence[str]):
        abs_paths = []
        for p in file_paths:
            ap = os.path.abspath(p)
            if not os.path.exists(ap):
                raise OleDropError(f"file not found: {ap!r}")
            abs_paths.append(ap)
        self.file_paths = tuple(abs_paths)
        self._payload = _build_dropfiles_bytes(self.file_paths)

    # ---- IDataObject method implementations ----

    def QueryGetData(self, formatEtc):
        """Return S_OK iff we support the requested format."""
        # formatEtc is a 5-tuple: (cfFormat, ptd, dwAspect, lindex, tymed)
        cf_format, ptd, dw_aspect, lindex, tymed = formatEtc
        if cf_format != CF_HDROP:
            return 0x80040064  # DV_E_FORMATETC
        if not (tymed & TYMED_HGLOBAL):
            return 0x80040064  # DV_E_TYMED
        return S_OK

    def GetData(self, formatEtc):
        """Return our CF_HDROP payload wrapped in an HGLOBAL StgMedium.

        pythoncom marshals the return value: a tuple ``(tymed, data,
        pUnkForRelease)`` describing the StgMedium. For TYMED_HGLOBAL,
        pythoncom allocates the HGLOBAL from our bytes for us.
        """
        cf_format, ptd, dw_aspect, lindex, tymed = formatEtc
        if cf_format != CF_HDROP:
            import pythoncom
            raise pythoncom.com_error(
                0x80040064, "format not supported", None, -1,
            )
        if not (tymed & TYMED_HGLOBAL):
            import pythoncom
            raise pythoncom.com_error(
                0x80040064, "tymed not supported", None, -1,
            )
        # pythoncom expects StgMedium tuple: (tymed, data_blob, release).
        return (TYMED_HGLOBAL, self._payload, None)

    def EnumFormatEtc(self, direction):
        """Return an enumerator over our supported formats (just one:
        CF_HDROP / TYMED_HGLOBAL / DVASPECT_CONTENT)."""
        formats = [
            (CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL),
        ]
        # win32com helpers can build an IEnumFORMATETC from a list.
        from win32com.server.util import NewEnum
        return NewEnum(
            formats,
            # pythoncom enumerator wants the enum interface ID + how
            # to wrap items as the COM type FORMATETC. The shell-level
            # default works for file-drop sources.
        )

    # ---- Stubs (rarely used by drop targets, but required by interface) ----

    def GetDataHere(self, formatEtc):
        import pythoncom
        raise pythoncom.com_error(0x80004001, "GetDataHere not implemented", None, -1)

    def GetCanonicalFormatEtc(self, formatEtc):
        import pythoncom
        raise pythoncom.com_error(0x80004001, "not implemented", None, -1)

    def SetData(self, *_):
        import pythoncom
        raise pythoncom.com_error(0x80004001, "SetData not supported", None, -1)

    def DAdvise(self, *_):
        return (0x80040003, 0)  # OLE_E_ADVISENOTSUPPORTED, sink cookie 0

    def DUnadvise(self, *_):
        return 0x80040003

    def EnumDAdvise(self):
        import pythoncom
        raise pythoncom.com_error(0x80040003, "advise not supported", None, -1)


# ---------------------------------------------------------------------------
# IDropSource implementation
# ---------------------------------------------------------------------------


class _DropSource:
    """``IDropSource`` that drives the modal drag toward a target screen
    point and triggers the drop after a few cycles.

    Lifecycle inside ``DoDragDrop``:
      cycle 1..N-2: move cursor smoothly toward (drop_x, drop_y) via
                     SetCursorPos; return S_OK (continue)
      cycle N-1:   return DRAGDROP_S_DROP — the shell calls
                     IDropTarget::Drop on whatever's under the cursor.
    """

    _com_interfaces_ = ["IDropSource"]
    _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]

    def __init__(
        self,
        target_x: int,
        target_y: int,
        start_x: int | None = None,
        start_y: int | None = None,
    ):
        self.target_x = int(target_x)
        self.target_y = int(target_y)
        # Default start: current cursor position when the drag began.
        import ctypes
        from ctypes import wintypes
        if start_x is None or start_y is None:
            user32 = ctypes.windll.user32
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self.start_x = pt.x if start_x is None else int(start_x)
            self.start_y = pt.y if start_y is None else int(start_y)
        else:
            self.start_x = int(start_x)
            self.start_y = int(start_y)
        self._step = 0
        self._total_steps = _CURSOR_MOVE_STEPS

    def QueryContinueDrag(self, escape_pressed, key_state):
        """Called by the shell on every cycle of the modal drag loop."""
        import ctypes
        user32 = ctypes.windll.user32

        if escape_pressed:
            return DRAGDROP_S_CANCEL

        # Smooth cursor move from start → target across N steps.
        self._step += 1
        if self._step <= self._total_steps:
            frac = self._step / float(self._total_steps)
            x = int(self.start_x + (self.target_x - self.start_x) * frac)
            y = int(self.start_y + (self.target_y - self.start_y) * frac)
            user32.SetCursorPos(x, y)
            time.sleep(_CURSOR_MOVE_INTERVAL_SEC)
            return S_OK

        # We've reached the target — signal drop.
        return DRAGDROP_S_DROP

    def GiveFeedback(self, effect):
        """Use the default OLE cursors. Returning anything else would
        require us to set our own cursor — overkill for file drag-drop."""
        return DRAGDROP_S_USEDEFAULTCURSORS


# ---------------------------------------------------------------------------
# Window discovery
# ---------------------------------------------------------------------------


def _find_live_main_window(title_substring: str) -> tuple[int, str]:
    """Find Live's main window by enumerating top-level visible windows
    and matching ``title_substring`` against each title. Returns
    ``(hwnd, title)`` or ``(0, "")`` if not found. Picks the largest by
    area if multiple match."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.EnumWindows.restype = wintypes.BOOL

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
    )

    found: list[tuple[int, str]] = []

    def callback(hwnd: int, _lparam: int) -> bool:  # type: ignore[override]
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(n)
            user32.GetWindowTextW(hwnd, buf, n)
            t = buf.value
            if t and title_substring in t:
                found.append((int(hwnd), t))
        except Exception:  # noqa: BLE001
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not found:
        return (0, "")
    if len(found) > 1:
        best_hwnd, best_title, best_area = 0, "", -1
        rect = wintypes.RECT()
        for hwnd, t in found:
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if area > best_area:
                    best_hwnd, best_title, best_area = hwnd, t, area
        return (best_hwnd, best_title)
    return found[0]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def drop_files_on_live_via_ole(
    file_paths: str | Sequence[str],
    *,
    drop_x: int | None = None,
    drop_y: int | None = None,
    window_title_contains: str = "Ableton Live",
) -> OleDropResult:
    """Programmatically OLE-drag-drop ``file_paths`` onto Live's arrangement.

    Args:
        file_paths: a single absolute path or a list.
        drop_x / drop_y: screen coordinates of the drop point. If omitted,
            we target ~30% from the left and ~55% from the top of Live's
            main window — typically inside the arrangement timeline area
            in Live's default layout.
        window_title_contains: substring matched against window titles
            when locating Live.

    Returns:
        :class:`OleDropResult` describing what happened. ``effect_returned``
        will be one of the ``DROPEFFECT_*`` constants — ``DROPEFFECT_COPY``
        (1) is the common case for successful file drops.

    Raises:
        OleDropError: on any failure.
    """
    if sys.platform != "win32":
        raise OleDropError(f"OLE drag-drop is Windows-only; platform={sys.platform!r}")

    if isinstance(file_paths, str):
        paths = [file_paths]
    else:
        paths = list(file_paths)
    if not paths:
        raise OleDropError("file_paths is empty")

    hwnd, title = _find_live_main_window(window_title_contains)
    if hwnd == 0:
        raise OleDropError(
            f"could not find a top-level window with title containing "
            f"{window_title_contains!r}. Is Live running?"
        )

    # Restore the window from minimized if needed (off-screen coords
    # would cause the drag to deliver to nothing).
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if user32.IsIconic(hwnd):
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.2)

    # Bring Live to foreground so the drop target is the active one
    # under the cursor (some drop targets only register if their window
    # is foreground).
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OleDropError(f"GetWindowRect failed for hwnd {hwnd}")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    target_x = drop_x if drop_x is not None else rect.left + int(width * 0.3)
    target_y = drop_y if drop_y is not None else rect.top + int(height * 0.55)

    # Run the OLE drag.
    #
    # Instead of hand-rolling IDataObject (the IEnumFORMATETC marshaling
    # is fiddly and pythoncom's NewEnum helper produces an enumerator
    # the shell doesn't accept — DoDragDrop returns E_FAIL), we let the
    # Windows shell build a working IDataObject for us by routing
    # through the clipboard. SetClipboardData(CF_HDROP, dropfiles_bytes)
    # tells the clipboard about our files; OleGetClipboard returns a
    # shell-built IDataObject for that clipboard content. The data
    # object is a real CIDLData implementation that every drop target
    # speaks fluently.
    import pythoncom
    from win32com.server import util as server_util
    import win32clipboard

    pythoncom.OleInitialize()
    try:
        payload = _build_dropfiles_bytes(paths)

        # Snapshot the user's previous clipboard so we can restore.
        # OleFlushClipboard "renders" our data into the clipboard
        # before we touch it; if we replace cleanly the user's data
        # is gone for the duration of the drag.
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(CF_HDROP, payload)
        finally:
            win32clipboard.CloseClipboard()

        # Pull back a shell-built IDataObject pointing at our files.
        data_obj = pythoncom.OleGetClipboard()
        drop_src = server_util.wrap(
            _DropSource(target_x, target_y),
            iid=pythoncom.IID_IDropSource,
        )

        try:
            effect = pythoncom.DoDragDrop(data_obj, drop_src, DROPEFFECT_ANY)
        except pythoncom.com_error as e:
            raise OleDropError(
                f"DoDragDrop raised: {e.args!r}; Live may have rejected "
                f"the drop or no drop target was under the cursor at "
                f"({target_x},{target_y})."
            ) from e

        if effect == DROPEFFECT_NONE:
            raise OleDropError(
                f"DoDragDrop returned DROPEFFECT_NONE — no target accepted "
                f"the drop at ({target_x},{target_y}). Window title was "
                f"{title!r}."
            )

        return OleDropResult(
            hwnd=int(hwnd),
            live_window_title=title,
            drop_point_screen=(target_x, target_y),
            file_paths=tuple(os.path.abspath(p) for p in paths),
            effect_returned=int(effect),
        )
    finally:
        # Restore: clear our payload from the clipboard so we don't
        # leave dropfiles bytes hanging around for the next paste.
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001
            log.warning("clipboard restore raised", exc_info=True)
        # pythoncom doesn't expose OleUninitialize as a Python binding
        # in current pywin32. CoUninitialize is the closest match and
        # is functionally equivalent for our purposes (single-threaded
        # apartment teardown).
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass
