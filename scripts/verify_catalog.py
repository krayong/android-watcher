"""Opt-in live verification script for the android-watcher source catalog.

Run manually before cutting a release to confirm that every enabled catalog source
is reachable and returns real content (not just a JS shell), and that every
android_sitemap source's path_prefix resolves to at least one sitemap URL.

Feed URLs in the catalog are CANDIDATES. This script is the release gate that
confirms each feed_url: an enabled feed source whose URL is missing, unreachable,
or renders below the minimum text threshold FAILS and blocks the release.

Exit code is non-zero when any enabled source fails, so an opt-in CI job fails loudly.

Usage:
    uv run python scripts/verify_catalog.py

DO NOT import this module from tests or production code. It hits live network.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys

from android_watcher.catalog import load_catalog
from android_watcher.detect.android_sitemap import load_sitemap, prefix_count
from android_watcher.fetch import USER_AGENT, Fetcher
from android_watcher.models import Source
from android_watcher.store import Store

MIN_RENDERED_TEXT = 500  # chars of server-side text below which a page is a JS shell
MIN_PREFIX_URLS = 1  # a prefix_count below this means the sitemap prefix is broken


async def verify_sitemap_prefix(source: Source, fetcher: Fetcher) -> tuple[bool, str]:
	"""Confirm source.path_prefix resolves to at least MIN_PREFIX_URLS sitemap URLs."""
	entries = await load_sitemap(fetcher)
	count = prefix_count(entries, source.path_prefix)
	ok = count >= MIN_PREFIX_URLS
	return ok, f"{count} URLs under prefix '{source.path_prefix}' (min {MIN_PREFIX_URLS})"


async def verify_content_renders(source: Source, fetcher: Fetcher) -> tuple[bool, str]:
	"""Confirm the page returns real server-side text (not just a JS shell)."""
	result = await fetcher.fetch(source.url)
	text_len = len(result.text.strip())
	ok = text_len >= MIN_RENDERED_TEXT
	return ok, f"{text_len} chars rendered (min {MIN_RENDERED_TEXT})"


async def _run() -> int:
	store = Store(":memory:")
	store.migrate()
	fetcher = Fetcher(store, user_agent=USER_AGENT)
	failures = 0
	try:
		for s in load_catalog():
			if not s.enabled:
				continue
			if s.detector == "android_sitemap":
				ok, detail = await verify_sitemap_prefix(s, fetcher)
			elif s.detector == "feed":
				# Feed URLs are CANDIDATES; this script is the release gate.
				if not s.feed_url:
					ok, detail = False, "no feed_url (CANDIDATE unresolved)"
				else:
					ok, detail = await verify_content_renders(
						dataclasses.replace(s, url=s.feed_url), fetcher
					)
			elif s.detector == "content":
				ok, detail = await verify_content_renders(s, fetcher)
			else:
				# sitemap detector: skipped (needs sitemap_url, not a content fetch)
				ok, detail = True, "skipped (sitemap detector: needs sitemap_url)"
			status = "OK  " if ok else "FAIL"
			print(f"[{status}] {s.id:28} {detail}")
			if not ok:
				failures += 1
	finally:
		await fetcher.close()
	print(f"\n{failures} failure(s)")
	return 1 if failures else 0


def main() -> int:
	return asyncio.run(_run())


if __name__ == "__main__":
	sys.exit(main())
