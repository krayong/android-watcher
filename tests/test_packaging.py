"""Packaging smoke tests: pyproject metadata, entrypoint, catalog data, detector registration."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
	with open(ROOT / "pyproject.toml", "rb") as fh:
		return tomllib.load(fh)


# ---------------------------------------------------------------------------
# Task 5 — pyproject metadata
# ---------------------------------------------------------------------------


def test_metadata_complete():
	proj = _pyproject()["project"]
	assert proj["name"] == "android-watcher"
	assert proj["requires-python"] == ">=3.11"
	assert proj["license"]["text"] == "MIT"
	assert any("OSI Approved :: MIT License" in c for c in proj["classifiers"])


def test_console_entrypoint():
	scripts = _pyproject()["project"]["scripts"]
	assert scripts["android-watcher"] == "android_watcher.cli:main"


def test_defusedxml_pinned():
	deps = _pyproject()["project"]["dependencies"]
	assert any(d.startswith("defusedxml") and any(c in d for c in "=<>~") for d in deps), (
		"defusedxml must be present and version-pinned"
	)


def test_lockfile_committed():
	assert (ROOT / "uv.lock").is_file()


def test_catalog_packaged_as_data():
	proj = _pyproject()
	includes = str(proj)
	assert "catalog.toml" in includes or "catalog" in includes


def test_seed_artifact_bundled():
	wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
	assert any("seed" in a for a in wheel.get("artifacts", [])), (
		"seed.sql.gz must be declared as a build artifact so it ships when generated"
	)


def test_seed_package_importable():
	from android_watcher.seed import apply_seed_if_empty, bundled_seed_sql

	assert callable(apply_seed_if_empty)
	assert callable(bundled_seed_sql)


def test_pyproject_declares_all_deps():
	deps = " ".join(_pyproject()["project"]["dependencies"])
	for pkg in ("httpx", "defusedxml", "platformdirs", "textual"):
		assert pkg in deps


def test_dev_deps_present():
	dev = " ".join(_pyproject()["dependency-groups"]["dev"])
	assert "pytest" in dev
	assert "ruff" in dev


# ---------------------------------------------------------------------------
# Task 6 — import smoke + entrypoint resolution + detect registration
# ---------------------------------------------------------------------------


def test_package_imports():
	import android_watcher

	assert isinstance(android_watcher.__version__, str)


def test_console_entry_resolves():
	from importlib.metadata import entry_points

	eps = entry_points(group="console_scripts")
	names = {ep.name: ep.value for ep in eps}
	assert names.get("android-watcher") == "android_watcher.cli:main"


def test_cli_main_is_callable():
	from android_watcher.cli import main

	assert callable(main)


def test_load_catalog_returns_sources():
	from android_watcher.catalog import load_catalog

	sources = load_catalog()
	assert len(sources) > 0
	assert all(hasattr(s, "id") and hasattr(s, "url") for s in sources)


def test_detect_registers_all_four_detectors():
	import android_watcher.detect  # noqa: F401 — side-effect: registers all four
	from android_watcher.detect.base import DETECTORS

	for name in ("feed", "android_sitemap", "sitemap", "content"):
		cls = DETECTORS.get(name)
		assert cls is not None, f"detector '{name}' not registered after import"


def test_catalog_toml_readable_via_importlib_resources():
	"""catalog.toml must be accessible through importlib.resources (package-data resolution)."""
	from importlib.resources import files

	data = files("android_watcher.catalog").joinpath("catalog.toml").read_bytes()
	assert len(data) > 100, "catalog.toml is empty or missing from package data"
	assert b"[[source]]" in data, "catalog.toml does not look like a valid catalog"
