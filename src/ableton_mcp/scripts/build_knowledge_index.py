"""Build the Ableton manual + Cookbook knowledge index.

Run from the repo root:

    # smoke-test crawl (5 pages each, fast):
    python -m ableton_mcp.scripts.build_knowledge_index --source both --max-pages 5

    # full rebuild:
    python -m ableton_mcp.scripts.build_knowledge_index --source both --rebuild

    # only re-index existing markdown (skip crawl):
    python -m ableton_mcp.scripts.build_knowledge_index --skip-crawl --rebuild

The crawler is polite: 1 req/sec, robots.txt respected, 404s skipped.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from ..knowledge.crawler import crawl_sources
from ..knowledge.indexer import build_index, pick_backend
from ..knowledge.search import default_index_dir


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Crawl Ableton's manual + Cookbook into local markdown and build the search index."
    )
    ap.add_argument(
        "--source",
        choices=["manual", "cookbook", "both"],
        default="both",
        help="which corpus to crawl",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="cap pages PER source (useful for smoke-testing — try 5)",
    )
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="discard the existing sqlite index before re-indexing",
    )
    ap.add_argument(
        "--skip-crawl",
        action="store_true",
        help="reuse existing markdown in data/knowledge/raw/ instead of fetching",
    )
    ap.add_argument(
        "--knowledge-dir",
        type=Path,
        default=None,
        help="override the data/knowledge directory (defaults to repo data/knowledge)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="seconds between HTTP requests (default 1.0; be polite!)",
    )
    ap.add_argument(
        "--backend",
        choices=["auto", "st", "tfidf"],
        default="auto",
        help="embedding backend; auto = sentence-transformers if installed else TF-IDF",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("build_knowledge_index")

    knowledge_dir = args.knowledge_dir or default_index_dir()
    raw_dir = knowledge_dir / "raw"
    db_path = knowledge_dir / "index.sqlite"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log.info("knowledge dir: %s", knowledge_dir)

    sources = ["manual", "cookbook"] if args.source == "both" else [args.source]

    if not args.skip_crawl:
        log.info("crawling sources=%s (max_pages=%s, delay=%.1fs)", sources, args.max_pages, args.delay)
        t0 = time.monotonic()
        results = crawl_sources(
            out_dir=raw_dir,
            sources=sources,
            max_pages=args.max_pages,
            delay_sec=args.delay,
        )
        dt = time.monotonic() - t0
        log.info("crawl done: %d pages in %.1fs", len(results), dt)
        if not results:
            log.error("crawl produced no pages — aborting before index build.")
            return 2
    else:
        log.info("skipping crawl; reusing %s", raw_dir)
        if not any(raw_dir.glob("*.md")):
            log.error("no markdown files in %s — nothing to index", raw_dir)
            return 2

    log.info("building index (backend=%s) ...", args.backend)

    def _progress(done: int, total: int) -> None:
        sys.stderr.write(f"\r  embedded {done}/{total} chunks")
        sys.stderr.flush()
        if done == total:
            sys.stderr.write("\n")

    backend = pick_backend(args.backend)
    summary = build_index(
        raw_dir=raw_dir,
        db_path=db_path,
        backend=backend,
        rebuild=args.rebuild,
        progress_cb=_progress,
    )
    log.info("index built: %s", summary)
    print(f"OK: {summary['chunks']} chunks ({summary['backend']}) at {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
