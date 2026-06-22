"""The run_once pipeline: source resolution, detection, and orchestration.

The first section covers source selection and the isolated async detector
driver; the rest is the full ``run_once`` orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import time
from datetime import datetime
from pathlib import Path

from android_watcher import __version__
from android_watcher.catalog import load_catalog
from android_watcher.config import Config, data_path, db_path, log_path
from android_watcher.detect.base import DETECTORS
from android_watcher.fetch import USER_AGENT, Fetcher
from android_watcher.lock import run_lock
from android_watcher.models import INTERVAL_DELTA, UTC, Change, Digest, Source
from android_watcher.notify.base import NOTIFIERS, NotifyError
from android_watcher.rank import rank
from android_watcher.seed import apply_seed_if_empty
from android_watcher.store import Store
from android_watcher.triage.base import TRIAGERS, TriageResult
from android_watcher.triage.claude_cli import MAX_TRIAGE_BATCH

log = logging.getLogger("android_watcher.run")


def configure_file_logging() -> str:
	"""Attach a rotating file handler to the package logger; return the log path.

	Idempotent: repeated calls do not stack handlers. Called by the CLI so both
	manual and scheduled runs append to the same log; not called from tests,
	which exercise run_once() directly and must not write to the user log dir.
	"""
	path = log_path()
	Path(path).parent.mkdir(parents=True, exist_ok=True)
	pkg = logging.getLogger("android_watcher")
	pkg.setLevel(logging.INFO)
	if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in pkg.handlers):
		handler = logging.handlers.RotatingFileHandler(
			path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
		)
		handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
		pkg.addHandler(handler)
	return path


def resolve_sources(config: Config) -> list[Source]:
	"""Resolve the watched sources from catalog + config.

	Start from the catalog entries that are enabled by their own flag. If
	``enabled_source_ids`` is non-empty, keep only those ids (an override). An
	empty/absent selection means "use the catalog enabled flags", never "watch
	nothing". Custom sources are always watched and override a catalog source on
	id collision.
	"""
	watched = [s for s in load_catalog() if s.enabled]
	if config.enabled_source_ids:
		watched = [s for s in watched if s.id in config.enabled_source_ids]
	by_id: dict[str, Source] = {s.id: s for s in watched}
	for s in config.custom_sources:  # custom always included, overrides catalog
		by_id[s.id] = s
	return list(by_id.values())


async def _detect_all(sources: list[Source], store: Store, fetcher: Fetcher) -> list[Change]:
	"""Run each source's detector, isolating per-source failures.

	One source raising never aborts the run: it is logged and skipped. Returns
	the flattened list of changes across all sources that succeeded.
	"""
	changes: list[Change] = []
	total = len(sources)
	for i, source in enumerate(sources, 1):
		log.info("detecting [%d/%d] %s (%s)", i, total, source.id, source.detector)
		t0 = time.monotonic()
		try:
			detector = DETECTORS.get(source.detector)()
			found = await detector.detect(source, store, fetcher)
			changes.extend(found)
			log.info("  %s: %d change(s) in %.1fs", source.id, len(found), time.monotonic() - t0)
		except Exception:  # isolation: one source must not abort the run
			log.exception("source %s failed after %.1fs", source.id, time.monotonic() - t0)
	return changes


def _enabled_channels(config: Config) -> set[str]:
	channels: set[str] = set()
	if config.email.enabled:
		channels.add("email")
	if config.slack.enabled:
		channels.add("slack")
	if config.telegram.enabled:
		channels.add("telegram")
	return channels


def _source_index(config: Config) -> dict[str, Source]:
	sources = {s.id: s for s in load_catalog()}
	sources.update({s.id: s for s in config.custom_sources})  # custom wins on collision
	return sources


def _catch_up_due(store: Store, config: Config, force: bool) -> bool:
	"""Whether this run should cover the current cycle.

	Due when forced, when nothing has ever run, or when the last successful run
	is at least one schedule interval in the past. cron has no fixed delta, so
	it is always due; the native scheduler enforces cron timing.
	"""
	last = store.last_successful_run()
	if force or last is None:
		return True
	delta = INTERVAL_DELTA.get(config.schedule.interval)
	if delta is None:  # cron => always due
		return True
	return datetime.now(UTC) - last >= delta


async def _run_async(sources: list[Source], store: Store, fetcher: Fetcher) -> list[Change]:
	try:
		return await _detect_all(sources, store, fetcher)
	finally:
		await fetcher.close()


def _triage_batched(triager, changes, ai_config, batch_size=MAX_TRIAGE_BATCH):
	all_changes: list = []
	tldr: str | None = None
	unavailable: str | None = None
	for i in range(0, len(changes), batch_size):
		batch = changes[i : i + batch_size]
		res = triager.triage(batch, ai_config)
		all_changes.extend(res.changes)
		if tldr is None:
			tldr = res.tldr
		if unavailable is None:
			unavailable = res.unavailable
	return TriageResult(changes=all_changes, tldr=tldr, unavailable=unavailable)


def _build_ledger_digest(
	store: Store,
	config: Config,
	channels: set[str],
	tldr: str | None,
	unavailable: str | None,
) -> Digest:
	digest = rank(store.changes_for_digest(channels), _source_index(config), config)
	digest.tldr = tldr  # intentionally unrendered — no TL;DR preamble in any channel output
	digest.ai_unavailable = unavailable
	# Scan-scope footer: how many sources are watched and how many pages are under
	# baseline. Shown in every delivered digest so the reader sees the coverage.
	digest.sources_scanned = len(resolve_sources(config))
	digest.pages_watched = store.snapshot_count()
	return digest


def _deliver_into(store: Store, digest: Digest, config: Config, channels: set[str]) -> None:
	"""Open a digest, deliver per channel, record exactly the change ids each
	channel conveyed, then commit. A channel that fails is left for next run."""
	digest_id = store.open_digest()
	for channel in channels:
		try:
			delivered = NOTIFIERS.get(channel)().send(digest, config)
		except NotifyError:
			log.exception("channel %s delivery failed; leaving for next run", channel)
			continue
		for change_id in delivered:
			store.record_delivery(change_id, channel)
	# Supersede older undelivered rows for every delivered (source_id, url).
	for g in digest.groups:
		for m in g.members:
			if m.id is not None:
				store.supersede_older(m.source_id, m.url, m.id)
	store.commit_digest(digest_id)


def run_once(config: Config, *, force: bool = False, dry_run: bool = False) -> Digest:
	"""Run the full detection-to-delivery pipeline once.

	The digest is built from the ledger (substantive changes not yet delivered
	to every enabled channel), never from this-run detections, so a prior run's
	undelivered backlog is always retried. ``dry_run`` previews that standing
	backlog without detecting, persisting, superseding, sending, or marking the
	run successful.
	"""
	store = Store(db_path())
	store.migrate()
	# Fresh DB: import the shipped baseline seed so the first run diffs against it
	# instead of crawling every page. No-op once any snapshot exists, or if no
	# seed is bundled (the detectors then baseline fetch-free on first sight).
	seeded = apply_seed_if_empty(store)
	if seeded:
		log.info("imported baseline seed dated %s (%d snapshots)", seeded, store.snapshot_count())
	channels = _enabled_channels(config)
	with run_lock(data_path()):
		# Zero channels: nothing can be delivered. Do no reconcile/detect/triage/
		# send, but still advance the catch-up window on the live path so
		# re-enabling a channel later is not treated as "missed every cycle".
		if not channels:
			if not dry_run:
				store.mark_successful_run(datetime.now(UTC))
			return Digest(groups=[])

		# dry_run: render from the existing ledger only; mutate nothing.
		if dry_run:
			return _build_ledger_digest(store, config, channels, None, None)

		# Reconcile a crashed run: re-deliver the still-owed changes (per-channel
		# idempotent, so no resend), then commit the stale inflight digest.
		# Mirror the live-path empty-digest gate: only call _deliver_into when
		# there is something to send (or config.digest.empty == "send"), but
		# ALWAYS commit the inflight row so it does not recur on the next run.
		inflight = store.inflight_digest()
		if inflight is not None:
			recon = _build_ledger_digest(store, config, channels, None, None)
			recon_send_empty = recon.is_empty and config.digest.empty == "send"
			if not recon.is_empty or recon_send_empty:
				_deliver_into(store, recon, config, channels)
			store.commit_digest(inflight)

		# Catch-up gate: skip when the last successful run already covers this
		# cycle and we are not forced.
		if not _catch_up_due(store, config, force):
			return Digest(groups=[])

		sources = resolve_sources(config)
		log.info(
			"run starting: %d source(s), channels=%s, force=%s",
			len(sources),
			",".join(sorted(channels)) or "none",
			force,
		)
		fetcher = Fetcher(store, user_agent=USER_AGENT.format(version=__version__))
		t_detect = time.monotonic()
		changes = asyncio.run(_run_async(sources, store, fetcher))
		log.info("detection phase: %.1fs (%d changes)", time.monotonic() - t_detect, len(changes))
		for change in changes:
			change.id = store.record_change(change)  # idempotent on (source,url,hash)

		# Triage is WRITE-ONCE: only rows whose verdict is still NULL. Re-detected
		# rows already carry a final verdict and must not be re-triaged.
		untriaged = [c for c in changes if c.verdict is None]
		mode = config.ai.mode if config.ai.mode != "off" else "noop"
		t_triage = time.monotonic()
		result = _triage_batched(TRIAGERS.get(mode)(), untriaged, config.ai)
		log.info("triage phase: %.1fs (%d triaged)", time.monotonic() - t_triage, len(untriaged))
		for change in result.changes:
			if change.id is not None and change.verdict is not None:
				store.set_verdict(
					change.id,
					change.verdict,
					change.description,
					change.group_key,
					change.group_summary,
					change.group_title,
				)

		# Digest comes from the ledger (undelivered backlog), not this-run changes.
		digest = _build_ledger_digest(store, config, channels, result.tldr, result.unavailable)

		# Empty "nothing notable" digests go out at most once per catch-up window:
		# only here, where the gate was due and mark_successful_run (below) will
		# advance the window so the next empty run is not due. Non-empty digests
		# are idempotent via the delivery ledger regardless.
		send_empty = digest.is_empty and config.digest.empty == "send"
		if not digest.is_empty or send_empty:
			_deliver_into(store, digest, config, channels)

		store.mark_successful_run(datetime.now(UTC))
		log.info(
			"run finished: %d detected, %d in digest, delivered to %s",
			len(changes),
			digest.change_count(),
			",".join(sorted(channels)) or "none",
		)
		return digest
