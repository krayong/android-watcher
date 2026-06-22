"""Render a Digest into email (HTML + plaintext), Telegram, and Slack blocks."""

from __future__ import annotations

import html as _html

from android_watcher.models import Digest, DigestGroup
from android_watcher.notify.html import clean_label, render_html
from android_watcher.rank import by_category


def _banner_text(digest: Digest) -> str | None:
	return f"AI unavailable: {digest.ai_unavailable}" if digest.ai_unavailable else None


def _scan_footer(digest: Digest) -> str | None:
	"""Scan-scope line shown at the foot of every delivered digest, or None."""
	if not digest.sources_scanned:
		return None
	return f"Scanned {digest.sources_scanned} Sources · Watching {digest.pages_watched:,} Pages"


def _points_plaintext(digest: Digest) -> list[str]:
	lines: list[str] = []
	for _cid, label, groups in by_category(digest.groups):
		lines.append(f"## {label}")
		for g in groups:
			pages = f" ({g.page_count} pages)" if g.page_count > 1 else ""
			lines.append(f"- {g.title}{pages} [{g.source_id}/{g.change_kind}]")
			if g.summary:
				lines.append(f"    {g.summary}")
	return lines


def render_email(digest: Digest) -> tuple[str, str]:
	html = render_html(digest)
	p_lines: list[str] = []
	banner = _banner_text(digest)
	if banner:
		p_lines.append(banner)
	p_lines.append("Android Watcher Digest")
	if digest.is_empty:
		p_lines.append("Nothing notable changed.")
	else:
		p_lines.extend(_points_plaintext(digest))
	footer = _scan_footer(digest)
	if footer:
		p_lines.append(footer)
	return html, "\n".join(p_lines)


_TELEGRAM_LIMIT = 4096


def render_telegram(digest: Digest) -> str:
	"""Render a digest as an HTML-formatted Telegram message.

	Uses parse_mode=HTML. User/content text is html-escaped. If the assembled
	message would exceed Telegram's 4096-character limit, groups are dropped
	(trailing ones first) and a "…(N more)" note is appended.
	"""
	parts: list[str] = ["<b>Android Watcher Digest</b>"]
	banner = _banner_text(digest)
	if banner:
		parts.insert(0, f"<b>{_html.escape(banner)}</b>")
	scan = _scan_footer(digest)
	scan_line = f"<i>{_html.escape(scan)}</i>" if scan else None
	if digest.is_empty:
		parts.append("Nothing notable changed.")
		if scan_line:
			parts.append(scan_line)
		return "\n".join(parts)

	item_lines: list[str] = []
	for g in digest.message_groups():
		pages = f" ({g.page_count} pages)" if g.page_count > 1 else ""
		label = _html.escape(g.title)
		src_tag = f"[{_html.escape(g.source_id)}/{_html.escape(g.change_kind)}]{pages}"
		line = f'<a href="{_html.escape(g.primary_url)}">{label}</a> {src_tag}'
		if g.summary:
			line += f"\n{_html.escape(g.summary)}"
		item_lines.append(line)

	carried = digest.carried_groups()
	footer_lines = [f"+{len(carried)} more groups"] if carried else []
	header = "\n".join(parts)
	footer = "\n".join(footer_lines)

	def _assemble(items: list[str], dropped: int) -> str:
		sections = [header, *items]
		if dropped:
			sections.append(f"…({dropped} more)")
		if footer:
			sections.append(footer)
		if scan_line:
			sections.append(scan_line)
		return "\n".join(sections)

	msg = _assemble(item_lines, 0)
	if len(msg) <= _TELEGRAM_LIMIT:
		return msg

	# Drop groups from the end until it fits.
	dropped = 0
	while item_lines and len(_assemble(item_lines, dropped)) > _TELEGRAM_LIMIT:
		item_lines.pop()
		dropped += 1

	return _assemble(item_lines, dropped)


_MEMBER_LINK_CAP = 12  # most member links to inline before summarizing the rest


def _slack_link_label(text: str) -> str:
	"""Clean a page title for display, then escape what would break a Slack
	`<url|label>` link: a raw `|` ends the label, and `& < >` need escaping."""
	label = clean_label(text)
	return label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "/")


