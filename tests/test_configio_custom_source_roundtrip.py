"""Round-trip test: a custom android_sitemap Source with a path_prefix
serializes via config_to_toml and loads back via load_config with all fields intact."""

from __future__ import annotations

from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
	load_config,
)
from android_watcher.models import Source
from android_watcher.tui.configio import write_config


def _config_with_custom(source: Source) -> Config:
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[source],
		enabled_source_ids=set(),
	)


def test_custom_android_sitemap_source_roundtrip(tmp_path):
	"""A custom android_sitemap Source round-trips through write_config/load_config."""
	src = Source(
		id="custom-1",
		name="Foo Sitemap",
		category="tooling",
		detector="android_sitemap",
		url="https://developer.android.com/foo",
		path_prefix="/foo",
	)
	cfg = _config_with_custom(src)
	out = str(tmp_path / "config.toml")
	write_config(cfg, out)

	loaded = load_config(out, expand=False)
	assert len(loaded.custom_sources) == 1
	got = loaded.custom_sources[0]
	assert got.id == "custom-1"
	assert got.name == "Foo Sitemap"
	assert got.category == "tooling"
	assert got.detector == "android_sitemap"
	assert got.url == "https://developer.android.com/foo"
	assert got.path_prefix == "/foo"
	assert got.feed_url == ""
	assert got.content_selector == ""
	assert got.default_weight == 0


def test_custom_feed_source_roundtrip(tmp_path):
	"""A custom feed Source round-trips with feed_url preserved."""
	src = Source(
		id="custom-2",
		name="My Feed",
		category="dev-blog",
		detector="feed",
		url="https://example.com",
		feed_url="https://example.com/feed.xml",
	)
	cfg = _config_with_custom(src)
	out = str(tmp_path / "config.toml")
	write_config(cfg, out)

	loaded = load_config(out, expand=False)
	got = loaded.custom_sources[0]
	assert got.detector == "feed"
	assert got.feed_url == "https://example.com/feed.xml"
	assert got.path_prefix == ""
	assert got.content_selector == ""
