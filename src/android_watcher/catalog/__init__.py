"""Packaged source catalog loader."""

from __future__ import annotations

import tomllib
from importlib.resources import files

from android_watcher.models import Source


def load_catalog() -> list[Source]:
	"""Load and return all sources from the packaged catalog.toml."""
	data = files(__name__).joinpath("catalog.toml").read_bytes()
	raw = tomllib.loads(data.decode())
	return [
		Source(
			id=e["id"],
			name=e["name"],
			category=e["category"],
			detector=e["detector"],
			url=e["url"],
			enabled=e.get("enabled", True),
			path_prefix=e.get("path_prefix", ""),
			feed_url=e.get("feed_url", ""),
			content_selector=e.get("content_selector", ""),
			default_weight=e.get("default_weight", 0),
			exclude_prefixes=tuple(e.get("exclude_prefixes", ())),
			require_segment=e.get("require_segment", ""),
			reference_mode=e.get("reference_mode", "index_only"),
		)
		for e in raw.get("source", [])
	]
