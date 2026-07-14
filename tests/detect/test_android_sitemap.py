import asyncio
import pathlib
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from android_watcher.detect._normalize import content_hash, extract_main, normalize_text
from android_watcher.detect.android_sitemap import (
	AndroidSitemapDetector,
	baseline_all,
	load_sitemap,
	make_shard_cache,
	prefix_count,
)
from android_watcher.models import FetchResult, Source
from android_watcher.store import Snapshot

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def read(name):
	return (FIX / name).read_text()


def seed_baseline(store, source_id, loc, html, lastmod, selector="#content"):
	"""Pre-record a captured content baseline (real hash), as if a prior run had
	already content-confirmed this URL. Lets tests skip the fetch-free baseline +
	first-capture passes when they only care about steady-state change detection.
	"""
	text = normalize_text(extract_main(html, selector))
	store.upsert_snapshot(
		source_id,
		loc,
		signal_type="sitemap",
		content_hash=content_hash(text),
		lastmod=lastmod,
		excerpt=text[:500],
	)


class FakeStore:
	def __init__(self):
		self.snaps = {}

	def get_snapshot(self, source_id, url):
		return self.snaps.get((source_id, url))

	def source_has_snapshots(self, source_id):
		return any(sid == source_id for sid, _url in self.snaps)

	def upsert_snapshot(
		self, source_id, url, *, signal_type, content_hash, lastmod, excerpt, content_text=""
	):
		self.snaps[(source_id, url)] = Snapshot(
			source_id=source_id,
			url=url,
			signal_type=signal_type,
			content_hash=content_hash,
			lastmod=lastmod,
			excerpt=excerpt,
			fetched_at=None,
			content_text=content_text,
		)


class CountingFetcher:
	def __init__(self, routes):
		self.routes = routes
		self.calls = {}

	async def fetch(self, url, *, conditional=False):
		self.calls[url] = self.calls.get(url, 0) + 1
		return FetchResult(url=url, status=200, text=self.routes[url])


class SlowCountingFetcher(CountingFetcher):
	"""Yields control mid-fetch so concurrent callers actually interleave.

	Without an await point between a caller's _loaded check and its shard
	fetches, asyncio.gather would run each coroutine to completion serially and
	a missing Lock would never be exposed. The sleep(0) hands the loop to the
	sibling coroutine, forcing the exact race the asyncio.Lock must serialize.
	"""

	async def fetch(self, url, *, conditional=False):
		await asyncio.sleep(0)
		return await super().fetch(url, conditional=conditional)


def studio_src():
	return Source(
		id="android-studio-releases",
		name="Studio releases",
		category="tooling",
		detector="android_sitemap",
		url="https://developer.android.com/studio/releases",
		path_prefix="/studio/releases",
		content_selector="#content",
	)


def preview_src():
	# prefix matches the path-based-locale i18n cluster in sitemap_shard_i18n.xml
	return Source(
		id="android-studio-preview",
		name="Studio preview",
		category="tooling",
		detector="android_sitemap",
		url="https://developer.android.com/studio/preview/features",
		path_prefix="/studio/preview",
		content_selector="#content",
	)


def base_routes():
	return {
		"https://developer.android.com/sitemap.xml": read("sitemap_index.xml"),
		"https://developer.android.com/sitemap-0.xml": read("sitemap_shard0.xml"),
		"https://developer.android.com/sitemap-i18n.xml": read("sitemap_shard_i18n.xml"),
		# canonical + path-based locale variants (real d.a.c uses /<locale>/...)
		"https://developer.android.com/studio/releases": read("content_before.html"),
		"https://developer.android.com/studio/preview/features": read("content_before.html"),
		"https://developer.android.com/fr/studio/preview/features": read("content_before.html"),
		"https://developer.android.com/de/studio/preview/features": read("content_before.html"),
		"https://developer.android.com/ja/studio/preview/features": read("content_before.html"),
	}


