from datetime import UTC, datetime

import pytest

from android_watcher.models import Change
from android_watcher.store import Snapshot, Store


@pytest.fixture()
def store(tmp_path):
	s = Store(str(tmp_path / "state.db"))
	s.migrate()
	return s


def test_migrate_creates_all_tables(store):
	rows = store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
	names = {r[0] for r in rows}
	assert {
		"snapshots",
		"changes",
		"deliveries",
		"digests",
		"seen_feed_items",
		"http_cache",
		"run_state",
	} <= names


def test_migrate_is_idempotent(tmp_path):
	s = Store(str(tmp_path / "state.db"))
	s.migrate()
	s.migrate()  # must not raise


def test_snapshot_roundtrip(store):
	assert store.get_snapshot("src", "https://e/p") is None
	store.upsert_snapshot(
		"src",
		"https://e/p",
		signal_type="content",
		content_hash="abc",
		lastmod="2026-06-01",
		excerpt="hello",
	)
	snap = store.get_snapshot("src", "https://e/p")
	assert isinstance(snap, Snapshot)
	assert snap.content_hash == "abc"
	assert snap.signal_type == "content"
	assert snap.excerpt == "hello"
	assert isinstance(snap.fetched_at, datetime)

	# upsert overwrites
	store.upsert_snapshot(
		"src",
		"https://e/p",
		signal_type="content",
		content_hash="def",
		lastmod="2026-06-02",
		excerpt="world",
	)
	assert store.get_snapshot("src", "https://e/p").content_hash == "def"


def test_change_record_and_digest_query(store):
	channels = {"email", "slack"}
	c = Change(source_id="src", url="https://e/p", change_kind="new", title="T")
	cid = store.record_change(c)
	assert isinstance(cid, int)
	assert c.id == cid

	# Not in digest until marked substantive.
	assert store.changes_for_digest(channels) == []

	store.set_verdict(cid, "substantive", "a description")
	pending = store.changes_for_digest(channels)
	assert len(pending) == 1
	assert pending[0].id == cid
	assert pending[0].verdict == "substantive"
	assert pending[0].description == "a description"

	# Cosmetic changes never appear.
	c2 = Change(source_id="src", url="https://e/q", change_kind="updated")
	cid2 = store.record_change(c2)
	store.set_verdict(cid2, "cosmetic", None)
	ids = {c.id for c in store.changes_for_digest(channels)}
	assert ids == {cid}


def test_changes_for_digest_empty_channels_returns_empty(store):
	# CONTRACTS: an empty channel set yields [] (no channel => nothing to send).
	c = Change(source_id="src", url="https://e/p", change_kind="new", fetched_hash="h1")
	cid = store.record_change(c)
	store.set_verdict(cid, "substantive", "d")
	assert store.changes_for_digest(set()) == []


def test_changes_for_digest_one_row_per_source_url(store):
	# Same (source_id, url) detected twice before delivery => the digest sees
	# only the LATEST row, never the stale earlier one.
	older = Change(
		source_id="src",
		url="https://e/p",
		change_kind="updated",
		fetched_hash="old",
		detected_at=datetime(2026, 6, 1, tzinfo=UTC),
	)
	oid = store.record_change(older)
	store.set_verdict(oid, "substantive", "old text")

	newer = Change(
		source_id="src",
		url="https://e/p",
		change_kind="updated",
		fetched_hash="new",
		detected_at=datetime(2026, 6, 2, tzinfo=UTC),
	)
	nid = store.record_change(newer)
	store.set_verdict(nid, "substantive", "new text")

	pending = store.changes_for_digest({"email"})
	assert [c.id for c in pending] == [nid]  # only the latest, exactly once


def test_supersede_older_excludes_stale_rows(store):
	older = Change(
		source_id="src",
		url="https://e/p",
		change_kind="updated",
		fetched_hash="old",
		detected_at=datetime(2026, 6, 1, tzinfo=UTC),
	)
	oid = store.record_change(older)
	store.set_verdict(oid, "substantive", "old text")

	newer = Change(
		source_id="src",
		url="https://e/p",
		change_kind="updated",
		fetched_hash="new",
		detected_at=datetime(2026, 6, 2, tzinfo=UTC),
	)
	nid = store.record_change(newer)
	store.set_verdict(nid, "substantive", "new text")

	store.supersede_older("src", "https://e/p", keep_id=nid)

	# The older row is now marked superseded; the kept row is not.
	assert (
		store._conn.execute("SELECT superseded FROM changes WHERE id = ?", (oid,)).fetchone()[
			"superseded"
		]
		== 1
	)
	assert (
		store._conn.execute("SELECT superseded FROM changes WHERE id = ?", (nid,)).fetchone()[
			"superseded"
		]
		== 0
	)
	# Digest still returns exactly the kept row.
	assert [c.id for c in store.changes_for_digest({"email"})] == [nid]


