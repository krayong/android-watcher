"""Config dataclasses, path helpers, TOML loader, and env interpolation."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Any, Literal

import platformdirs

from .models import ConfigError, Source  # ConfigError defined once in models.py

__all__ = [
	"AIConfig",
	"Config",
	"ConfigError",
	"DesktopChannel",
	"DigestConfig",
	"EmailChannel",
	"ScheduleConfig",
	"SlackChannel",
	"TelegramChannel",
	"config_path",
	"data_path",
	"db_path",
	"desktop_mechanism_available",
	"digests_dir",
	"load_config",
	"log_path",
]

APP_NAME = "android-watcher"
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_VALID_INTERVALS = {"hourly", "daily", "weekly", "cron"}
_VALID_AI_MODES = {"claude_cli", "off"}
_VALID_EMPTY = {"send", "skip"}


@dataclass
class ScheduleConfig:
	interval: Literal["hourly", "daily", "weekly", "cron"] = "daily"
	at: str = "09:00"  # one or more HH:MM, comma-separated
	days: str = "mon"  # weekly only: comma-separated weekday abbrevs (mon..sun)
	cron: str = ""
	# Extra environment variables baked into the native scheduler unit. The
	# scheduled job (and the claude CLI it shells out to for triage) inherit a
	# bare environment, so e.g. CLAUDE_ACCOUNT here lets an account-aware claude
	# wrapper resolve a profile when it cannot from the job's working directory.
	env: dict[str, str] = field(default_factory=dict)


@dataclass
class AIConfig:
	mode: Literal["claude_cli", "off"] = "claude_cli"
	model: str = "claude-sonnet-4-6"


@dataclass
class DigestConfig:
	max_items: int = 10
	empty: Literal["send", "skip"] = "send"


@dataclass
class EmailChannel:
	enabled: bool = False
	smtp_host: str = ""
	smtp_port: int = 465
	username: str = ""
	password: str = ""
	sender: str = ""  # maps to TOML key "from"
	recipient: str = ""  # maps to TOML key "to"


@dataclass
class SlackChannel:
	enabled: bool = False
	bot_token: str = ""  # secret; supports ${ENV_VAR}
	channel: str = ""


@dataclass
class TelegramChannel:
	enabled: bool = False
	bot_token: str = ""  # secret; supports ${ENV_VAR}
	chat_id: str = ""


@dataclass
class DesktopChannel:
	enabled: bool = False
	sound: str = "Glass"  # macOS notification sound name


@dataclass
class Config:
	schedule: ScheduleConfig
	ai: AIConfig
	digest: DigestConfig
	sort: dict[str, int]
	email: EmailChannel
	slack: SlackChannel
	telegram: TelegramChannel
	custom_sources: list[Source]
	# Defaulted (disabled) so existing Config(...) call sites need no change; a new
	# optional local channel that is off until the user opts in.
	desktop: DesktopChannel = field(default_factory=DesktopChannel)
	enabled_source_ids: set[str] = field(default_factory=set)


def config_path() -> str:
	return os.path.join(platformdirs.user_config_dir(APP_NAME), "config.toml")


def data_path() -> str:
	return platformdirs.user_data_dir(APP_NAME)


def db_path() -> str:
	return os.path.join(data_path(), "state.db")


def digests_dir() -> str:
	"""Directory where the desktop channel writes click-to-open HTML digests."""
	return os.path.join(data_path(), "digests")


def _desktop_binary() -> str | None:
	"""The required desktop-notification binary for this platform, or None.

	macOS uses terminal-notifier (the only mechanism that opens the digest on
	click); Linux uses notify-send. Other platforms have no supported mechanism.
	"""
	if sys.platform == "darwin":
		return "terminal-notifier"
	if sys.platform.startswith("linux"):
		return "notify-send"
	return None


def desktop_mechanism_available() -> bool:
	"""True if this platform's required desktop-notification binary is installed."""
	binary = _desktop_binary()
	return bool(binary and shutil.which(binary))


def log_path() -> str:
	"""Path to the run log. ~/Library/Logs/android-watcher.log on macOS."""
	import sys  # noqa: PLC0415 - localized; keeps module import graph lean

	if sys.platform == "darwin":
		return os.path.join(os.path.expanduser("~/Library/Logs"), "android-watcher.log")
	return os.path.join(platformdirs.user_log_dir(APP_NAME), "android-watcher.log")


def _interpolate(value: str, *, expand: bool) -> str:
	"""Resolve ${ENV} references in a secret-bearing field.

	When expand=False, leave the literal untouched and never raise (the TUI
	editor re-saves config without baking secrets into the file).
	"""
	if not expand:
		return value

	def repl(match: re.Match[str]) -> str:
		name = match.group(1)
		if name not in os.environ:
			raise ConfigError(f"config references undefined env var ${{{name}}}")
		return os.environ[name]

	return _ENV_RE.sub(repl, value)


