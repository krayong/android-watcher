"""Notifier protocol, re-export of NotifyError, and NOTIFIERS registry."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from android_watcher.config import Config
from android_watcher.models import Digest, NotifyError  # shared exception defined in models.py
from android_watcher.registry import Registry

__all__ = ["NotifyError", "Notifier", "NOTIFIERS"]


@runtime_checkable
class Notifier(Protocol):
	name: str

	def send(self, digest: Digest, config: Config) -> set[int]: ...


NOTIFIERS: Registry[Notifier] = Registry("notifier")
