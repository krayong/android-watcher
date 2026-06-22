import pytest

from android_watcher.detect.base import DETECTORS, Detector
from android_watcher.registry import Registry


def test_detectors_is_registry_named_detector():
	assert isinstance(DETECTORS, Registry)


def test_register_and_get_roundtrip():
	@DETECTORS.register("dummy_b3")
	class Dummy:
		async def detect(self, source, store, fetcher):
			return []

	assert DETECTORS.get("dummy_b3") is Dummy
	assert "dummy_b3" in DETECTORS.available()


def test_unknown_name_raises_with_listing():
	with pytest.raises(KeyError):
		DETECTORS.get("nope_does_not_exist")


def test_concrete_detector_satisfies_runtime_checkable_protocol():
	class LocalDetector:
		async def detect(self, source, store, fetcher):
			return []

	instance = LocalDetector()
	assert isinstance(instance, Detector)
