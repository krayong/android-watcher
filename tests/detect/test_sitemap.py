from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from android_watcher.detect.sitemap import SitemapDetector
from android_watcher.models import FetchResult, Source
from android_watcher.store import Snapshot

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def read(name: str) -> str:
	return (FIX / name).read_text()


class FakeStore:
	def __init__(self) -> None:
		self.snaps: dict[tuple[str, str], Snapshot] = {}

	def get_snapshot(self, source_id: str, url: str) -> Snapshot | None:
		return self.snaps.get((source_id, url))

	def upsert_snapshot(
		self,
		source_id: str,
		url: str,
		*,
		signal_type: str,
		content_hash: str,
		lastmod: str,
		excerpt: str,
		content_text: str = "",
	) -> None:
		self.snaps[(source_id, url)] = Snapshot(
			source_id=source_id,
			url=url,
			signal_type=signal_type,  # type: ignore[arg-type]
			content_hash=content_hash,
			lastmod=lastmod,
			excerpt=excerpt,
			fetched_at=datetime.now(UTC),
			content_text=content_text,
		)


class RouteFetcher:
	def __init__(self, routes: dict[str, str]) -> None:
		self.routes = routes

	async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
		return FetchResult(url=url, status=200, text=self.routes[url])


class Code304Fetcher:
	"""Returns 200 for the sitemap URL, 304 (not_modified) for confirm-page fetches."""

	def __init__(self, sitemap_xml: str) -> None:
		self.sitemap = sitemap_xml

	async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
		if url.endswith("sitemap.xml"):
			return FetchResult(url=url, status=200, text=self.sitemap)
		return FetchResult(url=url, status=304, text="", not_modified=True)


def src() -> Source:
	return Source(
		id="generic",
		name="Generic",
		category="guides",
		detector="sitemap",
		url="https://site.example.com/sitemap.xml",
		path_prefix="/docs",
		content_selector="#content",
	)


# ---------------------------------------------------------------------------
# first sight silently baselines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_sight_baselines_silently() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	changes = await det.detect(src(), store, RouteFetcher(routes))
	assert changes == []
	assert store.get_snapshot("generic", "https://site.example.com/docs/a") is not None


# ---------------------------------------------------------------------------
# core guarantee: lastmod bump + unchanged content => NO Change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lastmod_bump_with_unchanged_content_yields_no_change() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes))

	bumped = read("sitemap_simple.xml").replace("2026-06-10", "2026-06-20", 1)
	routes2 = {
		"https://site.example.com/sitemap.xml": bumped,
		"https://site.example.com/docs/a": read("content_before.html"),  # same page
	}
	changes = await det.detect(src(), store, RouteFetcher(routes2))
	assert changes == []


# ---------------------------------------------------------------------------
# real content change => one "updated" Change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lastmod_candidate_confirmed_by_content_change() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes))

	bumped = read("sitemap_simple.xml").replace("2026-06-10", "2026-06-20", 1)
	routes2 = {
		"https://site.example.com/sitemap.xml": bumped,
		"https://site.example.com/docs/a": read("content_after_real_change.html"),
	}
	changes = await det.detect(src(), store, RouteFetcher(routes2))
	assert len(changes) == 1
	assert changes[0].change_kind == "updated"
	assert changes[0].url == "https://site.example.com/docs/a"
	assert changes[0].source_id == "generic"


# ---------------------------------------------------------------------------
# raw_diff sent to triage is a diff that excludes unchanged nav boilerplate
# (content_selector="" watches the whole page, so nav sits at the front of the
# text; a fixed prefix would be all nav — a diff must surface the body change).
# ---------------------------------------------------------------------------


def _whole_page_src() -> Source:
	return Source(
		id="generic",
		name="Generic",
		category="guides",
		detector="sitemap",
		url="https://site.example.com/sitemap.xml",
		path_prefix="/docs",
		content_selector="",
	)


@pytest.mark.asyncio
async def test_raw_diff_excludes_unchanged_nav_and_carries_body_change() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("nav_page_before.html"),
	}
	await det.detect(_whole_page_src(), store, RouteFetcher(routes))

	bumped = read("sitemap_simple.xml").replace("2026-06-10", "2026-06-20", 1)
	routes2 = {
		"https://site.example.com/sitemap.xml": bumped,
		"https://site.example.com/docs/a": read("nav_page_after.html"),
	}
	changes = await det.detect(_whole_page_src(), store, RouteFetcher(routes2))

	assert len(changes) == 1
	diff = changes[0].raw_diff
	# The changed body text must reach triage.
	assert "3.2" in diff
	assert "background refresh" in diff
	# The shared, unchanged nav boilerplate must NOT dominate the diff.
	assert "Build AI experiences" not in diff


# ---------------------------------------------------------------------------
# chrome-only (cosmetic) change yields nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lastmod_bump_with_chrome_only_change_yields_nothing() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes))

	bumped = read("sitemap_simple.xml").replace("2026-06-10", "2026-06-20", 1)
	routes2 = {
		"https://site.example.com/sitemap.xml": bumped,
		"https://site.example.com/docs/a": read("content_after_chrome_only.html"),
	}
	changes = await det.detect(src(), store, RouteFetcher(routes2))
	assert changes == []


