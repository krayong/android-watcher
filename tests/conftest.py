"""Shared test fixtures.

Keeps the suite hermetic: nothing should touch the real user log directory.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_file_logging(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Stop the CLI's run/test commands from writing to ~/Library/Logs in tests."""
	noop = lambda: "/dev/null"  # noqa: E731 - trivial stub
	monkeypatch.setattr("android_watcher.run.configure_file_logging", noop, raising=False)
	monkeypatch.setattr("android_watcher.cli.configure_file_logging", noop, raising=False)
