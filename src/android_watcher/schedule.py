"""Schedule text generators and native scheduler integration.

This module has two layers:
  1. Pure generators: render_plist, render_timer, render_service, render_crontab
     — input ScheduleConfig -> output str; no subprocess calls.
  2. Side-effecting install/remove/status: install_schedule, remove_schedule,
     schedule_status — detect platform, write unit files, and call the loader.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from android_watcher.config import Config, ScheduleConfig
from android_watcher.models import Check

__all__ = [
	"CRON_BEGIN",
	"CRON_END",
	"LAUNCHD_LABEL",
	"SYSTEMD_UNIT_NAME",
	"ScheduleError",
	"_calendar_intervals",
	"_on_calendar",
	"install_schedule",
	"remove_schedule",
	"render_crontab",
	"render_plist",
	"render_service",
	"render_timer",
	"schedule_status",
]

LAUNCHD_LABEL = "com.krayong.android-watcher"
SYSTEMD_UNIT_NAME = "android-watcher"
CRON_BEGIN = "# >>> android-watcher >>>"
CRON_END = "# <<< android-watcher <<<"

_CRON_DOW_TO_SYSTEMD = {
	0: "Sun",
	1: "Mon",
	2: "Tue",
	3: "Wed",
	4: "Thu",
	5: "Fri",
	6: "Sat",
	7: "Sun",
}


_WD_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# launchd Weekday and cron day-of-week both use Sun=0, Mon=1 … Sat=6.
_WD_NUM = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
_WD_SYSTEMD = {
	"mon": "Mon",
	"tue": "Tue",
	"wed": "Wed",
	"thu": "Thu",
	"fri": "Fri",
	"sat": "Sat",
	"sun": "Sun",
}


class ScheduleError(RuntimeError):
	"""Raised when a ScheduleConfig cannot be translated to a native schedule."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_hm(at: str) -> tuple[int, int]:
	"""Parse an "HH:MM" string into (hour, minute) ints."""
	try:
		hh, mm = at.split(":")
		h, m = int(hh), int(mm)
	except ValueError:
		raise ScheduleError(f"schedule.at must be 'HH:MM', got {at!r}") from None
	if not (0 <= h <= 23 and 0 <= m <= 59):
		raise ScheduleError(f"schedule.at out of range: {at!r}")
	return h, m


def _parse_times(at: str) -> list[tuple[int, int]]:
	"""Parse one or more comma-separated "HH:MM" times into (hour, minute) pairs."""
	parts = [t.strip() for t in at.split(",") if t.strip()]
	return [_parse_hm(t) for t in (parts or ["09:00"])]


def _weekdays(days: str) -> list[str]:
	"""Parse a comma-separated weekday list into canonical abbrevs, in week order."""
	chosen = {d.strip().lower()[:3] for d in days.split(",")}
	ordered = [d for d in _WD_ORDER if d in chosen]
	return ordered or ["mon"]


def _parse_cron_field(raw: str, name: str) -> int | None:
	"""Return int for a literal digit field, None for '*'. Reject everything else."""
	if raw == "*":
		return None
	try:
		return int(raw)
	except ValueError:
		raise ScheduleError(
			f"cron field {name!r} must be '*' or a single integer, got {raw!r}; "
			"ranges, steps, and lists are not supported in the v0 subset"
		) from None


def _cron_fields(cron: str) -> tuple[str, str, str, str, str]:
	"""Split and validate a 5-field cron expression; return the five parts."""
	parts = cron.split()
	if len(parts) != 5:
		raise ScheduleError(f"cron must have 5 fields (min hour dom mon dow), got {cron!r}")
	return parts[0], parts[1], parts[2], parts[3], parts[4]


# ---------------------------------------------------------------------------
# launchd plist generation
# ---------------------------------------------------------------------------


def _calendar_intervals(sched: ScheduleConfig) -> list[dict[str, int]]:
	"""Translate a ScheduleConfig into launchd StartCalendarInterval dicts."""
	if sched.interval == "hourly":
		return [{"Minute": m} for _, m in _parse_times(sched.at)]
	if sched.interval == "daily":
		return [{"Hour": h, "Minute": m} for h, m in _parse_times(sched.at)]
	if sched.interval == "weekly":
		return [
			{"Weekday": _WD_NUM[d], "Hour": h, "Minute": m}
			for d in _weekdays(sched.days)
			for h, m in _parse_times(sched.at)
		]
	if sched.interval == "cron":
		minute, hour, dom, mon, dow = _cron_fields(sched.cron)
		if dom != "*" and dow != "*":
			raise ScheduleError(
				"cron sets both day-of-month and day-of-week; cron ORs them but "
				"launchd ANDs the keys in a single StartCalendarInterval, so the "
				"schedule would diverge. Set only one of dom/dow."
			)
		keys = (
			("Minute", minute),
			("Hour", hour),
			("Day", dom),
			("Month", mon),
			("Weekday", dow),
		)
		interval: dict[str, int] = {}
		for key, raw in keys:
			val = _parse_cron_field(raw, key)
			if val is not None:
				interval[key] = val
		if not interval:
			raise ScheduleError("refusing per-minute schedule (all cron fields are '*')")
		return [interval]
	raise ScheduleError(f"unknown interval {sched.interval!r}")