_ROMAN = [
	(1000, "m"),
	(900, "cm"),
	(500, "d"),
	(400, "cd"),
	(100, "c"),
	(90, "xc"),
	(50, "l"),
	(40, "xl"),
	(10, "x"),
	(9, "ix"),
	(5, "v"),
	(4, "iv"),
	(1, "i"),
]


def _roman(n: int) -> str:
	"""Lowercase roman numeral for the source list (i, ii, iii, …)."""
	out: list[str] = []
	for value, sym in _ROMAN:
		while n >= value:
			out.append(sym)
			n -= value
	return "".join(out)


def _group_section(g: DigestGroup, number: int | None = None) -> dict:
	"""One section per group: bold 'Title:' (numbered when its category holds
	more than one group), an optional normal-weight summary, then the source
	link(s). A single-source group shows '*Source:* <link>'; a multi-source group
	shows '*Sources:*' then a lowercase roman-numeral list (capped). No buttons:
	one button could never represent a group's N urls."""
	clean = clean_label(g.title)
	title = clean if clean.endswith(":") else f"{clean}:"
	head = f"{number}. {title}" if number else title
	text = f"*{head}*"
	if g.summary:
		text += f"\n{g.summary}"
	if g.page_count == 1:
		m = g.members[0]
		text += f"\n\n*Source:* <{m.url}|{_slack_link_label(m.title or m.url)}>"
	else:
		shown = g.members[:_MEMBER_LINK_CAP]
		rows = "\n".join(
			f"{_roman(i)}. <{m.url}|{_slack_link_label(m.title or m.url)}>"
			for i, m in enumerate(shown, 1)
		)
		text += f"\n\n*Sources:*\n{rows}"
		if g.page_count > _MEMBER_LINK_CAP:
			text += f"\n…+{g.page_count - _MEMBER_LINK_CAP} more"
	return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def render_slack(digest: Digest, *, thread_page: bool = False) -> dict:
	"""Render the capped Slack message. ``thread_page`` is True when the notifier
	will also upload the full-digest HTML page into a thread; the footer then
	mentions it. False omits the thread reference."""
	blocks: list[dict] = []
	banner = _banner_text(digest)
	if banner:
		blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{banner}*"}})
	date = digest.generated_at.strftime("%d %b %Y")
	blocks.append(
		{
			"type": "header",
			"text": {
				"type": "plain_text",
				"text": f"Android Watcher Digest · {date}",
				"emoji": True,
			},
		}
	)
	if digest.is_empty:
		blocks.append(
			{"type": "section", "text": {"type": "mrkdwn", "text": "Nothing notable changed."}}
		)
		footer = _scan_footer(digest)
		if footer:
			blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
		return {"blocks": blocks}

	shown = digest.message_groups()
	# A native divider opens each category (Slack has no thick/thin rule, so the
	# divider is the category separator and there is no inner rule). Groups in a
	# category with more than one group are numbered 1., 2., …
	for _cid, label, groups in by_category(shown):
		blocks.append({"type": "divider"})
		blocks.append(
			{"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}  ·  {len(groups)}*"}}
		)
		numbered = len(groups) > 1
		for i, g in enumerate(groups, 1):
			blocks.append(_group_section(g, i if numbered else None))

	carried = digest.carried_groups()
	footer_bits: list[str] = []
	if thread_page:
		footer_bits.append(
			f"Full digest - *{len(digest.groups)} groups"
			f" · {digest.change_count()} changes* - Attached in :thread:"
		)
	if carried:
		names = ", ".join(g.title for g in carried[:3])
		footer_bits.append(f"+{len(carried)} more groups: {names}")
	scan = _scan_footer(digest)
	tail: list[dict] = []
	if footer_bits:
		tail.append(
			{"type": "context", "elements": [{"type": "mrkdwn", "text": "   ".join(footer_bits)}]}
		)
	if scan:
		tail.append({"type": "context", "elements": [{"type": "mrkdwn", "text": scan}]})
	# Closing divider before the footer region (only when there is a footer).
	if tail:
		blocks.append({"type": "divider"})
		blocks.extend(tail)
	return {"blocks": blocks}
