"""Tests for triage/base.py: Triager protocol, TriageResult, TRIAGERS registry."""

from __future__ import annotations

import pytest

from android_watcher.config import AIConfig
from android_watcher.models import Change
from android_watcher.triage.base import TRIAGERS, TriageResult


def _make_change(n: int = 1) -> Change:
	return Change(source_id=f"src-{n}", url=f"https://example.com/{n}", change_kind="new")


def test_triage_result_defaults():
	result = TriageResult(changes=[])
	assert result.tldr is None
	assert result.unavailable is None


def test_triage_result_with_changes():
	changes = [_make_change(1), _make_change(2)]
	result = TriageResult(changes=changes, tldr="summary", unavailable=None)
	assert result.changes is changes
	assert result.tldr == "summary"
	assert result.unavailable is None


def test_triage_result_unavailable():
	result = TriageResult(changes=[], unavailable="something broke")
	assert result.unavailable == "something broke"
	assert result.tldr is None


def test_registered_dummy_triager_roundtrips():
	"""A dummy triager registered at import time is retrievable and callable."""

	@TRIAGERS.register("dummy_test")
	class DummyTriager:
		def triage(self, changes: list[Change], config: AIConfig) -> TriageResult:
			return TriageResult(changes=changes)

	cls = TRIAGERS.get("dummy_test")
	instance = cls()
	changes = [_make_change()]
	config = AIConfig()
	result = instance.triage(changes, config)
	assert isinstance(result, TriageResult)
	assert result.changes is changes


def test_unknown_name_raises_with_available_list():
	with pytest.raises(KeyError, match="available"):
		TRIAGERS.get("__no_such_triager__")
