"""Tests for cli.py: argparse router.

Covers the working catalog subcommand, the three newly implemented subcommands
(run, test, doctor), and the unchanged stubs (schedule, tui).
"""

from __future__ import annotations

import pytest

from android_watcher.cli import main
from android_watcher.models import AlreadyRunning, Check, ConfigError, Digest

# ---------------------------------------------------------------------------
# helpers / fakes
# ---------------------------------------------------------------------------


def _fake_config():
	from android_watcher.config import (
		AIConfig,
		Config,
		DigestConfig,
		EmailChannel,
		ScheduleConfig,
		SlackChannel,
		TelegramChannel,
	)

	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(mode="off"),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def _empty_digest():
	return Digest(groups=[])


# ---------------------------------------------------------------------------
# unchanged / structural tests
# ---------------------------------------------------------------------------


def test_catalog_lists_sources(capsys):
	rc = main(["catalog"])
	out = capsys.readouterr().out
	assert rc == 0
	assert "android-studio-releases" in out
	assert "android_sitemap" in out


def test_unknown_command_errors():
	with pytest.raises(SystemExit) as exc:
		main(["frobnicate"])
	assert exc.value.code != 0


def test_bare_invocation_launches_tui(monkeypatch):
	"""Bare invocation (no subcommand) should launch AndroidWatcher, not print a stub."""
	launched = []

	class _FakeApp:
		def __init__(self, *, config, first_run=False):
			pass

		def run(self):
			launched.append(True)

	fake_cfg = _fake_config()
	monkeypatch.setattr("android_watcher.cli.load_or_default", lambda: (fake_cfg, False))
	monkeypatch.setattr("android_watcher.cli.AndroidWatcher", _FakeApp)

	rc = main([])
	assert rc == 0
	assert launched == [True]


def test_schedule_status_dispatches(monkeypatch, capsys):
	"""schedule status should print the Check detail and exit 0 when ok."""
	import android_watcher.cli as cli_mod

	monkeypatch.setattr(
		cli_mod,
		"schedule_status",
		lambda: Check("schedule", True, "loaded"),
	)

	rc = main(["schedule", "status"])
	out = capsys.readouterr().out
	assert rc == 0
	assert "loaded" in out


# ---------------------------------------------------------------------------
# `run` subcommand
# ---------------------------------------------------------------------------


def test_run_calls_run_once(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	called = {}
	config = _fake_config()

	def _fake_run_once(cfg, force=False):
		called.update({"cfg": cfg, "force": force})
		return _empty_digest()

	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(cli_mod, "run_once", _fake_run_once)

	rc = main(["run"])
	out = capsys.readouterr().out

	assert rc == 0
	assert called["cfg"] is config
	assert called["force"] is False
	assert "0 change" in out


def test_run_force_flag(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	called = {}
	config = _fake_config()

	def _fake_run_once(cfg, force=False):
		called.update({"force": force})
		return _empty_digest()

	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(cli_mod, "run_once", _fake_run_once)

	rc = main(["run", "--force"])
	assert rc == 0
	assert called["force"] is True


def test_run_already_running_exits_zero(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	config = _fake_config()
	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)

	def _busy(cfg, force=False):
		raise AlreadyRunning("held")

	monkeypatch.setattr(cli_mod, "run_once", _busy)

	rc = main(["run"])
	out = capsys.readouterr().out

	assert rc == 0
	assert "in progress" in out.lower() or "another" in out.lower()


def test_run_config_error_friendly(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	def _bad_config(path):
		raise ConfigError("bad toml")

	monkeypatch.setattr(cli_mod, "load_config", _bad_config)

	rc = main(["run"])
	err = capsys.readouterr().err

	assert rc != 0
	assert "bad toml" in err.lower() or "error" in err.lower()


# ---------------------------------------------------------------------------
# `doctor` subcommand
# ---------------------------------------------------------------------------


def test_doctor_prints_checks_exit_zero_all_ok(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	config = _fake_config()
	checks = [
		Check("ai-backend", True, "claude found"),
		Check("schedule", True, "loaded"),
	]
	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(cli_mod, "run_doctor", lambda cfg: checks)

	rc = main(["doctor"])
	out = capsys.readouterr().out

	assert rc == 0
	assert "OK" in out
	assert "ai-backend" in out
	assert "schedule" in out


def test_doctor_exits_nonzero_on_failure(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	config = _fake_config()
	checks = [
		Check("ai-backend", False, "claude not found on PATH"),
		Check("schedule", True, "loaded"),
	]
	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(cli_mod, "run_doctor", lambda cfg: checks)

	rc = main(["doctor"])
	out = capsys.readouterr().out

	assert rc == 1
	assert "FAIL" in out
	assert "ai-backend" in out


# ---------------------------------------------------------------------------
# `test` subcommand
# ---------------------------------------------------------------------------


def test_test_renders_digest_to_stdout(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	config = _fake_config()
	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(cli_mod, "run_doctor", lambda cfg: [Check("ai-backend", True, "ok")])
	monkeypatch.setattr(cli_mod, "run_once", lambda cfg, dry_run=False: _empty_digest())
	monkeypatch.setattr(cli_mod, "render_email", lambda digest: ("html subject", "plaintext body"))

	rc = main(["test"])
	out = capsys.readouterr().out

	assert rc == 0
	assert "plaintext body" in out


def test_test_exits_one_on_failing_ai_backend(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	config = _fake_config()
	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(
		cli_mod,
		"run_doctor",
		lambda cfg: [Check("ai-backend", False, "claude not found on PATH")],
	)
	monkeypatch.setattr(cli_mod, "run_once", lambda cfg, dry_run=False: _empty_digest())
	monkeypatch.setattr(cli_mod, "render_email", lambda digest: ("subj", "body"))

	rc = main(["test"])
	out = capsys.readouterr().out

	assert rc == 1
	assert "FAIL" in out
	assert "ai-backend" in out


def test_test_passes_when_all_checks_ok(monkeypatch, capsys):
	import android_watcher.cli as cli_mod

	config = _fake_config()
	monkeypatch.setattr(cli_mod, "load_config", lambda path: config)
	monkeypatch.setattr(
		cli_mod,
		"run_doctor",
		lambda cfg: [Check("ai-backend", True, "ok"), Check("schedule", True, "ok")],
	)
	monkeypatch.setattr(cli_mod, "run_once", lambda cfg, dry_run=False: _empty_digest())
	monkeypatch.setattr(cli_mod, "render_email", lambda digest: ("subj", "body"))

	rc = main(["test"])
	assert rc == 0
