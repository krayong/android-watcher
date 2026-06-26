"""android-watcher CLI: argparse router."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .catalog import load_catalog
from .config import load_config
from .doctor import run_doctor
from .models import AlreadyRunning, ConfigError
from .notify.render import render_email
from .run import configure_file_logging, run_once
from .schedule import install_schedule, remove_schedule, schedule_status
from .tui.app import AndroidWatcher
from .tui.configio import load_or_default


def _cmd_catalog(args: argparse.Namespace) -> int:
	for source in load_catalog():
		state = "on " if source.enabled else "off"
		print(f"[{state}] {source.id:<28} {source.detector:<16} {source.category}")
	return 0


def _cmd_run(args: argparse.Namespace) -> int:
	try:
		config = load_config(args.config)
	except ConfigError as exc:
		print(f"android-watcher: configuration error: {exc}", file=sys.stderr)
		return 1
	log_file = configure_file_logging()
	print(f"android-watcher: running… (progress logged to {log_file})", file=sys.stderr)
	try:
		digest = run_once(config, force=args.force)
	except AlreadyRunning:
		print("android-watcher: another run is in progress; exiting.")
		return 0
	print(f"android-watcher: {digest.change_count()} change(s) delivered.")
	return 0


def _cmd_test(args: argparse.Namespace) -> int:
	try:
		config = load_config(args.config)
	except ConfigError as exc:
		print(f"android-watcher: configuration error: {exc}", file=sys.stderr)
		return 1
	configure_file_logging()
	checks = run_doctor(config)
	failed = [
		c for c in checks if not c.ok and (c.name == "ai-backend" or c.name.startswith("channel"))
	]
	digest = run_once(config, dry_run=True)
	_, plaintext = render_email(digest)
	print(plaintext)
	if failed:
		for c in failed:
			print(f"FAIL {c.name}: {c.detail}")
		return 1
	return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
	try:
		config = load_config(args.config)
	except ConfigError as exc:
		print(f"android-watcher: configuration error: {exc}", file=sys.stderr)
		return 1
	print("Running checks (verifying the sitemap may take up to ~30s)…", file=sys.stderr)
	checks = run_doctor(config)
	any_failed = False
	for c in checks:
		status = "OK  " if c.ok else "FAIL"
		if not c.ok:
			any_failed = True
		print(f"{status} {c.name}: {c.detail}")
	return 1 if any_failed else 0


def _cmd_schedule(args: argparse.Namespace) -> int:
	if args.action == "install":
		config = load_config(args.config) if args.config else load_or_default()[0]
		install_schedule(config)
		return 0
	if args.action == "remove":
		remove_schedule()
		return 0
	# status
	check = schedule_status()
	status = "OK  " if check.ok else "FAIL"
	print(f"{status} {check.name}: {check.detail}")
	return 0 if check.ok else 1


def _cmd_tui(args: argparse.Namespace) -> int:
	config, existed = load_or_default()
	# Configuration is incomplete until a delivery channel is set, so run the
	# wizard from the start in that case too — not only when the file is absent.
	# (Short-circuits on a fresh install before touching the config fields.)
	first_run = (not existed) or not (
		config.email.enabled
		or config.slack.enabled
		or config.telegram.enabled
		or config.desktop.enabled
	)
	result = AndroidWatcher(config=config, first_run=first_run).run()
	if isinstance(result, str):
		print(result)
	return 0


def _build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		prog="android-watcher",
		description="Watch official Google Android sites and deliver a ranked digest.",
	)
	parser.add_argument("--version", action="version", version=f"android-watcher {__version__}")
	parser.add_argument(
		"--config",
		default=None,
		metavar="PATH",
		help="Path to config file (default: platform config dir).",
	)
	parser.set_defaults(func=_cmd_tui)

	sub = parser.add_subparsers(dest="command")

	run_p = sub.add_parser("run", help="Run one detection-triage-notify cycle.")
	run_p.add_argument(
		"--force",
		action="store_true",
		help="Run even if the last cycle is not yet due.",
	)

	sub.add_parser("test", help="Dry-run and render the current digest to stdout.")
	sub.add_parser("catalog", help="List configured sources from the catalog.")
	sub.add_parser("doctor", help="Run health checks.")

	schedule_p = sub.add_parser("schedule", help="Manage the native scheduled job.")
	schedule_p.add_argument(
		"action",
		choices=["install", "remove", "status"],
		help="Schedule action to perform.",
	)

	# Wire up handlers after all subparsers are registered.
	for name, func in [
		("run", _cmd_run),
		("test", _cmd_test),
		("catalog", _cmd_catalog),
		("doctor", _cmd_doctor),
		("schedule", _cmd_schedule),
	]:
		sub.choices[name].set_defaults(func=func)

	return parser


def main(argv: list[str] | None = None) -> int:
	parser = _build_parser()
	args = parser.parse_args(argv)
	return args.func(args)
