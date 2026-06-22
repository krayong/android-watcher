from android_watcher.models import Change, Digest, DigestGroup
from android_watcher.notify.html import render_html


def _digest():
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
				source_id="developer-android", url="https://w", change_kind="updated", title="Wear"
			)
		],
		score=50,
	)
	return Digest(groups=[gki, solo], max_items=10, sources_scanned=41, pages_watched=3812)


def test_html_has_title_and_groups():
	html = render_html(_digest())
	assert "Android Watcher Digest" in html
	assert "New GKI builds across 5 kernel branches" in html
	assert "41" in html and "3,812" in html  # coverage band
	assert '<ol class="sources"' in html  # multi-page group lists its sources
	assert 'class="btn"' not in html  # no Open/Open page buttons


def test_html_lists_member_pages_for_multipage_group():
	html = render_html(_digest())
	for i in range(7):
		assert f'href="https://x/{i}"' in html  # every member page linked, expanded
	assert "<details" not in html  # shown expanded, not behind a collapse


def test_html_single_page_group_shows_source_link():
	html = render_html(_digest())
	assert "Source: " in html  # singleton group labelled "Source:"
	assert 'href="https://w"' in html


def test_html_escapes_untrusted_text():
	g = DigestGroup(
		key="k",
		title="<script>bad</script>",
		summary="a & b <x>",
		category="guides",
		source_id="s",
		change_kind="updated",
		members=[Change(source_id="s", url="https://u", change_kind="updated", title="<script>")],
		score=1,
	)
	html = render_html(Digest(groups=[g], max_items=10))
	assert "<script>bad" not in html
	assert "&lt;script&gt;bad" in html


def test_html_ai_unavailable_banner():
	g = DigestGroup(
		key="k",
		title="Some change",
		summary="A summary",
		category="guides",
		source_id="s",
		change_kind="updated",
		members=[
			Change(source_id="s", url="https://u", change_kind="updated", title="Some change")
		],
		score=1,
	)
	html = render_html(Digest(groups=[g], max_items=10, ai_unavailable="claude timed out"))
	assert "AI unavailable" in html
	assert "claude timed out" in html
	assert 'class="banner"' in html


def test_html_escapes_change_kind():
	g = DigestGroup(
		key="k",
		title="Some change",
		summary=None,
		category="guides",
		source_id="s",
		change_kind="<x>",  # type: ignore[arg-type]
		members=[
			Change(source_id="s", url="https://u", change_kind="updated", title="Some change")
		],
		score=1,
	)
	html = render_html(Digest(groups=[g], max_items=10))
	assert "&lt;x&gt;" in html
	assert "· <x>" not in html
