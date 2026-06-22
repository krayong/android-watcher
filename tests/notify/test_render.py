"""Tests for notify/render.py: email, Telegram, and Slack rendering."""

from __future__ import annotations

import json

from android_watcher.models import Change, Digest, DigestGroup
from android_watcher.notify.render import render_email, render_slack, render_telegram


def _digest(n: int = 3, max_items: int = 10) -> Digest:
	"""Build a Digest of n DigestGroups."""
	groups = [
		DigestGroup(
			key=f"k{i}",
			title=f"Group {i}",
			summary=f"Summary for group {i}",
			category="guides",
			source_id="compose-releases",
			change_kind="updated",
			members=[
				Change(
					source_id="compose-releases",
					url=f"https://developer.android.com/group{i}",
					change_kind="updated",
					title=f"Group {i}",
				)
			],
			score=100 - i,
		)
		for i in range(n)
	]
	return Digest(groups=groups, max_items=max_items)


def _digest_with_multipage() -> Digest:
	"""Digest with a multi-page group (7 members) and a solo group."""
	gki = DigestGroup(
		key="src::gki",
		title="New GKI builds across 5 kernel branches",
		summary="New release builds for android13-5.15 through android17-6.18",
		category="platform-release",
		source_id="source-android",
		change_kind="updated",
		members=[
			Change(
				source_id="source-android",
				url=f"https://x/{i}",
				change_kind="updated",
				title=f"b{i}",
			)
			for i in range(7)
		],
		score=100,
	)
	solo = DigestGroup(
		key="dev::wear",
		title="Wear OS 6 behavior changes",
		summary=None,
		category="guides",
		source_id="developer-android",
		change_kind="updated",
		members=[
			Change(
				source_id="developer-android",
				url="https://w",
				change_kind="updated",
				title="Wear",
			)
		],
		score=50,
	)
	return Digest(groups=[gki, solo], max_items=10)


# ---------------------------------------------------------------------------
# render_email
# ---------------------------------------------------------------------------


def test_email_html_is_full_page_plaintext_has_points() -> None:
	from android_watcher.notify.render import render_email

	html, plain = render_email(_digest_with_multipage())
	assert "Android Watcher Digest" in html
	assert '<ol class="sources"' in html  # multi-page group lists its sources, expanded
	assert '<div class="row"' in html  # each group is a row
	assert "New GKI builds across 5 kernel branches" in plain
	assert "7 pages" in plain


def test_email_empty_digest() -> None:
	html, plain = render_email(Digest(groups=[]))
	# HTML part is the full render_html page (no inline nothing-notable text)
	assert "Android Watcher Digest" in html
	assert "Nothing notable changed." in plain


def test_email_ai_unavailable_banner() -> None:
	d = Digest(groups=_digest().groups, ai_unavailable="claude timed out after 120.0s")
	_html_out, plain = render_email(d)
	# Banner is in the plaintext part; HTML part is from render_html
	assert "AI unavailable: claude timed out after 120.0s" in plain


def test_email_title_in_plaintext() -> None:
	_html_out, plain = render_email(_digest())
	assert "Android Watcher Digest" in plain


# ---------------------------------------------------------------------------
# render_telegram
# ---------------------------------------------------------------------------


def test_telegram_points_and_truncation() -> None:
	from android_watcher.notify.render import render_telegram

	text = render_telegram(_digest_with_multipage())
	assert "Android Watcher Digest" in text
	assert len(text) <= 4096


def test_telegram_empty_digest() -> None:
	text = render_telegram(Digest(groups=[]))
	assert "Nothing notable" in text


def test_telegram_ai_unavailable_banner() -> None:
	d = Digest(groups=_digest().groups, ai_unavailable="claude not found")
	text = render_telegram(d)
	assert "claude not found" in text
	assert "<b>" in text


def test_telegram_truncation_when_exceeds_4096() -> None:
	"""When groups would push over 4096 chars, trailing items are dropped."""
	groups = [
		DigestGroup(
			key=f"k{i}",
			title="A" * 40,
			summary="B" * 80,
			category="guides",
			source_id="src",
			change_kind="updated",
			members=[
				Change(
					source_id="src",
					url=f"https://developer.android.com/page-{i}",
					change_kind="updated",
					title="A" * 40,
				)
			],
			score=100 - i,
		)
		for i in range(100)
	]
	text = render_telegram(Digest(groups=groups, max_items=100))
	assert len(text) <= 4096
	assert "more)" in text  # truncation note appended


def test_telegram_html_escapes_title() -> None:
	g = DigestGroup(
		key="k",
		title="<script>alert('xss')</script>",
		summary=None,
		category="guides",
		source_id="s",
		change_kind="updated",
		members=[Change(source_id="s", url="https://u", change_kind="updated", title="<s>")],
		score=1,
	)
	text = render_telegram(Digest(groups=[g], max_items=10))
	assert "<script>" not in text
	assert "&lt;script&gt;" in text


# ---------------------------------------------------------------------------
# scan-scope footer (all three channels)
# ---------------------------------------------------------------------------