def render_plist(
	label: str,
	program_args: list[str],
	sched: ScheduleConfig,
	path_env: str | None = None,
) -> str:
	"""Render a launchd plist string for the given label, args, and schedule.

	A launchd job inherits a bare PATH (``/usr/bin:/bin:/usr/sbin:/sbin``), so
	when *path_env* is given it is embedded as ``EnvironmentVariables/PATH`` —
	without it the run cannot reach the ``claude`` CLI for triage.
	"""
	intervals = _calendar_intervals(sched)
	payload: dict[str, object] = {
		"Label": label,
		"ProgramArguments": program_args,
		"RunAtLoad": False,
		"StartCalendarInterval": intervals[0] if len(intervals) == 1 else intervals,
	}
	if path_env:
		payload["EnvironmentVariables"] = {"PATH": path_env}
	return plistlib.dumps(payload, sort_keys=True).decode("utf-8")


# ---------------------------------------------------------------------------
# systemd timer + service, and crontab generation
# ---------------------------------------------------------------------------


def _on_calendars(sched: ScheduleConfig, tz: str) -> list[str]:
	"""Build one systemd OnCalendar expression per scheduled time."""
	if sched.interval == "hourly":
		return [f"*-*-* *:{m:02d}:00 {tz}" for _, m in _parse_times(sched.at)]
	if sched.interval == "daily":
		return [f"*-*-* {h:02d}:{m:02d}:00 {tz}" for h, m in _parse_times(sched.at)]
	if sched.interval == "weekly":
		dow = ",".join(_WD_SYSTEMD[d] for d in _weekdays(sched.days))
		return [f"{dow} *-*-* {h:02d}:{m:02d}:00 {tz}" for h, m in _parse_times(sched.at)]
	return [_on_calendar(sched, tz)]


def _on_calendar(sched: ScheduleConfig, tz: str) -> str:
	"""Build a single systemd OnCalendar expression (first scheduled time)."""
	if sched.interval == "hourly":
		_, m = _parse_times(sched.at)[0]
		return f"*-*-* *:{m:02d}:00 {tz}"
	if sched.interval == "daily":
		h, m = _parse_times(sched.at)[0]
		return f"*-*-* {h:02d}:{m:02d}:00 {tz}"
	if sched.interval == "weekly":
		h, m = _parse_times(sched.at)[0]
		dow = ",".join(_WD_SYSTEMD[d] for d in _weekdays(sched.days))
		return f"{dow} *-*-* {h:02d}:{m:02d}:00 {tz}"
	if sched.interval == "cron":
		minute, hour, dom, mon, dow = _cron_fields(sched.cron)
		mm = _parse_cron_field(minute, "minute")
		hh = _parse_cron_field(hour, "hour")
		dd = _parse_cron_field(dom, "dom")
		mo = _parse_cron_field(mon, "mon")
		wd = _parse_cron_field(dow, "dow")
		if mm is None and hh is None and dd is None and mo is None and wd is None:
			raise ScheduleError("refusing per-minute schedule (all cron fields are '*')")
		if dd is not None and wd is not None:
			raise ScheduleError(
				"cron sets both day-of-month and day-of-week; cron ORs them but "
				"launchd/systemd AND them, so the schedules would diverge. "
				"Set only one of dom/dow."
			)
		dow_part = "" if wd is None else f"{_CRON_DOW_TO_SYSTEMD[wd]} "
		mon_s = "*" if mo is None else f"{mo:02d}"
		dom_s = "*" if dd is None else f"{dd:02d}"
		hour_s = "*" if hh is None else f"{hh:02d}"
		min_s = "*" if mm is None else f"{mm:02d}"
		return f"{dow_part}*-{mon_s}-{dom_s} {hour_s}:{min_s}:00 {tz}"
	raise ScheduleError(f"unknown interval {sched.interval!r}")


