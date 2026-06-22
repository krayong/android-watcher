"""Tests for doctor.py: run_doctor health checks.

Exercises the guarantees: stale prefix => ok=False; healthy prefix => ok=True;
AI present/missing/off; schedule soft-dep absent degrades gracefully;
schedule present returns its Check; sitemap unavailable path.
"""

from __future__ import annotations

from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.models import Check, Source

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_config(*, ai_mode="off", custom_sources=None, enabled_ids=None):
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(mode=ai_mode),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=custom_sources or [],
		enabled_source_ids=enabled_ids or set(),
	)


def sitemap_source(prefix="/guide/"):
	return Source(
		id="custom-sitemap",
		name="Custom Sitemap",
		category="guides",
		detector="android_sitemap",
		url="https://developer.android.com/sitemap.xml",
		enabled=True,
		path_prefix=prefix,
	)


# ---------------------------------------------------------------------------
# AI backend checks
# ---------------------------------------------------------------------------


def test_ai_present_claude_cli(monkeypatch):
	import android_watcher.doctor as doc

	monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude")
	config = make_config(ai_mode="claude_cli")
	check = doc._check_ai(config)
	assert check.name == "ai-backend"
	assert check.ok is True
	assert "claude" in check.detail.lower()


def test_ai_missing_claude_cli(monkeypatch):
	import android_watcher.doctor as doc

	monkeypatch.setattr("shutil.which", lambda name: None)
	config = make_config(ai_mode="claude_cli")
	check = doc._check_ai(config)
	assert check.name == "ai-backend"
	assert check.ok is False
	assert "path" in check.detail.lower() or "not found" in check.detail.lower()


def test_ai_off_mode(monkeypatch):
	import android_watcher.doctor as doc

	config = make_config(ai_mode="off")
	check = doc._check_ai(config)
	assert check.name == "ai-backend"
	assert check.ok is True
	assert "disabled" in check.detail.lower()


# ---------------------------------------------------------------------------
# Schedule soft-dep
# ---------------------------------------------------------------------------


def test_schedule_soft_dep_absent(monkeypatch):
	"""ImportError from schedule module => ok=False, detail explains unavailability."""
	import android_watcher.doctor as doc

	original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

	def _failing_import(name, *args, **kwargs):
		if name == "android_watcher.schedule":
			raise ImportError("not installed")
		return original_import(name, *args, **kwargs)

	monkeypatch.setattr("builtins.__import__", _failing_import)
	check = doc._check_schedule()
	assert check.name == "schedule"
	assert check.ok is False
	assert "unavailable" in check.detail.lower() or "module" in check.detail.lower()


def test_schedule_soft_dep_present(monkeypatch):
	"""When schedule_status is importable, its Check is returned."""
	import types

	import android_watcher.doctor as doc

	fake_schedule = types.ModuleType("android_watcher.schedule")
	fake_schedule.schedule_status = lambda: Check("schedule", True, "launchd loaded")

	import sys

	monkeypatch.setitem(sys.modules, "android_watcher.schedule", fake_schedule)
	check = doc._check_schedule()
	assert check.name == "schedule"
	assert check.ok is True
	assert "loaded" in check.detail


# ---------------------------------------------------------------------------
# Prefix checks
# ---------------------------------------------------------------------------


def test_prefix_resolves(monkeypatch):
	"""Sitemap entries match prefix => ok=True."""
	import android_watcher.doctor as doc

	src = sitemap_source(prefix="/guide/")
	monkeypatch.setattr("android_watcher.doctor.resolve_sources", lambda config: [src])

	matching = [
		("https://developer.android.com/guide/topics/manifest", "2024-01-01"),
		("https://developer.android.com/guide/app-basics", "2024-01-01"),
	]
	monkeypatch.setattr("android_watcher.doctor._load_sitemap_entries", lambda *_a: matching)

	config = make_config()
	checks = doc._check_prefixes(config)

	assert len(checks) == 1
	assert checks[0].name == "prefix:/guide/"
	assert checks[0].ok is True
	assert "2" in checks[0].detail  # resolves (2 URLs)


