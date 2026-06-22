"""Task 10: run_once orchestration — ledger-authoritative delivery, reconcile,
catch-up gate, write-once triage, supersede, transactional per-channel delivery,
zero-channels short-circuit, empty-digest idempotency, and dry_run.

Tests exercise the guarantees: a fake Store records the ledger calls, fake
notifiers record sends, and the digest is always seeded into the ledger (never
this-run detections) so the ledger-authoritative contract is what is proven.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pytest

import android_watcher.run as run_mod
from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.models import AlreadyRunning, Change
from android_watcher.notify.base import NOTIFIERS, NotifyError
from android_watcher.triage.base import TRIAGERS, TriageResult

# --- fakes -----------------------------------------------------------------


class FakeStore:
	"""Records the ledger interactions run_once performs.

	``digest_changes`` is the authoritative digest source; tests seed it
	explicitly so the digest never depends on this-run detections.
	"""

	def __init__(self):
		self.digest_changes: list[Change] = []
		self._delivered: dict[int, set[str]] = {}
		self.deliveries: list[tuple[int, str]] = []
		self.recorded: list[Change] = []
		self.verdicts: list[tuple[int, str, str | None]] = []
		self.superseded: list[tuple[str, str, int]] = []
		self.opened: list[int] = []
		self.committed: list[int] = []
		self._inflight: int | None = None
		self.marked: list[datetime] = []
		self._last_run: datetime | None = None
		self._next_digest_id = 1
		self.migrated = False

	def migrate(self):
		self.migrated = True

	def snapshot_count(self) -> int:
		return 0  # scan-scope footer stat; irrelevant to ledger-delivery tests

	# changes / ledger
	def record_change(self, change: Change) -> int:
		self.recorded.append(change)
		if change.id is None:
			change.id = 1000 + len(self.recorded)
		return change.id

	def changes_for_digest(self, channels: set[str]) -> list[Change]:
		if not channels:
			return []
		return list(self.digest_changes)

	def supersede_older(self, source_id: str, url: str, keep_id: int) -> None:
		self.superseded.append((source_id, url, keep_id))

	def set_verdict(
		self, change_id, verdict, description, group_key=None, group_summary=None, group_title=None
	) -> None:
		self.verdicts.append((change_id, verdict, description))

	# delivery
	def delivered_channels(self, change_id: int) -> set[str]:
		return set(self._delivered.get(change_id, set()))

	def record_delivery(self, change_id: int, channel: str) -> None:
		# INSERT OR IGNORE: skip if already in delivered state (mimics real store)
		if channel not in self._delivered.get(change_id, set()):
			self.deliveries.append((change_id, channel))
			self._delivered.setdefault(change_id, set()).add(channel)

	# in-flight digest
	def open_digest(self) -> int:
		did = self._next_digest_id
		self._next_digest_id += 1
		self.opened.append(did)
		self._inflight = did
		return did

	def commit_digest(self, digest_id: int) -> None:
		self.committed.append(digest_id)
		if self._inflight == digest_id:
			self._inflight = None

	def inflight_digest(self) -> int | None:
		return self._inflight

	# run bookkeeping
	def last_successful_run(self) -> datetime | None:
		return self._last_run

	def mark_successful_run(self, when: datetime) -> None:
		self.marked.append(when)
		self._last_run = when


class FakeNotifier:
	sends: list[tuple[str, list[int]]] = []

	def __init__(self, name, fail=False):
		self.name = name
		self.fail = fail

	def send(self, digest, config) -> set[int]:
		member_ids = [m.id for g in digest.groups for m in g.members]
		FakeNotifier.sends.append((self.name, member_ids))
		if self.fail:
			raise NotifyError(f"{self.name} boom")
		return {mid for mid in member_ids if mid is not None}


def install_notifiers(monkeypatch, *, email_fail=False, slack_fail=False, telegram_fail=False):
	FakeNotifier.sends = []
	impls = {
		"email": lambda: FakeNotifier("email", fail=email_fail),
		"slack": lambda: FakeNotifier("slack", fail=slack_fail),
		"telegram": lambda: FakeNotifier("telegram", fail=telegram_fail),
	}
	monkeypatch.setattr(NOTIFIERS, "get", lambda name: impls[name])


def install_triager(monkeypatch, result_factory):
	captured = {}

	class _T:
		def triage(self, changes, config):
			captured["changes"] = changes
			return result_factory(changes)

	monkeypatch.setattr(TRIAGERS, "get", lambda name: _T)
	return captured


# --- config / source helpers -----------------------------------------------


def make_config(
	*, email=True, slack=True, telegram=False, empty="send", ai_mode="off", interval="daily"
):  # noqa: E501
	return Config(
		schedule=ScheduleConfig(interval=interval),
		ai=AIConfig(mode=ai_mode),
		digest=DigestConfig(empty=empty),
		sort={},
		email=EmailChannel(enabled=email),
		slack=SlackChannel(enabled=slack),
		telegram=TelegramChannel(enabled=telegram),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def sub_change(cid, sid="src", url="https://x/a"):
	return Change(source_id=sid, url=url, change_kind="updated", verdict="substantive", id=cid)


@pytest.fixture
def patched(monkeypatch, tmp_path):
	"""Patch run_once's external boundaries to keep tests offline.

	Detection returns [] by default (tests seed the ledger directly). A
	real fetcher is never constructed; Store is replaced with the fake one.
	"""
	store = FakeStore()
	monkeypatch.setattr(run_mod, "Store", lambda path: store)
	monkeypatch.setattr(run_mod, "db_path", lambda: str(tmp_path / "state.db"))
	monkeypatch.setattr(run_mod, "data_path", lambda: str(tmp_path))
	# Seeding is tested separately; the FakeStore here is a ledger double.
	monkeypatch.setattr(run_mod, "apply_seed_if_empty", lambda store: None)

	@contextlib.contextmanager
	def _lock(_dir):
		yield

	monkeypatch.setattr(run_mod, "run_lock", _lock)
	monkeypatch.setattr(run_mod, "resolve_sources", lambda config: [])

	detect_calls = {"n": 0, "changes": []}

	def _fake_run_async(sources, store, fetcher):
		detect_calls["n"] += 1
		return list(detect_calls["changes"])

	# Bypass asyncio + Fetcher entirely: replace _run_async with a sync stub and
	# asyncio.run with a pass-through caller.
	monkeypatch.setattr(run_mod, "_run_async", _fake_run_async)
	monkeypatch.setattr(run_mod.asyncio, "run", lambda coro: coro)
	monkeypatch.setattr(run_mod, "Fetcher", lambda *a, **k: object())

	# Default triager: pass-through verdicts already set, no banner.
	install_triager(monkeypatch, lambda changes: TriageResult(changes=changes))
	return store, detect_calls


# --- tests -----------------------------------------------------------------


def test_happy_path_both_channels(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	store.digest_changes = [sub_change(1), sub_change(2, url="https://x/b")]

	digest = run_mod.run_once(make_config())

	channels_sent = {name for name, _ in FakeNotifier.sends}
	assert channels_sent == {"email", "slack"}
	assert set(store.deliveries) == {(1, "email"), (1, "slack"), (2, "email"), (2, "slack")}
	assert store.opened and store.committed
	assert store.opened == store.committed
	assert len(store.marked) == 1
	assert digest.change_count() == 2


def test_ledger_backlog_retried_not_this_run(patched, monkeypatch):
	store, detect_calls = patched
	install_notifiers(monkeypatch)
	# This run detects NOTHING, but a prior run left an undelivered change.
	detect_calls["changes"] = []
	backlog = sub_change(7)
	store.digest_changes = [backlog]

	run_mod.run_once(make_config())

	sent_ids = [ids for _, ids in FakeNotifier.sends]
	assert all(ids == [7] for ids in sent_ids)
	assert (7, "email") in store.deliveries
	assert (7, "slack") in store.deliveries


def test_delivery_idempotency_email_ok_slack_fail(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch, slack_fail=True)
	store.digest_changes = [sub_change(3)]

	run_mod.run_once(make_config())

	# email delivery recorded; slack failed so NOT recorded; run still marked.
	assert (3, "email") in store.deliveries
	assert (3, "slack") not in store.deliveries
	assert len(store.marked) == 1

	# Next run (force past the catch-up gate, slack now healthy): both notifiers
	# are called (per-channel skip is gone; idempotency is in record_delivery).
	# Email's record_delivery is a no-op (INSERT OR IGNORE); slack records.
	install_notifiers(monkeypatch)  # both channels healthy now
	store.digest_changes = [sub_change(3)]
	run_mod.run_once(make_config(), force=True)
	assert (3, "slack") in store.deliveries
	# email delivery count is still 1 (idempotent; not re-recorded)
	assert store.deliveries.count((3, "email")) == 1


def test_reconcile_redelivers_then_commits(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	# Crash left an inflight digest; change delivered to email, owes slack.
	inflight_id = store.open_digest()  # id=1, sets inflight
	store.committed.clear()
	store.opened.clear()
	change = sub_change(5)
	store._delivered[5] = {"email"}
	store.digest_changes = [change]

	run_mod.run_once(make_config())

	# slack delivered change 5 and recorded; email already had it (INSERT OR IGNORE).
	assert (5, "slack") in store.deliveries
	assert (5, "email") not in store.deliveries  # idempotent: not re-recorded
	slack_sends = [ids for name, ids in FakeNotifier.sends if name == "slack"]
	assert [5] in slack_sends
	# inflight committed
	assert inflight_id in store.committed


def test_catch_up_gate_not_due_skips(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	store._last_run = datetime.now(UTC) - timedelta(minutes=5)  # within daily interval
	store.digest_changes = [sub_change(9)]

	digest = run_mod.run_once(make_config(interval="daily"))

	assert digest.is_empty
	assert FakeNotifier.sends == []
	assert store.marked == []  # not marked when skipped


def test_catch_up_force_overrides_gate(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	store._last_run = datetime.now(UTC) - timedelta(minutes=5)
	store.digest_changes = [sub_change(9)]

	run_mod.run_once(make_config(interval="daily"), force=True)

	assert FakeNotifier.sends  # force ran it anyway
	assert len(store.marked) == 1


def test_empty_digest_sent_at_most_once_per_window(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	store.digest_changes = []  # nothing notable

	# First run crosses the catch-up boundary (last_run None) -> sends empty once.
	run_mod.run_once(make_config(empty="send", interval="daily"))
	first_empty_sends = len(FakeNotifier.sends)

	# Second run, still within the same window -> not due -> sends nothing.
	FakeNotifier.sends = []
	run_mod.run_once(make_config(empty="send", interval="daily"))
	second_empty_sends = len(FakeNotifier.sends)

	assert first_empty_sends >= 1
	assert second_empty_sends == 0
	assert first_empty_sends + second_empty_sends <= 2  # one per channel, one window


def test_run_lock_prevents_overlap(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)

	@contextlib.contextmanager
	def _busy(_dir):
		raise AlreadyRunning("held")
		yield  # pragma: no cover

	monkeypatch.setattr(run_mod, "run_lock", _busy)
	store.digest_changes = [sub_change(1)]

	with pytest.raises(AlreadyRunning):
		run_mod.run_once(make_config())

	assert FakeNotifier.sends == []
	assert store.marked == []


def test_ai_unavailable_banner_and_still_sends(patched, monkeypatch):
	store, detect_calls = patched
	install_notifiers(monkeypatch)
	detect_calls["changes"] = [Change(source_id="src", url="https://x/a", change_kind="new")]
	store.digest_changes = [sub_change(1)]
	install_triager(
		monkeypatch,
		lambda changes: TriageResult(changes=changes, unavailable="boom"),
	)

	digest = run_mod.run_once(make_config(ai_mode="claude_cli"))

	assert digest.ai_unavailable == "boom"
	assert FakeNotifier.sends  # channels still sent despite AI failure


def test_dry_run_no_mutation(patched, monkeypatch):
	store, detect_calls = patched
	install_notifiers(monkeypatch)

	# If detection runs, fail loudly.
	def _boom(*a, **k):
		raise AssertionError("dry_run must not detect")

	monkeypatch.setattr(run_mod, "_run_async", _boom)
	store.digest_changes = [sub_change(1), sub_change(2, url="https://x/b")]

	digest = run_mod.run_once(make_config(), dry_run=True)

	assert digest.change_count() == 2  # rendered from existing ledger
	assert FakeNotifier.sends == []
	assert store.deliveries == []
	assert store.opened == []
	assert store.marked == []
	assert store.recorded == []
	assert store.verdicts == []
	assert store.superseded == []


def test_empty_and_skip_marks_run_no_send(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	store.digest_changes = []

	run_mod.run_once(make_config(empty="skip"))

	assert FakeNotifier.sends == []
	assert len(store.marked) == 1


def test_supersede_called_per_ranked_item_before_delivery(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	c1 = sub_change(42, sid="src", url="https://x/a")
	c2 = sub_change(43, sid="src2", url="https://x/b")
	store.digest_changes = [c1, c2]

	run_mod.run_once(make_config())

	assert ("src", "https://x/a", 42) in store.superseded
	assert ("src2", "https://x/b", 43) in store.superseded
	assert len(store.superseded) == 2


def test_write_once_triage_only_verdict_null(patched, monkeypatch):
	store, detect_calls = patched
	install_notifiers(monkeypatch)
	fresh = Change(source_id="src", url="https://x/a", change_kind="new", id=100)  # verdict None
	redetected = Change(
		source_id="src",
		url="https://x/b",
		change_kind="updated",
		verdict="substantive",
		id=101,
	)
	detect_calls["changes"] = [fresh, redetected]
	store.digest_changes = []  # digest irrelevant here

	captured = install_triager(
		monkeypatch,
		lambda changes: TriageResult(changes=[_set(c) for c in changes]),
	)

	run_mod.run_once(make_config(ai_mode="claude_cli"))

	triaged_ids = [c.id for c in captured["changes"]]
	assert triaged_ids == [100]  # only the verdict-None change
	# set_verdict called for the newly triaged row only
	assert [v[0] for v in store.verdicts] == [100]


def _set(change):
	change.verdict = "substantive"
	change.description = "d"
	return change


def test_zero_channels_short_circuit_live_and_dry(patched, monkeypatch):
	store, _ = patched
	install_notifiers(monkeypatch)
	store.digest_changes = [sub_change(1)]  # would deliver if channels existed

	# Live run, both channels disabled.
	digest = run_mod.run_once(make_config(email=False, slack=False))
	assert digest.is_empty
	assert FakeNotifier.sends == []
	assert store.deliveries == []
	assert store.opened == []
	assert len(store.marked) == 1  # window still advances

	# dry_run with zero channels: empty digest, NOT marked.
	store.marked.clear()
	digest = run_mod.run_once(make_config(email=False, slack=False), dry_run=True)
	assert digest.is_empty
	assert store.marked == []


def test_reconcile_empty_no_spurious_send_but_commits(patched, monkeypatch):
	"""Crash-recovery with an empty backlog must NOT send an empty digest
	off-cycle (bypassing the empty-digest-once anchor), but MUST commit the
	inflight row so it does not recur on the next run.

	Covers the fix to the reconcile path: mirrors the live-path empty-digest
	guard so _deliver_into is skipped when digest.empty=="skip" (or "send" but
	the backlog is truly empty and we're off-cycle), while commit_digest always
	fires.
	"""
	store, _ = patched
	install_notifiers(monkeypatch)
	# Simulate a crash: an inflight digest was opened but never committed, and
	# all changes have since been delivered — so the backlog is now empty.
	inflight_id = store.open_digest()
	store.committed.clear()
	store.opened.clear()
	store.digest_changes = []  # empty backlog

	# With empty="skip", the reconcile path must not deliver an empty digest.
	run_mod.run_once(make_config(empty="skip"))

	assert FakeNotifier.sends == []  # no spurious empty send
	assert inflight_id in store.committed  # inflight MUST be closed


def test_reconcile_empty_send_mode_still_sends(patched, monkeypatch):
	"""With empty='send', an empty reconcile digest IS sent (mirrors live path)."""
	store, _ = patched
	install_notifiers(monkeypatch)
	inflight_id = store.open_digest()
	store.committed.clear()
	store.opened.clear()
	store.digest_changes = []  # empty backlog

	run_mod.run_once(make_config(empty="send"))

	# _deliver_into was called for the reconcile path => at least one open_digest
	assert store.opened  # _deliver_into opens its own digest row
	assert inflight_id in store.committed


def test_telegram_channel_included_when_enabled(patched, monkeypatch):
	"""_enabled_channels includes 'telegram' when telegram is enabled."""
	store, _ = patched
	install_notifiers(monkeypatch)
	store.digest_changes = [sub_change(1)]

	run_mod.run_once(make_config(email=False, slack=False, telegram=True))

	channels_sent = {name for name, _ in FakeNotifier.sends}
	assert "telegram" in channels_sent


def test_triage_batched_splits_calls():
	from android_watcher.config import AIConfig
	from android_watcher.models import Change
	from android_watcher.run import _triage_batched
	from android_watcher.triage.base import TriageResult

	calls = []

	class _Spy:
		def triage(self, changes, config):
			calls.append(len(changes))
			for c in changes:
				c.verdict = "substantive"
			return TriageResult(changes=changes, tldr=None, unavailable=None)

	changes = [Change(source_id="s", url=f"u{i}", change_kind="updated") for i in range(60)]
	result = _triage_batched(_Spy(), changes, AIConfig(), batch_size=25)
	assert calls == [25, 25, 10]
	assert len(result.changes) == 60
	assert all(c.verdict == "substantive" for c in result.changes)


def test_e2e_grouping_cap_delivery_idempotency(patched, monkeypatch):
	"""End-to-end: grouping collapses same-key changes, max_items caps the message,
	every conveyed id has a delivery row, and a second run does not re-deliver.

	Guarantees exercised:
	  (a) Two changes sharing a group_key for one source collapse into one DigestGroup
	      with page_count == 2, not two separate groups.
	  (b) message_groups() returns at most max_items groups; the rest are carried.
	  (c) After delivery, every change id a channel conveyed has a delivery row for
	      that channel in the store ledger.
	  (d) A second run_once against an empty backlog (fully-delivered) sends nothing
	      and records no new delivery rows (idempotent).
	"""
	store, _ = patched
	install_notifiers(monkeypatch)

	# Two changes sharing a group_key on the same source — they must collapse.
	grouped_a = Change(
		source_id="src",
		url="https://x/page-a",
		change_kind="updated",
		verdict="substantive",
		id=10,
		group_key="compose-updates",
	)
	grouped_b = Change(
		source_id="src",
		url="https://x/page-b",
		change_kind="updated",
		verdict="substantive",
		id=11,
		group_key="compose-updates",
	)
	# Singletons across other sources to push total groups beyond max_items=2.
	singleton_1 = Change(
		source_id="src2", url="https://x/c", change_kind="new", verdict="substantive", id=20
	)
	singleton_2 = Change(
		source_id="src3", url="https://x/d", change_kind="new", verdict="substantive", id=21
	)
	singleton_3 = Change(
		source_id="src4", url="https://x/e", change_kind="updated", verdict="substantive", id=22
	)

	# Four items in the backlog: the grouped pair (counts as 1 group) + 3 singletons = 4 groups.
	# max_items=2 means message_groups() shows 2 and carried_groups() holds 2.
	store.digest_changes = [grouped_a, grouped_b, singleton_1, singleton_2, singleton_3]
	config = Config(
		schedule=ScheduleConfig(interval="daily"),
		ai=AIConfig(mode="off"),
		digest=DigestConfig(max_items=2, empty="skip"),
		sort={},
		email=EmailChannel(enabled=True),
		slack=SlackChannel(enabled=False),
		telegram=TelegramChannel(enabled=False),
		custom_sources=[],
		enabled_source_ids=set(),
	)

	# --- first run -----------------------------------------------------------
	digest = run_mod.run_once(config)

	# (a) Grouping: the two compose-updates changes collapse into one group.
	group_keys = [g.key for g in digest.groups]
	composed_groups = [g for g in digest.groups if "compose-updates" in g.key]
	assert len(composed_groups) == 1, (
		f"expected 1 composed group, got {len(composed_groups)}; groups={group_keys}"
	)
	assert composed_groups[0].page_count == 2

	# (b) max_items cap: 4 groups total (1 composed + 3 singletons), cap=2.
	assert len(digest.groups) == 4
	assert len(digest.message_groups()) == 2
	assert len(digest.carried_groups()) == 2

	# (c) Every id conveyed by email has a delivery row.
	email_sends = [ids for name, ids in FakeNotifier.sends if name == "email"]
	assert len(email_sends) == 1
	conveyed_ids = set(email_sends[0])
	# All five change ids must be represented (the full digest, not just message_groups).
	assert {10, 11, 20, 21, 22} == conveyed_ids
	for cid in conveyed_ids:
		assert (cid, "email") in store.deliveries, (
			f"change {cid} was conveyed but has no delivery row for 'email'"
		)

	# (d) Idempotency: simulate what the real store does — return empty backlog
	# for a second run (all changes fully delivered to every enabled channel).
	store.digest_changes = []
	FakeNotifier.sends = []
	run_mod.run_once(config, force=True)

	assert FakeNotifier.sends == [], "second run must not send when backlog is empty"
	# No new delivery rows were added.
	assert store.deliveries.count((10, "email")) == 1
	assert store.deliveries.count((11, "email")) == 1


if __name__ == "__main__":  # pragma: no cover
	raise SystemExit(pytest.main([__file__, "-v"]))
