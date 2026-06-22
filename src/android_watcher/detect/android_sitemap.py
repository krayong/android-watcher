"""Host-agnostic sitemap detector.

Parses a host's sitemap (a ``<sitemapindex>`` of shards, or a single
``<urlset>``) once per run via a run-scoped cache held on the Fetcher instance
and keyed by the sitemap-index URL (derived from each source's host). Several
sources on the same host share one download; different hosts each get their own.
Originally specific to developer.android.com (hence the registered name), it now
also serves source.android.com, developers.google.com, kotlinlang.org, etc.

English only: locale-prefixed URLs (/fr/...) and ``?hl=<non-en>`` variants are
dropped at parse time, so only canonical English pages are ever watched.

Per-source filtering (applied against the shared, cached entry list):
- ``path_prefix``     — include only URLs under this path ("" = whole host).
- ``exclude_prefixes``— drop URLs under any of these paths.
- ``require_segment`` — keep only URLs whose path has a matching segment
                        (``seg == s`` or ``seg startswith s-``); e.g. "android".
- ``reference_mode``  — keep | drop | index_only for /reference docs. index_only
                        keeps only index/summary pages (Kotlin-preferred), so
                        the huge per-symbol class/function reference is dropped.
- most-specific-prefix-wins: a URL under a nested same-host source's longer
  prefix belongs to that source.
- version-dedup: URLs differing only by a dotted version segment (9.4) or a
  ?version=/?api= query collapse to the latest; bare-integer paths like
  /about/versions/14 are untouched (distinct releases).

Baseline / change semantics:
- First sight of a brand-new URL is baselined fetch-free (sitemap lastmod, empty
  content_hash, no fetch) when the source has no baseline yet. Once a baseline
  exists, a never-seen URL is content-confirmed and reported as Change("new").
- An already-baselined URL whose lastmod moves is content-confirmed; a real
  content move is Change("updated"). lastmod alone never emits a Change.

Public API (consumed by doctor, the seed builder, and the catalog verify-script):
- ``load_sitemap(fetcher, index_url=INDEX_URL)`` — flat [(loc, lastmod), ...].
- ``prefix_count(entries, prefix)`` — count matching URLs (0 => stale prefix).
- ``make_shard_cache()`` — factory; ``ShardCache.load(fetcher, index_url)``.
- ``baseline_all(source, store, fetcher)`` — full-content baseline (seed builder).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from urllib.parse import parse_qs, urlsplit

from defusedxml.ElementTree import iterparse

from ..catalog import load_catalog
from ..models import Change, Source
from .base import DETECTORS
from .sitemap import confirm_candidate

logger = logging.getLogger(__name__)

_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
INDEX_URL = "https://developer.android.com/sitemap.xml"

# Leading locale path segment (/fr/..., /pt-br/...) — matched against an explicit
# set rather than a 2-letter regex so real sections like /tv, /xr, /ai survive.
_LOCALES = frozenset(
	{
		"ar",
		"bn",
		"de",
		"en",
		"es",
		"es-419",
		"fa",
		"fr",
		"he",
		"hi",
		"id",
		"in",
		"it",
		"iw",
		"ja",
		"ko",
		"ms",
		"pl",
		"pt",
		"pt-br",
		"ru",
		"th",
		"tr",
		"uk",
		"vi",
		"zh-cn",
		"zh-hk",
		"zh-tw",
	}
)

# Reserved reference index/summary leaf names (everything else under /reference
# is a per-symbol class/function page). Derived empirically from the sitemap.
_REFERENCE_INDEX_LEAVES = frozenset(
	{"package-summary", "packages", "classes", "composables", "modifiers"}
)

# A dotted version segment (9.4, 8.13.0). Requires a dot, so bare integers like
# /about/versions/14 (distinct Android releases) are NOT treated as versions.
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")

# Non-HTML asset extensions. Sitemaps list PDFs, images, archives, etc.; fetching
# them as text feeds binary to the HTML parser (it throws) and pollutes baselines
# with binary "excerpts" (the source of NUL bytes). Never watch these.
_BINARY_EXT = (
	".pdf",
	".png",
	".jpg",
	".jpeg",
	".gif",
	".svg",
	".webp",
	".ico",
	".bmp",
	".mp4",
	".webm",
	".mov",
	".zip",
	".tar",
	".gz",
	".tgz",
	".jar",
	".aar",
	".apk",
	".aab",
	".woff",
	".woff2",
	".ttf",
	".otf",
	".eot",
	".css",
	".js",
	".wasm",
	".bin",
	".dmg",
	".exe",
)

# >= this many prefix URLs sharing one NEW lastmod => treat as a section
# regeneration and collapse to a single candidate.
BULK_COLLAPSE_THRESHOLD = 3


def _is_localized(loc: str) -> bool:
	"""True if *loc* is a translation: a leading locale path segment, or an
	`hl` query parameter requesting any language other than English."""
	parts = urlsplit(loc)
	seg = parts.path.split("/", 2)
	if len(seg) > 1 and seg[1] in _LOCALES:
		return True
	hl = parse_qs(parts.query).get("hl")
	return bool(hl) and hl[0] != "en"


def _iter_shard(xml: str):
	"""Stream <url> elements, yield (loc, lastmod) for canonical English URLs.

	Uses iterparse + element.clear() so a 34 MB shard never materializes as a
	DOM. No path filtering here — one cached parse serves every source on the
	host; per-source filtering happens later against the cached list.
	"""
	loc = lastmod = ""
	for _event, el in iterparse(io.StringIO(xml), events=("end",)):
		tag = el.tag
		if tag == f"{_SM}loc":
			loc = (el.text or "").strip()
		elif tag == f"{_SM}lastmod":
			lastmod = (el.text or "").strip()
		elif tag == f"{_SM}url":
			if loc and not _is_localized(loc):
				yield loc, lastmod
			loc = lastmod = ""
			el.clear()


def _index_url_for(source: Source) -> str:
	"""Derive the sitemap-index URL from a source's host: <scheme>://<host>/sitemap.xml."""
	p = urlsplit(source.url)
	return f"{p.scheme}://{p.netloc}/sitemap.xml"


