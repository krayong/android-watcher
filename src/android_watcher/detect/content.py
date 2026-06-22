"""Content-hash detector.

Fetches a page, extracts and normalises the selected region, and emits an
"updated" Change when the normalised text moves.  Cosmetic edits (CSS classes,
whitespace, attributes) leave the normalised text — and therefore the hash —
unchanged, so they never raise a signal.

Empty-render guard: a page whose normalised main-text falls below
``EMPTY_RENDER_THRESHOLD`` is a client-rendered shell, a HEALTH condition rather
than a change.  The detector refuses to baseline it and emits no Change; it logs
a warning and ``doctor`` surfaces the condition.
"""

from __future__ import annotations

import difflib
import logging

from ..models import Change, Source
from ._normalize import (
	EMPTY_RENDER_THRESHOLD,
	content_hash,
	extract_main,
	extract_title,
	normalize_text,
)
from .base import DETECTORS

log = logging.getLogger(__name__)


@DETECTORS.register("content")
class ContentDetector:
	async def detect(self, source: Source, store, fetcher) -> list[Change]:
		res = await fetcher.fetch(source.url, conditional=True)
		if res.not_modified or not res.text:
			return []

		text = normalize_text(extract_main(res.text, source.content_selector))

		if len(text) < EMPTY_RENDER_THRESHOLD:
			# Health, not changes: a JS shell renders almost no text. Do NOT
			# baseline (so we never lock in an empty hash) and do NOT emit a
			# Change. doctor's content-render check surfaces the condition.
			log.warning(
				"content detector: %s rendered empty (%d chars < %d) — "
				"likely a JS shell; not baselined",
				source.url,
				len(text),
				EMPTY_RENDER_THRESHOLD,
			)
			return []

		new_hash = content_hash(text)
		snap = store.get_snapshot(source.id, source.url)

		if snap is None:
			# First sight: baseline silently (snapshot only, no Change).
			store.upsert_snapshot(
				source.id,
				source.url,
				signal_type="content",
				content_hash=new_hash,
				lastmod="",
				excerpt=text[:500],
			)
			return []

		if snap.content_hash == new_hash:
			return []

		raw_diff = "\n".join(
			difflib.unified_diff(
				snap.excerpt.splitlines(),
				text.splitlines(),
				fromfile="before",
				tofile="after",
				lineterm="",
			)
		)

		store.upsert_snapshot(
			source.id,
			source.url,
			signal_type="content",
			content_hash=new_hash,
			lastmod="",
			excerpt=text[:500],
		)
		return [
			Change(
				source_id=source.id,
				url=source.url,
				change_kind="updated",
				title=extract_title(res.text) or source.name,
				raw_diff=raw_diff[:2000],
				fetched_hash=new_hash,
			)
		]
