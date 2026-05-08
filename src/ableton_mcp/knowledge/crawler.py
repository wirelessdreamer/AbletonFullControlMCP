"""Polite crawler for the Ableton Live 11 manual + the Ableton Cookbook.

Saves cleaned markdown into `data/knowledge/raw/`, one file per page (filename
derived from the URL slug). Each file starts with a YAML-ish front-matter so
the indexer can recover `source_url` and `chapter`.

Design notes:
- We deliberately avoid third-party HTML libs (no bs4, no markdownify) so this
  ships with stock dependencies. The HTML→markdown converter below is small
  but adequate for Ableton's static manual/cookbook templates.
- Politeness: 1 request/second, robots.txt respected, simple URL cache.
- 404s and other non-2xx responses are skipped, not raised.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "AbletonMCP-knowledge-crawler/0.1 (+https://github.com/AbletonMCP)"

MANUAL_ROOT = "https://www.ableton.com/en/manual/welcome-to-live/"
COOKBOOK_ROOT = "https://www.ableton.com/en/cookbook/"
ALLOWED_HOST = "www.ableton.com"

# Tags whose content we drop wholesale. Header/nav/footer hold no manual prose.
_STRIP_TAGS = {
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "noscript",
    "form",
    "svg",
    "iframe",
}
# Tags that map to markdown markers.
_HEADING_TAGS = {f"h{i}" for i in range(1, 7)}


# ---------------------------------------------------------------------------
# HTML -> Markdown (minimal, dependency-free)
# ---------------------------------------------------------------------------


class _MarkdownExtractor(HTMLParser):
    """Pull a 'main content' subtree out of a page and emit markdown.

    Heuristic: prefer <main>, then <article>, then the largest <div class*="content">,
    then fall back to <body>. Drops nav/header/footer/script/style entirely.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth_stack: list[str] = []
        self._strip_depth = 0  # >0 means we're inside a stripped tag
        self._capture = False
        self._capture_depth: int | None = None
        self._buf: list[str] = []
        self._inline_buf: list[str] = []
        self._title: str | None = None
        self._in_title = False
        self._list_stack: list[str] = []  # 'ul' or 'ol' frames

    # public output --------------------------------------------------------

    @property
    def title(self) -> str | None:
        return self._title

    def text(self) -> str:
        self._flush_inline()
        # Collapse 3+ blank lines and trim.
        out = "\n".join(self._buf)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip() + "\n"

    # internal helpers -----------------------------------------------------

    def _flush_inline(self) -> None:
        if not self._inline_buf:
            return
        line = "".join(self._inline_buf)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            self._buf.append(line)
        self._inline_buf = []

    def _emit_block(self, line: str = "") -> None:
        self._flush_inline()
        self._buf.append(line)

    # parser callbacks -----------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth_stack.append(tag)

        if tag in _STRIP_TAGS:
            self._strip_depth += 1
            return
        if self._strip_depth:
            return

        if tag == "title":
            self._in_title = True
            return

        # Open the capture region the first time we see a likely main-content container.
        if not self._capture and tag in {"main", "article"}:
            self._capture = True
            self._capture_depth = len(self._depth_stack)
        elif not self._capture and tag == "div":
            attr_dict = {k: (v or "") for k, v in attrs}
            cls = attr_dict.get("class", "")
            if "content" in cls or "manual" in cls or "cookbook" in cls:
                self._capture = True
                self._capture_depth = len(self._depth_stack)

        if not self._capture:
            return

        if tag in _HEADING_TAGS:
            self._emit_block()
            level = int(tag[1])
            self._inline_buf.append("#" * level + " ")
        elif tag == "p":
            self._emit_block()
        elif tag == "br":
            self._inline_buf.append("\n")
        elif tag in {"strong", "b"}:
            self._inline_buf.append("**")
        elif tag in {"em", "i"}:
            self._inline_buf.append("*")
        elif tag == "code":
            self._inline_buf.append("`")
        elif tag == "pre":
            self._emit_block("```")
        elif tag == "ul":
            self._emit_block()
            self._list_stack.append("ul")
        elif tag == "ol":
            self._emit_block()
            self._list_stack.append("ol")
        elif tag == "li":
            self._flush_inline()
            marker = "- " if (not self._list_stack or self._list_stack[-1] == "ul") else "1. "
            self._inline_buf.append(marker)
        elif tag == "blockquote":
            self._emit_block()
            self._inline_buf.append("> ")
        elif tag == "a":
            attr_dict = {k: (v or "") for k, v in attrs}
            href = attr_dict.get("href", "")
            self._inline_buf.append("[")
            self._link_href = href  # type: ignore[attr-defined]
        elif tag == "img":
            attr_dict = {k: (v or "") for k, v in attrs}
            alt = attr_dict.get("alt", "").strip()
            if alt:
                self._inline_buf.append(f"[image: {alt}]")

    def handle_endtag(self, tag: str) -> None:
        # Pop matching frame from the depth stack (handle malformed nesting gracefully).
        for i in range(len(self._depth_stack) - 1, -1, -1):
            if self._depth_stack[i] == tag:
                del self._depth_stack[i:]
                break

        if tag in _STRIP_TAGS:
            self._strip_depth = max(0, self._strip_depth - 1)
            return
        if self._strip_depth:
            return

        if tag == "title":
            self._in_title = False
            return

        if not self._capture:
            return

        if tag in _HEADING_TAGS:
            self._emit_block()
            self._emit_block()  # blank line after heading
        elif tag == "p":
            self._emit_block()
            self._emit_block()
        elif tag in {"strong", "b"}:
            self._inline_buf.append("**")
        elif tag in {"em", "i"}:
            self._inline_buf.append("*")
        elif tag == "code":
            self._inline_buf.append("`")
        elif tag == "pre":
            self._emit_block("```")
            self._emit_block()
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._emit_block()
        elif tag == "li":
            self._flush_inline()
        elif tag == "blockquote":
            self._emit_block()
        elif tag == "a":
            href = getattr(self, "_link_href", "")
            self._inline_buf.append(f"]({href})" if href else "]")
            self._link_href = ""  # type: ignore[attr-defined]

        # Close capture when we leave the capture root.
        if self._capture_depth is not None and len(self._depth_stack) < self._capture_depth:
            self._capture = False
            self._capture_depth = None

    def handle_data(self, data: str) -> None:
        if self._in_title and self._title is None:
            txt = data.strip()
            if txt:
                self._title = txt
        if self._strip_depth or not self._capture:
            return
        self._inline_buf.append(data)


