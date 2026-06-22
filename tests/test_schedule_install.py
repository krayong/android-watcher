"""Tests for schedule install/remove (side-effecting, subprocess mocked)."""

from __future__ import annotations

import subprocess

from android_watcher import schedule as sched_mod
from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
)
from android_watcher.schedule import (
	CRON_BEGIN,
	CRON_END,
	LAUNCHD_LABEL,
	SYSTEMD_UNIT_NAME,
	install_schedule,
	remove_schedule,
	render_plist,
	render_service,
	render_timer,
)


def _make_config(interval: str = "daily", at: str = "09:00", cron: str = "") -> Config:
	return Config(
		schedule=ScheduleConfig(interval=interval, at=at, cron=cron),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
	)


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
	return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# macOS install
# ---------------------------------------------------------------------------


def test_install_macos_writes_plist_and_loads(monkeypatch, tmp_path):
	plist_path = tmp_path / "agent.plist"
	calls: list[list[str]] = []

	monkeypatch.setattr(sched_mod, "_platform", lambda: "darwin")
	monkeypatch.setattr(sched_mod, "_launchd_plist_path", lambda: str(plist_path))
	monkeypatch.setattr(sched_mod, "_program_args", lambda: ["/usr/bin/android-watcher", "run"])

	def fake_run(argv, *, input=None):
		calls.append(argv)
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_run", fake_run)

	config = _make_config()
	install_schedule(config)

	assert plist_path.exists()
	expected_plist = render_plist(
		LAUNCHD_LABEL, ["/usr/bin/android-watcher", "run"], config.schedule
	)
	assert plist_path.read_text() == expected_plist

	# Must call launchctl load (with -w flag)
	assert ["launchctl", "load", "-w", str(plist_path)] in calls


def test_install_macos_warns_login_session(monkeypatch, tmp_path, capsys):
	plist_path = tmp_path / "agent.plist"

	monkeypatch.setattr(sched_mod, "_platform", lambda: "darwin")
	monkeypatch.setattr(sched_mod, "_launchd_plist_path", lambda: str(plist_path))
	monkeypatch.setattr(sched_mod, "_program_args", lambda: ["/usr/bin/android-watcher", "run"])
	monkeypatch.setattr(sched_mod, "_run", lambda argv, **kw: _fake_completed())

	install_schedule(_make_config())

	captured = capsys.readouterr()
	assert "logged in" in captured.err or "GUI session" in captured.err


# ---------------------------------------------------------------------------
# Linux systemd install
# ---------------------------------------------------------------------------


def test_install_linux_systemd_writes_units_and_enables(monkeypatch, tmp_path):
	calls: list[list[str]] = []

	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: True)
	monkeypatch.setattr(sched_mod, "_systemd_dir", lambda: str(tmp_path))
	monkeypatch.setattr(sched_mod, "_local_tz", lambda: "Europe/Berlin")
	monkeypatch.setattr(sched_mod, "_linger_enabled", lambda: True)
	monkeypatch.setattr(sched_mod, "_program_args", lambda: ["/usr/bin/android-watcher", "run"])

	def fake_run(argv, *, input=None):
		calls.append(argv)
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_run", fake_run)

	config = _make_config()
	install_schedule(config)

	service_file = tmp_path / f"{SYSTEMD_UNIT_NAME}.service"
	timer_file = tmp_path / f"{SYSTEMD_UNIT_NAME}.timer"
	assert service_file.exists()
	assert timer_file.exists()

	expected_service = render_service("/usr/bin/android-watcher", ["run"])
	assert service_file.read_text() == expected_service

	expected_timer = render_timer(config.schedule, "Europe/Berlin")
	assert timer_file.read_text() == expected_timer
	assert "Persistent=true" in expected_timer

	assert ["systemctl", "--user", "daemon-reload"] in calls
	assert ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNIT_NAME}.timer"] in calls


def test_install_linux_no_linger_warns(monkeypatch, tmp_path):
	warnings: list[str] = []

	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: True)
	monkeypatch.setattr(sched_mod, "_systemd_dir", lambda: str(tmp_path))
	monkeypatch.setattr(sched_mod, "_local_tz", lambda: "Europe/Berlin")
	monkeypatch.setattr(sched_mod, "_linger_enabled", lambda: False)
	monkeypatch.setattr(sched_mod, "_program_args", lambda: ["/usr/bin/android-watcher", "run"])
	monkeypatch.setattr(sched_mod, "_run", lambda argv, **kw: _fake_completed())
	monkeypatch.setattr(sched_mod, "_warn", lambda msg: warnings.append(msg))

	install_schedule(_make_config())

	assert any("loginctl enable-linger" in w for w in warnings)
	# Install must still have completed (unit files written)
	assert (tmp_path / f"{SYSTEMD_UNIT_NAME}.timer").exists()