@pytest.mark.asyncio
async def test_shared_cache_fetches_index_and_shards_once():
	store = FakeStore()
	fetcher = CountingFetcher(base_routes())
	det = AndroidSitemapDetector()
	# two sources in the same run share the same fetcher
	await det.detect(studio_src(), store, fetcher)
	s2 = Source(
		id="android-previews",
		name="Previews",
		category="platform-release",
		detector="android_sitemap",
		url="https://developer.android.com/about/versions",
		path_prefix="/about/versions",
		content_selector="#content",
	)
	await det.detect(s2, store, fetcher)
	assert fetcher.calls["https://developer.android.com/sitemap.xml"] == 1
	assert fetcher.calls["https://developer.android.com/sitemap-0.xml"] == 1
	assert fetcher.calls["https://developer.android.com/sitemap-i18n.xml"] == 1


@pytest.mark.asyncio
async def test_concurrent_callers_fetch_shards_once_via_lock():
	# CONTRACTS: several android_sitemap sources call load_sitemap CONCURRENTLY.
	# The asyncio.Lock on the shared ShardCache must serialize population so the
	# index + shards are fetched exactly once even under gather(). A naive
	# check-then-fetch fails here: both coroutines would see _loaded is False
	# (the SlowCountingFetcher yields the loop between check and fetch) and each
	# would pull all shards (count == 2). The Lock forces count == 1.
	fetcher = SlowCountingFetcher(base_routes())
	await asyncio.gather(
		load_sitemap(fetcher),
		load_sitemap(fetcher),
		load_sitemap(fetcher),
	)
	assert fetcher.calls["https://developer.android.com/sitemap.xml"] == 1
	assert fetcher.calls["https://developer.android.com/sitemap-0.xml"] == 1
	assert fetcher.calls["https://developer.android.com/sitemap-i18n.xml"] == 1


# An en-only shard: three English pages under /studio/preview, one shared lastmod.
_PREVIEW_INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://developer.android.com/sitemap-preview.xml</loc></sitemap>
</sitemapindex>"""

_PREVIEW_PAGES = [
	"https://developer.android.com/studio/preview/features",
	"https://developer.android.com/studio/preview/install",
	"https://developer.android.com/studio/preview/release-notes",
]


def _preview_shard(lastmod):
	urls = "\n".join(
		f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>" for loc in _PREVIEW_PAGES
	)
	return (
		'<?xml version="1.0" encoding="UTF-8"?>\n'
		'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
		f"{urls}\n</urlset>"
	)


@pytest.mark.asyncio
async def test_en_bulk_section_regen_collapses_to_one_confirm():
	# Three EN pages under /studio/preview, already content-baselined. The whole
	# section is regenerated to a single NEW lastmod with a real content change:
	# a cluster >= BULK_COLLAPSE_THRESHOLD => collapse to ONE confirm of the
	# representative (shortest-path) URL; the rest just quiesce.
	src = preview_src()
	store = FakeStore()
	before = read("content_before.html")
	for loc in _PREVIEW_PAGES:
		seed_baseline(store, src.id, loc, before, "2026-06-10")

	after = read("content_after_real_change.html")
	routes = {
		"https://developer.android.com/sitemap.xml": _PREVIEW_INDEX,
		"https://developer.android.com/sitemap-preview.xml": _preview_shard("2026-06-20"),
		**{loc: after for loc in _PREVIEW_PAGES},
	}
	fetcher = CountingFetcher(routes)
	det = AndroidSitemapDetector()
	changes = await det.detect(src, store, fetcher)

	rep = min(_PREVIEW_PAGES, key=lambda u: len(urlsplit(u).path))
	assert len(changes) == 1
	assert changes[0].url == rep
	# only the representative was confirm-fetched; the cluster collapsed
	fetched_pages = [u for u in fetcher.calls if u in _PREVIEW_PAGES]
	assert fetched_pages == [rep]
	# every cluster member quiesced: re-running with the same lastmod yields nothing
	again = await det.detect(src, store, CountingFetcher(routes))
	assert again == []


@pytest.mark.asyncio
async def test_locale_variants_are_dropped_en_only():
	# The i18n shard holds canonical + fr/de/ja variants of one page. Only the
	# canonical English URL survives parsing; the translations never appear.
	entries = await load_sitemap(CountingFetcher(base_routes()))
	paths = [urlsplit(loc).path for loc, _ in entries]
	assert "/studio/preview/features" in paths
	assert all(
		not (p.startswith("/fr/") or p.startswith("/de/") or p.startswith("/ja/")) for p in paths
	)


@pytest.mark.asyncio
async def test_two_letter_english_sections_are_not_treated_as_locales():
	# /tv, /xr, /ai are real English sections, not translations — a naive
	# two-letter locale regex would wrongly drop them. They must survive; the
	# genuine /fr/... translation must not.
	index = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://developer.android.com/sitemap-x.xml</loc></sitemap>
</sitemapindex>"""
	shard = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://developer.android.com/tv/overview</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/xr/develop</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/ai/overview</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/fr/tv/overview</loc><lastmod>2026-06-20</lastmod></url>