class ShardCache:
	"""Run-scoped parse of one host's sitemap (index-of-shards or single urlset)."""

	def __init__(self) -> None:
		self._loaded = False
		self._lock = asyncio.Lock()  # serializes concurrent population
		self.entries: list[tuple[str, str]] = []  # flat (loc, lastmod)

	async def load(self, fetcher, index_url: str = INDEX_URL) -> list[tuple[str, str]]:
		# CONCURRENCY: many sources on a host call load() concurrently in one run.
		# Guard population with an asyncio.Lock so the FIRST caller fetches once and
		# the rest await the in-flight load. Double-checked: fast path skips the lock.
		if self._loaded:
			return self.entries
		async with self._lock:
			if self._loaded:
				return self.entries
			t0 = time.monotonic()
			idx = await fetcher.fetch(index_url, conditional=True)
			text = idx.text if not idx.not_modified else ""
			if text and "<sitemapindex" in text[:500]:
				await self._load_index(fetcher, text)
			elif text:
				# A bare <urlset> served directly at the sitemap URL (single shard).
				self.entries.extend(_iter_shard(text))
				logger.info(
					"sitemap: %d urls (single urlset) in %.1fs",
					len(self.entries),
					time.monotonic() - t0,
				)
			self._loaded = True
			return self.entries

	async def _load_index(self, fetcher, index_text: str) -> None:
		shard_locs: list[str] = []
		for _e, el in iterparse(io.StringIO(index_text), events=("end",)):
			if el.tag == f"{_SM}loc" and el.text:
				shard_locs.append(el.text.strip())
			if el.tag == f"{_SM}sitemap":
				el.clear()
		total = len(shard_locs)
		logger.info("sitemap index: %d shard(s)", total)
		t = time.monotonic()

		async def _fetch(n: int, url: str):
			ts = time.monotonic()
			res = await fetcher.fetch(url, conditional=True)
			return n, res, time.monotonic() - ts

		tasks = [asyncio.create_task(_fetch(n, u)) for n, u in enumerate(shard_locs, 1)]
		for fut in asyncio.as_completed(tasks):
			n, res, dt = await fut
			if res.not_modified or not res.text:
				logger.info("  shard %d/%d: 304/empty in %.1fs", n, total, dt)
				continue
			before = len(self.entries)
			self.entries.extend(_iter_shard(res.text))
			logger.info(
				"  shard %d/%d: %dKB, %d urls, %.1fs",
				n,
				total,
				len(res.text) // 1024,
				len(self.entries) - before,
				dt,
			)
		logger.info(
			"sitemap loaded: %d urls from %d shard(s) in %.1fs",
			len(self.entries),
			total,
			time.monotonic() - t,
		)

	def stale_prefix(self, prefix: str) -> bool:
		return prefix_count(self.entries, prefix) == 0


