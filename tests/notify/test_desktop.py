"""Tests for notify/desktop.py: local HTML write + native click-to-open notification."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

import android_watcher.notify.desktop as desktop_mod
from android_watcher.config import Config, DesktopChannel
from android_watcher.models import Change, Digest, DigestGroup, NotifyError
from android_watcher.notify.desktop import DesktopNotifier


def _config(sound: str = "Glass") -> Config:
	return Config(
		schedule=None,
		ai=None,
		digest=None,
		sort={},
		email=None,
		slack=None,
		telegram=None,
		custom_sources=[],
		desktop=DesktopChannel(enabled=True, sound=sound),
	)


def _digest(*, generated_at: datetime | None = None) -> Digest:
	g = DigestGroup(
		key="k1",
		title="New page",
		summary="A real change.",
		category="guides",
		source_id="src1",
		change_kind="new",
		members=[
			Change(source_id="src1", url="https://x/1", change_kind="new", title="New page", id=7)
		],
		score=5,
	)
	d = Digest(groups=[g], max_items=10)
	if generated_at is not None:
		d.generated_at = generated_at
	return d


@pytest.fixture
def _macos(monkeypatch, tmp_path):
	"""macOS with terminal-notifier available, digests written under tmp_path, and a
	captured (not executed) subprocess.run."""
	calls: list[list[str]] = []
	monkeypatch.setattr(desktop_mod.sys, "platform", "darwin")
	monkeypatch.setattr(desktop_mod, "desktop_mechanism_available", lambda: True)
	monkeypatch.setattr(desktop_mod, "digests_dir", lambda: str(tmp_path / "digests"))

	def fake_run(argv, **kw):
		calls.append(argv)

		class _P:
			returncode = 0

		return _P()

	monkeypatch.setattr(desktop_mod.subprocess, "run", fake_run)
	return calls, tmp_path


def test_writes_timestamped_html_file(_macos):
	_calls, tmp_path = _macos
	digest = _digest(generated_at=datetime(2026, 6, 23, 14, 5, 9, tzinfo=UTC))
	DesktopNotifier().send(digest, _config())
	files = list((tmp_path / "digests").glob("*.html"))
	assert len(files) == 1
	# android-watcher-digest-<DDMMYYYY>-<HHMMSS>.html (local time of generated_at)
	assert re.fullmatch(r"android-watcher-digest-\d{8}-\d{6}\.html", files[0].name)
	assert "Android Watcher Digest" in files[0].read_text()


def test_macos_argv_opens_file_on_click(_macos):
	calls, tmp_path = _macos
	DesktopNotifier().send(_digest(), _config(sound="Ping"))
	assert len(calls) == 1
	argv = calls[0]
	assert argv[0] == "terminal-notifier"
	assert "-sound" in argv and argv[argv.index("-sound") + 1] == "Ping"
	exec_val = argv[argv.index("-execute") + 1]
	written = next((tmp_path / "digests").glob("*.html"))
	assert exec_val == f"open {written}"  # no spaces in tmp path; quoting verified separately


def test_macos_execute_quotes_path_with_spaces(monkeypatch, tmp_path):
	calls: list[list[str]] = []
	spaced = tmp_path / "a dir"
	monkeypatch.setattr(desktop_mod.sys, "platform", "darwin")
	monkeypatch.setattr(desktop_mod, "desktop_mechanism_available", lambda: True)
	monkeypatch.setattr(desktop_mod, "digests_dir", lambda: str(spaced))
	monkeypatch.setattr(desktop_mod.subprocess, "run", lambda argv, **kw: calls.append(argv))
	DesktopNotifier().send(_digest(), _config())
	exec_val = calls[0][calls[0].index("-execute") + 1]
	assert "'" in exec_val  # shlex.quote wrapped the spaced path


def test_linux_uses_notify_send_with_path_in_body(monkeypatch, tmp_path):
	calls: list[list[str]] = []
	monkeypatch.setattr(desktop_mod.sys, "platform", "linux")
	monkeypatch.setattr(desktop_mod, "desktop_mechanism_available", lambda: True)
	monkeypatch.setattr(desktop_mod, "digests_dir", lambda: str(tmp_path / "digests"))
	monkeypatch.setattr(desktop_mod.subprocess, "run", lambda argv, **kw: calls.append(argv))
	DesktopNotifier().send(_digest(), _config())
	argv = calls[0]
	assert argv[0] == "notify-send"
	assert any("file://" in part for part in argv)


def test_empty_digest_no_file_no_notification(_macos):
	calls, tmp_path = _macos
	result = DesktopNotifier().send(Digest(groups=[]), _config())
	assert result == set()
	assert calls == []
	assert not (tmp_path / "digests").exists() or list((tmp_path / "digests").glob("*.html")) == []


def test_missing_mechanism_raises(monkeypatch, tmp_path):
	monkeypatch.setattr(desktop_mod, "desktop_mechanism_available", lambda: False)
	monkeypatch.setattr(desktop_mod, "digests_dir", lambda: str(tmp_path / "digests"))
	called = []
	monkeypatch.setattr(desktop_mod.subprocess, "run", lambda *a, **k: called.append(a))
	with pytest.raises(NotifyError):
		DesktopNotifier().send(_digest(), _config())
	assert called == []


def test_returns_member_ids(_macos):
	ids = DesktopNotifier().send(_digest(), _config())
	assert ids == {7}


def test_nonzero_exit_raises(monkeypatch, tmp_path):
	import subprocess

	monkeypatch.setattr(desktop_mod.sys, "platform", "darwin")
	monkeypatch.setattr(desktop_mod, "desktop_mechanism_available", lambda: True)
	monkeypatch.setattr(desktop_mod, "digests_dir", lambda: str(tmp_path / "digests"))

	def boom(argv, **kw):
		raise subprocess.CalledProcessError(1, argv)

	monkeypatch.setattr(desktop_mod.subprocess, "run", boom)
	with pytest.raises(NotifyError):
		DesktopNotifier().send(_digest(), _config())
