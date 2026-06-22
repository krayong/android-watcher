"""Shared types: dataclasses, type aliases, exceptions, and constants.

Everything here is defined ONCE and imported everywhere else. Keeping the four
exceptions, ``Check``, ``SignalType``, and ``INTERVAL_DELTA`` in this one module
gives the package a single source of truth and an acyclic import graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

DetectorName = Literal["feed", "android_sitemap", "sitemap", "content"]
Verdict = Literal["substantive", "cosmetic"]
ChangeKind = Literal["new", "updated"]
# How a sitemap source treats reference docs:
#   keep        - no special handling
#   drop        - exclude any URL with a "reference" path segment
#   index_only  - keep only reference index/summary pages (Kotlin-preferred),
#                 dropping per-symbol class/function pages
ReferenceMode = Literal["keep", "drop", "index_only"]
SignalType = Literal["sitemap", "content"]  # snapshots.signal_type
# android_sitemap + sitemap write "sitemap" (lastmod-confirmed-by-content);
# content detector writes "content". The feed detector writes NO snapshot
# (it dedupes per-item via seen_feed_items), so there is no "feed" signal_type.

# Shared schedule-interval mapping: defined ONCE here, imported by the catch-up
# gate (run.py), the scheduler (schedule.py), and doctor. cron => always due.
INTERVAL_DELTA = {
	"hourly": timedelta(hours=1),
	"daily": timedelta(days=1),
	"weekly": timedelta(days=7),
}


# Shared exceptions: defined ONCE here, imported by lock/config/fetch/notify so
# there is exactly one class per error and no circular imports.
class ConfigError(ValueError):
	"""Raised on a malformed or contradictory configuration."""


class AlreadyRunning(RuntimeError):
	"""Raised when another android-watcher run already holds the run lock."""


class Disallowed(RuntimeError):
	"""Raised when robots.txt forbids fetching a URL."""


class NotifyError(RuntimeError):
	"""Raised when a Notifier fails to deliver a digest."""


@dataclass(frozen=True)
class Source:
	id: str
	name: str
	category: str
	detector: DetectorName
	url: str
	enabled: bool = True
	path_prefix: str = ""
	feed_url: str = ""
	content_selector: str = ""
	default_weight: int = 0  # 0 => use category weight
	# Sitemap-source filters (host-agnostic android_sitemap detector):
	exclude_prefixes: tuple[str, ...] = ()  # drop URLs under any of these paths
	require_segment: str = ""  # if set, keep only URLs with a matching path segment
	# Default index_only: reference docs are filtered to index/summary pages site-
	# wide (a no-op on sources with no /reference pages). Override per source.
	reference_mode: ReferenceMode = "index_only"


@dataclass
class Change:
	source_id: str
	url: str
	change_kind: ChangeKind
	title: str = ""
	raw_diff: str = ""  # short excerpt / diff text
	fetched_hash: str = ""  # confirmed content hash
	detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
	id: int | None = None  # set by Store.record_change
	verdict: Verdict | None = None
	description: str | None = None  # filled by Triager for substantive
	group_key: str | None = None  # model-assigned grouping slug; same slug = same group
	group_summary: str | None = None  # merged one-line summary for the group, or None
	group_title: str | None = None  # short model headline naming the group, or None


@dataclass
class DigestItem:
	change: Change
	score: int


@dataclass
class DigestGroup:
	key: str
	title: str
	summary: str | None
	category: str
	source_id: str
	change_kind: ChangeKind
	members: list[Change]  # newest-first; len >= 1
	score: int = 0

	@property
	def primary_url(self) -> str:
		return self.members[0].url

	@property
	def page_count(self) -> int:
		return len(self.members)


@dataclass
class Digest:
	groups: list[DigestGroup]  # all groups, ranked (score DESC)
	max_items: int = 10  # cap on groups shown in the on-channel message
	tldr: str | None = None
	ai_unavailable: str | None = None
	sources_scanned: int = 0
	pages_watched: int = 0
	generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

	@property
	def is_empty(self) -> bool:
		return not self.groups

	def message_groups(self) -> list[DigestGroup]:
		return self.groups[: self.max_items]

	def carried_groups(self) -> list[DigestGroup]:
		return self.groups[self.max_items :]

	def change_count(self) -> int:
		return sum(g.page_count for g in self.groups)


@dataclass(frozen=True)
class Check:
	name: str  # health-check result; lives here so doctor.py and schedule.py
	ok: bool  # both import it without a circular import
	detail: str


@dataclass
class FetchResult:
	url: str
	status: int  # real HTTP status; 304 when not_modified
	text: str  # "" when not_modified
	etag: str = ""
	last_modified: str = ""
	not_modified: bool = False  # True on HTTP 304
