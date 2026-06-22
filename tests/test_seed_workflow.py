"""The seed crawl lives in its own workflow, never on the release path."""

from pathlib import Path

WF_DIR = Path(__file__).resolve().parents[1] / ".github/workflows"
SEED = WF_DIR / "seed.yml"
RELEASE = WF_DIR / "release.yml"


def _load(path):
	try:
		import yaml  # type: ignore
	except ImportError:
		import pytest

		pytest.skip("pyyaml not installed; workflow YAML parse skipped")
	return yaml.safe_load(path.read_text())


def test_seed_workflow_exists_and_builds_seed():
	assert SEED.is_file()
	text = SEED.read_text()
	assert "scripts/build_seed.py" in text
	assert "src/android_watcher/seed/seed.sql.gz" in text


def test_seed_workflow_is_manual_and_weekly():
	doc = _load(SEED)
	on = doc[True] if True in doc else doc["on"]  # PyYAML parses 'on:' as True
	assert "workflow_dispatch" in on
	crons = [s["cron"] for s in on["schedule"]]
	assert "30 23 * * 0" in crons  # Mondays 05:00 IST (Sun 23:30 UTC)


def test_seed_workflow_can_push():
	doc = _load(SEED)
	assert doc["permissions"]["contents"] == "write"


def test_release_does_not_run_the_crawl():
	# The hours-long live crawl must never sit on the release critical path.
	assert "build_seed" not in RELEASE.read_text()
