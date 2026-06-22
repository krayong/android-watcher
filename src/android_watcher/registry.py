from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
	"""A name->implementation registry shared by detectors, triagers, notifiers.

	Registries store CLASSES, not instances. ``@reg.register("feed")`` decorates
	a class; ``reg.get("feed")`` returns the class; the caller instantiates with
	no args (``DETECTORS.get("feed")()``).
	"""

	def __init__(self, kind: str) -> None:
		self.kind = kind
		self._items: dict[str, type[T]] = {}

	def register(self, name: str) -> Callable[[type[T]], type[T]]:
		def decorator(impl: type[T]) -> type[T]:
			if name in self._items:
				raise ValueError(f"{self.kind} {name!r} is already registered")
			self._items[name] = impl
			return impl

		return decorator

	def get(self, name: str) -> type[T]:
		try:
			return self._items[name]
		except KeyError:
			avail = ", ".join(self.available()) or "(none registered)"
			raise KeyError(f"{self.kind} {name!r} not found; available: {avail}") from None

	def available(self) -> list[str]:
		return sorted(self._items)
