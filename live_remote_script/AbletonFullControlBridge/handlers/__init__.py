"""Handler modules for AbletonFullControlBridge.

Each module exports an `EXPORTS` tuple listing the handler function names that
should be registered. Handler signatures are `fn(c_instance, **kwargs) -> Any`.
The return value must be JSON-serialisable.
"""