# ---- public API -------------------------------------------------------------


def make_shard_cache() -> ShardCache:
	return ShardCache()


async def load_sitemap(fetcher, index_url: str = INDEX_URL) -> list[tuple[str, str]]:
	"""Parse a host's sitemap once per run; cached on the Fetcher per index URL."""
	return await _cache_for(fetcher, index_url).load(fetcher, index_url)


def prefix_count(entries: list[tuple[str, str]], prefix: str) -> int:
	"""How many sitemap URLs fall under a path prefix (0 => stale prefix)."""
	if not prefix:
		return len(entries)
	return sum(1 for loc, _lastmod in entries if urlsplit(loc).path.startswith(prefix))


def _cache_for(fetcher, index_url: str = INDEX_URL) -> ShardCache:
	caches = getattr(fetcher, "_shard_caches", None)
	if caches is None:
		caches = {}
		fetcher._shard_caches = caches
	cache = caches.get(index_url)
	if cache is None:
		cache = make_shard_cache()
		caches[index_url] = cache
	return cache


def _representative(locs: list[str]) -> str:
	"""Pick the shortest-path URL for a cluster as its canonical representative."""
	return min(locs, key=lambda loc: len(urlsplit(loc).path))


def _descendant_prefixes(source: Source) -> set[str]:
	"""Path prefixes of OTHER enabled same-host sitemap sources nested strictly
	under this source's prefix (most-specific-prefix-wins)."""
	out: set[str] = set()
	src_index = _index_url_for(source)
	for cat_src in load_catalog():
		if (
			cat_src.detector == "android_sitemap"
			and cat_src.enabled
			and cat_src.id != source.id
			and cat_src.path_prefix
			and _index_url_for(cat_src) == src_index
			and len(cat_src.path_prefix) > len(source.path_prefix)
			and cat_src.path_prefix.startswith(source.path_prefix)
		):
			out.add(cat_src.path_prefix)
	return out


def _is_reference(path: str) -> bool:
	return "reference" in path.strip("/").split("/")


def _kotlin_twin(path: str) -> str | None:
	"""The Kotlin-variant path for a Java reference URL, or None if already Kotlin
	(or not a reference URL). Inserts 'kotlin' right after the 'reference' segment."""
	segs = path.strip("/").split("/")
	if "reference" not in segs:
		return None
	i = segs.index("reference")
	if i + 1 < len(segs) and segs[i + 1] == "kotlin":
		return None
	return "/" + "/".join(segs[: i + 1] + ["kotlin"] + segs[i + 1 :])


