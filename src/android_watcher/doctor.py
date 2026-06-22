"""Doctor health checks.

``run_doctor(config)`` returns a list of ``Check`` objects covering:
- sitemap path-prefix resolution for each android_sitemap source
- AI backend availability
- schedule status (soft dependency on the schedule module, imported lazily)
"""

from __future__ import annotations

import asyncio
import shutil

from android_watcher import __version__
from android_watcher.config import Config, db_path
from android_watcher.detect.android_sitemap import (
	INDEX_URL,
	_index_url_for,
	load_sitemap,
	prefix_count,
)
from android_watcher.fetch import USER_AGENT, Fetcher
from android_watcher.models import Check
from android_watcher.run import resolve_sources
from android_watcher.store import Store


def _check_ai(config: Config) -> Check:
	if config.ai.mode == "off":
		return Check("ai-backend", True, "AI disabled")
	path = shutil.which("claude")
	if path:
		return Check("ai-backend", True, f"claude found at {path}")
	return Check("ai-backend", False, "claude not found on PATH")


def _check_seed() -> Check:
	"""Report the imported baseline seed date and snapshot count, if any."""
	store = Store(db_path())
	store.migrate()
	try:
		count = store.snapshot_count()
		date = store.seed_date()
	finally:
		store.close()
	if count == 0:
		return Check("seed", True, "no baseline yet; first run will establish one")
	if date:
		return Check("seed", True, f"baseline seeded {date} ({count} snapshots)")
	return Check("seed", True, f"baseline established ({count} snapshots)")


def _check_schedule() -> Check:
	try:
		from android_watcher.schedule import schedule_status  # noqa: PLC0415
	except ImportError:
		return Check("schedule", False, "schedule module unavailable")
	return schedule_status()


def _load_sitemap_entries(index_url: str = INDEX_URL) -> list[tuple[str, str]]:
	"""Fetch one host's sitemap once and return the flat entry list.

	Extracted so tests can patch this function directly instead of having to
	wire up a real Store + Fetcher + asyncio event loop.
	"""

	async def _load() -> list[tuple[str, str]]:
		store = Store(db_path())
		store.migrate()
		fetcher = Fetcher(store, user_agent=USER_AGENT.format(version=__version__))
		try:
			# Time-box it: a sitemap can be large (~300 MB uncached), so doctor
			# reports a slow/unavailable sitemap rather than appearing to hang.
			return await asyncio.wait_for(load_sitemap(fetcher, index_url), timeout=30)
		finally:
			await fetcher.close()

	return asyncio.run(_load())


def _check_prefixes(config: Config) -> list[Check]:
	targets = [s for s in resolve_sources(config) if s.detector == "android_sitemap"]
	if not targets:
		return []

	by_host: dict[str, list] = {}
	for s in targets:
		by_host.setdefault(_index_url_for(s), []).append(s)

	checks: list[Check] = []
	for index_url, srcs in by_host.items():
		host = index_url.split("/sitemap.xml")[0]
		try:
			entries = _load_sitemap_entries(index_url)
		except TimeoutError:
			checks.append(Check(f"sitemap:{host}", True, "fetch slow; run once to cache"))
			continue
		except Exception as exc:  # noqa: BLE001 - any fetch/parse failure is a soft check
			checks.append(Check(f"sitemap:{host}", False, f"unavailable; run once first ({exc})"))
			continue
		if not entries:
			checks.append(Check(f"sitemap:{host}", True, "cached (304); not re-verified"))
			continue
		for s in srcs:
			if not s.path_prefix:
				checks.append(Check(f"prefix:{s.id}", True, f"watches host ({len(entries)} URLs)"))
				continue
			count = prefix_count(entries, s.path_prefix)
			if count == 0:
				checks.append(
					Check(f"prefix:{s.path_prefix}", False, "stale prefix: 0 sitemap URLs match")
				)
			else:
				checks.append(Check(f"prefix:{s.path_prefix}", True, f"resolves ({count} URLs)"))
	return checks


def run_doctor(config: Config) -> list[Check]:
	checks: list[Check] = []
	checks.extend(_check_prefixes(config))
	checks.append(_check_seed())
	checks.append(_check_ai(config))
	checks.append(_check_schedule())
	return checks
