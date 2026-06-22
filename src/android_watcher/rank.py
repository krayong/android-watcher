"""Rank a list of Changes into a Digest.

Scoring:
  base = source.default_weight if nonzero, else CATEGORY_WEIGHTS[source.category]
         (falls back to DEFAULT_CATEGORY_WEIGHT for unknown categories or missing sources)
  score = base + config.sort override (source_id key takes precedence over category key)

Tie-break: detected_at DESC.

Groups are sorted globally by (score, members[0].detected_at) DESC; max_items caps
how many appear in the on-channel message vs. carried over.
"""

from __future__ import annotations

from .config import Config
from .group import group_changes
from .models import Change, Digest, DigestGroup, Source

CATEGORY_WEIGHTS: dict[str, int] = {
	"platform-release": 100,
	"api-reference": 80,
	"tooling": 70,
	"guides": 50,
	"dev-blog": 40,
	"design": 30,
	"news": 20,
}
DEFAULT_CATEGORY_WEIGHT = 10

# Display order for category subheadings. Anything not listed falls to "Other".
CATEGORY_ORDER: list[tuple[str, str]] = [
	("platform-release", "Platform & Releases"),
	("api-reference", "API Reference"),
	("tooling", "Developer Tooling"),
	("guides", "Guides & Blog"),
	("dev-blog", "Guides & Blog"),
	("design", "Design"),
	("news", "News"),
]


def _score(change: Change, source: Source | None, config: Config) -> int:
	# Unknown source_id (not in sources map) => DEFAULT_CATEGORY_WEIGHT, no override.
	if source is None:
		return DEFAULT_CATEGORY_WEIGHT

	if source.default_weight:
		base = source.default_weight
	else:
		base = CATEGORY_WEIGHTS.get(source.category, DEFAULT_CATEGORY_WEIGHT)

	# source_id override takes precedence over category override
	override = config.sort.get(change.source_id)
	if override is None:
		override = config.sort.get(source.category)
	return base + (override or 0)


def rank(changes: list[Change], sources: dict[str, Source], config: Config) -> Digest:
	substantive = [c for c in changes if c.verdict == "substantive"]
	groups = group_changes(substantive, sources, config)
	groups.sort(key=lambda g: (g.score, g.members[0].detected_at), reverse=True)
	return Digest(groups=groups, max_items=config.digest.max_items)


def by_category(groups: list[DigestGroup]) -> list[tuple[str, str, list[DigestGroup]]]:
	"""Bucket groups under category labels in CATEGORY_ORDER; preserve rank order."""
	label_for = dict(CATEGORY_ORDER)
	order = [cid for cid, _ in CATEGORY_ORDER]
	buckets: dict[str, list[DigestGroup]] = {}
	for g in groups:
		buckets.setdefault(g.category, []).append(g)
	out: list[tuple[str, str, list[DigestGroup]]] = []
	seen: set[str] = set()
	for cid in order:
		if cid in buckets and cid not in seen:
			out.append((cid, label_for[cid], buckets[cid]))
			seen.add(cid)
	# Unknown categories last, under "Other".
	other = [g for cid, gs in buckets.items() if cid not in label_for for g in gs]
	if other:
		out.append(("other", "Other", other))
	return out
