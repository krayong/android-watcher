"""Tests for launchd plist generation (pure, no subprocess calls)."""

from __future__ import annotations

import pathlib

import pytest

from android_watcher.config import ScheduleConfig
from android_watcher.schedule import ScheduleError, _calendar_intervals, render_plist

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "schedule"


def test_calendar_intervals_daily() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	assert _calendar_intervals(sched) == [{"Hour": 9, "Minute": 0}]


def test_calendar_intervals_hourly() -> None:
	sched = ScheduleConfig(interval="hourly", at="00:15")
	assert _calendar_intervals(sched) == [{"Minute": 15}]


def test_calendar_intervals_weekly() -> None:
	sched = ScheduleConfig(interval="weekly", at="08:30")
	assert _calendar_intervals(sched) == [{"Weekday": 1, "Hour": 8, "Minute": 30}]


def test_calendar_intervals_cron_subset() -> None:
	sched = ScheduleConfig(interval="cron", cron="30 6 * * 1")
	assert _calendar_intervals(sched) == [{"Minute": 30, "Hour": 6, "Weekday": 1}]


def test_calendar_intervals_daily_multiple_times() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00,18:30")
	assert _calendar_intervals(sched) == [
		{"Hour": 9, "Minute": 0},
		{"Hour": 18, "Minute": 30},
	]


def test_calendar_intervals_weekly_multiple_days_and_times() -> None:
	sched = ScheduleConfig(interval="weekly", at="09:00,18:00", days="mon,wed,sun")
	assert _calendar_intervals(sched) == [
		{"Weekday": 1, "Hour": 9, "Minute": 0},
		{"Weekday": 1, "Hour": 18, "Minute": 0},
		{"Weekday": 3, "Hour": 9, "Minute": 0},
		{"Weekday": 3, "Hour": 18, "Minute": 0},
		{"Weekday": 0, "Hour": 9, "Minute": 0},
		{"Weekday": 0, "Hour": 18, "Minute": 0},
	]


def test_calendar_intervals_cron_rejects_ranges() -> None:
	sched = ScheduleConfig(interval="cron", cron="0-30 6 * * *")
	with pytest.raises(ScheduleError):
		_calendar_intervals(sched)


def test_calendar_intervals_cron_rejects_all_star() -> None:
	sched = ScheduleConfig(interval="cron", cron="* * * * *")
	with pytest.raises(ScheduleError):
		_calendar_intervals(sched)


def test_calendar_intervals_cron_rejects_dom_and_dow() -> None:
	# cron ORs dom/dow; launchd ANDs keys in a single StartCalendarInterval
	sched = ScheduleConfig(interval="cron", cron="0 9 1 * 1")
	with pytest.raises(ScheduleError):
		_calendar_intervals(sched)


def test_render_plist_daily_golden() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_plist(
		"com.krayong.android-watcher",
		["/usr/bin/android-watcher", "run"],
		sched,
	)
	golden = (FIXTURES / "launchd_daily.plist").read_text()
	assert result == golden


def test_render_plist_embeds_path_env() -> None:
	# A launchd job inherits a bare PATH; the embedded EnvironmentVariables/PATH
	# is what lets the scheduled run reach the claude CLI for triage.
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_plist(
		"com.krayong.android-watcher",
		["/usr/bin/android-watcher", "run"],
		sched,
		path_env="/home/me/.local/bin:/usr/bin:/bin",
	)
	assert "<key>EnvironmentVariables</key>" in result
	assert "<key>PATH</key>" in result
	assert "/home/me/.local/bin:/usr/bin:/bin" in result


def test_render_plist_omits_env_without_path() -> None:
	sched = ScheduleConfig(interval="daily", at="09:00")
	result = render_plist(
		"com.krayong.android-watcher",
		["/usr/bin/android-watcher", "run"],
		sched,
	)
	assert "EnvironmentVariables" not in result
