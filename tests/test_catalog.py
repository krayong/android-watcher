from android_watcher.catalog import load_catalog
from android_watcher.models import Source


def test_load_catalog_returns_sources():
	sources = load_catalog()
	assert sources
	assert all(isinstance(s, Source) for s in sources)


def test_catalog_has_unique_ids():
	sources = load_catalog()
	ids = [s.id for s in sources]
	assert len(ids) == len(set(ids))


def test_catalog_covers_each_detector_type():
	detectors = {s.detector for s in load_catalog()}
	assert {"feed", "android_sitemap", "content"} <= detectors


def test_catalog_fields_populated():
	by_id = {s.id: s for s in load_catalog()}
	studio = by_id["android-studio-releases"]
	assert studio.detector == "android_sitemap"
	assert studio.category == "tooling"
	assert studio.path_prefix == "/studio/releases"
	assert studio.enabled is True