def test_supersede_older_leaves_delivered_rows(store):
	# A row already delivered to a channel must NOT be retro-superseded, or its
	# delivery ledger would be lost.
	delivered = Change(source_id="src", url="https://e/q", change_kind="new", fetched_hash="d1")
	did = store.record_change(delivered)
	store.set_verdict(did, "substantive", "d")
	store.record_delivery(did, "email")

	newer = Change(source_id="src", url="https://e/q", change_kind="updated", fetched_hash="d2")
	nid = store.record_change(newer)
	store.set_verdict(nid, "substantive", "d2")

	store.supersede_older("src", "https://e/q", keep_id=nid)
	assert (
		store._conn.execute("SELECT superseded FROM changes WHERE id = ?", (did,)).fetchone()[
			"superseded"
		]
		== 0
	)


def test_set_verdict_is_write_once(store):
	c = Change(source_id="src", url="https://e/p", change_kind="new", fetched_hash="h1")
	cid = store.record_change(c)
	store.set_verdict(cid, "substantive", "first")
	# A second set_verdict on the same row is a no-op (verdict is final).
	store.set_verdict(cid, "cosmetic", "second")
	row = store._conn.execute(
		"SELECT verdict, description FROM changes WHERE id = ?", (cid,)
	).fetchone()
	assert row["verdict"] == "substantive"
	assert row["description"] == "first"


def test_record_change_is_idempotent(store):
	c = Change(source_id="src", url="https://e/p", change_kind="new", fetched_hash="abc123")
	cid = store.record_change(c)
	store.set_verdict(cid, "substantive", "kept")

	# Same (source_id, url, fetched_hash) -> same id, no new row, verdict intact.
	again = Change(source_id="src", url="https://e/p", change_kind="updated", fetched_hash="abc123")
	cid_again = store.record_change(again)
	assert cid_again == cid
	assert again.id == cid

	rows = store._conn.execute("SELECT COUNT(*) AS n FROM changes").fetchone()
	assert rows["n"] == 1

	# Re-recording did NOT wipe the previously-set verdict.
	row = store._conn.execute(
		"SELECT verdict, description FROM changes WHERE id = ?", (cid,)
	).fetchone()
	assert row["verdict"] == "substantive"
	assert row["description"] == "kept"

	# A different hash for the same url IS a new row (content actually changed).
	other = Change(source_id="src", url="https://e/p", change_kind="updated", fetched_hash="def456")
	cid_other = store.record_change(other)
	assert cid_other != cid


def test_record_change_idempotent_preserves_verdict(store):
	# A second record_change call after set_verdict must return the SAME id and
	# must NOT reset the verdict (ON CONFLICT DO NOTHING leaves the row intact).
	c = Change(source_id="src", url="https://e/p", change_kind="new", fetched_hash="xyz")
	cid = store.record_change(c)
	store.set_verdict(cid, "substantive", "original description")

	again = Change(source_id="src", url="https://e/p", change_kind="updated", fetched_hash="xyz")
	cid_again = store.record_change(again)

	assert cid_again == cid, "repeat call must return the existing row id"
	row = store._conn.execute(
		"SELECT verdict, description FROM changes WHERE id = ?", (cid,)
	).fetchone()
	assert row["verdict"] == "substantive", "verdict must not be reset"
	assert row["description"] == "original description", "description must not be reset"
	count = store._conn.execute("SELECT COUNT(*) AS n FROM changes").fetchone()["n"]
	assert count == 1, "no duplicate row must be inserted"


def test_changes_for_digest_is_delivery_aware(store):
	channels = {"email", "slack"}

	# Fully delivered change is excluded.
	delivered = Change(
		source_id="src", url="https://e/done", change_kind="new", fetched_hash="h-done"
	)
	did = store.record_change(delivered)
	store.set_verdict(did, "substantive", "d")
	store.record_delivery(did, "email")
	store.record_delivery(did, "slack")
	assert did not in {c.id for c in store.changes_for_digest(channels)}

	# Partially delivered change (only email) is still returned.
	partial = Change(
		source_id="src", url="https://e/partial", change_kind="new", fetched_hash="h-part"
	)
	pid = store.record_change(partial)
	store.set_verdict(pid, "substantive", "d")
	store.record_delivery(pid, "email")
	returned = {c.id for c in store.changes_for_digest(channels)}
	assert pid in returned
	assert did not in returned

	# Once slack also gets it, it drops out.
	store.record_delivery(pid, "slack")
	assert pid not in {c.id for c in store.changes_for_digest(channels)}


