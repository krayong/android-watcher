"""Generic sitemap detector: candidate-then-confirm.

A <lastmod> bump in the sitemap is a *candidate* — it is never recorded as a
Change on its own.  The page is fetched and its normalized content is hashed;
only a real content-hash move produces a Change.

If the confirm fetch returns 304 (ETag/If-Modified-Since unchanged), the
sitemap lastmod is bumped in the snapshot (so the candidate quiesces next run)
but no Change is emitted.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import defusedxml.ElementTree as ET

from ..models import Change, Source
from ._normalize import (
	EMPTY_RENDER_THRESHOLD,
	content_hash,
	extract_main,
	extract_title,
	normalize_text,
)
from .base import DETECTORS

logger = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _parse_urlset(xml_text: str) -> list[tuple[str, str]]:
	"""Return [(loc, lastmod), ...] from a sitemap <urlset> XML string.

	Missing or empty <lastmod> values are returned as empty strings.
	"""
	root = ET.fromstring(xml_text)
	entries: list[tuple[str, str]] = []
	for url_el in root.findall(f"{{{_SITEMAP_NS}}}url"):
		loc_el = url_el.find(f"{{{_SITEMAP_NS}}}loc")
		lastmod_el = url_el.find(f"{{{_SITEMAP_NS}}}lastmod")
		if loc_el is None or not (loc_el.text or "").strip():
			continue
		loc = loc_el.text.strip()
		lastmod = (lastmod_el.text or "").strip() if lastmod_el is not None else ""
		entries.append((loc, lastmod))
	return entries


def _matches_prefix(loc: str, prefix: str) -> bool:
	if not prefix:
		return True
	return urlsplit(loc).path.startswith(prefix)


async def confirm_candidate(
	source: Source,
	store: object,
	fetcher: object,
	loc: str,
	lastmod: str,
	*,
	emit_new: bool = False,
) -> Change | None:
	"""Per-URL confirm that never raises. Any failure — robots-blocked
	(``Disallowed``), a binary masquerading as HTML (the stdlib HTML parser
	throws on it), a transport error — is logged and skipped, so one bad URL
	cannot abort a source's whole detection run."""
	try:
		return await _confirm_candidate(source, store, fetcher, loc, lastmod, emit_new=emit_new)
	except Exception as exc:  # noqa: BLE001 - per-URL isolation is the point
		logger.warning("confirm_candidate skipped %r: %s", loc, exc)
		return None


async def _confirm_candidate(
	source: Source,
	store: object,
	fetcher: object,
	loc: str,
	lastmod: str,
	*,
	emit_new: bool = False,
) -> Change | None:
	"""Fetch *loc*, hash normalized content, return a Change only on a real move.

	Contract (pinned):
	- First content capture (never seen, or fetch-free baseline with an empty
	  content_hash): baseline silently, return None — UNLESS ``emit_new`` is set
	  (a genuinely new URL discovered after a baseline exists), in which case the
	  captured content is returned as Change(change_kind="new").
	- Identical re-confirm (hash unchanged): return None.
	- Content-hash move: return Change(change_kind="updated").
	- 304 from server: persist new lastmod (quiesce), return None.
	"""
	res = await fetcher.fetch(loc, conditional=True)  # type: ignore[union-attr]
	snap = store.get_snapshot(source.id, loc)  # type: ignore[union-attr]

	if res.not_modified:
		# Server confirms content is unchanged despite a lastmod advance.
		# Persist the new lastmod so this candidate won't re-fire next run.
		if snap is not None:
			store.upsert_snapshot(  # type: ignore[union-attr]
				source.id,
				loc,
				signal_type="sitemap",
				content_hash=snap.content_hash,
				lastmod=lastmod,
				excerpt=snap.excerpt,
			)
		return None

	text = normalize_text(extract_main(res.text, source.content_selector))

	if len(text) < EMPTY_RENDER_THRESHOLD:
		logger.warning(
			"sitemap detector: page at %r returned a JS-shell (text length %d < %d) — "
			"skipping baseline/change; doctor will surface this",
			loc,
			len(text),
			EMPTY_RENDER_THRESHOLD,
		)
		return None

	new_hash = content_hash(text)
	# The page's own <title> names the change; the source name is only a fallback
	# (so a digest never reads "Android Open Source Project" for every page).
	title = extract_title(res.text) or source.name

	# First content capture is silent: either the URL was never seen, or it was
	# baselined fetch-free on a prior run (empty content_hash) and this is its
	# first real fetch. A Change requires a genuine prior content hash that
	# moved — a lastmod bump alone never counts.
	first_capture = snap is None or not snap.content_hash
	store.upsert_snapshot(  # type: ignore[union-attr]
		source.id,
		loc,
		signal_type="sitemap",
		content_hash=new_hash,
		lastmod=lastmod,
		excerpt=text[:500],
	)

	if first_capture:
		if emit_new:
			# Genuinely new URL after baseline: report its first capture as "new".
			return Change(
				source_id=source.id,
				url=loc,
				change_kind="new",
				title=title,
				raw_diff=text[:500],
				fetched_hash=new_hash,
			)
		return None  # baseline silently

	if snap.content_hash == new_hash:
		return None  # content re-confirmed identical

	return Change(
		source_id=source.id,
		url=loc,
		change_kind="updated",
		title=title,
		raw_diff=text[:500],
		fetched_hash=new_hash,
	)


@DETECTORS.register("sitemap")
class SitemapDetector:
	async def detect(self, source: Source, store: object, fetcher: object) -> list[Change]:
		res = await fetcher.fetch(source.url, conditional=True)  # type: ignore[union-attr]
		if res.not_modified or not res.text:
			return []

		entries = _parse_urlset(res.text)
		matched = [
			(loc, lastmod) for loc, lastmod in entries if _matches_prefix(loc, source.path_prefix)
		]

		if source.path_prefix and not matched:
			logger.warning(
				"sitemap detector: no URLs matched path_prefix %r for source %r — "
				"check the prefix or the sitemap URL",
				source.path_prefix,
				source.id,
			)
			return []

		changes: list[Change] = []
		for loc, lastmod in matched:
			snap = store.get_snapshot(source.id, loc)  # type: ignore[union-attr]
			# Skip if lastmod is present and unchanged — not even a candidate
			if snap is not None and lastmod and snap.lastmod == lastmod:
				continue
			change = await confirm_candidate(source, store, fetcher, loc, lastmod)
			if change is not None:
				changes.append(change)

		return changes
