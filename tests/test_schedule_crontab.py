"""Tests for crontab block text generator (pure, no subprocess calls)."""

from __future__ import annotations

from android_watcher.config import ScheduleConfig
from android_watcher.schedule import CRON_BEGIN, CRON_END, render_crontab


def test_render_crontab_daily() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_crontab("/usr/bin/android-watcher run", sched, "Europe/Berlin")
	expected = (
		"# >>> android-watcher >>>\n"
		"CRON_TZ=Europe/Berlin\n"
		"0 9 * * * /usr/bin/android-watcher run\n"
		"# <<< android-watcher <<<\n"
	)
	assert result == expected


def test_render_crontab_hourly() -> None:
	sched = ScheduleConfig(interval="hourly", at="00:15")
	result = render_crontab("/usr/bin/android-watcher run", sched, "Europe/Berlin")
	assert "15 * * * * /usr/bin/android-watcher run" in result
	assert "CRON_TZ=Europe/Berlin" in result


def test_render_crontab_weekly() -> None:
	sched = ScheduleConfig(interval="weekly", at="08:30")
	result = render_crontab("/usr/bin/android-watcher run", sched, "Europe/Berlin")
	assert "30 8 * * 1 /usr/bin/android-watcher run" in result


def test_render_crontab_weekly_multiple_days_and_times() -> None:
	sched = ScheduleConfig(interval="weekly", at="09:00,18:30", days="mon,fri")
	result = render_crontab("/usr/bin/android-watcher run", sched, "Europe/Berlin")
	assert "0 9 * * 1,5 /usr/bin/android-watcher run" in result
	assert "30 18 * * 1,5 /usr/bin/android-watcher run" in result


def test_render_crontab_cron() -> None:
	# Raw cron expression is emitted verbatim (crontab accepts full cron syntax)
	sched = ScheduleConfig(interval="cron", cron="*/30 * * * *")
	result = render_crontab("/usr/bin/android-watcher run", sched, "UTC")
	assert "*/30 * * * * /usr/bin/android-watcher run" in result


def test_render_crontab_emits_cron_tz() -> None:
	sched = ScheduleConfig(interval="daily", at="07:00")
	result = render_crontab("/usr/bin/android-watcher run", sched, "America/New_York")
	assert "CRON_TZ=America/New_York" in result
	assert result.startswith(CRON_BEGIN)
	assert result.rstrip("\n").endswith(CRON_END.rstrip("\n"))


def test_render_crontab_embeds_path_env() -> None:
	# cron runs with a minimal PATH; embed the snapshot so the run reaches claude.
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_crontab(
		"/usr/bin/android-watcher run",
		sched,
		"Europe/Berlin",
		path_env="/home/me/.local/bin:/usr/bin:/bin",
	)
	assert "PATH=/home/me/.local/bin:/usr/bin:/bin" in result
	# The PATH assignment must precede the schedule line to take effect.
	assert result.index("PATH=/home/me") < result.index("0 9 * * *")


def test_render_crontab_omits_path_without_path_env() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_crontab("/usr/bin/android-watcher run", sched, "Europe/Berlin")
	assert "\nPATH=" not in result