def load_config(path: str | None = None, *, expand: bool = True) -> Config:
	"""Load config from *path* (defaults to the platform config path).

	Missing file returns all defaults. Raises ConfigError on invalid TOML or
	contradictory schedule, or when slack is enabled without bot_token+channel.
	Interpolation of ${ENV_VAR} applies only to secret-bearing fields (email
	password, slack bot_token, telegram bot_token).
	"""
	target = path or config_path()
	try:
		with open(target, "rb") as fh:
			raw = tomllib.load(fh)
	except FileNotFoundError:
		raw = {}
	except tomllib.TOMLDecodeError as exc:
		raise ConfigError(f"invalid TOML in {target}: {exc}") from exc

	# Interpolation is scoped to secret-bearing fields only (see _load_email /
	# _load_slack / _load_telegram). Every other string, including URLs, is
	# passed through verbatim so a literal "${" is never an error.
	schedule = _load_schedule(raw.get("schedule", {}))
	ai = _load_ai(raw.get("ai", {}))
	digest = _load_digest(raw.get("digest", {}))
	sort = _load_sort(raw.get("sort", {}))
	channels = raw.get("channels", {})
	email = _load_email(channels.get("email", {}), expand=expand)
	slack = _load_slack(channels.get("slack", {}), expand=expand)
	telegram = _load_telegram(channels.get("telegram", {}), expand=expand)
	desktop = _load_desktop(channels.get("desktop", {}))
	custom_sources = [_load_source(e) for e in raw.get("custom_source", [])]
	enabled = set(raw.get("enabled_sources", []))

	if slack.enabled and not (slack.bot_token and slack.channel):
		raise ConfigError("slack channel enabled but bot_token and channel are required")

	return Config(
		schedule=schedule,
		ai=ai,
		digest=digest,
		sort=sort,
		email=email,
		slack=slack,
		telegram=telegram,
		desktop=desktop,
		custom_sources=custom_sources,
		enabled_source_ids=enabled,
	)


def _load_schedule(d: dict[str, Any]) -> ScheduleConfig:
	interval = d.get("interval", "daily")
	if interval not in _VALID_INTERVALS:
		raise ConfigError(
			f"schedule.interval must be one of {sorted(_VALID_INTERVALS)}, got {interval!r}"
		)
	cron = d.get("cron", "")
	if interval == "cron" and not cron:
		raise ConfigError("schedule.interval = 'cron' requires a non-empty schedule.cron")
	if interval != "cron" and cron:
		raise ConfigError(
			f"schedule.cron is set but interval is {interval!r}; "
			"set interval = 'cron' or clear cron"
		)
	env = {str(k): str(v) for k, v in d.get("env", {}).items()}
	return ScheduleConfig(
		interval=interval, at=d.get("at", "09:00"), days=d.get("days", "mon"), cron=cron, env=env
	)


def _load_ai(d: dict[str, Any]) -> AIConfig:
	mode = d.get("mode", "claude_cli")
	if mode not in _VALID_AI_MODES:
		raise ConfigError(f"ai.mode must be one of {sorted(_VALID_AI_MODES)}, got {mode!r}")
	return AIConfig(mode=mode, model=d.get("model", "claude-sonnet-4-6"))


def _load_digest(d: dict[str, Any]) -> DigestConfig:
	empty = d.get("empty", "send")
	if empty not in _VALID_EMPTY:
		raise ConfigError(f"digest.empty must be one of {sorted(_VALID_EMPTY)}, got {empty!r}")
	return DigestConfig(
		max_items=int(d.get("max_items", 10)),
		empty=empty,
	)


def _load_sort(d: dict[str, Any]) -> dict[str, int]:
	return {str(k): int(v) for k, v in d.items()}


def _load_email(d: dict[str, Any], *, expand: bool) -> EmailChannel:
	return EmailChannel(
		enabled=bool(d.get("enabled", False)),
		smtp_host=d.get("smtp_host", ""),
		smtp_port=int(d.get("smtp_port", 465)),
		username=d.get("username", ""),
		password=_interpolate(d.get("password", ""), expand=expand),  # secret
		sender=d.get("from", ""),
		recipient=d.get("to", ""),
	)


def _load_slack(d: dict[str, Any], *, expand: bool) -> SlackChannel:
	return SlackChannel(
		enabled=bool(d.get("enabled", False)),
		bot_token=_interpolate(d.get("bot_token", ""), expand=expand),  # secret
		channel=d.get("channel", ""),
	)


def _load_telegram(d: dict[str, Any], *, expand: bool) -> TelegramChannel:
	return TelegramChannel(
		enabled=bool(d.get("enabled", False)),
		bot_token=_interpolate(d.get("bot_token", ""), expand=expand),  # secret
		chat_id=d.get("chat_id", ""),
	)


def _load_desktop(d: dict[str, Any]) -> DesktopChannel:
	# No secret-bearing fields, so no ${ENV} interpolation needed.
	return DesktopChannel(
		enabled=bool(d.get("enabled", False)),
		sound=d.get("sound", "Glass"),
	)


def _load_source(e: dict[str, Any]) -> Source:
	return Source(
		id=e["id"],
		name=e["name"],
		category=e["category"],
		detector=e["detector"],
		url=e["url"],
		enabled=e.get("enabled", True),
		path_prefix=e.get("path_prefix", ""),
		feed_url=e.get("feed_url", ""),
		content_selector=e.get("content_selector", ""),
		default_weight=e.get("default_weight", 0),
	)
