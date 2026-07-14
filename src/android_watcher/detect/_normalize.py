"""Content normalisation helpers shared across detectors.

Uses only stdlib (``html.parser``) — no third-party HTML library required.
Later detectors (sitemap content-confirm, etc.) import from here.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from html.parser import HTMLParser

# A page whose normalised main-text falls below this character count is treated
# as a client-side render shell.  The content detector refuses to baseline it
# and emits no Change; ``doctor`` surfaces the condition separately.
EMPTY_RENDER_THRESHOLD: int = 50

# Tags whose text content we discard entirely.
_SKIP_TAGS: frozenset[str] = frozenset({"script", "style", "noscript", "template"})


class _TextExtractor(HTMLParser):
	"""Walk HTML and collect visible text, discarding script/style."""

	def __init__(self) -> None:
		super().__init__(convert_charrefs=True)
		self._skip_depth: int = 0
		self._parts: list[str] = []

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if tag in _SKIP_TAGS:
			self._skip_depth += 1

	def handle_endtag(self, tag: str) -> None:
		if tag in _SKIP_TAGS and self._skip_depth > 0:
			self._skip_depth -= 1

	def handle_data(self, data: str) -> None:
		if self._skip_depth == 0:
			stripped = data.strip()
			if stripped:
				self._parts.append(stripped)

	def text(self) -> str:
		return " ".join(self._parts)


class _SelectorExtractor(HTMLParser):
	"""Extract the inner HTML of the first element matching a simple selector.

	Supported selector forms (the subset needed by this project):
	- ``#id``   — matches the first element with that id attribute.
	- ``tag``   — matches the first element with that tag name.
	- ``""``    — no selector; returns the full HTML unchanged (caller should
	              pass the raw HTML back to ``_TextExtractor``).
	"""

	def __init__(self, selector: str) -> None:
		super().__init__(convert_charrefs=False)
		self._selector = selector.strip()
		self._match_id: str | None = None
		self._match_tag: str | None = None

		if self._selector.startswith("#"):
			self._match_id = self._selector[1:]
		elif self._selector:
			self._match_tag = self._selector

		self._depth: int = 0  # nesting depth inside the matched element
		self._capturing: bool = False
		self._found: bool = False
		self._raw_parts: list[str] = []

	def _matches(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
		if self._match_id is not None:
			attr_dict = dict(attrs)
			return attr_dict.get("id") == self._match_id
		if self._match_tag is not None:
			return tag == self._match_tag
		return False

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if self._found:
			return
		if self._capturing:
			self._depth += 1
			# Re-serialise the opening tag so inner elements are kept.
			attr_str = "".join(f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs)
			self._raw_parts.append(f"<{tag}{attr_str}>")
			return
		if self._matches(tag, attrs):
			self._capturing = True
			self._depth = 1

	def handle_endtag(self, tag: str) -> None:
		if not self._capturing or self._found:
			return
		self._depth -= 1
		if self._depth == 0:
			self._capturing = False
			self._found = True
		else:
			self._raw_parts.append(f"</{tag}>")

	def handle_data(self, data: str) -> None:
		if self._capturing and not self._found:
			self._raw_parts.append(data)

	def handle_entityref(self, name: str) -> None:  # type: ignore[override]
		if self._capturing and not self._found:
			self._raw_parts.append(f"&{name};")

	def handle_charref(self, name: str) -> None:  # type: ignore[override]
		if self._capturing and not self._found:
			self._raw_parts.append(f"&#{name};")

	def fragment(self) -> str | None:
		"""Return the captured inner HTML, or None if no match was found."""
		return "".join(self._raw_parts) if self._found else None


def extract_main(html: str, selector: str = "") -> str:
	"""Return the HTML fragment addressed by *selector*, or *html* if no match.

	Selector forms: ``#id``, ``tagname``, or ``""`` (whole document).
	Falls back to the full *html* when the selector matches nothing so the
	caller always gets something to normalise.
	"""
	if not selector:
		return html
	extractor = _SelectorExtractor(selector)
	extractor.feed(html)
	return extractor.fragment() or html


def normalize_text(html_fragment: str) -> str:
	"""Strip all markup and attributes; collapse whitespace to single spaces.

	Two fragments that differ only in CSS classes, random data attributes, or
	extra whitespace will produce the same normalised string and therefore the
	same :func:`content_hash`.
	"""
	parser = _TextExtractor()
	parser.feed(html_fragment)
	text = parser.text()
	# Collapse any remaining internal whitespace runs.
	return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
	"""SHA-256 of the normalised text (hex string)."""
	return hashlib.sha256(text.encode()).hexdigest()


def _sentences(text: str) -> list[str]:
	"""Split collapsed one-line text into sentence-ish units for diffing.

	``normalize_text`` flattens newlines, so a line-based diff would treat each
	page as a single line and report the whole thing as changed. Splitting on
	sentence boundaries gives units that align: shared nav/boilerplate sentences
	are identical run-to-run and drop out of the diff, leaving the body change.
	"""
	return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def diff_excerpt(old_text: str, new_text: str, *, cap: int = 2000) -> str:
	"""A unified diff of two normalized page texts, length-capped for triage.

	Unchanged shared content (site nav, headers, boilerplate) matches and is
	excluded; only the changed sentences carry ``+``/``-`` markers. When there is
	no prior text to diff against (a first content capture), fall back to a plain
	excerpt of the new text so triage still gets something to read.
	"""
	if not old_text:
		return new_text[:cap]
	diff = "\n".join(
		difflib.unified_diff(
			_sentences(old_text),
			_sentences(new_text),
			fromfile="before",
			tofile="after",
			lineterm="",
		)
	)
	return diff[:cap]


class _TitleExtractor(HTMLParser):
	"""Capture the text inside the first <title> element."""

	def __init__(self) -> None:
		super().__init__(convert_charrefs=True)
		self._in = False
		self._done = False
		self._parts: list[str] = []

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if tag == "title" and not self._done:
			self._in = True

	def handle_endtag(self, tag: str) -> None:
		if tag == "title" and self._in:
			self._in = False
			self._done = True

	def handle_data(self, data: str) -> None:
		if self._in:
			self._parts.append(data)

	def title(self) -> str:
		return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def extract_title(html: str) -> str:
	"""The page's <title> text (whitespace-collapsed), or "" if absent/unparsable.

	The stdlib HTML parser raises on some binary payloads; callers treat a
	failure as "no title", so this never propagates an exception.
	"""
	parser = _TitleExtractor()
	try:
		parser.feed(html)
	except Exception:  # noqa: BLE001 - binary/garbage HTML => no title
		return ""
	return parser.title()
