from android_watcher.detect._normalize import (
	EMPTY_RENDER_THRESHOLD,
	content_hash,
	extract_main,
	extract_title,
	normalize_text,
)


def test_extract_title_reads_title_tag():
	html = "<html><head><title>  GKI Release Builds  |  AOSP </title></head><body>x</body></html>"
	assert extract_title(html) == "GKI Release Builds | AOSP"


def test_extract_title_empty_when_absent():
	assert extract_title("<html><body>no title here</body></html>") == ""


def test_normalize_strips_attrs_and_collapses_whitespace():
	a = normalize_text("<p class='x'>Hello   world</p>")
	b = normalize_text('<p data-y="1">Hello world</p>')
	assert a == b == "Hello world"


def test_normalize_strips_script_and_style_content():
	html = "<div><style>body{color:red}</style><script>alert(1)</script><p>Keep this</p></div>"
	assert normalize_text(html) == "Keep this"


def test_normalize_collapses_nested_whitespace():
	html = "<div>  <p>  first  </p>  <p>  second  </p>  </div>"
	result = normalize_text(html)
	assert result == "first second"


def test_extract_main_returns_full_body_when_no_selector():
	html = "<html><body><nav>Nav</nav><main>Body</main></body></html>"
	result = extract_main(html, selector="")
	assert "Nav" in result
	assert "Body" in result


def test_extract_main_respects_id_selector():
	html = '<html><body><nav>Skip me</nav><main id="content"><p>Keep me</p></main></body></html>'
	result = extract_main(html, selector="#content")
	assert "Keep me" in result
	assert "Skip me" not in result


def test_extract_main_respects_tag_selector():
	html = "<html><body><article><p>Article text</p></article><footer>Footer</footer></body></html>"
	result = extract_main(html, selector="article")
	assert "Article text" in result
	assert "Footer" not in result


def test_extract_main_falls_back_to_full_html_on_missing_selector():
	html = "<html><body><p>All content</p></body></html>"
	result = extract_main(html, selector="#nonexistent")
	assert "All content" in result


def test_content_hash_is_stable():
	text = "Install the SDK using version 34.0.0."
	assert content_hash(text) == content_hash(text)


def test_content_hash_differs_for_different_text():
	assert content_hash("version 34") != content_hash("version 35")


def test_cosmetic_attr_change_produces_same_hash():
	"""CSS class change on the same text must not move the hash."""
	before = '<p class="old-class">Install SDK version 34.</p>'
	after = '<p class="new-randomized-class">Install SDK version 34.</p>'
	assert content_hash(normalize_text(before)) == content_hash(normalize_text(after))


def test_empty_render_threshold_is_positive_int():
	assert isinstance(EMPTY_RENDER_THRESHOLD, int)
	assert EMPTY_RENDER_THRESHOLD > 0
