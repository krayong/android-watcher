"""Tests for cli.py schedule subcommand and bare-invocation TUI dispatch."""

from __future__ import annotations

from android_watcher import cli
from android_watcher.models import Check

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeApp:
	"""Minimal stand-in for AndroidWatcher — records whether .run() was called."""

	def __init__(self, *, config, first_run: bool = False) -> None:
		self.config = config
		self.first_run = first_run
		self.ran = False

	def run(self) -> None:
		self.ran = True


# ---------------------------------------------------------------------------
# schedule install
# ---------------------------------------------------------------------------


def test_cli_schedule_install_dispatches(monkeypatch):
	calls = []

	def fake_install(config):
		calls.append(config)

	monkeypatch.setattr("android_watcher.cli.install_schedule", fake_install)

	# load_or_default used when --config not supplied
	fake_cfg = object()
	monkeypatch.setattr("android_watcher.cli.load_or_default", lambda: (fake_cfg, False))

	rc = cli.main(["schedule", "install"])

	assert rc == 0
	assert len(calls) == 1


# ---------------------------------------------------------------------------
# schedule remove
# ---------------------------------------------------------------------------


def test_cli_schedule_remove_dispatches(monkeypatch):
	calls = []

	monkeypatch.setattr("android_watcher.cli.remove_schedule", lambda: calls.append(True))

	rc = cli.main(["schedule", "remove"])

	assert rc == 0
	assert len(calls) == 1


# ---------------------------------------------------------------------------
# schedule status — ok
# ---------------------------------------------------------------------------


def test_cli_schedule_status_ok(monkeypatch, capsys):
	monkeypatch.setattr(
		"android_watcher.cli.schedule_status",
		lambda: Check("schedule", True, "loaded"),
	)

	rc = cli.main(["schedule", "status"])

	assert rc == 0
	out = capsys.readouterr().out
	assert "loaded" in out


# ---------------------------------------------------------------------------
# schedule status — not ok → non-zero exit
# ---------------------------------------------------------------------------


def test_cli_schedule_status_fail_nonzero(monkeypatch, capsys):
	monkeypatch.setattr(
		"android_watcher.cli.schedule_status",
		lambda: Check("schedule", False, "not loaded"),
	)

	rc = cli.main(["schedule", "status"])

	assert rc != 0
	out = capsys.readouterr().out
	assert "not loaded" in out


# ---------------------------------------------------------------------------
# bare invocation → TUI
# ---------------------------------------------------------------------------


def test_cli_bare_launches_tui(monkeypatch):
	launched = []

	class _TrackedApp(_FakeApp):
		def run(self) -> None:
			launched.append(True)

	fake_cfg = object()
	monkeypatch.setattr("android_watcher.cli.load_or_default", lambda: (fake_cfg, False))
	monkeypatch.setattr("android_watcher.cli.AndroidWatcher", _TrackedApp)

	rc = cli.main([])

	assert rc == 0
	assert launched == [True]
