from android_watcher.catalog import load_catalog
from android_watcher.detect.android_sitemap import _index_url_for
from android_watcher.rank import CATEGORY_WEIGHTS

KNOWN_DETECTORS = {"feed", "android_sitemap", "sitemap", "content"}
KNOWN_CATEGORIES = set(CATEGORY_WEIGHTS)  # the seven canonical categories


def _sources():
	return load_catalog()


def test_catalog_loads_all_entries():
	sources = _sources()
	assert len(sources) == 41
	assert sum(s.enabled for s in sources) == 41
	assert sum(not s.enabled for s in sources) == 0


def test_ids_unique():
	ids = [s.id for s in _sources()]
	assert len(ids) == len(set(ids)), "duplicate source id"


def test_enabled_is_bool():
	for s in _sources():
		assert isinstance(s.enabled, bool), f"{s.id}: enabled must be bool"


def test_categories_within_known_set():
	for s in _sources():
		assert s.category in KNOWN_CATEGORIES, f"{s.id}: bad category {s.category!r}"


def test_detectors_within_known_set():
	for s in _sources():
		assert s.detector in KNOWN_DETECTORS, f"{s.id}: bad detector {s.detector!r}"


def test_required_fields_per_detector():
	for s in _sources():
		assert s.id and s.name and s.url, f"{s.id}: id/name/url required"
		if s.detector == "android_sitemap":
			# A host catch-all uses an empty prefix; a scoped source's prefix must
			# be a real path.
			if s.path_prefix:
				assert s.path_prefix.startswith("/"), f"{s.id}: path_prefix must start with /"
			assert not s.feed_url, f"{s.id}: android_sitemap must not set feed_url"
		if s.detector == "feed" and s.enabled:
			assert s.feed_url, f"{s.id}: enabled feed source requires feed_url"
		if s.detector == "content":
			assert not s.path_prefix, f"{s.id}: content must not set path_prefix"
			assert not s.feed_url, f"{s.id}: content must not set feed_url"


def test_no_duplicate_enabled_sitemap_prefix():
	# Two ENABLED android_sitemap sources sharing the SAME (host, path_prefix)
	# would make the same sitemap URLs match both and double-report. Scoped by
	# host: a "" catch-all per host is fine, and nested prefixes are fine (the
	# detector routes each URL to its most-specific prefix).
	pairs = [
		(_index_url_for(s), s.path_prefix)
		for s in _sources()
		if s.enabled and s.detector == "android_sitemap"
	]
	assert len(pairs) == len(set(pairs)), f"duplicate enabled (host, path_prefix): {pairs}"


def test_default_weight_zero_for_shipped():
	for s in _sources():
		assert s.default_weight == 0, f"{s.id}: shipped entries use category weight"
