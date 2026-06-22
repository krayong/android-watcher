"""Guard test: docs/scheduling.md must exist and contain required content."""

from __future__ import annotations

from pathlib import Path

_DOC = Path(__file__).parent.parent / "docs" / "scheduling.md"

_REQUIRED = [
	"loginctl enable-linger",
	"launchctl",
	"Persistent=true",
	"DST",
	"CRON_TZ",
	"enabled_sources",
]


def test_scheduling_doc_exists():
	assert _DOC.exists(), f"docs/scheduling.md not found at {_DOC}"


def test_scheduling_doc_contains_required_strings():
	text = _DOC.read_text()
	missing = [s for s in _REQUIRED if s not in text]
	assert not missing, f"docs/scheduling.md missing required strings: {missing}"