# ---------------------------------------------------------------------------
# 304 on confirm: quiesce (store new lastmod, keep old hash, emit no Change)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lastmod_bump_with_304_confirm_quiesces_and_stores_new_lastmod() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes))
	snap0 = store.get_snapshot("generic", "https://site.example.com/docs/a")
	assert snap0 is not None
	assert snap0.lastmod == "2026-06-10"
	hash0 = snap0.content_hash

	bumped = read("sitemap_simple.xml").replace("2026-06-10", "2026-06-20", 1)
	changes = await det.detect(src(), store, Code304Fetcher(bumped))
	assert changes == []

	snap1 = store.get_snapshot("generic", "https://site.example.com/docs/a")
	assert snap1 is not None
	assert snap1.lastmod == "2026-06-20"  # new lastmod persisted
	assert snap1.content_hash == hash0  # prior hash preserved

	# next run with same lastmod: candidate quiesces, no confirms fired
	again = await det.detect(src(), store, Code304Fetcher(bumped))
	assert again == []


# ---------------------------------------------------------------------------
# identical re-confirm (second full fetch of same content) => None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_reconfirm_yields_nothing() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes))

	# Same sitemap, same content — lastmod unchanged, no candidate at all
	changes = await det.detect(src(), store, RouteFetcher(routes))
	assert changes == []


# ---------------------------------------------------------------------------
# path_prefix filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_path_prefix_urls_considered() -> None:
	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes))

	# /other/b is outside path_prefix /docs — never fetched or baselined
	assert store.get_snapshot("generic", "https://site.example.com/other/b") is None


# ---------------------------------------------------------------------------
# zero-match prefix: warning + empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_match_prefix_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
	import logging

	store = FakeStore()
	det = SitemapDetector()
	routes = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
	}

	no_match_source = Source(
		id="generic",
		name="Generic",
		category="guides",
		detector="sitemap",
		url="https://site.example.com/sitemap.xml",
		path_prefix="/nonexistent",
		content_selector="",
	)

	with caplog.at_level(logging.WARNING):
		changes = await det.detect(no_match_source, store, RouteFetcher(routes))

	assert changes == []
	assert any(
		"nonexistent" in r.message or "zero" in r.message.lower() or "no url" in r.message.lower()
		for r in caplog.records
	)


# ---------------------------------------------------------------------------
# empty-render guard: JS-shell pages must never be baselined or recorded
# ---------------------------------------------------------------------------


class SingleURLFetcher:
	"""Returns a fixed response for one content URL; the sitemap gets real XML."""

	def __init__(self, sitemap_xml: str, page_url: str, page_html: str) -> None:
		self.sitemap = sitemap_xml
		self.page_url = page_url
		self.page_html = page_html

	async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
		if url.endswith("sitemap.xml"):
			return FetchResult(url=url, status=200, text=self.sitemap)
		return FetchResult(url=url, status=200, text=self.page_html)


@pytest.mark.asyncio
async def test_js_shell_on_first_sight_returns_none_and_writes_no_snapshot(
	caplog: pytest.LogCaptureFixture,
) -> None:
	"""A JS-shell page on first sight must return None and leave no snapshot."""
	import logging

	store = FakeStore()
	det = SitemapDetector()
	shell_html = read("content_js_shell.html")
	fetcher = SingleURLFetcher(
		read("sitemap_simple.xml"),
		"https://site.example.com/docs/a",
		shell_html,
	)

	with caplog.at_level(logging.WARNING):
		changes = await det.detect(src(), store, fetcher)

	assert changes == []
	# No snapshot written — the shell must not be baselined
	assert store.get_snapshot("generic", "https://site.example.com/docs/a") is None
	# A warning must be emitted
	assert any(
		"shell" in r.message.lower() or "js-shell" in r.message.lower() for r in caplog.records
	)


@pytest.mark.asyncio
async def test_js_shell_after_real_content_emits_no_change_and_preserves_snapshot() -> None:
	"""A real->JS-shell transition must not produce a Change and must not clobber the snapshot."""
	store = FakeStore()
	det = SitemapDetector()

	# First pass: real content baselines
	routes_real = {
		"https://site.example.com/sitemap.xml": read("sitemap_simple.xml"),
		"https://site.example.com/docs/a": read("content_before.html"),
	}
	await det.detect(src(), store, RouteFetcher(routes_real))
	snap_before = store.get_snapshot("generic", "https://site.example.com/docs/a")
	assert snap_before is not None
	hash_before = snap_before.content_hash

	# Second pass: lastmod bumps, but page now returns a JS shell
	bumped_sitemap = read("sitemap_simple.xml").replace("2026-06-10", "2026-06-20", 1)
	shell_fetcher = SingleURLFetcher(
		bumped_sitemap,
		"https://site.example.com/docs/a",
		read("content_js_shell.html"),
	)
	changes = await det.detect(src(), store, shell_fetcher)

	# No change emitted
	assert changes == []
	# Snapshot must still hold the prior real hash — shell must not overwrite it
	snap_after = store.get_snapshot("generic", "https://site.example.com/docs/a")
	assert snap_after is not None
	assert snap_after.content_hash == hash_before