</urlset>"""
	routes = {
		"https://developer.android.com/sitemap.xml": index,
		"https://developer.android.com/sitemap-x.xml": shard,
	}
	entries = await load_sitemap(CountingFetcher(routes))
	paths = {urlsplit(loc).path for loc, _ in entries}
	assert paths == {"/tv/overview", "/xr/develop", "/ai/overview"}


@pytest.mark.asyncio
async def test_hl_query_locale_variants_are_dropped():
	# Translations also hide in the `hl` query param (?hl=ko), not just the path.
	# en-only must drop those; only the canonical English URL (no hl, or hl=en)
	# survives.
	index = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://developer.android.com/sitemap-x.xml</loc></sitemap>
</sitemapindex>"""
	shard = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://developer.android.com/about/versions/13</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/about/versions/13?hl=ko</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/about/versions/13?hl=ja</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/about/versions/14?hl=en</loc><lastmod>2026-06-20</lastmod></url>
</urlset>"""
	routes = {
		"https://developer.android.com/sitemap.xml": index,
		"https://developer.android.com/sitemap-x.xml": shard,
	}
	entries = await load_sitemap(CountingFetcher(routes))
	locs = {loc for loc, _ in entries}
	assert locs == {
		"https://developer.android.com/about/versions/13",
		"https://developer.android.com/about/versions/14?hl=en",
	}


@pytest.mark.asyncio
async def test_first_run_baselines_without_content_fetch():
	# A brand-new URL is baselined from the sitemap lastmod alone — no page fetch,
	# empty content_hash, no Change — so install never hammers the network.
	store = FakeStore()
	fetcher = CountingFetcher(base_routes())
	det = AndroidSitemapDetector()
	changes = await det.detect(studio_src(), store, fetcher)
	assert changes == []
	assert "https://developer.android.com/studio/releases" not in fetcher.calls
	snap = store.get_snapshot(
		"android-studio-releases", "https://developer.android.com/studio/releases"
	)
	assert snap is not None
	assert snap.content_hash == ""
	assert snap.lastmod == "2026-06-10"


@pytest.mark.asyncio
async def test_baseline_all_fetches_content_and_is_resumable():
	# The seed builder needs real content hashes, so baseline_all DOES fetch every
	# URL under the prefix (unlike fetch-free detect), and skips ones already done.
	store = FakeStore()
	url = "https://developer.android.com/studio/releases"
	fetcher = CountingFetcher(base_routes())
	n = await baseline_all(studio_src(), store, fetcher)
	assert n == 1
	assert fetcher.calls.get(url) == 1  # content-fetched, not fetch-free
	snap = store.get_snapshot("android-studio-releases", url)
	assert snap is not None and snap.content_hash != ""

	# resumable: a re-run skips already-baselined URLs without re-fetching
	fetcher2 = CountingFetcher(base_routes())
	n2 = await baseline_all(studio_src(), store, fetcher2)
	assert n2 == 0
	assert url not in fetcher2.calls


@pytest.mark.asyncio
async def test_baseline_then_first_capture_is_silent_then_change_fires():
	# Lifecycle: fetch-free baseline -> first content capture (silent) -> change.
	store = FakeStore()
	src = studio_src()
	url = "https://developer.android.com/studio/releases"
	det = AndroidSitemapDetector()

	# run 1: fetch-free baseline at 2026-06-10
	assert await det.detect(src, store, CountingFetcher(base_routes())) == []

	# run 2: lastmod advances -> first real content fetch, captured silently
	r2 = base_routes()
	r2["https://developer.android.com/sitemap-0.xml"] = read("sitemap_shard0.xml").replace(
		"2026-06-10", "2026-06-15"
	)
	r2[url] = read("content_before.html")
	assert await det.detect(src, store, CountingFetcher(r2)) == []

	# run 3: lastmod advances again + real content change -> Change
	r3 = base_routes()
	r3["https://developer.android.com/sitemap-0.xml"] = read("sitemap_shard0.xml").replace(
		"2026-06-10", "2026-06-20"
	)
	r3[url] = read("content_after_real_change.html")
	changes = await det.detect(src, store, CountingFetcher(r3))
	assert len(changes) == 1
	assert changes[0].change_kind == "updated"
	assert changes[0].url == url


@pytest.mark.asyncio
async def test_prefix_count_and_stale_prefix_zero_match():
	fetcher = CountingFetcher(base_routes())
	entries = await load_sitemap(fetcher)
	# en-only: locale variants dropped, so just the canonical page counts
	assert prefix_count(entries, "/studio/preview") == 1
	assert prefix_count(entries, "/studio/releases") == 1
	assert prefix_count(entries, "/this/prefix/is/gone") == 0
	cache = make_shard_cache()
	await cache.load(CountingFetcher(base_routes()))
	assert cache.stale_prefix("/studio/releases") is False
	assert cache.stale_prefix("/this/prefix/is/gone") is True


# ---------------------------------------------------------------------------
# Host-agnostic filtering: exclude / require_segment / reference_mode / versions
# ---------------------------------------------------------------------------


def _src(**kw):
	base = dict(
		id="s",
		name="S",
		category="guides",
		detector="android_sitemap",
		url="https://developer.android.com/x",
		path_prefix="",
		content_selector="#content",
	)
	base.update(kw)
	return Source(**base)


def _index(shard_url):
	return (
		'<?xml version="1.0" encoding="UTF-8"?>\n'
		'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
		f"<sitemap><loc>{shard_url}</loc></sitemap></sitemapindex>"
	)


def _urlset(paths, host="https://developer.android.com", lastmod="2026-06-20"):
	urls = "".join(f"<url><loc>{host}{p}</loc><lastmod>{lastmod}</lastmod></url>" for p in paths)
	return (
		'<?xml version="1.0" encoding="UTF-8"?>\n'
		f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
	)


def _routes(paths, host="https://developer.android.com"):
	"""sitemapindex at /sitemap.xml -> one shard urlset of the given paths."""
	return {
		f"{host}/sitemap.xml": _index(f"{host}/sitemap-0.xml"),
		f"{host}/sitemap-0.xml": _urlset(paths, host),
	}


async def _watched_paths(src, paths, host="https://developer.android.com"):
	"""Run detect on a fresh store; return the set of baselined paths (= watched)."""
	store = FakeStore()
	routes = _routes(paths, host)
	with patch("android_watcher.detect.android_sitemap.load_catalog", return_value=[src]):
		await AndroidSitemapDetector().detect(src, store, CountingFetcher(routes))
	return {urlsplit(url).path for (_sid, url) in store.snaps}


@pytest.mark.asyncio
async def test_binary_assets_are_not_watched():
	# PDFs, images, archives, fonts, etc. are not text pages and must never be
	# confirm-fetched (feeding binary to the HTML parser throws).
	got = await _watched_paths(
		_src(),
		[
			"/docs/security/report.pdf",
			"/images/hero.png",
			"/downloads/tools.zip",
			"/fonts/roboto.woff2",
			"/guide/real-page",
		],
	)
	assert got == {"/guide/real-page"}


@pytest.mark.asyncio
async def test_exclude_prefixes_drops_subtree():
	got = await _watched_paths(
		_src(exclude_prefixes=("/sdk",)), ["/sdk/api/x", "/studio/y", "/develop/z"]
	)
	assert got == {"/studio/y", "/develop/z"}


@pytest.mark.asyncio
async def test_source_android_excludes_gki_build_lists():
	"""The per-version GKI build-list pages auto-regenerate constantly (new build
	rows), so the shipped source-android catalog entry must drop them while still
	watching the curated GKI docs."""
	from android_watcher.catalog import load_catalog

	source_android = next(s for s in load_catalog() if s.id == "source-android")
	got = await _watched_paths(
		source_android,
		[
			"/docs/core/architecture/kernel/gki-android14-5_15-release-builds",
			"/docs/core/architecture/kernel/gki-android17-6_18-deprecated-builds",
			"/docs/core/architecture/kernel/gki-faq",
			"/docs/core/architecture/kernel/gki-releases",
		],
		host="https://source.android.com",
	)
	assert got == {
		"/docs/core/architecture/kernel/gki-faq",
		"/docs/core/architecture/kernel/gki-releases",
	}


@pytest.mark.asyncio
async def test_require_segment_keeps_android_drops_others():
	src = _src(
		url="https://developers.google.com/x",
		require_segment="android",
		reference_mode="drop",
	)
	got = await _watched_paths(
		src,
		[
			"/admob/android/get-started",
			"/maps/web/overview",
			"/android-publisher/api-ref/v3",
			"/admob/android/reference/SomeMethod",
		],
		host="https://developers.google.com",
	)
	assert got == {"/admob/android/get-started", "/android-publisher/api-ref/v3"}


@pytest.mark.asyncio
async def test_reference_index_only_kotlin_preferred():
	got = await _watched_paths(
		_src(reference_mode="index_only"),
		[
			"/reference/kotlin/androidx/activity/package-summary",  # keep (kotlin index)
			"/reference/kotlin/androidx/activity/Foo",  # drop (symbol)
			"/reference/androidx/activity/package-summary",  # drop (java; kotlin twin exists)
			"/reference/com/android/billingclient/packages",  # keep (no kotlin twin)
			"/develop/guide",  # keep (non-reference)
		],
	)
	assert got == {
		"/reference/kotlin/androidx/activity/package-summary",
		"/reference/com/android/billingclient/packages",
		"/develop/guide",
	}


@pytest.mark.asyncio
async def test_version_dedup_keeps_latest_but_spares_about_versions():
	got = await _watched_paths(
		_src(reference_mode="index_only"),
		[
			"/reference/tools/gradle-api/8.0/packages",
			"/reference/tools/gradle-api/9.4/packages",
			"/about/versions/14",
			"/about/versions/15",
		],
	)
	assert got == {
		"/reference/tools/gradle-api/9.4/packages",  # latest dotted version kept
		"/about/versions/14",  # bare-integer releases both kept
		"/about/versions/15",
	}


@pytest.mark.asyncio
async def test_urlset_sitemap_served_directly():
	# A host whose /sitemap.xml is a bare <urlset> (not an index) still loads.
	routes = {
		"https://kotlinlang.org/sitemap.xml": _urlset(
			["/docs/home", "/docs/basics"], "https://kotlinlang.org"
		)
	}
	entries = await load_sitemap(CountingFetcher(routes), "https://kotlinlang.org/sitemap.xml")
	assert {urlsplit(loc).path for loc, _ in entries} == {"/docs/home", "/docs/basics"}


@pytest.mark.asyncio
async def test_new_page_reported_after_baseline():
	# With a baseline present, a never-seen URL is confirmed and reported as "new".
	store = FakeStore()
	src = _src()
	seen = "https://developer.android.com/develop/old"
	store.upsert_snapshot(
		src.id, seen, signal_type="sitemap", content_hash="h", lastmod="2026-06-20", excerpt=""
	)
	routes = _routes(["/develop/old", "/develop/brand-new"])
	routes["https://developer.android.com/develop/brand-new"] = read("content_before.html")
	with patch("android_watcher.detect.android_sitemap.load_catalog", return_value=[src]):
		changes = await AndroidSitemapDetector().detect(src, store, CountingFetcher(routes))
	assert len(changes) == 1
	assert changes[0].change_kind == "new"
	assert changes[0].url == "https://developer.android.com/develop/brand-new"


@pytest.mark.asyncio
async def test_first_run_no_new_flood():
	# No baseline yet => every URL is a silent fetch-free baseline, no "new" spam.
	store = FakeStore()
	src = _src()
	with patch("android_watcher.detect.android_sitemap.load_catalog", return_value=[src]):
		changes = await AndroidSitemapDetector().detect(
			src, store, CountingFetcher(_routes(["/develop/a", "/develop/b"]))
		)
	assert changes == []
	assert len(store.snaps) == 2  # both baselined fetch-free


# ---------------------------------------------------------------------------
# Most-specific-prefix-wins: nested prefix deduplication
# ---------------------------------------------------------------------------

# Shard with two lastmod values so URLs are individual candidates (below BULK_COLLAPSE_THRESHOLD).
# The studio-only URLs get one lastmod; the emulator URLs get a different one so they're separate
# clusters and each confirm-fetched individually.
_NESTED_SHARD_V1 = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://developer.android.com/studio/releases</loc><lastmod>2026-06-10</lastmod></url>
  <url><loc>https://developer.android.com/studio/releases/gradle-plugin</loc><lastmod>2026-06-11</lastmod></url>
  <url><loc>https://developer.android.com/studio/releases/emulator</loc><lastmod>2026-06-12</lastmod></url>
  <url><loc>https://developer.android.com/studio/releases/emulator/34-1</loc><lastmod>2026-06-13</lastmod></url>
  <url><loc>https://developer.android.com/guide/unrelated</loc><lastmod>2026-06-10</lastmod></url>
</urlset>"""

