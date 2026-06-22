from android_watcher.models import (
	AlreadyRunning,
	Change,
	Check,
	ConfigError,
	Digest,
	DigestGroup,
	Disallowed,
	FetchResult,
	NotifyError,
	Source,
)


def test_source_defaults_and_frozen():
	s = Source(id="x", name="X", category="guides", detector="content", url="https://e/")
	assert s.enabled is True
	assert s.path_prefix == ""
	assert s.default_weight == 0
	try:
		s.id = "y"  # frozen
	except Exception as exc:  # FrozenInstanceError subclasses Exception
		assert "cannot assign" in str(exc) or "frozen" in str(exc).lower()
	else:
		raise AssertionError("Source should be frozen")


def test_change_defaults():
	c = Change(source_id="x", url="https://e/p", change_kind="new")
	assert c.title == ""
	assert c.verdict is None
	assert c.description is None
	assert c.id is None
	assert c.fetched_hash == ""
	assert c.raw_diff == ""
	assert c.detected_at.tzinfo is not None  # timezone-aware


def test_digest_groups_and_empty():
	c = Change(source_id="x", url="https://e/p", change_kind="updated")
	g = DigestGroup(
		key="k1",
		title="T",
		summary=None,
		category="guides",
		source_id="x",
		change_kind="updated",
		members=[c],
		score=5,
	)

	empty = Digest(groups=[])
	assert empty.is_empty is True
	assert empty.ai_unavailable is None
	assert empty.tldr is None
	assert empty.generated_at.tzinfo is not None

	full = Digest(groups=[g])
	assert full.is_empty is False
	assert full.change_count() == 1

	# message_groups / carried_groups
	groups = [
		DigestGroup(
			key=f"k{i}",
			title="T",
			summary=None,
			category="c",
			source_id="s",
			change_kind="new",
			members=[Change(source_id="s", url=f"u{i}", change_kind="new")],
			score=i,
		)
		for i in range(12)
	]
	d = Digest(groups=groups, max_items=10)
	assert len(d.message_groups()) == 10
	assert len(d.carried_groups()) == 2


def test_fetch_result_defaults():
	r = FetchResult(url="https://e/", status=200, text="hi")
	assert r.etag == ""
	assert r.not_modified is False


def test_check_is_frozen_dataclass():
	c = Check(name="smtp", ok=True, detail="connected")
	assert c.name == "smtp"
	assert c.ok is True
	assert c.detail == "connected"
	try:
		c.ok = False  # frozen
	except Exception as exc:
		assert "cannot assign" in str(exc) or "frozen" in str(exc).lower()
	else:
		raise AssertionError("Check should be frozen")


def test_shared_exception_hierarchy():
	assert issubclass(ConfigError, ValueError)
	assert issubclass(AlreadyRunning, RuntimeError)
	assert issubclass(Disallowed, RuntimeError)
	assert issubclass(NotifyError, RuntimeError)


def test_signal_type_alias_values():
	# SignalType is the snapshots.signal_type vocabulary (distinct from
	# DetectorName: android_sitemap + sitemap both write "sitemap"). The feed
	# detector writes NO snapshot (it dedupes per-item via seen_feed_items), so
	# there is no "feed" signal_type -- only "sitemap" and "content".
	from typing import get_args

	from android_watcher.models import SignalType

	assert set(get_args(SignalType)) == {"sitemap", "content"}