def _watched(source: Source, loc: str, all_paths: set[str]) -> bool:
	"""Whether *loc* passes this source's include/exclude/segment/reference filters."""
	path = urlsplit(loc).path
	if path.lower().endswith(_BINARY_EXT):
		return False  # PDFs, images, archives, etc. are not text pages
	if source.path_prefix and not path.startswith(source.path_prefix):
		return False
	if any(path.startswith(x) for x in source.exclude_prefixes):
		return False
	if source.require_segment:
		rs = source.require_segment
		if not any(s == rs or s.startswith(rs + "-") for s in path.strip("/").split("/")):
			return False
	if _is_reference(path):
		mode = source.reference_mode
		if mode == "drop":
			return False
		if mode == "index_only":
			leaf = path.rstrip("/").split("/")[-1]
			if leaf not in _REFERENCE_INDEX_LEAVES:
				return False
			twin = _kotlin_twin(path)  # Kotlin-preferred: drop Java if a Kotlin twin exists
			if twin is not None and twin in all_paths:
				return False
	return True


def _version_key(loc: str):
	"""(canonical-group, version-tuple) if *loc* carries a dotted version segment
	or a numeric ?version=/?api= query, else (None, None)."""
	parts = urlsplit(loc)
	segs = parts.path.strip("/").split("/")
	for i, s in enumerate(segs):
		if _VERSION_RE.match(s):
			canon = "/" + "/".join(segs[:i] + ["*"] + segs[i + 1 :]) + "?" + parts.query
			return canon, tuple(int(x) for x in s.split("."))
	q = parse_qs(parts.query)
	for key in ("version", "api", "apilevel"):
		vals = q.get(key)
		if vals and vals[0].replace(".", "").isdigit():
			base = parts.path + "|" + key
			v = vals[0]
			return base, (tuple(int(x) for x in v.split(".")) if "." in v else (int(v),))
	return None, None


