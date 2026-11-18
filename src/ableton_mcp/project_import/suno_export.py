"""Stage actual file exports supplied by an authenticated Suno MCP host."""

from __future__ import annotations

import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, Field

from .assets import AUDIO, MIDI, ImportError, safe_name

MAX_EXPORT_FILES = 64
MAX_EXPORT_BYTES = 2 * 1024**3
EXPORT_TIMEOUT_SEC = 600


class ExportFile(BaseModel):
    filename: str = Field(description="Export filename including WAV, MP3, MIDI or ZIP extension")
    url: str = Field(description="Direct HTTPS file link returned by Suno MCP, not a song/share URL")


def _check_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        valid = (parts.scheme == "https" and parts.username is None
                 and parts.password is None and parts.port in (None, 443) and not parts.fragment
                 and host.endswith((".suno.com", ".suno.ai")))
    except ValueError:
        valid = False
    if not valid or len(url) > 16384 or "\\" in url or any(ord(c) <= 32 for c in url):
        raise ImportError("Expected a direct HTTPS Suno file link on a Suno-owned host")


class _SunoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def stage_export(files: list[ExportFile], directory: Path) -> list[str]:
    """Download only validated Suno assets; return names without persisting URLs."""
    if not 1 <= len(files) <= MAX_EXPORT_FILES:
        raise ImportError(f"Provide between 1 and {MAX_EXPORT_FILES} Suno export files")
    names: set[str] = set()
    for entry in files:
        name = entry.filename
        if name != safe_name(name) or Path(name).suffix.lower() not in AUDIO | MIDI | {".zip"}:
            raise ImportError("Use a simple export filename ending in a supported audio, MIDI or ZIP extension")
        if name.casefold() in names:
            raise ImportError("Suno export filenames must be unique (case insensitive)")
        names.add(name.casefold())
        _check_url(entry.url)

    # Keep OAuth and ambient proxy/cookie credentials out of the file fetch.
    opener = build_opener(ProxyHandler({}), _SunoRedirect())
    remaining = MAX_EXPORT_BYTES
    deadline = time.monotonic() + EXPORT_TIMEOUT_SEC
    for entry in files:
        path = directory / entry.filename
        if time.monotonic() >= deadline:
            raise ImportError("Suno export download timed out")
        try:
            with opener.open(Request(entry.url), timeout=20) as response:
                if (response.status != 200 or response.headers.get_content_type().startswith("text/")
                        or response.headers.get_content_type() == "application/json"):
                    raise ImportError(f"Suno did not return a file for {entry.filename}")
                declared = response.headers.get("Content-Length")
                if declared is not None and not 0 < int(declared) <= remaining:
                    raise ImportError("Suno export exceeds the 2 GiB download limit or contains an empty file")
                size = 0
                with path.open("xb") as stream:
                    while block := response.read1(1024 * 1024):
                        remaining -= len(block)
                        size += len(block)
                        if remaining < 0 or time.monotonic() > deadline:
                            raise ImportError("Suno export exceeded the download size or time limit")
                        stream.write(block)
                if not size or (declared is not None and size != int(declared)):
                    raise ImportError(f"Suno returned an empty or incomplete file for {entry.filename}")
        except HTTPError as exc:
            raise ImportError(f"Suno download returned HTTP {exc.code} for {entry.filename}; refresh the export link") from None
        except ImportError:
            raise
        except (URLError, OSError, ValueError, HTTPException) as exc:
            # Underlying exceptions can contain private signed URLs. Do not persist or return them.
            raise ImportError(f"Could not download {entry.filename} from Suno ({type(exc).__name__})") from None
    return [entry.filename for entry in files]
