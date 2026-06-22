"""android-watcher: watch official Google Android sites and deliver a ranked digest."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
	__version__ = version("android-watcher")
except PackageNotFoundError:  # running from a source tree without installed metadata
	__version__ = "0.0.0"
