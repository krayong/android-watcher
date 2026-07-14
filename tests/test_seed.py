"""Seed import/export: round-trip, fresh-DB import, and run_once integration."""

from __future__ import annotations

import android_watcher.run as run_mod
import android_watcher.seed as seed_mod
from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.seed import apply_seed_if_empty
from android_watcher.store import Store


def _store(tmp_path, name="state.db"):
	store = Store(str(tmp_path / name))
	store.migrate()
	return store


def test_seed_export_excludes_content_text(tmp_path):
	"""The seed must not carry full page bodies (content_text).

	content_text is a local runtime cache used to diff a page against its prior
	version; it is not needed to detect changes (content_hash does that) and it
	bloats the shipped seed past GitHub's 100MB file limit and the wheel. On a
	seeded install it imports empty and self-heals on the first change per page.
	"""
	src = _store(tmp_path, "src.db")
	big_body = "UNIQUE_BODY_MARKER " * 1000
	src.upsert_snapshot(
		"s1",
		"https://developer.android.com/x",
		signal_type="sitemap",
		content_hash="h",
		lastmod="2026-06-20",
		excerpt="short excerpt",
		content_text=big_body,
	)
	sql = src.export_seed_sql("2026-06-21")
	src.close()

	# The full body must not appear anywhere in the seed.
	assert "UNIQUE_BODY_MARKER" not in sql
	assert "content_text" not in sql

	# Round-trip still works: hash + excerpt survive, content_text imports empty.
	dst = _store(tmp_path, "dst.db")
	dst.import_seed_sql(sql)
	snap = dst.get_snapshot("s1", "https://developer.android.com/x")
	assert snap is not None
	assert snap.content_hash == "h"
	assert snap.excerpt == "short excerpt"
	assert snap.content_text == ""


def test_export_import_roundtrip(tmp_path):
	src = _store(tmp_path, "src.db")
	src.upsert_snapshot(
		"s1",
		"https://developer.android.com/about/versions/15",
		signal_type="sitemap",
		content_hash="abc123",
		lastmod="2026-06-20",
		excerpt="release notes",
	)
	src.upsert_seen_feed_item("feed1", "item-1", "hash-1")
	src.http_cache_put("https://developer.android.com/sitemap.xml", "etag-x", "Mon, 01 Jan")

	sql = src.export_seed_sql("2026-06-21")
	src.close()

	dst = _store(tmp_path, "dst.db")
	dst.import_seed_sql(sql)

	assert dst.snapshot_count() == 1
	snap = dst.get_snapshot("s1", "https://developer.android.com/about/versions/15")
	assert snap is not None
	assert snap.content_hash == "abc123"
	assert snap.lastmod == "2026-06-20"
	assert dst.seen_feed_item("feed1", "item-1") == "hash-1"
	assert dst.http_cache_get("https://developer.android.com/sitemap.xml") == (
		"etag-x",
		"Mon, 01 Jan",
	)
	assert dst.seed_date() == "2026-06-21"


def test_export_escapes_single_quotes(tmp_path):
	src = _store(tmp_path, "src.db")
	src.upsert_snapshot(
		"s1",
		"https://x/it's-fine",
		signal_type="content",
		content_hash="h",
		lastmod="",
		excerpt="don't break",
	)
	sql = src.export_seed_sql("2026-06-21")
	src.close()

	dst = _store(tmp_path, "dst.db")
	dst.import_seed_sql(sql)
	snap = dst.get_snapshot("s1", "https://x/it's-fine")
	assert snap is not None and snap.excerpt == "don't break"


def test_export_strips_control_chars_so_import_survives(tmp_path):
	# Page excerpts can carry a NUL (or other control byte); executescript runs
	# SQL as a C string and rejects an embedded null. The serializer must strip
	# them so the dump always imports.
	src = _store(tmp_path, "ctrl_src.db")
	src.upsert_snapshot(
		"s1",
		"https://x/a",
		signal_type="content",
		content_hash="h",
		lastmod="",
		excerpt="before\x00after\x07ctrl",
	)
	sql = src.export_seed_sql("2026-06-21")
	src.close()
	assert "\x00" not in sql

	dst = _store(tmp_path, "ctrl_dst.db")
	dst.import_seed_sql(sql)  # must not raise ValueError: embedded null character
	snap = dst.get_snapshot("s1", "https://x/a")
	assert snap is not None and snap.excerpt == "beforeafterctrl"


def test_apply_seed_imports_into_empty_db(tmp_path, monkeypatch):
	seed = _store(tmp_path, "seed.db")
	seed.upsert_snapshot(
		"s1", "https://x/a", signal_type="content", content_hash="h", lastmod="", excerpt=""
	)
	sql = seed.export_seed_sql("2026-06-21")
	seed.close()

	monkeypatch.setattr(seed_mod, "bundled_seed_sql", lambda: sql)
	dst = _store(tmp_path, "dst.db")
	date = apply_seed_if_empty(dst)

	assert date == "2026-06-21"
	assert dst.snapshot_count() == 1


def test_apply_seed_skips_when_snapshots_exist(tmp_path, monkeypatch):
	# A populated DB must never be overwritten by the seed.
	called = {"n": 0}

	def _boom():
		called["n"] += 1
		return "INSERT OR IGNORE INTO snapshots VALUES ('z','z','content','z','','','2026');"

	monkeypatch.setattr(seed_mod, "bundled_seed_sql", _boom)
	dst = _store(tmp_path, "dst.db")
	dst.upsert_snapshot(
		"s1", "https://x/a", signal_type="content", content_hash="real", lastmod="", excerpt=""
	)

	assert apply_seed_if_empty(dst) is None
	assert called["n"] == 0  # short-circuits before even loading the seed
	assert dst.snapshot_count() == 1


def test_apply_seed_noop_without_bundle(tmp_path, monkeypatch):
	monkeypatch.setattr(seed_mod, "bundled_seed_sql", lambda: None)
	dst = _store(tmp_path, "dst.db")
	assert apply_seed_if_empty(dst) is None
	assert dst.snapshot_count() == 0


def _zero_channel_config():
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(mode="off"),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(enabled=False),
		slack=SlackChannel(enabled=False),
		telegram=TelegramChannel(enabled=False),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def test_run_once_imports_seed_on_fresh_db(tmp_path, monkeypatch):
	# A fresh DB on the first run imports the bundled seed before anything else.
	seed = _store(tmp_path, "seed.db")
	seed.upsert_snapshot(
		"s1", "https://x/a", signal_type="content", content_hash="h", lastmod="", excerpt=""
	)
	sql = seed.export_seed_sql("2026-06-21")
	seed.close()

	db = tmp_path / "state.db"
	monkeypatch.setattr(run_mod, "db_path", lambda: str(db))
	monkeypatch.setattr(run_mod, "data_path", lambda: str(tmp_path))
	monkeypatch.setattr(seed_mod, "bundled_seed_sql", lambda: sql)

	# Zero channels: run_once seeds, then short-circuits without detecting.
	run_mod.run_once(_zero_channel_config())

	check = Store(str(db))
	try:
		assert check.snapshot_count() == 1
		assert check.seed_date() == "2026-06-21"
	finally:
		check.close()