def render_service(exec_path: str, args: list[str], path_env: str | None = None) -> str:
	"""Render a systemd .service unit for android-watcher.

	systemd user services start from a minimal PATH, so when *path_env* is given
	it is embedded as ``Environment=PATH=`` — without it the run cannot reach the
	``claude`` CLI for triage.
	"""
	exec_start = " ".join([exec_path, *args])
	env_line = f"Environment=PATH={path_env}\n" if path_env else ""
	return (
		"[Unit]\n"
		"Description=android-watcher scheduled run\n"
		"\n"
		"[Service]\n"
		"Type=oneshot\n"
		f"{env_line}"
		f"ExecStart={exec_start}\n"
	)


def render_timer(sched: ScheduleConfig, tz: str) -> str:
	"""Render a systemd .timer unit for android-watcher."""
	on_cals = "".join(f"OnCalendar={c}\n" for c in _on_calendars(sched, tz))
	return (
		"[Unit]\n"
		"Description=android-watcher scheduled run timer\n"
		"\n"
		"[Timer]\n"
		f"{on_cals}"
		"Persistent=true\n"
		"\n"
		"[Install]\n"
		"WantedBy=timers.target\n"
	)


def _cron_lines(sched: ScheduleConfig) -> list[str]:
	"""Return the 5-field cron time spec(s) — one per scheduled time."""
	if sched.interval == "cron":
		_cron_fields(sched.cron)  # validate 5 fields
		return [sched.cron]
	times = _parse_times(sched.at)
	if sched.interval == "hourly":
		return [f"{m} * * * *" for _, m in times]
	if sched.interval == "daily":
		return [f"{m} {h} * * *" for h, m in times]
	if sched.interval == "weekly":
		dow = ",".join(str(_WD_NUM[d]) for d in _weekdays(sched.days))
		return [f"{m} {h} * * {dow}" for h, m in times]
	raise ScheduleError(f"unknown interval {sched.interval!r}")


def _cron_line(sched: ScheduleConfig) -> str:
	"""Return the first 5-field cron time spec (back-compat single-line helper)."""
	return _cron_lines(sched)[0]


def render_crontab(
	line_command: str, sched: ScheduleConfig, tz: str, path_env: str | None = None
) -> str:
	"""Render a marked crontab block for android-watcher (one line per scheduled time).

	cron runs with a minimal PATH, so when *path_env* is given a ``PATH=``
	assignment is emitted ahead of the schedule lines — without it the run cannot
	reach the ``claude`` CLI for triage.
	"""
	body = "\n".join(f"{spec} {line_command}" for spec in _cron_lines(sched))
	path_line = f"PATH={path_env}\n" if path_env else ""
	return f"{CRON_BEGIN}\nCRON_TZ={tz}\n{path_line}{body}\n{CRON_END}\n"


# ---------------------------------------------------------------------------
# Platform detection helpers (seams for testing via monkeypatch)
# ---------------------------------------------------------------------------


def _platform() -> str:
	return sys.platform


def _has_systemd() -> bool:
	return shutil.which("systemctl") is not None and Path("/run/systemd/system").exists()


def _local_tz() -> str:
	"""Best-effort IANA zone name; falls back to the /etc/localtime symlink."""
	tz = os.environ.get("TZ")
	if tz:
		return tz
	try:
		link = os.readlink("/etc/localtime")
		if "zoneinfo/" in link:
			return link.split("zoneinfo/", 1)[1]
	except OSError:
		pass
	return time.tzname[0] or "UTC"


def _program_args() -> list[str]:
	exe = shutil.which("android-watcher") or sys.argv[0]
	return [exe, "run"]


def _env_path() -> str:
	"""Snapshot the install-time PATH to embed in the scheduled unit.

	Native schedulers (launchd, systemd user, cron) run with a minimal PATH that
	omits per-user bin dirs like ``~/.local/bin``, so the ``claude`` CLI would be
	unreachable. Capturing the PATH from the shell that ran the install preserves
	parity with the environment where ``claude`` was found.
	"""
	return os.environ.get("PATH", "")


