"""Import-only smoke test for scripts/verify_catalog.py — no live network."""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

# Make scripts/ importable without installing it as a package.
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS_DIR not in sys.path:
	sys.path.insert(0, _SCRIPTS_DIR)


def test_verify_catalog_module_imports():
	mod = importlib.import_module("verify_catalog")
	for name in ("main", "verify_sitemap_prefix", "verify_content_renders"):
		fn = getattr(mod, name)
		assert callable(fn), f"{name} must be callable"


def test_verify_catalog_uses_sitemap_api():
	from android_watcher.detect.android_sitemap import load_sitemap, prefix_count

	assert callable(load_sitemap) and callable(prefix_count)
	src = inspect.getsource(importlib.import_module("verify_catalog"))
	assert "load_sitemap" in src and "prefix_count" in src
