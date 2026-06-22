"""Smoke test: all four detectors resolve by registered name.

Each detector module registers itself via @DETECTORS.register(...) at import
time.  The detect package __init__ is intentionally empty, so import the
modules explicitly here — mirroring how the pipeline will load them.
"""

from __future__ import annotations

import inspect

import android_watcher.detect.android_sitemap  # noqa: F401
import android_watcher.detect.content  # noqa: F401
import android_watcher.detect.feed  # noqa: F401
import android_watcher.detect.sitemap  # noqa: F401
from android_watcher.detect.base import DETECTORS


def test_all_four_detectors_registered_and_callable() -> None:
	for name in ("feed", "android_sitemap", "sitemap", "content"):
		cls = DETECTORS.get(name)
		inst = cls()
		assert inspect.iscoroutinefunction(inst.detect)
	assert {"feed", "android_sitemap", "sitemap", "content"}.issubset(set(DETECTORS.available()))