# Same URLs with advanced lastmods — triggers candidates on the second detect run.
_NESTED_SHARD_V2 = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://developer.android.com/studio/releases</loc><lastmod>2026-06-20</lastmod></url>
  <url><loc>https://developer.android.com/studio/releases/gradle-plugin</loc><lastmod>2026-06-21</lastmod></url>
  <url><loc>https://developer.android.com/studio/releases/emulator</loc><lastmod>2026-06-22</lastmod></url>
  <url><loc>https://developer.android.com/studio/releases/emulator/34-1</loc><lastmod>2026-06-23</lastmod></url>
  <url><loc>https://developer.android.com/guide/unrelated</loc><lastmod>2026-06-20</lastmod></url>
</urlset>"""

_NESTED_INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://developer.android.com/sitemap-nested.xml</loc></sitemap>
</sitemapindex>"""

_CONTENT_HTML_V1 = (
	"<html><body><div id='content'>"
	+ "Android release notes with substantive documentation content. " * 3
	+ "</div></body></html>"
)

_CONTENT_HTML_V2 = (
	"<html><body><div id='content'>"
	+ "Updated Android release notes with new substantive content changes. " * 4
	+ "</div></body></html>"
)


def _nested_routes_v1():
	return {
		"https://developer.android.com/sitemap.xml": _NESTED_INDEX,
		"https://developer.android.com/sitemap-nested.xml": _NESTED_SHARD_V1,
		"https://developer.android.com/studio/releases": _CONTENT_HTML_V1,
		"https://developer.android.com/studio/releases/gradle-plugin": _CONTENT_HTML_V1,
		"https://developer.android.com/studio/releases/emulator": _CONTENT_HTML_V1,
		"https://developer.android.com/studio/releases/emulator/34-1": _CONTENT_HTML_V1,
	}


