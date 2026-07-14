"""Tests for the content detector."""

from __future__ import annotations

import pathlib

import pytest

from android_watcher.detect._normalize import EMPTY_RENDER_THRESHOLD
from android_watcher.detect.content import ContentDetector
from android_watcher.models import FetchResult, Source
from android_watcher.store import Snapshot

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def read(name: str) -> str:
	return (FIXTURES / name).read_text()


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
			fetched_at=None,  # type: ignore[arg-type]
			content_text=content_text,
		)


class FakeFetcher:
	def __init__(self, body: str) -> None:
		self.body = body

	async def fetch(self, url: str, **_kwargs: object) -> FetchResult:
		return FetchResult(url=url, status=200, text=self.body)


def src() -> Source:
	return Source(
		id="aosp",
		name="AOSP",
		category="platform-release",
		detector="content",
		url="https://source.android.com/x",
		content_selector="#content",
	)


# ---------------------------------------------------------------------------
# Baseline (first run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_run_baselines_silently() -> None:
	store = FakeStore()
	changes = await ContentDetector().detect(src(), store, FakeFetcher(read("content_before.html")))
	assert changes == []
	assert store.get_snapshot("aosp", "https://source.android.com/x") is not None


# ---------------------------------------------------------------------------
# Cosmetic-only change (CSS classes, whitespace): hash must not move
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chrome_only_change_yields_no_signal() -> None:
	store = FakeStore()
	det = ContentDetector()
	await det.detect(src(), store, FakeFetcher(read("content_before.html")))
	changes = await det.detect(src(), store, FakeFetcher(read("content_after_chrome_only.html")))
	assert changes == []


# ---------------------------------------------------------------------------
# Real content change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_content_change_yields_updated() -> None:
	store = FakeStore()
	det = ContentDetector()
	await det.detect(src(), store, FakeFetcher(read("content_before.html")))
	changes = await det.detect(src(), store, FakeFetcher(read("content_after_real_change.html")))
	assert len(changes) == 1
	assert changes[0].change_kind == "updated"
	assert changes[0].source_id == "aosp"
	assert changes[0].url == "https://source.android.com/x"


@pytest.mark.asyncio
async def test_real_change_raw_diff_is_unified_diff() -> None:
	store = FakeStore()
	det = ContentDetector()
	await det.detect(src(), store, FakeFetcher(read("content_before.html")))
	changes = await det.detect(src(), store, FakeFetcher(read("content_after_real_change.html")))
	assert len(changes) == 1
	diff = changes[0].raw_diff
	# A unified diff has lines starting with +/- for changed content
	assert any(line.startswith("-") for line in diff.splitlines())
	assert any(line.startswith("+") for line in diff.splitlines())


@pytest.mark.asyncio
async def test_no_change_when_content_identical() -> None:
	store = FakeStore()
	det = ContentDetector()
	html = read("content_before.html")
	await det.detect(src(), store, FakeFetcher(html))
	changes = await det.detect(src(), store, FakeFetcher(html))
	assert changes == []


# ---------------------------------------------------------------------------
# Empty-render guard (JS shell)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_js_shell_trips_empty_render_guard_and_does_not_baseline(
	caplog: pytest.LogCaptureFixture,
) -> None:
	store = FakeStore()
	with caplog.at_level("WARNING", logger="android_watcher.detect.content"):
		changes = await ContentDetector().detect(
			src(), store, FakeFetcher(read("content_js_shell.html"))
		)
	# Health, not changes: no Change emitted
	assert changes == []
	# No snapshot written (not baselined)
	assert store.get_snapshot("aosp", "https://source.android.com/x") is None
	# Warning logged
	assert any(
		"empty" in r.message.lower() or str(EMPTY_RENDER_THRESHOLD) in r.message
		for r in caplog.records
	)


@pytest.mark.asyncio
async def test_not_modified_returns_empty() -> None:
	"""A 304 response short-circuits with no changes and no store writes."""

	class NotModifiedFetcher:
		async def fetch(self, url: str, **_kwargs: object) -> FetchResult:
			return FetchResult(url=url, status=304, text="", not_modified=True)

	store = FakeStore()
	changes = await ContentDetector().detect(src(), store, NotModifiedFetcher())
	assert changes == []
	assert store.get_snapshot("aosp", "https://source.android.com/x") is None


# ---------------------------------------------------------------------------
# No selector: whole body used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_selector_uses_full_body() -> None:
	src_no_sel = Source(
		id="aosp",
		name="AOSP",
		category="platform-release",
		detector="content",
		url="https://source.android.com/x",
		content_selector="",
	)
	store = FakeStore()
	changes = await ContentDetector().detect(
		src_no_sel, store, FakeFetcher(read("content_before.html"))
	)
	assert changes == []
	assert store.get_snapshot("aosp", "https://source.android.com/x") is not None