def test_prefix_stale(monkeypatch):
	"""No entries match prefix => ok=False, mentions stale/0."""
	import android_watcher.doctor as doc

	src = sitemap_source(prefix="/studio/")
	monkeypatch.setattr("android_watcher.doctor.resolve_sources", lambda config: [src])

	# Entries exist but none match /studio/
	no_match = [
		("https://developer.android.com/guide/topics/manifest", "2024-01-01"),
	]
	monkeypatch.setattr("android_watcher.doctor._load_sitemap_entries", lambda *_a: no_match)

	config = make_config()
	checks = doc._check_prefixes(config)

	assert len(checks) == 1
	assert checks[0].name == "prefix:/studio/"
	assert checks[0].ok is False
	assert "stale" in checks[0].detail.lower() or "0" in checks[0].detail


def test_prefix_sitemap_unavailable(monkeypatch):
	"""Exception from _load_sitemap_entries => ok=False with 'sitemap unavailable'."""
	import android_watcher.doctor as doc

	src = sitemap_source(prefix="/guide/")
	monkeypatch.setattr("android_watcher.doctor.resolve_sources", lambda config: [src])

	def _boom(*_a):
		raise RuntimeError("offline")

	monkeypatch.setattr("android_watcher.doctor._load_sitemap_entries", _boom)

	config = make_config()
	checks = doc._check_prefixes(config)

	assert len(checks) == 1
	assert checks[0].ok is False
	assert "unavailable" in checks[0].detail.lower()


def test_no_sitemap_sources_returns_no_prefix_checks(monkeypatch):
	"""When no android_sitemap sources are watched, no prefix checks are emitted."""
	import android_watcher.doctor as doc

	non_sitemap = Source(
		id="feed-src",
		name="Feed",
		category="news",
		detector="feed",
		url="https://example.com/feed.xml",
		enabled=True,
	)
	monkeypatch.setattr("android_watcher.doctor.resolve_sources", lambda config: [non_sitemap])

	config = make_config()
	checks = doc._check_prefixes(config)
	assert checks == []


# ---------------------------------------------------------------------------
# run_doctor integration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Seed check
# ---------------------------------------------------------------------------


def test_seed_check_unseeded(monkeypatch, tmp_path):
	import android_watcher.doctor as doc

	monkeypatch.setattr("android_watcher.doctor.db_path", lambda: str(tmp_path / "state.db"))
	check = doc._check_seed()
	assert check.name == "seed"
	assert check.ok is True
	assert "no baseline" in check.detail.lower()


def test_seed_check_reports_date_and_count(monkeypatch, tmp_path):
	import android_watcher.doctor as doc
	from android_watcher.store import Store

	db = tmp_path / "state.db"
	store = Store(str(db))
	store.migrate()
	store.upsert_snapshot(
		"s1", "https://x/a", signal_type="content", content_hash="h", lastmod="", excerpt=""
	)
	store.import_seed_sql(
		"INSERT OR IGNORE INTO run_state (key, value) VALUES ('seed_date', '2026-06-21');"
	)
	store.close()

	monkeypatch.setattr("android_watcher.doctor.db_path", lambda: str(db))
	check = doc._check_seed()
	assert check.ok is True
	assert "2026-06-21" in check.detail
	assert "1" in check.detail


def test_run_doctor_returns_all_check_types(monkeypatch, tmp_path):
	"""run_doctor emits prefix, seed, ai-backend, and schedule checks."""
	import sys
	import types

	import android_watcher.doctor as doc

	src = sitemap_source(prefix="/guide/")
	monkeypatch.setattr("android_watcher.doctor.resolve_sources", lambda config: [src])
	monkeypatch.setattr(
		"android_watcher.doctor._load_sitemap_entries",
		lambda *_a: [("https://developer.android.com/guide/x", "2024")],
	)
	monkeypatch.setattr("android_watcher.doctor.db_path", lambda: str(tmp_path / "state.db"))
	monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")

	fake_schedule = types.ModuleType("android_watcher.schedule")
	fake_schedule.schedule_status = lambda: Check("schedule", True, "ok")
	monkeypatch.setitem(sys.modules, "android_watcher.schedule", fake_schedule)

	config = make_config(ai_mode="claude_cli")
	checks = doc.run_doctor(config)

	names = [c.name for c in checks]
	assert any(n.startswith("prefix:") for n in names)
	assert "seed" in names
	assert "ai-backend" in names
	assert "schedule" in names