def html_to_markdown(html: str) -> tuple[str, str | None]:
    """Convert an Ableton manual/cookbook HTML page to (markdown, page_title)."""
    p = _MarkdownExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception as e:
        log.warning("HTML parse failed: %s", e)
    return p.text(), p.title


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------


class _LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for k, v in attrs:
            if k == "href" and v:
                href = urljoin(self.base_url, v)
                href, _ = urldefrag(href)
                self.links.append(href)


def _discover_links(html: str, base_url: str) -> list[str]:
    p = _LinkExtractor(base_url)
    try:
        p.feed(html)
    except Exception:
        pass
    return p.links


# ---------------------------------------------------------------------------
# URL filters
# ---------------------------------------------------------------------------


def _slug_from_url(url: str) -> str:
    """Make a filesystem-safe filename from a URL path."""
    path = urlparse(url).path.strip("/")
    if not path:
        path = "index"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", path).strip("-")
    return slug[:200] or "index"


def _allowed_url(url: str, source: str) -> bool:
    """Filter to URLs we actually want to crawl."""
    pu = urlparse(url)
    if pu.netloc != ALLOWED_HOST:
        return False
    if pu.scheme not in {"http", "https"}:
        return False
    if pu.fragment:
        return False
    path = pu.path
    # Drop binaries / asset pages.
    if re.search(r"\.(jpg|jpeg|png|gif|pdf|zip|mp4|mp3|webp|svg|ico|css|js)$", path, re.I):
        return False
    if source == "manual":
        return path.startswith("/en/manual/")
    if source == "cookbook":
        return path.startswith("/en/cookbook/")
    return False


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrawlResult:
    url: str
    chapter: str
    path: Path
    bytes_written: int


