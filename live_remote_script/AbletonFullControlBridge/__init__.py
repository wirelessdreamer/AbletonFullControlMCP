"""AbletonFullControlBridge — Live Remote Script entry point.

Live calls `create_instance(c_instance)` once when the user picks this control
surface in Preferences → Link/Tempo/MIDI → Control Surface. The returned object
must implement `disconnect()` and `update_display()` (called ~60×/s on the main
thread). We use `update_display` as our event-loop heartbeat so the LOM is only
ever touched from Live's own thread.

This script intentionally does NOT use any Python 3-only syntax beyond what
Live's bundled CPython supports (Live 11 ships Python 3.7-ish for control
surfaces, Live 12 ships Python 3.11). The handlers stick to a conservative
subset.
"""

from __future__ import absolute_import

from .bridge_server import BridgeServer


def create_instance(c_instance):
    """Live entry point. `c_instance` is a Live control-surface handle."""
    return BridgeServer(c_instance)
