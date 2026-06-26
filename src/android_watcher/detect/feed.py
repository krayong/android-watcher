from __future__ import annotations

import hashlib
import html as _html
import re
from urllib.parse import urlsplit, urlunsplit

from defusedxml import ElementTree as ET

from ..models import Change, Source
from .base import DETECTORS

_ATOM = "{http://www.w3.org/2005/Atom}"

# A whole feed title that is *only* a date (e.g. the AndroidX aggregate feed,
# which titles every entry "June 24, 2026"). Such a title makes a useless digest
# headline, so it is replaced by the library/version names from the summary.
_DATE_TITLE_RE = re.compile(
	r"^(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
	r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
	r"\d{1,2},\s+\d{4}$",
	re.IGNORECASE,
)
_LINK_TEXT_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _is_date_title(title: str) -> bool:
	return bool(_DATE_TITLE_RE.match(title.strip()))


def _summary_items(summary: str) -> list[str]:
	"""Library/version labels from a summary's <a> link texts, in order.

	"Media3 Version 1.11.0-alpha01" -> "Media3 1.11.0-alpha01" (the boilerplate
	word "Version" is dropped). Returns [] when the summary has no links.
	"""
	items: list[str] = []
	for raw in _LINK_TEXT_RE.findall(summary):
		text = _html.unescape(_TAG_RE.sub("", raw))
		text = re.sub(r"\bversion\b", "", text, flags=re.IGNORECASE)
		text = re.sub(r"\s+", " ", text).strip()
		if text:
			items.append(text)
	return items


def _synthesize_title(summary: str) -> str | None:
	"""A digest headline built from a date-titled entry's summary, or None.

	One library -> its label; several -> the first two labels then "+N more".
	"""
	items = _summary_items(summary)
	if not items:
		return None
	if len(items) == 1:
		return items[0]
	head = ", ".join(items[:2])
	rest = len(items) - 2
	return f"{head} +{rest} more" if rest else head


def _display_title(item: dict) -> str:
	"""The entry's own title, unless it is a bare date and the summary yields a
	better headline. Identity and the dedupe hash still use the original title."""
	title = item["title"]
	if _is_date_title(title):
		return _synthesize_title(item["summary"]) or title
	return title


def _normalize_link(link: str) -> str:
	parts = urlsplit(link.strip())
	# Strip query and fragment; keep scheme/host/path; drop trailing slash on path.
	path = parts.path.rstrip("/") or "/"
	return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _hash(title: str, summary: str) -> str:
	h = hashlib.sha256()
	h.update(title.strip().encode())
	h.update(b"\x00")
	h.update(summary.strip().encode())
	return h.hexdigest()


def _text(el: ET.Element | None) -> str:
	return (el.text or "").strip() if el is not None else ""


def _parse_items(xml: str) -> list[dict]:
	root = ET.fromstring(xml)
	items: list[dict] = []

	# Atom
	for entry in root.findall(f"{_ATOM}entry"):
		link_el = entry.find(f"{_ATOM}link")
		link = (link_el.get("href", "") if link_el is not None else "").strip()
		id_raw = _text(entry.find(f"{_ATOM}id"))
		title = _text(entry.find(f"{_ATOM}title"))
		summary_el = entry.find(f"{_ATOM}summary")
		if summary_el is None:
			summary_el = entry.find(f"{_ATOM}content")
		summary = _text(summary_el)
		# Atom <id> is always treated as a permalink identity (opaque IRI).
		items.append(
			{
				"id_raw": id_raw,
				"id_is_permalink": bool(id_raw),
				"link": link,
				"title": title,
				"summary": summary,
			}
		)

	# RSS (root tag is <rss> or <channel> is a child)
	channel = root.find("channel")
	if channel is None and root.tag == "channel":
		channel = root
	if channel is not None:
		for item in channel.findall("item"):
			guid_el = item.find("guid")
			guid_raw = _text(guid_el)
			is_permalink = (
				guid_el is not None
				and guid_el.get("isPermaLink", "true").lower() != "false"
				and bool(guid_raw)
			)
			link = _text(item.find("link"))
			title = _text(item.find("title"))
			summary = _text(item.find("description"))
			items.append(
				{
					"id_raw": guid_raw,
					"id_is_permalink": is_permalink,
					"link": link,
					"title": title,
					"summary": summary,
				}
			)

	return items


def _identity(item: dict) -> str:
	# Prefer a permalink id/guid; use it VERBATIM (an Atom <id> is an opaque IRI,
	# often a tag: URI that must not be URL-normalized). Only the link-URL
	# fallback is normalized. Never trust a non-permalink raw guid alone
	# (Medium/Blogger reuse them).
	if item["id_is_permalink"] and item["id_raw"]:
		return item["id_raw"]
	return _normalize_link(item["link"])


@DETECTORS.register("feed")
class FeedDetector:
	async def detect(self, source: Source, store, fetcher) -> list[Change]:
		url = source.feed_url or source.url
		res = await fetcher.fetch(url)
		if res.not_modified or not res.text:
			return []
		changes: list[Change] = []
		for item in _parse_items(res.text):
			identity = _identity(item)
			if not identity:
				continue
			content_hash = _hash(item["title"], item["summary"])
			title = _display_title(item)
			prior = store.seen_feed_item(source.id, identity)
			if prior is None:
				changes.append(
					Change(
						source_id=source.id,
						url=item["link"] or identity,
						change_kind="new",
						title=title,
						raw_diff=f"{item['title']}\n\n{item['summary']}".strip()[:500],
						fetched_hash=content_hash,
					)
				)
				store.upsert_seen_feed_item(source.id, identity, content_hash)
			elif prior != content_hash:
				changes.append(
					Change(
						source_id=source.id,
						url=item["link"] or identity,
						change_kind="updated",
						title=title,
						raw_diff=f"{item['title']}\n\n{item['summary']}".strip()[:500],
						fetched_hash=content_hash,
					)
				)
				store.upsert_seen_feed_item(source.id, identity, content_hash)
		return changes
