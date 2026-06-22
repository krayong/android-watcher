from __future__ import annotations

import contextlib
import fcntl
import os
from collections.abc import Iterator

from .models import AlreadyRunning  # single definition lives in models.py

__all__ = ["AlreadyRunning", "run_lock"]


@contextlib.contextmanager
def run_lock(data_dir: str) -> Iterator[None]:
	"""Acquire an exclusive flock on ``data_dir/run.lock``.

	Raises ``AlreadyRunning`` if the lock is already held by another process.
	"""
	os.makedirs(data_dir, exist_ok=True)
	lock_path = os.path.join(data_dir, "run.lock")
	fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
	try:
		try:
			fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except OSError as exc:
			raise AlreadyRunning(f"another android-watcher run holds {lock_path}") from exc
		try:
			yield
		finally:
			fcntl.flock(fd, fcntl.LOCK_UN)
	finally:
		os.close(fd)
