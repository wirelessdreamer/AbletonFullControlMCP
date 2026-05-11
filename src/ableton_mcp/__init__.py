"""AbletonMCP — a Model Context Protocol server for Ableton Live 11."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("ableton-full-control-mcp")
except PackageNotFoundError:  # pragma: no cover — running from a tree without install
    __version__ = "0.0.0+unknown"

del _pkg_version, PackageNotFoundError
