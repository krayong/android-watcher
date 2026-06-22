"""Task 9: resolve_sources selection rules + isolated async detector driver."""

from __future__ import annotations

import asyncio

import pytest

from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.detect.base import DETECTORS
from android_watcher.models import Change, Source
from android_watcher.run import _detect_all, resolve_sources


def make_config(custom=None, enabled_ids=None):
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=custom or [],
		enabled_source_ids=enabled_ids or set(),
	)


def src(id_, *, enabled=True, detector="content"):
	return Source(
		id=id_,
		name=id_,
		category="guides",
		detector=detector,
		url=f"https://x/{id_}",
		enabled=enabled,
	)


# --- resolve_sources -------------------------------------------------------


def test_resolve_override_set_intersects_and_adds_custom(monkeypatch):
	catalog = [src("a"), src("b"), src("c")]
	monkeypatch.setattr("android_watcher.run.load_catalog", lambda: catalog)
	config = make_config(custom=[src("z")], enabled_ids={"a", "c"})

	ids = {s.id for s in resolve_sources(config)}

	assert ids == {"a", "c", "z"}


def test_resolve_override_cannot_revive_catalog_disabled(monkeypatch):
	# A catalog entry disabled by its own flag is excluded even if named in
	# enabled_source_ids (the override starts from catalog-enabled only).
	catalog = [src("a"), src("b", enabled=False)]
	monkeypatch.setattr("android_watcher.run.load_catalog", lambda: catalog)
	config = make_config(enabled_ids={"a", "b"})

	ids = {s.id for s in resolve_sources(config)}

	assert ids == {"a"}


def test_resolve_empty_selection_means_catalog_flags_not_none(monkeypatch):
	# Empty enabled_source_ids => use catalog `enabled` flags (NOT "watch nothing").
	catalog = [src("a"), src("b"), src("c", enabled=False)]
	monkeypatch.setattr("android_watcher.run.load_catalog", lambda: catalog)
	config = make_config(custom=[src("z")], enabled_ids=set())

	ids = {s.id for s in resolve_sources(config)}

	assert ids == {"a", "b", "z"}  # c dropped by its own flag; never empty


def test_resolve_custom_overrides_catalog_on_id_collision(monkeypatch):
	catalog = [src("a", detector="content")]
	monkeypatch.setattr("android_watcher.run.load_catalog", lambda: catalog)
	custom_a = Source(
		id="a", name="custom-a", category="news", detector="feed", url="https://custom/a"
	)
	config = make_config(custom=[custom_a])

	resolved = {s.id: s for s in resolve_sources(config)}

	assert resolved["a"].name == "custom-a"
	assert resolved["a"].detector == "feed"


# --- _detect_all -----------------------------------------------------------


def test_detect_all_isolates_per_source_failure():
	ch1 = Change(source_id="s1", url="u1", change_kind="new")
	ch2 = Change(source_id="s1", url="u2", change_kind="new")

	@DETECTORS.register("_fake_ok")
	class _OkDetector:
		async def detect(self, source, store, fetcher):
			return [ch1, ch2]

	@DETECTORS.register("_fake_boom")
	class _BoomDetector:
		async def detect(self, source, store, fetcher):
			raise RuntimeError("detector exploded")

	sources = [src("s1", detector="_fake_ok"), src("s2", detector="_fake_boom")]
	try:
		changes = asyncio.run(_detect_all(sources, store=object(), fetcher=object()))
	finally:
		DETECTORS._items.pop("_fake_ok", None)
		DETECTORS._items.pop("_fake_boom", None)

	# source2's exception is isolated; source1's two changes survive, run not aborted.
	assert changes == [ch1, ch2]


def test_wiring_triage_notify_packages_register_on_import():
	# Importing ONLY the package (not the concrete modules) must populate the
	# registries, so run_once can rely on them.
	import importlib

	import android_watcher.notify as notify_pkg
	import android_watcher.triage as triage_pkg

	importlib.reload(triage_pkg)
	importlib.reload(notify_pkg)

	from android_watcher.notify.base import NOTIFIERS
	from android_watcher.triage.base import TRIAGERS

	assert TRIAGERS.get("noop")() is not None
	assert TRIAGERS.get("claude_cli")() is not None
	assert NOTIFIERS.get("email")() is not None
	assert NOTIFIERS.get("slack")() is not None


if __name__ == "__main__":  # pragma: no cover
	raise SystemExit(pytest.main([__file__, "-v"]))
