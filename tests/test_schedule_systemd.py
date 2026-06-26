"""Tests for systemd timer and service text generators (pure, no subprocess calls)."""

from __future__ import annotations

import pathlib

import pytest

from android_watcher.config import ScheduleConfig
from android_watcher.schedule import ScheduleError, _on_calendar, render_service, render_timer

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "schedule"


def test_on_calendar_daily() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	assert _on_calendar(sched, "Europe/Berlin") == "*-*-* 09:00:00 Europe/Berlin"


def test_on_calendar_hourly() -> None:
	sched = ScheduleConfig(interval="hourly", at="00:15")
	assert _on_calendar(sched, "Europe/Berlin") == "*-*-* *:15:00 Europe/Berlin"


def test_on_calendar_weekly() -> None:
	sched = ScheduleConfig(interval="weekly", at="08:30")
	assert _on_calendar(sched, "Europe/Berlin") == "Mon *-*-* 08:30:00 Europe/Berlin"


def test_on_calendar_cron() -> None:
	sched = ScheduleConfig(interval="cron", cron="30 6 * * 1")
	assert _on_calendar(sched, "Europe/Berlin") == "Mon *-*-* 06:30:00 Europe/Berlin"


def test_on_calendar_cron_rejects_dom_and_dow() -> None:
	# cron ORs dom/dow; systemd ANDs them — divergent schedule, must reject
	sched = ScheduleConfig(interval="cron", cron="0 9 1 * 1")
	with pytest.raises(ScheduleError):
		_on_calendar(sched, "Europe/Berlin")


def test_on_calendar_cron_rejects_all_star() -> None:
	# all-star means per-minute; systemd path must refuse just like launchd
	sched = ScheduleConfig(interval="cron", cron="* * * * *")
	with pytest.raises(ScheduleError, match="refusing per-minute schedule"):
		_on_calendar(sched, "Europe/Berlin")


def test_render_timer_golden() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_timer(sched, "Europe/Berlin")
	golden = (FIXTURES / "android-watcher.timer").read_text()
	assert result == golden
	assert "Persistent=true" in result


def test_render_service_golden() -> None:
	result = render_service("/usr/bin/android-watcher", ["run"])
	golden = (FIXTURES / "android-watcher.service").read_text()
	assert result == golden
	assert "Type=oneshot" in result


def test_render_service_embeds_path_env() -> None:
	# systemd user services start from a minimal PATH; embed the snapshot so the
	# run can reach the claude CLI for triage.
	result = render_service(
		"/usr/bin/android-watcher", ["run"], path_env="/home/me/.local/bin:/usr/bin:/bin"
	)
	assert "Environment=PATH=/home/me/.local/bin:/usr/bin:/bin" in result


def test_render_service_omits_env_without_path() -> None:
	result = render_service("/usr/bin/android-watcher", ["run"])
	assert "Environment=" not in result


def test_render_service_embeds_schedule_env() -> None:
	result = render_service(
		"/usr/bin/android-watcher",
		["run"],
		path_env="/usr/bin:/bin",
		env={"CLAUDE_ACCOUNT": "personal"},
	)
	assert "Environment=PATH=/usr/bin:/bin" in result
	assert "Environment=CLAUDE_ACCOUNT=personal" in result
