from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.group import group_changes, heuristic_prefix
from android_watcher.models import Change, Source


def _config():
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def _sources():
	return {
		"src": Source(
			id="src",
			name="S",
			category="platform-release",
			detector="android_sitemap",
			url="https://x",
		)
	}


def test_model_keys_group_together():
	changes = [
		Change(
			source_id="src",
			url="u1",
			change_kind="updated",
			title="GKI a",
			group_key="gki",
			group_summary="GKI builds",
		),
		Change(
			source_id="src",
			url="u2",
			change_kind="updated",
			title="GKI b",
			group_key="gki",
			group_summary="GKI builds",
		),
		Change(source_id="src", url="u3", change_kind="updated", title="CTS", group_key="cts"),
	]
	groups = group_changes(changes, _sources(), _config())
	keys = sorted(g.key for g in groups)
	assert keys == ["src::cts", "src::gki"]
	gki = next(g for g in groups if g.key == "src::gki")
	assert gki.page_count == 2
	assert gki.summary == "GKI builds"


def test_heuristic_groups_when_no_model_key():
	changes = [
		Change(source_id="src", url="u1", change_kind="updated", title="Android 13 release builds"),
		Change(source_id="src", url="u2", change_kind="updated", title="Android 14 release builds"),
	]
	groups = group_changes(changes, _sources(), _config())
	# Same heuristic prefix -> one group of two.
	assert len(groups) == 1
	assert groups[0].page_count == 2


def test_group_headline_prefers_model_group_title():
	changes = [
		Change(
			source_id="src",
			url="u1",
			change_kind="updated",
			title="android13-5.15 release builds",
			group_key="gki",
			group_title="GKI Release Builds",
		),
		Change(
			source_id="src",
			url="u2",
			change_kind="updated",
			title="android14-6.1 release builds",
			group_key="gki",
			group_title="GKI Release Builds",
		),
	]
	groups = group_changes(changes, _sources(), _config())
	assert groups[0].title == "GKI Release Builds"  # model headline, not a member page title


def test_group_summary_falls_back_to_member_description():
	changes = [
		Change(
			source_id="src",
			url="u1",
			change_kind="updated",
			title="Android Studio Nightly",
			group_key="studio",
			group_summary=None,
			description="A new nightly build was published.",
		),
	]
	groups = group_changes(changes, _sources(), _config())
	assert groups[0].summary == "A new nightly build was published."


def test_heuristic_prefix_stops_at_digits():
	assert heuristic_prefix("Android 13 release builds") == heuristic_prefix(
		"Android 99 release builds"
	)