class Crawler:
    """Polite crawler.

    Args:
        out_dir: where to write markdown files (e.g. data/knowledge/raw)
        delay_sec: per-request sleep (default 1.0)
        timeout_sec: per-request timeout
        respect_robots: whether to consult /robots.txt
    """

    def __init__(
        self,
        out_dir: Path,
        delay_sec: float = 1.0,
        timeout_sec: float = 20.0,
        respect_robots: bool = True,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.delay_sec = delay_sec
        self.timeout_sec = timeout_sec
        self.respect_robots = respect_robots
        self._client = httpx.Client(
            timeout=timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._robot: robotparser.RobotFileParser | None = None
        self._last_fetch = 0.0

    # context manager ------------------------------------------------------

    def __enter__(self) -> "Crawler":
        if self.respect_robots:
            self._init_robots()
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    # internals ------------------------------------------------------------

    def _init_robots(self) -> None:
        rp = robotparser.RobotFileParser()
        try:
            r = self._client.get(f"https://{ALLOWED_HOST}/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])
        except Exception as e:
            log.warning("robots.txt fetch failed (%s); proceeding without it.", e)
            rp.parse([])
        self._robot = rp

    def _allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots or self._robot is None:
            return True
        try:
            return self._robot.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._last_fetch + self.delay_sec - now
        if wait > 0:
            time.sleep(wait)
        self._last_fetch = time.monotonic()

    def _fetch(self, url: str) -> str | None:
        if not self._allowed_by_robots(url):
            log.info("robots disallows %s; skipping.", url)
            return None
        self._throttle()
        try:
            r = self._client.get(url)
        except httpx.HTTPError as e:
            log.warning("fetch failed for %s: %s", url, e)
            return None
        if r.status_code == 404:
            log.info("404 %s", url)
            return None
        if r.status_code >= 400:
            log.warning("HTTP %s for %s", r.status_code, url)
            return None
        return r.text

    # public api -----------------------------------------------------------

    def crawl(
        self,
        source: str,
        max_pages: int | None = None,
    ) -> Iterator[CrawlResult]:
        """BFS-crawl the manual or cookbook, writing markdown as we go."""
        if source == "manual":
            seeds = [MANUAL_ROOT]
        elif source == "cookbook":
            seeds = [COOKBOOK_ROOT]
        else:
            raise ValueError(f"unknown source: {source!r}")

        seen: set[str] = set()
        queue: list[str] = list(seeds)
        n_written = 0

        while queue:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)

            html = self._fetch(url)
            if html is None:
                continue

            md, title = html_to_markdown(html)
            chapter = title or _slug_from_url(url)

            # Discover more links from THIS page (HTML, not stripped markdown).
            for link in _discover_links(html, url):
                if link not in seen and _allowed_url(link, source):
                    queue.append(link)

            # Skip pages that produced no real content (e.g. index / nav-only).
            if len(md.strip()) < 200:
                log.debug("skipping low-content page %s (%d chars)", url, len(md.strip()))
                continue

            slug = _slug_from_url(url)
            out_path = self.out_dir / f"{source}__{slug}.md"
            front_matter = (
                f"---\nsource_url: {url}\nchapter: {chapter}\nsource: {source}\n---\n\n"
            )
            payload = front_matter + md
            out_path.write_text(payload, encoding="utf-8")
            n_written += 1
            yield CrawlResult(url=url, chapter=chapter, path=out_path, bytes_written=len(payload))

            if max_pages is not None and n_written >= max_pages:
                log.info("hit max_pages=%d, stopping.", max_pages)
                return


def crawl_sources(
    out_dir: Path,
    sources: Iterable[str],
    max_pages: int | None = None,
    delay_sec: float = 1.0,
) -> list[CrawlResult]:
    """Convenience wrapper used by the CLI script. Returns all results across sources."""
    results: list[CrawlResult] = []
    with Crawler(out_dir=out_dir, delay_sec=delay_sec) as c:
        for src in sources:
            for r in c.crawl(src, max_pages=max_pages):
                results.append(r)
    return results