def _nested_routes_v2():
	return {
		"https://developer.android.com/sitemap.xml": _NESTED_INDEX,
		"https://developer.android.com/sitemap-nested.xml": _NESTED_SHARD_V2,
		"https://developer.android.com/studio/releases": _CONTENT_HTML_V2,
		"https://developer.android.com/studio/releases/gradle-plugin": _CONTENT_HTML_V2,
		"https://developer.android.com/studio/releases/emulator": _CONTENT_HTML_V2,
		"https://developer.android.com/studio/releases/emulator/34-1": _CONTENT_HTML_V2,
	}


def _emulator_src():
	return Source(
		id="emulator",
		name="Emulator",
		category="tooling",
		detector="android_sitemap",
		url="https://developer.android.com/studio/releases/emulator",
		path_prefix="/studio/releases/emulator",
		content_selector="#content",
	)


def _fake_catalog_for_nested():
	"""Two catalog sources: the broad parent and the narrow child."""
	return [
		Source(
			id="android-studio-releases",
			name="Studio releases",
			category="tooling",
			detector="android_sitemap",
			url="https://developer.android.com/studio/releases",
			path_prefix="/studio/releases",
			content_selector="#content",
		),
		Source(
			id="emulator",
			name="Emulator",
			category="tooling",
			detector="android_sitemap",
			url="https://developer.android.com/studio/releases/emulator",
			path_prefix="/studio/releases/emulator",
			content_selector="#content",
		),
	]


