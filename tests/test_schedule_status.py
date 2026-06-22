"""Tests for schedule_status() — mocked subprocess, no live launchctl/systemctl."""

from __future__ import annotations

import subprocess

from android_watcher import schedule as sched_mod
from android_watcher.models import Check
from android_watcher.schedule import CRON_BEGIN, LAUNCHD_LABEL, schedule_status


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
	return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def test_status_macos_loaded(monkeypatch):
	monkeypatch.setattr(sched_mod, "_platform", lambda: "darwin")
	monkeypatch.setattr(
		sched_mod,
		"_run",
		lambda argv, **kw: _fake_completed(stdout=f"PID\tStatus\tLabel\n-\t0\t{LAUNCHD_LABEL}\n"),
	)

	result = schedule_status()

	assert isinstance(result, Check)
	assert result.name == "schedule"
	assert result.ok is True
	assert LAUNCHD_LABEL in result.detail


def test_status_macos_not_loaded(monkeypatch):
	monkeypatch.setattr(sched_mod, "_platform", lambda: "darwin")
	monkeypatch.setattr(
		sched_mod,
		"_run",
		lambda argv, **kw: _fake_completed(stdout="PID\tStatus\tLabel\n-\t0\tcom.other.app\n"),
	)

	result = schedule_status()

	assert result.name == "schedule"
	assert result.ok is False


# ---------------------------------------------------------------------------
# Linux + systemd
# ---------------------------------------------------------------------------


def test_status_systemd_active(monkeypatch):
	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: True)
	monkeypatch.setattr(sched_mod, "_linger_enabled", lambda: True)

	def fake_run(argv, **kw):
		if "is-active" in argv:
			return _fake_completed(stdout="active\n")
		if "is-enabled" in argv:
			return _fake_completed(stdout="enabled\n")
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_run", fake_run)

	result = schedule_status()

	assert result.name == "schedule"
	assert result.ok is True
	assert "active" in result.detail


def test_status_systemd_inactive(monkeypatch):
	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: True)
	monkeypatch.setattr(sched_mod, "_linger_enabled", lambda: False)

	def fake_run(argv, **kw):
		if "is-active" in argv:
			return _fake_completed(stdout="inactive\n")
		if "is-enabled" in argv:
			return _fake_completed(stdout="disabled\n")
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_run", fake_run)

	result = schedule_status()

	assert result.name == "schedule"
	assert result.ok is False
	assert "inactive" in result.detail


# ---------------------------------------------------------------------------
# Linux + crontab fallback
# ---------------------------------------------------------------------------


def test_status_crontab_present(monkeypatch):
	crontab_text = (
		f"# header\n{CRON_BEGIN}\n"
		"0 9 * * * /usr/bin/android-watcher run\n"
		"# <<< android-watcher <<<\n"
	)

	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: False)
	monkeypatch.setattr(sched_mod, "_run", lambda argv, **kw: _fake_completed(stdout=crontab_text))

	result = schedule_status()

	assert result.name == "schedule"
	assert result.ok is True
	assert "present" in result.detail


def test_status_crontab_absent(monkeypatch):
	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: False)
	monkeypatch.setattr(
		sched_mod,
		"_run",
		lambda argv, **kw: _fake_completed(stdout="# unrelated crontab content\n"),
	)

	result = schedule_status()

	assert result.name == "schedule"
	assert result.ok is False
	assert "absent" in result.detail
