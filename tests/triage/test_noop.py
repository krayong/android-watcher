"""Tests for triage/noop.py: NoopTriager marks all changes substantive, no filter."""

from __future__ import annotations

import android_watcher.triage.noop  # noqa: F401 – registers "noop" with TRIAGERS
from android_watcher.config import AIConfig
from android_watcher.models import Change
from android_watcher.triage.base import TRIAGERS


def _make_change(n: int, kind: str = "new") -> Change:
	return Change(source_id=f"src-{n}", url=f"https://example.com/{n}", change_kind=kind)


def test_noop_marks_all_substantive():
	changes = [
		_make_change(1, "new"),
		_make_change(2, "updated"),
		_make_change(3, "new"),
	]
	config = AIConfig(mode="off")

	result = TRIAGERS.get("noop")().triage(changes, config)

	assert len(result.changes) == 3
	for change in result.changes:
		assert change.verdict == "substantive"
		assert change.description is None


def test_noop_mutates_in_place():
	c1 = _make_change(1)
	c2 = _make_change(2)
	changes = [c1, c2]

	TRIAGERS.get("noop")().triage(changes, AIConfig())

	# The original objects are mutated, not copies
	assert c1.verdict == "substantive"
	assert c2.verdict == "substantive"


def test_noop_result_has_no_tldr_or_unavailable():
	changes = [_make_change(1)]
	result = TRIAGERS.get("noop")().triage(changes, AIConfig())
	assert result.tldr is None
	assert result.unavailable is None


def test_noop_empty_changes():
	result = TRIAGERS.get("noop")().triage([], AIConfig())
	assert result.changes == []
	assert result.tldr is None
	assert result.unavailable is None


def test_noop_returns_same_list():
	changes = [_make_change(1)]
	result = TRIAGERS.get("noop")().triage(changes, AIConfig())
	assert result.changes is changes
