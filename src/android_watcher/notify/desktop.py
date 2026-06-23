"""Desktop notifier: a native notification that opens a local HTML digest on click.

macOS uses ``terminal-notifier`` (whose ``-execute`` runs ``open <file>`` on click);
Linux uses ``notify-send`` (no portable click action, so the digest path is shown in
the body). Both render the full digest to ``<data_dir>/digests`` first.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from pathlib import Path

from android_watcher.config import Config, desktop_mechanism_available, digests_dir
from android_watcher.models import Digest, DigestGroup, NotifyError
from android_watcher.notify.base import NOTIFIERS
from android_watcher.notify.html import render_html

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0


def _member_ids(groups: list[DigestGroup]) -> set[int]:
	return {m.id for g in groups for m in g.members if m.id is not None}


def _digest_filename(digest: Digest) -> str:
	# Local time in the human-facing filename; UTC is kept internally on the digest.
	local = digest.generated_at.astimezone()
	return f"android-watcher-digest-{local:%d%m%Y-%H%M%S}.html"


def _write_html(digest: Digest) -> Path:
	out_dir = Path(digests_dir())
	out_dir.mkdir(parents=True, exist_ok=True)
	path = out_dir / _digest_filename(digest)
	path.write_text(render_html(digest), encoding="utf-8")
	return path


def _summary(digest: Digest) -> str:
	"""Counts only — never raw page titles, which come from untrusted sources."""
	n = digest.change_count()
	g = len(digest.groups)
	return f"{n} change{'' if n == 1 else 's'} across {g} group{'' if g == 1 else 's'}"


def _notify(title: str, message: str, path: Path, sound: str) -> None:
	if sys.platform == "darwin":
		argv = [
			"terminal-notifier",
			"-title",
			title,
			"-message",
			message,
			"-sound",
			sound,
			"-group",
			"android-watcher",
			# The path is app-generated; quote it so a space in the data dir is safe.
			"-execute",
			f"open {shlex.quote(str(path))}",
		]
	elif sys.platform.startswith("linux"):
		# notify-send has no portable click-to-run action; show the digest URI instead.
		argv = ["notify-send", title, f"{message}\n{path.as_uri()}"]
	else:  # pragma: no cover - guarded earlier by desktop_mechanism_available()
		raise NotifyError(f"desktop notifications unsupported on {sys.platform!r}")
	try:
		subprocess.run(argv, check=True, capture_output=True, timeout=_TIMEOUT)
	except FileNotFoundError as exc:
		raise NotifyError(f"desktop notifier binary not found: {argv[0]}") from exc
	except subprocess.CalledProcessError as exc:
		raise NotifyError(f"{argv[0]} exited {exc.returncode}") from exc
	except subprocess.TimeoutExpired as exc:
		raise NotifyError(f"{argv[0]} timed out after {_TIMEOUT}s") from exc


@NOTIFIERS.register("desktop")
class DesktopNotifier:
	name = "desktop"

	def send(self, digest: Digest, config: Config) -> set[int]:
		# Notify only when there is something to show; no daily empty-digest pop.
		if not digest.groups:
			return set()
		if not desktop_mechanism_available():
			raise NotifyError("no desktop notification mechanism available")
		path = _write_html(digest)
		logger.info("desktop: wrote digest page %s; firing notification", path)
		_notify("Android Watcher", _summary(digest), path, config.desktop.sound)
		return _member_ids(digest.groups)