def _launchd_plist_path() -> str:
	return str(Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist")


def _systemd_dir() -> str:
	base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
	return str(Path(base) / "systemd" / "user")


def _linger_enabled() -> bool:
	try:
		out = subprocess.run(
			["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"],
			capture_output=True,
			text=True,
			check=False,
		)
		return "Linger=yes" in out.stdout
	except (OSError, subprocess.SubprocessError):
		return False


def _warn(msg: str) -> None:
	print(f"warning: {msg}", file=sys.stderr)


def _run(argv: list[str], *, input: str | None = None) -> subprocess.CompletedProcess:
	return subprocess.run(argv, input=input, capture_output=True, text=True, check=False)


# ---------------------------------------------------------------------------
# Internal per-platform install helpers
# ---------------------------------------------------------------------------


def _install_macos(config: Config) -> None:
	path = Path(_launchd_plist_path())
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(render_plist(LAUNCHD_LABEL, _program_args(), config.schedule, _env_path()))
	_run(["launchctl", "unload", str(path)])
	_run(["launchctl", "load", "-w", str(path)])
	_warn(
		"launchd LaunchAgents only fire while you are logged in to a GUI session; "
		"the job runs once on wake if a scheduled time was missed."
	)


def _install_systemd(config: Config) -> None:
	d = Path(_systemd_dir())
	d.mkdir(parents=True, exist_ok=True)
	exe, *run_args = _program_args()
	(d / f"{SYSTEMD_UNIT_NAME}.service").write_text(render_service(exe, run_args, _env_path()))
	(d / f"{SYSTEMD_UNIT_NAME}.timer").write_text(render_timer(config.schedule, _local_tz()))
	_run(["systemctl", "--user", "daemon-reload"])
	_run(["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNIT_NAME}.timer"])
	if not _linger_enabled():
		_warn(
			"systemd user timers only run while you are logged in unless lingering is enabled. "
			f"Run: loginctl enable-linger {os.environ.get('USER', '$USER')}"
		)


def _strip_cron_block(text: str) -> str:
	"""Return *text* with the android-watcher marked block removed."""
	lines = text.splitlines()
	out: list[str] = []
	skip = False
	for line in lines:
		if line.strip() == CRON_BEGIN:
			skip = True
			continue
		if line.strip() == CRON_END:
			skip = False
			continue
		if not skip:
			out.append(line)
	return "\n".join(out)


def _install_crontab(config: Config) -> None:
	existing = _run(["crontab", "-l"]).stdout
	block = render_crontab(" ".join(_program_args()), config.schedule, _local_tz(), _env_path())
	cleaned = _strip_cron_block(existing)
	new = (cleaned.rstrip("\n") + "\n" if cleaned.strip() else "") + block
	_run(["crontab", "-"], input=new)
	_warn(
		"crontab fallback pins the timezone via CRON_TZ where supported (Vixie/cronie); "
		"on cron builds that ignore CRON_TZ it uses the system timezone. "
		"Missed runs backfill via the run catch-up gate."
	)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def install_schedule(config: Config) -> None:
	"""Install the native scheduled job for the current platform."""
	if _platform() == "darwin":
		_install_macos(config)
	elif _platform().startswith("linux"):
		if _has_systemd():
			_install_systemd(config)
		else:
			_install_crontab(config)
	else:
		raise ScheduleError(f"unsupported platform {_platform()!r}")


def remove_schedule() -> None:
	"""Remove the native scheduled job."""
	if _platform() == "darwin":
		path = Path(_launchd_plist_path())
		if path.exists():
			_run(["launchctl", "unload", str(path)])
			path.unlink()
	elif _platform().startswith("linux"):
		if _has_systemd():
			_run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT_NAME}.timer"])
			d = Path(_systemd_dir())
			for name in (f"{SYSTEMD_UNIT_NAME}.timer", f"{SYSTEMD_UNIT_NAME}.service"):
				(d / name).unlink(missing_ok=True)
			_run(["systemctl", "--user", "daemon-reload"])
		else:
			existing = _run(["crontab", "-l"]).stdout
			_run(["crontab", "-"], input=_strip_cron_block(existing) + "\n")
	else:
		raise ScheduleError(f"unsupported platform {_platform()!r}")


def schedule_status() -> Check:
	"""Return a Check indicating whether the scheduled job is actually loaded/active."""
	name = "schedule"
	if _platform() == "darwin":
		out = _run(["launchctl", "list"]).stdout
		loaded = LAUNCHD_LABEL in out
		return Check(
			name=name,
			ok=loaded,
			detail=f"launchd agent {LAUNCHD_LABEL}: {'loaded' if loaded else 'not loaded'}",
		)
	if _platform().startswith("linux"):
		if _has_systemd():
			active = _run(
				["systemctl", "--user", "is-active", f"{SYSTEMD_UNIT_NAME}.timer"]
			).stdout.strip()
			enabled = _run(
				["systemctl", "--user", "is-enabled", f"{SYSTEMD_UNIT_NAME}.timer"]
			).stdout.strip()
			linger = "linger on" if _linger_enabled() else "linger OFF (runs only while logged in)"
			ok = active == "active"
			return Check(
				name=name,
				ok=ok,
				detail=(
					f"systemd timer is-active={active or 'unknown'} "
					f"is-enabled={enabled or 'unknown'}; {linger}"
				),
			)
		present = CRON_BEGIN in _run(["crontab", "-l"]).stdout
		return Check(
			name=name,
			ok=present,
			detail=f"crontab entry {'present' if present else 'absent'}",
		)
	return Check(name=name, ok=False, detail=f"unsupported platform {_platform()!r}")
