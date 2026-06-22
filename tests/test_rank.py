"""Tests for rank.py — scoring, weights, sort overrides, tie-break, caps, round-robin."""

from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.models import Change, Source
from android_watcher.rank import CATEGORY_WEIGHTS, DEFAULT_CATEGORY_WEIGHT, by_category, rank


def _config(max_items=10, sort=None):
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(max_items=max_items),
		sort=sort or {},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def _src(sid, category, default_weight=0):
	return Source(
		id=sid,
		name=sid,
		category=category,
		detector="content",
		url="https://x",
		default_weight=default_weight,
	)


# ---------------------------------------------------------------------------
# Task 4 — rank over groups, global cap, category ordering
# ---------------------------------------------------------------------------


def test_cap_selects_top_groups_rest_carried():
	sources = {f"s{i}": _src(f"s{i}", "guides") for i in range(12)}
	changes = [
		Change(
			source_id=f"s{i}",
			url=f"u{i}",
			change_kind="updated",
			title=f"t{i}",
			group_key=f"g{i}",
			verdict="substantive",
		)
		for i in range(12)
	]
	digest = rank(changes, sources, _config(max_items=10))
	assert len(digest.groups) == 12
	assert len(digest.message_groups()) == 10
	assert len(digest.carried_groups()) == 2


def test_grouped_pages_count_as_one_group():
	sources = {"s": _src("s", "platform-release")}
	changes = [
		Change(
			source_id="s",
			url=f"u{i}",
			change_kind="updated",
			title=f"GKI {i}",
			group_key="gki",
			verdict="substantive",
		)
		for i in range(7)
	]
	digest = rank(changes, sources, _config())
	assert len(digest.groups) == 1
	assert digest.groups[0].page_count == 7
	assert digest.change_count() == 7


def test_by_category_orders_groups():
	sources = {"a": _src("a", "platform-release"), "b": _src("b", "guides")}
	changes = [
		Change(
			source_id="a",
			url="ua",
			change_kind="updated",
			title="A",
			group_key="ga",
			verdict="substantive",
		),
		Change(
			source_id="b",
			url="ub",
			change_kind="updated",
			title="B",
			group_key="gb",
			verdict="substantive",
		),
	]
	digest = rank(changes, sources, _config())
	cats = [cid for cid, _label, _g in by_category(digest.groups)]
	assert cats.index("platform-release") < cats.index("guides")


def test_only_substantive_ranked():
	sources = {
		"plat": _src("plat", "platform-release"),
		"guide": _src("guide", "guides"),
	}
	changes = [
		Change(source_id="plat", url="p", change_kind="new", group_key="p", verdict="cosmetic"),
		Change(source_id="guide", url="g", change_kind="new", group_key="g", verdict="substantive"),
	]
	d = rank(changes, sources, _config())
	assert len(d.groups) == 1
	assert d.groups[0].source_id == "guide"


def test_score_drives_group_order():
	sources = {
		"plat": _src("plat", "platform-release"),
		"guide": _src("guide", "guides"),
	}
	changes = [
		Change(source_id="guide", url="g", change_kind="new", group_key="g", verdict="substantive"),
		Change(source_id="plat", url="p", change_kind="new", group_key="p", verdict="substantive"),
	]
	digest = rank(changes, sources, _config())
	assert digest.groups[0].source_id == "plat"
	assert digest.groups[0].score == CATEGORY_WEIGHTS["platform-release"]


def test_default_weight_overrides_category():
	sources = {"weighted": _src("weighted", "guides", default_weight=999)}
	changes = [
		Change(
			source_id="weighted",
			url="w",
			change_kind="new",
			group_key="w",
			verdict="substantive",
		)
	]
	digest = rank(changes, sources, _config())
	assert digest.groups[0].score == 999


def test_sort_override_by_source_id():
	sources = {"guide": _src("guide", "guides")}
	changes = [
		Change(source_id="guide", url="g", change_kind="new", group_key="g", verdict="substantive")
	]
	d = rank(changes, sources, _config(sort={"guide": 1000}))
	assert d.groups[0].score == CATEGORY_WEIGHTS["guides"] + 1000


def test_source_id_override_takes_precedence_over_category():
	sources = {"guide": _src("guide", "guides")}
	changes = [
		Change(source_id="guide", url="g", change_kind="new", group_key="g", verdict="substantive")
	]
	d = rank(changes, sources, _config(sort={"guide": 500, "guides": 1}))
	assert d.groups[0].score == CATEGORY_WEIGHTS["guides"] + 500


def test_category_override_applied_when_no_source_id_override():
	sources = {"guide": _src("guide", "guides")}
	changes = [
		Change(source_id="guide", url="g", change_kind="new", group_key="g", verdict="substantive")
	]
	d = rank(changes, sources, _config(sort={"guides": 200}))
	assert d.groups[0].score == CATEGORY_WEIGHTS["guides"] + 200


def test_unknown_category_uses_default_weight():
	sources = {"weird": _src("weird", "nope")}
	changes = [
		Change(source_id="weird", url="x", change_kind="new", group_key="x", verdict="substantive")
	]
	d = rank(changes, sources, _config())
	assert d.groups[0].score == DEFAULT_CATEGORY_WEIGHT


def test_unknown_source_id_uses_default_weight_no_override():
	# source_id not in sources dict at all => DEFAULT_CATEGORY_WEIGHT, no override.
	changes = [
		Change(source_id="ghost", url="x", change_kind="new", group_key="x", verdict="substantive")
	]
	d = rank(changes, {}, _config(sort={"ghost": 9000}))
	assert d.groups[0].score == DEFAULT_CATEGORY_WEIGHT


def test_is_empty_when_no_changes():
	digest = rank([], {}, _config())
	assert digest.is_empty


def test_by_category_unknown_falls_to_other():
	sources = {"z": _src("z", "unknown-category")}
	changes = [
		Change(
			source_id="z",
			url="uz",
			change_kind="updated",
			title="Z",
			group_key="gz",
			verdict="substantive",
		)
	]
	digest = rank(changes, sources, _config())
	cats = [cid for cid, _label, _g in by_category(digest.groups)]
	assert "other" in cats


# ---------------------------------------------------------------------------
# Task 9 — scoring, weights, sort overrides, tie-break
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task 10 — per-source cap, round-robin fill, overflow collapse
# ---------------------------------------------------------------------------
