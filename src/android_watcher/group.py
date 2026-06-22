"""Group ledger changes into DigestGroups: model group_key first, heuristic fallback."""

from __future__ import annotations

import re

from .config import Config
from .models import Change, DigestGroup, Source

_WORD = re.compile(r"[a-z0-9]+")
_DIGITS = re.compile(r"\d")


def _title_case(text: str) -> str:
	"""Capitalize the first letter of each word, preserving existing capitals so
	acronyms survive (GKI stays GKI, OS stays OS, not Gki/Os)."""
	return " ".join(w[:1].upper() + w[1:] if w else w for w in text.split(" "))


def heuristic_prefix(title: str) -> str:
	"""A coarse grouping key for changes without a model group_key.

	Lowercase the title, keep leading words up to the first numeric run (so
	'Android 13 release builds' and 'Android 14 release builds' collide), capped
	at four words so unrelated titles do not over-merge.
	"""
	words = _WORD.findall(title.lower())
	kept: list[str] = []
	for w in words:
		if _DIGITS.search(w):
			break
		kept.append(w)
		if len(kept) >= 4:
			break
	return " ".join(kept) if kept else title.strip().lower()


def group_changes(
	changes: list[Change], sources: dict[str, Source], config: Config
) -> list[DigestGroup]:
	# Lazy import avoids a rank<->group module cycle: rank.py will import
	# group_changes at the top level (Task 4), so a top-level import of _score
	# here would create rank -> group -> rank with _score not yet defined.
	from .rank import _score

	buckets: dict[str, list[Change]] = {}
	for c in changes:
		sub = c.group_key or heuristic_prefix(c.title)
		key = f"{c.source_id}::{sub}"
		buckets.setdefault(key, []).append(c)

	groups: list[DigestGroup] = []
	for key, members in buckets.items():
		members.sort(key=lambda c: (c.detected_at, c.id or 0), reverse=True)
		source = sources.get(members[0].source_id)
		# Heading: prefer the model's group headline; else the representative page
		# title. Summary: prefer the model's group summary; else the representative
		# change's own one-line description (so every group shows a sentence).
		summary = next((m.group_summary for m in members if m.group_summary), None) or next(
			(m.description for m in members if m.description), None
		)
		raw_title = next((m.group_title for m in members if m.group_title), None) or next(
			(m.title for m in members if m.title), None
		)
		title = _title_case(raw_title) if raw_title else members[0].url
		score = max(_score(m, source, config) for m in members)
		groups.append(
			DigestGroup(
				key=key,
				title=title,
				summary=summary,
				category=source.category if source else "",
				source_id=members[0].source_id,
				change_kind=members[0].change_kind,
				members=members,
				score=score,
			)
		)
	return groups
