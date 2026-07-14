"""Regression guard: confirm_candidate is one shared helper used by both sitemap
detectors.  If a refactor accidentally splits the helper, these tests break and
flag the divergence immediately.
"""

import pathlib

import pytest

from android_watcher.detect.sitemap import confirm_candidate
from android_watcher.models import FetchResult, Source
from android_watcher.store import Snapshot

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def read(name):
	return (FIX / name).read_text()


class FakeStore:
	def __init__(self):
		self.snaps = {}

	def get_snapshot(self, source_id, url):
		return self.snaps.get((source_id, url))

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


class OneFetcher:
	def __init__(self, body):
		self.body = body

	async def fetch(self, url, *, conditional=False):
		return FetchResult(url=url, status=200, text=self.body)


class RaisingFetcher:
	def __init__(self, exc):
		self.exc = exc

	async def fetch(self, url, *, conditional=False):
		raise self.exc


def src():
	return Source(
		id="s",
		name="S",
		category="guides",
		detector="sitemap",
		url="https://x/sitemap.xml",
		path_prefix="/",
		content_selector="#content",
	)


@pytest.mark.asyncio
async def test_confirm_baselines_then_detects_then_quiets():
	store = FakeStore()
	loc = "https://x/docs/a"
	# baseline -> None
	c0 = await confirm_candidate(
		src(), store, OneFetcher(read("content_before.html")), loc, "2026-06-10"
	)
	assert c0 is None
	# same content again -> None
	c1 = await confirm_candidate(
		src(), store, OneFetcher(read("content_before.html")), loc, "2026-06-11"
	)
	assert c1 is None
	# real change -> Change
	c2 = await confirm_candidate(
		src(), store, OneFetcher(read("content_after_real_change.html")), loc, "2026-06-20"
	)
	assert c2 is not None and c2.change_kind == "updated"


@pytest.mark.asyncio
async def test_confirm_uses_page_title_not_source_name():
	"""Change.title comes from the page <title>, not the source name (so a digest
	does not read 'S' for every page of a source)."""
	store = FakeStore()
	loc = "https://x/docs/gki"
	head = "<title>GKI Release Builds | Android Open Source Project</title>"

	def page(body: str) -> str:
		return f'<html><head>{head}</head><body><div id="content">{body}</div></body></html>'

	before = page("old body, long enough to clear the empty-render threshold here.")
	after = page("new body, also long enough and clearly different from before now.")
	await confirm_candidate(src(), store, OneFetcher(before), loc, "2026-06-10")  # baseline
	change = await confirm_candidate(src(), store, OneFetcher(after), loc, "2026-06-20")
	assert change is not None
	assert change.title == "GKI Release Builds | Android Open Source Project"
	assert change.title != src().name


@pytest.mark.asyncio
async def test_confirm_never_raises_on_robots_disallow():
	from android_watcher.models import Disallowed

	store = FakeStore()
	out = await confirm_candidate(
		src(), store, RaisingFetcher(Disallowed("https://x/a.pdf")), "https://x/a.pdf", "2026-06-20"
	)
	assert out is None  # robots-blocked URL is skipped, not raised


@pytest.mark.asyncio
async def test_confirm_never_raises_on_binary_content():
	# A binary body fed to the HTML parser throws AssertionError; confirm must
	# swallow it (one bad URL can't abort a source's run).
	store = FakeStore()
	binary = "<![>" + "".join(chr(c) for c in range(1, 30)) + "not html"
	out = await confirm_candidate(
		src(), store, OneFetcher(binary), "https://x/file.pdf", "2026-06-20"
	)
	assert out is None
