"""Contract: importing the android_watcher.detect package registers all four detectors.

Importing the submodules directly must NOT be necessary — the package __init__
does it as a side-effect.  This test pins that guarantee.
"""

from __future__ import annotations

import inspect

# Import ONLY the package, not the individual submodules.
import android_watcher.detect  # noqa: F401
from android_watcher.detect.base import DETECTORS


def test_package_import_registers_all_four_detectors() -> None:
	for name in ("feed", "sitemap", "android_sitemap", "content"):
		cls = DETECTORS.get(name)
		inst = cls()
		assert inspect.iscoroutinefunction(inst.detect), (
			f"detector {name!r} .detect() is not a coroutine function"
		)
	assert {"feed", "sitemap", "android_sitemap", "content"}.issubset(set(DETECTORS.available()))
