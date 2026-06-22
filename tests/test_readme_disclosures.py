"""Guard tests: README must contain required install paths and security disclosures."""

from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_exists():
	assert README.is_file()


def test_install_paths_documented():
	text = README.read_text()
	assert "uv tool install android-watcher" in text
	assert "pipx install android-watcher" in text
	assert "brew install" in text


def test_security_egress_disclosures():
	text = README.read_text().lower()
	raw = README.read_text()
	assert "claude" in text and "page content" in text  # AI egress
	assert "${" in raw  # env-var ref pattern
	assert ".gitignore" in text
	# "don't commit config in a git tree" warning
	assert "config" in text and "git" in text
	assert "commit" in text or "tracked" in text
	assert "off" in text  # no-egress mode
