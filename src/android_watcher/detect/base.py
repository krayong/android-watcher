from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Change, Source
from ..registry import Registry


@runtime_checkable
class Detector(Protocol):
	async def detect(self, source: Source, store, fetcher) -> list[Change]: ...


DETECTORS: Registry[Detector] = Registry("detector")
