import pathlib

from android_watcher.detect.feed import FeedDetector
from android_watcher.models import FetchResult, Source

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def read(name: str) -> str:
	return (FIX / name).read_text()


class FakeStore:
	def __init__(self) -> None:
		self.seen: dict[tuple[str, str], str] = {}

	def seen_feed_item(self, source_id: str, item_id: str) -> str | None:
		return self.seen.get((source_id, item_id))

	def upsert_seen_feed_item(self, source_id: str, item_id: str, content_hash: str) -> None:
		self.seen[(source_id, item_id)] = content_hash


class FakeFetcher:
	def __init__(self, body: str) -> None:
		self.body = body

	async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
		return FetchResult(url=url, status=200, text=self.body)


def src() -> Source:
	return Source(
		id="blog",
		name="Blog",
		category="dev-blog",
		detector="feed",
		url="https://blog.example.com/feed.xml",
	)


async def test_first_run_all_new_and_persists():
	store = FakeStore()
	det = FeedDetector()
	changes = await det.detect(src(), store, FakeFetcher(read("feed_initial.xml")))
	assert {c.change_kind for c in changes} == {"new"}
	assert len(changes) == 2
	# seen-set persisted for both
	assert store.seen_feed_item("blog", "https://blog.example.com/post-a")
	assert store.seen_feed_item("blog", "https://blog.example.com/post-b")
	# raw_diff includes both title and summary text
	post_a = next(c for c in changes if "post-a" in c.url)
	assert "Post A" in post_a.raw_diff
	assert "First version of A." in post_a.raw_diff


async def test_unchanged_items_yield_nothing():
	store = FakeStore()
	det = FeedDetector()
	f = FakeFetcher(read("feed_initial.xml"))
	await det.detect(src(), store, f)
	again = await det.detect(src(), store, f)
	assert again == []


async def test_updated_only_when_title_summary_hash_moves():
	store = FakeStore()
	det = FeedDetector()
	await det.detect(src(), store, FakeFetcher(read("feed_initial.xml")))
	changes = await det.detect(src(), store, FakeFetcher(read("feed_updated_summary.xml")))
	# Post A summary changed -> updated; Post B only <updated> bumped -> no change
	kinds = {c.url: c.change_kind for c in changes}
	assert kinds == {"https://blog.example.com/post-a": "updated"}


async def test_guid_reuse_uses_link_not_raw_guid():
	store = FakeStore()
	det = FeedDetector()
	changes = await det.detect(src(), store, FakeFetcher(read("feed_guid_reuse.xml")))
	assert len(changes) == 1 and changes[0].change_kind == "new"
	# identity is the normalized link (query stripped), not the raw guid
	assert store.seen_feed_item("blog", "https://medium.com/androiddevelopers/new-post")
	assert store.seen_feed_item("blog", "reused-guid-123") is None


async def test_not_modified_returns_empty():
	store = FakeStore()
	det = FeedDetector()

	class NotModifiedFetcher:
		async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
			return FetchResult(url=url, status=304, text="", not_modified=True)

	changes = await det.detect(src(), store, NotModifiedFetcher())
	assert changes == []


async def test_atom_id_used_verbatim_as_identity():
	"""An Atom <id> that is a tag: URI must not be URL-normalized; it IS the identity key."""
	xml = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:blogger.com,1999:blog-123.post-456</id>
    <title>Tagged post</title>
    <link href="https://example.com/tagged-post"/>
    <summary>Content here.</summary>
    <updated>2026-06-01T00:00:00Z</updated>
  </entry>
</feed>"""
	store = FakeStore()
	det = FeedDetector()
	changes = await det.detect(src(), store, FakeFetcher(xml))
	assert len(changes) == 1 and changes[0].change_kind == "new"
	# key stored under the verbatim tag: URI, not the normalized link
	assert store.seen_feed_item("blog", "tag:blogger.com,1999:blog-123.post-456")
	assert store.seen_feed_item("blog", "https://example.com/tagged-post") is None


async def test_feed_url_preferred_over_url():
	"""Source.feed_url should be fetched when set, not Source.url."""
	fetched_urls: list[str] = []

	class TrackingFetcher:
		async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
			fetched_urls.append(url)
			return FetchResult(url=url, status=200, text=read("feed_initial.xml"))

	source = Source(
		id="blog",
		name="Blog",
		category="dev-blog",
		detector="feed",
		url="https://blog.example.com/",
		feed_url="https://blog.example.com/feed.xml",
	)
	await FeedDetector().detect(source, FakeStore(), TrackingFetcher())
	assert fetched_urls == ["https://blog.example.com/feed.xml"]