def test_scan_footer_shown_when_stats_present() -> None:
	d = Digest(groups=_digest().groups, sources_scanned=41, pages_watched=10960)

	slack = render_slack(d)
	assert any("Scanned 41 Sources" in str(b) for b in slack["blocks"])

	_html_out, plain = render_email(d)
	assert "Scanned 41 Sources" in plain

	tg = render_telegram(d)
	assert "Scanned 41 Sources" in tg


def test_no_scan_footer_when_stats_absent() -> None:
	d = Digest(groups=_digest().groups)  # sources_scanned=0 -> no footer
	_html_out, plain = render_email(d)
	assert "Scanned" not in plain
	assert "Scanned" not in render_telegram(d)


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def test_slack_header_and_sections_slack() -> None:
	from android_watcher.notify.render import render_slack

	payload = render_slack(_digest())
	blocks = payload["blocks"]
	assert blocks[0]["type"] == "header"
	assert "Android Watcher Digest" in blocks[0]["text"]["text"]
	assert any(b["type"] == "section" for b in blocks)
	assert len(blocks) < 50


def test_slack_caps_to_message_groups_and_notes_carried_slack() -> None:
	from android_watcher.notify.render import render_slack

	groups = [
		DigestGroup(
			key=f"k{i}",
			title=f"t{i}",
			summary=None,
			category="guides",
			source_id="s",
			change_kind="updated",
			members=[Change(source_id="s", url=f"u{i}", change_kind="updated", title=f"t{i}")],
			score=100 - i,
		)
		for i in range(12)
	]
	payload = render_slack(Digest(groups=groups, max_items=10))
	# Each group section carries a "*Source:*" / "*Sources:*" line; count those.
	group_sections = [
		b for b in payload["blocks"] if b["type"] == "section" and "Source" in b["text"]["text"]
	]
	assert len(group_sections) == 10
	footer_text = " ".join(
		el["text"]
		for b in payload["blocks"]
		if b["type"] == "context"
		for el in b.get("elements", [])
	)
	assert "+2 more groups" in footer_text


def test_render_slack_empty_slack() -> None:
	digest = Digest(groups=[])
	result = render_slack(digest)

	texts = [b["text"]["text"] for b in result["blocks"] if b["type"] == "section"]
	assert any("Nothing notable changed." in t for t in texts)
	assert json.dumps(result)


def test_render_slack_overflow_slack() -> None:
	digest = _digest(n=12, max_items=10)
	result = render_slack(digest)
	footer_text = " ".join(
		el["text"]
		for b in result["blocks"]
		if b["type"] == "context"
		for el in b.get("elements", [])
	)
	assert "+2 more groups" in footer_text
	assert json.dumps(result)


def _multipage_digest() -> Digest:
	members = [
		Change(source_id="s", url=f"https://x/{i}", change_kind="updated", title=f"page {i}")
		for i in range(3)
	]
	g = DigestGroup(
		key="s::g",
		title="Grouped story",
		summary="three pages changed",
		category="guides",
		source_id="s",
		change_kind="updated",
		members=members,
		score=100,
	)
	return Digest(groups=[g], max_items=10)


def test_slack_has_no_buttons() -> None:
	# Uniform UX: no buttons anywhere; every group exposes its url(s) via inline links.
	for digest in (_digest(n=2), _multipage_digest()):
		result = render_slack(digest)
		assert not any(b["type"] == "actions" for b in result["blocks"])


def test_slack_single_page_group_inlines_its_link() -> None:
	# A single-page group lists its one link inline too, same as a multi-page group.
	result = render_slack(_digest(n=1))
	section_text = " ".join(b["text"]["text"] for b in result["blocks"] if b["type"] == "section")
	assert "<https://developer.android.com/group0|" in section_text


def test_slack_grouped_entry_inlines_member_links() -> None:
	result = render_slack(_multipage_digest())
	section_text = " ".join(b["text"]["text"] for b in result["blocks"] if b["type"] == "section")
	# Every individual page URL is reachable from the message itself, not just
	# the thread page.
	for i in range(3):
		assert f"<https://x/{i}|" in section_text


def test_slack_footer_mentions_thread_only_when_attached() -> None:
	digest = _digest(n=12, max_items=10)
	no_thread = " ".join(
		el["text"]
		for b in render_slack(digest, thread_page=False)["blocks"]
		if b["type"] == "context"
		for el in b.get("elements", [])
	)
	with_thread = " ".join(
		el["text"]
		for b in render_slack(digest, thread_page=True)["blocks"]
		if b["type"] == "context"
		for el in b.get("elements", [])
	)
	assert "thread" not in no_thread.lower()
	assert "thread" in with_thread.lower()


def test_render_slack_ai_unavailable_slack() -> None:
	digest = Digest(groups=[_digest().groups[0]], ai_unavailable="claude timed out after 120.0s")
	result = render_slack(digest)

	all_text = json.dumps(result)
	assert "AI unavailable: claude timed out after 120.0s" in all_text
	assert "blocks" in result
	assert json.dumps(result)


def test_render_slack_returns_blocks_dict_slack() -> None:
	digest = _digest(n=1)
	result = render_slack(digest)
	assert isinstance(result, dict)
	assert "blocks" in result
	assert isinstance(result["blocks"], list)
	assert json.dumps(result)