def test_delivery_tracking(store):
	c = Change(source_id="src", url="u", change_kind="new")
	cid = store.record_change(c)
	store.set_verdict(cid, "substantive", "d")

	assert store.delivered_channels(cid) == set()
	store.record_delivery(cid, "email")
	assert store.delivered_channels(cid) == {"email"}
	store.record_delivery(cid, "slack")
	assert store.delivered_channels(cid) == {"email", "slack"}
	# Idempotent re-record.
	store.record_delivery(cid, "email")
	assert store.delivered_channels(cid) == {"email", "slack"}


def test_inflight_digest_lifecycle(store):
	assert store.inflight_digest() is None
	did = store.open_digest()
	assert store.inflight_digest() == did
	store.commit_digest(did)
	assert store.inflight_digest() is None


def test_seen_feed_items(store):
	assert store.seen_feed_item("src", "item-1") is None
	store.upsert_seen_feed_item("src", "item-1", "h1")
	assert store.seen_feed_item("src", "item-1") == "h1"
	store.upsert_seen_feed_item("src", "item-1", "h2")
	assert store.seen_feed_item("src", "item-1") == "h2"


def test_http_cache(store):
	assert store.http_cache_get("https://e/s.xml") == ("", "")
	store.http_cache_put("https://e/s.xml", "etag-1", "Mon, 01 Jun 2026 00:00:00 GMT")
	assert store.http_cache_get("https://e/s.xml") == (
		"etag-1",
		"Mon, 01 Jun 2026 00:00:00 GMT",
	)
	store.http_cache_put("https://e/s.xml", "etag-2", "")
	assert store.http_cache_get("https://e/s.xml") == ("etag-2", "")


def test_run_state(store):
	assert store.last_successful_run() is None
	when = datetime(2026, 6, 20, 9, 0, 0, tzinfo=UTC)
	store.mark_successful_run(when)
	got = store.last_successful_run()
	assert got is not None
	assert got.year == 2026 and got.hour == 9


def test_datetime_roundtrip_is_utc_aware_and_comparable(store):
	when = datetime.now(UTC)
	store.mark_successful_run(when)
	got = store.last_successful_run()
	assert got is not None
	assert got.tzinfo is not None  # round-trips as aware, not naive
	# Comparable against another aware datetime without raising.
	assert got <= datetime.now(UTC)
	assert abs((got - when).total_seconds()) < 1.0


def test_close_and_context_manager(tmp_path):
	path = str(tmp_path / "state.db")
	with Store(path) as s:
		s.migrate()
		s.mark_successful_run(datetime.now(UTC))
	# After the context exits the connection is closed.
	import sqlite3

	with pytest.raises(sqlite3.ProgrammingError):
		s._conn.execute("SELECT 1")

	# close() is also directly callable and idempotent.
	s2 = Store(path)
	s2.close()
	s2.close()  # must not raise


def test_set_verdict_persists_group_fields(tmp_path):
	from android_watcher.models import Change
	from android_watcher.store import Store

	store = Store(tmp_path / "db.sqlite3")
	store.migrate()
	cid = store.record_change(
		Change(
			source_id="s", url="u", change_kind="updated", title="t", raw_diff="d", fetched_hash="h"
		)
	)
	store.set_verdict(cid, "substantive", "desc", group_key="gki", group_summary="GKI builds")

	got = store.changes_for_digest({"slack"})
	assert len(got) == 1
	assert got[0].group_key == "gki"
	assert got[0].group_summary == "GKI builds"


def test_set_verdict_group_fields_write_once(tmp_path):
	from android_watcher.models import Change
	from android_watcher.store import Store

	store = Store(tmp_path / "db.sqlite3")
	store.migrate()
	cid = store.record_change(
		Change(source_id="s", url="u", change_kind="updated", fetched_hash="h")
	)
	store.set_verdict(cid, "substantive", "d", group_key="first", group_summary="one")
	store.set_verdict(cid, "substantive", "d2", group_key="second", group_summary="two")
	got = store.changes_for_digest({"slack"})
	assert got[0].group_key == "first"  # write-once: second call is a no-op