# ---------------------------------------------------------------------------
# Linux crontab fallback install
# ---------------------------------------------------------------------------


def test_install_linux_crontab_fallback(monkeypatch, tmp_path):
	existing_crontab = "# existing line\n"
	received_inputs: list[str] = []

	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: False)
	monkeypatch.setattr(sched_mod, "_local_tz", lambda: "Europe/Berlin")
	monkeypatch.setattr(sched_mod, "_program_args", lambda: ["/usr/bin/android-watcher", "run"])

	def fake_run(argv, *, input=None):
		if argv == ["crontab", "-l"]:
			return _fake_completed(stdout=existing_crontab)
		if argv == ["crontab", "-"]:
			received_inputs.append(input or "")
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_run", fake_run)

	config = _make_config()
	install_schedule(config)

	assert len(received_inputs) == 1
	new_crontab = received_inputs[0]
	assert "# existing line" in new_crontab
	assert CRON_BEGIN in new_crontab
	assert "CRON_TZ=Europe/Berlin" in new_crontab
	assert CRON_END in new_crontab


# ---------------------------------------------------------------------------
# macOS remove
# ---------------------------------------------------------------------------


def test_remove_macos(monkeypatch, tmp_path):
	plist_path = tmp_path / "agent.plist"
	plist_path.write_text("dummy plist content")
	calls: list[list[str]] = []

	def fake_run(argv, **kw):
		calls.append(argv)
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_platform", lambda: "darwin")
	monkeypatch.setattr(sched_mod, "_launchd_plist_path", lambda: str(plist_path))
	monkeypatch.setattr(sched_mod, "_run", fake_run)

	remove_schedule()

	assert ["launchctl", "unload", str(plist_path)] in calls
	assert not plist_path.exists()


# ---------------------------------------------------------------------------
# Linux systemd remove
# ---------------------------------------------------------------------------


def test_remove_linux_systemd(monkeypatch, tmp_path):
	calls: list[list[str]] = []

	service_file = tmp_path / f"{SYSTEMD_UNIT_NAME}.service"
	timer_file = tmp_path / f"{SYSTEMD_UNIT_NAME}.timer"
	service_file.write_text("service")
	timer_file.write_text("timer")

	def fake_run(argv, **kw):
		calls.append(argv)
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: True)
	monkeypatch.setattr(sched_mod, "_systemd_dir", lambda: str(tmp_path))
	monkeypatch.setattr(sched_mod, "_run", fake_run)

	remove_schedule()

	assert ["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT_NAME}.timer"] in calls
	assert ["systemctl", "--user", "daemon-reload"] in calls
	assert not service_file.exists()
	assert not timer_file.exists()


# ---------------------------------------------------------------------------
# Crontab remove
# ---------------------------------------------------------------------------


def test_remove_crontab_strips_block(monkeypatch):
	crontab_with_block = (
		"# unrelated line\n"
		f"{CRON_BEGIN}\n"
		"CRON_TZ=Europe/Berlin\n"
		"0 9 * * * /usr/bin/android-watcher run\n"
		f"{CRON_END}\n"
		"# another unrelated line\n"
	)
	received_inputs: list[str] = []

	monkeypatch.setattr(sched_mod, "_platform", lambda: "linux")
	monkeypatch.setattr(sched_mod, "_has_systemd", lambda: False)

	def fake_run(argv, *, input=None):
		if argv == ["crontab", "-l"]:
			return _fake_completed(stdout=crontab_with_block)
		if argv == ["crontab", "-"]:
			received_inputs.append(input or "")
		return _fake_completed()

	monkeypatch.setattr(sched_mod, "_run", fake_run)

	remove_schedule()

	assert len(received_inputs) == 1
	result = received_inputs[0]
	assert CRON_BEGIN not in result
	assert CRON_END not in result
	assert "CRON_TZ=Europe/Berlin" not in result
	assert "# unrelated line" in result
	assert "# another unrelated line" in result
