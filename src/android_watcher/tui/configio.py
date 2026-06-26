"""Pure Config<->TOML (de)serialization and validation.

No UI, no network. All functions are synchronous and side-effect-free
except write_config (which writes a file and chmods it 0600) and
validate_config (which writes a temp file to round-trip through load_config).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from android_watcher.config import (
	AIConfig,
	Config,
	ConfigError,
	DesktopChannel,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
	config_path,
	desktop_mechanism_available,
	load_config,
)
from android_watcher.models import Source

__all__ = [
	"config_to_toml",
	"load_or_default",
	"validate_config",
	"write_config",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _toml_str(value: str) -> str:
	"""Emit a TOML basic string, preserving ${ENV} refs verbatim."""
	escaped = value.replace("\\", "\\\\").replace('"', '\\"')
	return f'"{escaped}"'


def _source_table(prefix: str, s: Source) -> str:
	return (
		f"[[{prefix}]]\n"
		f"id = {_toml_str(s.id)}\n"
		f"name = {_toml_str(s.name)}\n"
		f"category = {_toml_str(s.category)}\n"
		f"detector = {_toml_str(s.detector)}\n"
		f"url = {_toml_str(s.url)}\n"
		f"enabled = {'true' if s.enabled else 'false'}\n"
		f"path_prefix = {_toml_str(s.path_prefix)}\n"
		f"feed_url = {_toml_str(s.feed_url)}\n"
		f"content_selector = {_toml_str(s.content_selector)}\n"
		f"default_weight = {s.default_weight}\n"
	)


def _in_git_worktree(path: str) -> bool:
	"""Return True if *path* is inside a git work tree."""
	current = Path(path).resolve()
	for parent in [current, *current.parents]:
		if (parent / ".git").exists():
			return True
	return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def config_to_toml(config: Config) -> str:
	"""Serialize *config* to a TOML string.

	Only the surfaced channels (Slack, Desktop) are written. The Slack bot_token
	is written exactly as held — a ${ENV_VAR} ref is preserved verbatim, never
	expanded.
	"""
	sc, ai, dg = config.schedule, config.ai, config.digest
	sl = config.slack
	lines: list[str] = []

	# Top-level scalar keys must come before any section headers so TOML
	# parsers assign them to the document root, not the preceding section.
	ids = ", ".join(_toml_str(i) for i in sorted(config.enabled_source_ids))
	lines.append(f"enabled_sources = [{ids}]")
	lines.append("")

	lines.append("[schedule]")
	lines.append(f"interval = {_toml_str(sc.interval)}")
	lines.append(f"at = {_toml_str(sc.at)}")
	lines.append(f"days = {_toml_str(sc.days)}")
	lines.append(f"cron = {_toml_str(sc.cron)}")
	lines.append("")

	if sc.env:
		lines.append("[schedule.env]")
		for key, val in sc.env.items():
			lines.append(f"{_toml_str(key)} = {_toml_str(val)}")
		lines.append("")

	lines.append("[ai]")
	lines.append(f"mode = {_toml_str(ai.mode)}")
	lines.append(f"model = {_toml_str(ai.model)}")
	lines.append("")

	lines.append("[digest]")
	lines.append(f"max_items = {dg.max_items}")
	lines.append(f"empty = {_toml_str(dg.empty)}")
	lines.append("")

	lines.append("[sort]")
	for key, weight in sorted(config.sort.items()):
		lines.append(f"{_toml_str(key)} = {weight}")
	lines.append("")

	# Only the surfaced channels (Slack, Desktop) are serialized. Email and
	# Telegram remain fully functional in the code and load_config still parses a
	# hand-added [channels.email] / [channels.telegram] section, but the TUI no
	# longer writes or manages them.
	lines.append("[channels.slack]")
	lines.append(f"enabled = {'true' if sl.enabled else 'false'}")
	lines.append(f"bot_token = {_toml_str(sl.bot_token)}")
	lines.append(f"channel = {_toml_str(sl.channel)}")
	lines.append("")

	ds = config.desktop
	lines.append("[channels.desktop]")
	lines.append(f"enabled = {'true' if ds.enabled else 'false'}")
	lines.append(f"sound = {_toml_str(ds.sound)}")
	lines.append("")

	for s in config.custom_sources:
		lines.append(_source_table("custom_source", s))

	return "\n".join(lines).rstrip("\n") + "\n"


def write_config(config: Config, path: str) -> None:
	"""Write *config* as TOML to *path* and set permissions to 0600.

	Creates parent directories as needed. SECURITY: chmod to 0600 so
	the file (which may contain secrets or ${ENV} refs to secrets) is
	not readable by other users.
	"""
	p = Path(path)
	p.parent.mkdir(parents=True, exist_ok=True)
	p.write_text(config_to_toml(config), encoding="utf-8")
	os.chmod(path, 0o600)


def validate_config(config: Config) -> list[str]:
	"""Return a list of human-readable error strings; empty means valid.

	Re-checks the same contradictions load_config enforces by round-tripping
	through load_config(tmp, expand=False) on a temp file. Also warns when
	the platform config path is inside a git work tree.
	"""
	errors: list[str] = []
	with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as tf:
		tf.write(config_to_toml(config))
		tmp = tf.name
	try:
		# expand=False: validate structure without requiring env vars to be set.
		load_config(tmp, expand=False)
	except ConfigError as exc:
		errors.append(str(exc))
	finally:
		os.unlink(tmp)
	# Any enabled channel satisfies the requirement (a hand-configured email or
	# telegram still counts), but the guidance names only the surfaced channels.
	if not (
		config.email.enabled
		or config.slack.enabled
		or config.telegram.enabled
		or config.desktop.enabled
	):
		errors.append("enable at least one delivery channel (Slack or Desktop) to receive digests")
	sl = config.slack
	if sl.enabled and not (sl.bot_token and sl.channel):
		errors.append("slack channel is enabled but bot_token + channel are required")
	if config.desktop.enabled and not desktop_mechanism_available():
		errors.append(
			"desktop channel is enabled but no notifier is available "
			"(install terminal-notifier on macOS or notify-send on Linux)"
		)
	if _in_git_worktree(config_path()):
		errors.append(
			f"warning: config path {config_path()} is inside a git work tree; "
			"secrets could be committed. Use ${ENV_VAR} refs or move the file."
		)
	return errors


def load_or_default() -> tuple[Config, bool]:
	"""Load the platform config with expand=False, or return a blank default.

	Returns (config, existed). expand=False keeps ${ENV} refs literal so the
	editor never bakes resolved secrets into the saved file, and opening a
	config whose referenced env var is unset never raises.
	"""
	path = config_path()
	if Path(path).exists():
		return load_config(path, expand=False), True
	blank = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		desktop=DesktopChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	return blank, False
