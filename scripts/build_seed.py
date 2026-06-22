"""Build the shipped baseline seed (``src/android_watcher/seed/seed.sql.gz``).

Crawls every enabled catalog source live and records a full-content baseline
(snapshots with real content hashes, the feed seen-set, and HTTP validators),
then dumps it to a gzipped, date-tagged SQL file bundled into the package. Users
who install the package import this seed on first run, so the first scheduled run
diffs against it instead of crawling every page to establish a baseline.

This is the expensive crawl — done once here so nobody downstream pays it. It is
polite (the Fetcher's per-host crawl delay + robots handling apply) and therefore
slow; expect it to run for a while. It is resumable: ``baseline_all`` skips URLs
already baselined, so re-running continues an interrupted crawl. To resume across
invocations, point --db at a persistent path instead of the default temp file.

Usage:
    uv run python scripts/build_seed.py            # crawl all, write the seed
    uv run python scripts/build_seed.py --db seed-wip.db   # resumable crawl

DO NOT import this module from tests or production code. It hits live network.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import logging
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from android_watcher import __version__
from android_watcher.catalog import load_catalog
from android_watcher.detect.android_sitemap import baseline_all
from android_watcher.detect.base import DETECTORS
from android_watcher.fetch import USER_AGENT, Fetcher
from android_watcher.store import Store

SEED_PATH = Path(__file__).resolve().parents[1] / "src/android_watcher/seed/seed.sql.gz"


async def _crawl(store: Store, *, concurrency: int, delay: float) -> None:
	# A controlled one-off maintainer crawl: faster than the daily runtime, but
	# still throttled by the per-host crawl delay (and by robots.txt, which the
	# Fetcher honors over `delay` when the host declares its own crawl-delay).
	fetcher = Fetcher(
		store,
		user_agent=USER_AGENT.format(version=__version__),
		concurrency=concurrency,
		crawl_delay=delay,
	)
	try:
		for s in load_catalog():
			if not s.enabled:
				continue
			print(f"baselining {s.id} ({s.detector})…", flush=True)
			# Isolate per-source failures (mirrors the live pipeline): a source
			# blocked by robots.txt or otherwise unreachable must not abort the
			# whole crawl and lose every other source's work before export.
			try:
				if s.detector == "android_sitemap":
					# Full-content baseline; detect() is fetch-free on first sight.
					n = await baseline_all(s, store, fetcher)
					print(f"  {s.id}: {n} URL(s) baselined", flush=True)
				else:
					# feed/content/sitemap detectors baseline with content via detect().
					await DETECTORS.get(s.detector)().detect(s, store, fetcher)
			except Exception as exc:  # noqa: BLE001 - one bad source must not abort the crawl
				print(f"  {s.id}: SKIPPED ({type(exc).__name__}: {exc})", flush=True)
	finally:
		await fetcher.close()


async def _run(db_path: str, out_path: Path, *, concurrency: int, delay: float) -> int:
	store = Store(db_path)
	store.migrate()
	try:
		await _crawl(store, concurrency=concurrency, delay=delay)
		seed_date = datetime.now(UTC).date().isoformat()
		sql = store.export_seed_sql(seed_date)
		count = store.snapshot_count()
	finally:
		store.close()

	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_path.write_bytes(gzip.compress(sql.encode()))
	print(
		f"\nwrote {out_path} ({out_path.stat().st_size} bytes, "
		f"{count} snapshots, dated {seed_date})"
	)
	return 0


def main() -> int:
	parser = argparse.ArgumentParser(description="Build the shipped baseline seed.")
	parser.add_argument(
		"--db",
		default=str(Path(tempfile.gettempdir()) / "android-watcher-seed.db"),
		help="working DB path (persistent => resumable across runs)",
	)
	parser.add_argument("--out", default=str(SEED_PATH), help="output seed.sql.gz path")
	parser.add_argument(
		"--concurrency", type=int, default=8, help="max in-flight fetches (default 8)"
	)
	parser.add_argument(
		"--delay",
		type=float,
		default=0.2,
		help="per-host crawl delay in seconds; robots.txt wins if stricter (default 0.2)",
	)
	args = parser.parse_args()

	# Progress to stdout: keep the per-page baseline heartbeat and shard-load
	# lines, but silence the per-URL "downloading …" noise from the fetch layer.
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
	logging.getLogger("android_watcher.fetch").setLevel(logging.WARNING)
	logging.getLogger("httpx").setLevel(logging.WARNING)  # silence per-request "HTTP Request" lines

	return asyncio.run(
		_run(args.db, Path(args.out), concurrency=args.concurrency, delay=args.delay)
	)


if __name__ == "__main__":
	sys.exit(main())
