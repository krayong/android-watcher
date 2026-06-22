"""Tests for notify/base.py: Notifier protocol, NOTIFIERS registry, NotifyError re-export."""

from __future__ import annotations

import pytest

from android_watcher.models import NotifyError as ModelsNotifyError
from android_watcher.notify.base import NOTIFIERS, Notifier, NotifyError


def test_notifyerror_is_runtimeerror_subclass() -> None:
	assert issubclass(NotifyError, RuntimeError)


def test_notifyerror_is_same_object_as_models_notifyerror() -> None:
	assert NotifyError is ModelsNotifyError


def test_dummy_notifier_registers_and_resolves() -> None:
	@NOTIFIERS.register("dummy")
	class DummyNotifier:
		name = "dummy"

		def send(self, digest, config) -> None:  # noqa: ANN001
			pass

	cls = NOTIFIERS.get("dummy")
	assert cls is DummyNotifier
	instance = cls()
	assert isinstance(instance, Notifier)


def test_get_unknown_raises_with_available_listed() -> None:
	with pytest.raises(KeyError, match="dummy"):
		NOTIFIERS.get("nope")
