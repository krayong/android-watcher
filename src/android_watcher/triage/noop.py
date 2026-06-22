"""Noop triager: AI-off backend that marks every change substantive, no filtering."""

from __future__ import annotations

from android_watcher.config import AIConfig
from android_watcher.models import Change
from android_watcher.triage.base import TRIAGERS, TriageResult


@TRIAGERS.register("noop")
class NoopTriager:
	"""Mark every change substantive with no description and no filtering.

	Used when AI triage is off (``config.ai.mode == "off"``). All watched
	changes still surface in the digest; the description field is left empty
	because there is no AI to fill it. ``unavailable`` is None because AI being
	off is a deliberate choice, not a failure — no banner is shown.
	"""

	def triage(self, changes: list[Change], config: AIConfig) -> TriageResult:
		for change in changes:
			change.verdict = "substantive"
			change.description = None
		return TriageResult(changes=changes, tldr=None, unavailable=None)