@pytest.mark.asyncio
async def test_most_specific_prefix_wins_no_double_claim():
	"""URLs under /studio/releases/emulator/... are claimed ONLY by the emulator
	source, not also by android-studio-releases, even though both prefixes match.

	Strategy: baseline both sources on a v1 shard run (confirm_candidate silently
	stores snapshots), then re-run with v2 shard (advanced lastmods + changed
	content) so every URL produces a real Change.  Assert no URL appears in both
	sources' change sets.
	"""
	det = AndroidSitemapDetector()
	catalog = _fake_catalog_for_nested()

	with patch("android_watcher.detect.android_sitemap.load_catalog", return_value=catalog):
		# Seed each source's claimed URLs with a captured v1 content baseline, so
		# the single v2 pass (advanced lastmods + changed content) fires real
		# Changes without the fetch-free baseline + first-capture passes.
		store = FakeStore()
		for loc, lastmod in (
			("https://developer.android.com/studio/releases", "2026-06-10"),
			("https://developer.android.com/studio/releases/gradle-plugin", "2026-06-11"),
		):
			seed_baseline(store, "android-studio-releases", loc, _CONTENT_HTML_V1, lastmod)
		for loc, lastmod in (
			("https://developer.android.com/studio/releases/emulator", "2026-06-12"),
			("https://developer.android.com/studio/releases/emulator/34-1", "2026-06-13"),
		):
			seed_baseline(store, "emulator", loc, _CONTENT_HTML_V1, lastmod)

		# --- advanced lastmods + changed content → every claimed URL fires ---
		fetcher2a = CountingFetcher(_nested_routes_v2())
		studio_changes = await det.detect(studio_src(), store, fetcher2a)
		fetcher2b = CountingFetcher(_nested_routes_v2())
		emulator_changes = await det.detect(_emulator_src(), store, fetcher2b)

	studio_urls = {c.url for c in studio_changes}
	emulator_urls = {c.url for c in emulator_changes}

	# Emulator URLs must appear ONLY under the emulator source.
	emulator_sitemap_urls = {
		"https://developer.android.com/studio/releases/emulator",
		"https://developer.android.com/studio/releases/emulator/34-1",
	}
	assert emulator_sitemap_urls <= emulator_urls, (
		f"emulator source missed its own URLs: {emulator_sitemap_urls - emulator_urls}"
	)
	double_claimed = studio_urls & emulator_sitemap_urls
	assert not double_claimed, (
		f"android-studio-releases double-claimed emulator URLs: {double_claimed}"
	)

	# Studio source still claims non-emulator /studio/releases/... URLs.
	studio_only_urls = {
		"https://developer.android.com/studio/releases",
		"https://developer.android.com/studio/releases/gradle-plugin",
	}
	assert studio_only_urls <= studio_urls, (
		f"android-studio-releases missed its own URLs: {studio_only_urls - studio_urls}"
	)

	# No URL claimed by both sources.
	assert not (studio_urls & emulator_urls), f"Double-claimed URLs: {studio_urls & emulator_urls}"
