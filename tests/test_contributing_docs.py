"""Guard tests: CONTRIBUTING.md and CODE_OF_CONDUCT.md must exist and cover key topics."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_files_exist():
	assert (ROOT / "CONTRIBUTING.md").is_file()
	assert (ROOT / "CODE_OF_CONDUCT.md").is_file()


def test_contributing_covers_extension_points():
	text = (ROOT / "CONTRIBUTING.md").read_text()
	assert "catalog.toml" in text  # add a source
	assert "register" in text.lower()  # register the name
	assert "fixture" in text.lower()  # add a test/fixture
	for kind in ("detector", "channel", "triager"):
		assert kind in text.lower()


def test_coc_has_contact():
	text = (ROOT / "CODE_OF_CONDUCT.md").read_text().lower()
	assert "contributor covenant" in text
