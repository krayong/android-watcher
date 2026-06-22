from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def _load():
	try:
		import yaml  # type: ignore
	except ImportError:
		import pytest

		pytest.skip("pyyaml not installed; workflow YAML parse skipped")
	return yaml.safe_load(WF.read_text())


def test_workflow_exists():
	assert WF.is_file()


def test_manual_bump_dispatch():
	doc = _load()
	on = doc[True] if True in doc else doc["on"]  # PyYAML parses 'on:' as True
	bump = on["workflow_dispatch"]["inputs"]["bump"]
	assert bump["type"] == "choice"
	assert set(bump["options"]) == {"patch", "minor", "major"}


def test_publishes_with_token_in_prod_environment():
	doc = _load()
	job = doc["jobs"]["release"]
	assert job["environment"] == "prod"
	text = WF.read_text()
	assert "uv build" in text
	assert "pypa/gh-action-pypi-publish" in text
	assert "secrets.PYPI_TOKEN" in text  # token publishing
	assert "id-token: write" not in text  # OIDC trusted publishing removed


def test_commits_bump_as_bot_and_tags():
	text = WF.read_text()
	assert "chore: bump version to v" in text  # required commit message
	assert "github-actions[bot]" in text  # commit author
	assert "git tag" in text  # tag created
	assert "contents: write" in text  # permission to push commit + tag


def test_updates_homebrew_formula():
	text = WF.read_text()
	assert "brew update-python-resources" in text
	assert "Formula/android-watcher.rb" in text