def _dedup_versions(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
	"""Collapse URLs differing only by a dotted version / version query to the
	latest; everything without a detectable version passes through unchanged."""
	best: dict[str, tuple[tuple[int, ...], str, str]] = {}
	passthrough: list[tuple[str, str]] = []
	for loc, lastmod in items:
		canon, vkey = _version_key(loc)
		if canon is None:
			passthrough.append((loc, lastmod))
			continue
		cur = best.get(canon)
		if cur is None or vkey > cur[0]:
			best[canon] = (vkey, loc, lastmod)
	return passthrough + [(loc, lastmod) for _v, loc, lastmod in best.values()]


def _gather_watched(source: Source, entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
	"""Apply all source filters + most-specific-prefix dedup + version-dedup."""
	all_paths = {urlsplit(loc).path for loc, _ in entries}
	descendants = _descendant_prefixes(source)
	out: list[tuple[str, str]] = []
	for loc, lastmod in entries:
		path = urlsplit(loc).path
		if any(path.startswith(dp) for dp in descendants):
			continue
		if not _watched(source, loc, all_paths):
			continue
		out.append((loc, lastmod))
	return _dedup_versions(out)


async def baseline_all(source: Source, store, fetcher) -> int:
	"""Full-content baseline for the seed builder: fetch + hash EVERY watched URL
	for this source (no fetch-free shortcut), recording a snapshot.

	Returns the number newly baselined. Resumable: URLs already baselined with a
	non-empty content_hash are skipped. Confirm-fetches run concurrently (the
	Fetcher's semaphore + crawl delay bound the rate); progress logs every 100.
	"""
	entries = await load_sitemap(fetcher, _index_url_for(source))
	todo = [
		(loc, lastmod)
		for loc, lastmod in _gather_watched(source, entries)
		if not ((snap := store.get_snapshot(source.id, loc)) is not None and snap.content_hash)
	]
	total = len(todo)
	if not total:
		return 0
	logger.info("%s: baselining %d URL(s)…", source.id, total)
	tasks = [
		asyncio.create_task(confirm_candidate(source, store, fetcher, loc, lastmod))
		for loc, lastmod in todo
	]
	done = 0
	for fut in asyncio.as_completed(tasks):
		await fut
		done += 1
		if done % 100 == 0 or done == total:
			logger.info("%s: baselined %d/%d", source.id, done, total)
	return total


@DETECTORS.register("android_sitemap")
class AndroidSitemapDetector:
	async def detect(self, source: Source, store, fetcher) -> list[Change]:
		entries = await load_sitemap(fetcher, _index_url_for(source))

		# Stale-prefix health check: a non-empty prefix that matches nothing is a
		# config problem, not a change. Log and return []; doctor surfaces it.
		if source.path_prefix and prefix_count(entries, source.path_prefix) == 0:
			logger.warning(
				"android_sitemap: path_prefix %r matches zero sitemap URLs for %r",
				source.path_prefix,
				source.id,
			)
			return []

		watched = _gather_watched(source, entries)
		has_baseline = store.source_has_snapshots(source.id)

		# Partition: brand-new URLs (genuinely new vs first-ever baseline) and
		# already-seen URLs whose lastmod moved.
		new_pages: list[tuple[str, str]] = []  # new after baseline -> Change("new")
		fetchfree: list[tuple[str, str]] = []  # first-ever baseline -> silent
		candidates: list[tuple[str, str]] = []  # lastmod moved -> Change("updated")
		for loc, lastmod in watched:
			snap = store.get_snapshot(source.id, loc)
			if snap is None:
				(new_pages if has_baseline else fetchfree).append((loc, lastmod))
				continue
			if lastmod and snap.lastmod == lastmod:
				continue
			candidates.append((loc, lastmod))

		# First-ever baseline: record lastmod with empty hash, no fetch.
		for loc, lastmod in fetchfree:
			store.upsert_snapshot(
				source.id, loc, signal_type="sitemap", content_hash="", lastmod=lastmod, excerpt=""
			)
		if fetchfree:
			logger.info("%s: %d new URL(s) baselined fetch-free", source.id, len(fetchfree))

		changes: list[Change] = []

		# Genuinely new URLs (after baseline): confirm-fetch + report as "new".
		if new_pages:
			logger.info("%s: %d new page(s) -> confirm…", source.id, len(new_pages))
			tasks = [
				asyncio.create_task(
					confirm_candidate(source, store, fetcher, loc, lastmod, emit_new=True)
				)
				for loc, lastmod in new_pages
			]
			for fut in asyncio.as_completed(tasks):
				ch = await fut
				if ch is not None:
					changes.append(ch)

		if not candidates:
			return changes

		# Updated URLs: cluster by lastmod, collapse bulk section regenerations.
		by_lastmod: dict[str, list[str]] = {}
		for loc, lastmod in candidates:
			by_lastmod.setdefault(lastmod, []).append(loc)
		fetches = sum(
			1 if len(locs) >= BULK_COLLAPSE_THRESHOLD else len(locs) for locs in by_lastmod.values()
		)
		logger.info(
			"%s: %d candidate(s), %d cluster(s) -> ~%d confirm-fetch(es)…",
			source.id,
			len(candidates),
			len(by_lastmod),
			fetches,
		)
		for lastmod, locs in by_lastmod.items():
			if len(locs) >= BULK_COLLAPSE_THRESHOLD:
				rep = _representative(locs)
				change = await confirm_candidate(source, store, fetcher, rep, lastmod)
				for loc in locs:
					if loc != rep:
						snap = store.get_snapshot(source.id, loc)
						prior = snap.content_hash if snap is not None else ""
						excerpt = snap.excerpt if snap is not None else ""
						store.upsert_snapshot(
							source.id,
							loc,
							signal_type="sitemap",
							content_hash=prior,
							lastmod=lastmod,
							excerpt=excerpt,
						)
				if change is not None:
					changes.append(change)
			else:
				for loc in locs:
					change = await confirm_candidate(source, store, fetcher, loc, lastmod)
					if change is not None:
						changes.append(change)

		return changes
