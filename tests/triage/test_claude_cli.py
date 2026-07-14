"""Tests for claude_cli triager subprocess call, JSON parsing, and failure paths."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import android_watcher.triage.claude_cli  # noqa: F401 – registers "claude_cli"
from android_watcher.config import AIConfig
from android_watcher.models import Change
from android_watcher.triage.base import TRIAGERS
from android_watcher.triage.claude_cli import (
	SUBPROCESS_TIMEOUT,
	build_argv,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "claude_cli_envelope.json"


def _load_envelope() -> dict:
	return json.loads(_FIXTURE.read_text())


def _make_change(n: int = 1, kind: str = "updated") -> Change:
	return Change(
		source_id=f"src-{n}",
		url=f"https://developer.android.com/page-{n}",
		change_kind=kind,
		title=f"Title {n}",
		raw_diff=f"Content diff for page {n}.",
	)


def _make_triager():
	return TRIAGERS.get("claude_cli")()


# ---------------------------------------------------------------------------
# Happy path — real envelope shape from fixture
# ---------------------------------------------------------------------------


def test_happy_path_uses_real_envelope():
	"""Triage succeeds using the actual envelope shape captured from claude CLI."""
	changes = [_make_change(1), _make_change(2)]
	inner = {
		"changes": [
			{"index": 1, "verdict": "substantive", "description": "New API added"},
			{"index": 2, "verdict": "cosmetic", "description": None},
		],
		"tldr": "one new API",
	}
	envelope = _load_envelope()
	envelope["result"] = json.dumps(inner)
	stdout = json.dumps(envelope)

	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=build_argv(config), returncode=0, stdout=stdout, stderr=""
		)
		result = _make_triager().triage(changes, config)

	assert result.unavailable is None
	assert result.tldr == "one new API"
	assert changes[0].verdict == "substantive"
	assert changes[0].description == "New API added"
	assert changes[1].verdict == "cosmetic"
	assert changes[1].description is None


# ---------------------------------------------------------------------------
# Markdown code-fence strip
# ---------------------------------------------------------------------------


def test_markdown_fenced_result_parses_identically():
	"""A ```json … ``` fence around the inner JSON is stripped before parsing."""
	changes = [_make_change(1)]
	inner = {
		"changes": [{"index": 1, "verdict": "substantive", "description": "Something changed"}],
		"tldr": None,
	}
	envelope = _load_envelope()
	envelope["result"] = f"```json\n{json.dumps(inner)}\n```"
	stdout = json.dumps(envelope)

	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
		result = _make_triager().triage(changes, config)

	assert result.unavailable is None
	assert changes[0].verdict == "substantive"
	assert changes[0].description == "Something changed"


# ---------------------------------------------------------------------------
# Prose wrapped around the JSON — claude -p is an agent and sometimes
# editorializes despite "respond with ONLY JSON". Extract the object anyway.
# ---------------------------------------------------------------------------


def test_prose_preamble_before_fenced_json_parses():
	"""A prose preamble before the ```json block must not defeat parsing."""
	changes = [_make_change(1)]
	inner = {
		"changes": [{"index": 1, "verdict": "substantive", "description": "New API added"}],
		"tldr": None,
	}
	envelope = _load_envelope()
	envelope["result"] = (
		"Before answering, one thing worth flagging: the snippets look truncated.\n\n"
		f"```json\n{json.dumps(inner)}\n```"
	)

	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout=json.dumps(envelope), stderr=""
		)
		result = _make_triager().triage(changes, config)

	assert result.unavailable is None
	assert changes[0].verdict == "substantive"
	assert changes[0].description == "New API added"


def test_trailing_prose_after_fenced_json_parses():
	"""Commentary appended after the ```json block must not defeat parsing."""
	changes = [_make_change(1)]
	inner = {
		"changes": [{"index": 1, "verdict": "cosmetic", "description": None}],
		"tldr": None,
	}
	envelope = _load_envelope()
	envelope["result"] = (
		f"```json\n{json.dumps(inner)}\n```\n\n"
		"One flag outside that JSON: every snippet is identical nav boilerplate."
	)

	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout=json.dumps(envelope), stderr=""
		)
		result = _make_triager().triage(changes, config)

	assert result.unavailable is None
	assert changes[0].verdict == "cosmetic"


# ---------------------------------------------------------------------------
# Cosmetic-with-description scrub
# ---------------------------------------------------------------------------


def test_cosmetic_verdict_forces_description_to_none():
	"""Even if the model sends a description for a cosmetic change, we force it None."""
	changes = [_make_change(1)]
	inner = {
		"changes": [{"index": 1, "verdict": "cosmetic", "description": "Should be scrubbed"}],
		"tldr": None,
	}
	envelope = _load_envelope()
	envelope["result"] = json.dumps(inner)

	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout=json.dumps(envelope), stderr=""
		)
		result = _make_triager().triage(changes, config)

	assert changes[0].verdict == "cosmetic"
	assert changes[0].description is None
	assert result.unavailable is None


# ---------------------------------------------------------------------------
# Omitted change — fail open
# ---------------------------------------------------------------------------


def test_omitted_change_defaults_to_substantive():
	"""A change the model skips defaults to substantive so it is never silently dropped."""
	changes = [_make_change(1), _make_change(2)]
	inner = {
		"changes": [{"index": 1, "verdict": "cosmetic", "description": None}],
		# index 2 omitted
		"tldr": None,
	}
	envelope = _load_envelope()
	envelope["result"] = json.dumps(inner)

	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout=json.dumps(envelope), stderr=""
		)
		result = _make_triager().triage(changes, config)

	assert changes[1].verdict == "substantive"
	assert changes[1].description is None
	assert result.unavailable is None


# ---------------------------------------------------------------------------
# Failure paths — all must return unavailable, never raise
# ---------------------------------------------------------------------------


def test_claude_not_found_returns_unavailable():
	config = AIConfig()
	with patch("subprocess.run", side_effect=FileNotFoundError):
		result = _make_triager().triage([_make_change()], config)
	assert result.unavailable == "claude binary not found on PATH"
	assert not isinstance(result, Exception)


def test_timeout_returns_unavailable():
	config = AIConfig()
	with patch(
		"subprocess.run",
		side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=SUBPROCESS_TIMEOUT),
	):
		result = _make_triager().triage([_make_change()], config)
	assert result.unavailable is not None
	assert "timed out" in result.unavailable


def test_nonzero_exit_returns_unavailable():
	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(args=[], returncode=2, stdout="", stderr="boom")
		result = _make_triager().triage([_make_change()], config)
	assert result.unavailable is not None
	assert "exited 2" in result.unavailable
	assert "boom" in result.unavailable


def test_bad_json_returns_unavailable():
	config = AIConfig()
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout="not json", stderr=""
		)
		result = _make_triager().triage([_make_change()], config)
	assert result.unavailable is not None
	assert "could not parse" in result.unavailable


def test_malformed_changes_list_returns_unavailable():
	"""If 'changes' is not a list, treat as parse failure."""
	config = AIConfig()
	bad_inner = {"changes": "oops", "tldr": None}
	envelope = _load_envelope()
	envelope["result"] = json.dumps(bad_inner)
	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout=json.dumps(envelope), stderr=""
		)
		result = _make_triager().triage([_make_change()], config)
	assert result.unavailable is not None
	assert "could not parse" in result.unavailable


# ---------------------------------------------------------------------------
# Group fields — prompt requests them and parse loop sets them
# ---------------------------------------------------------------------------


def test_prompt_requests_group_fields():
	from android_watcher.triage.claude_cli import build_prompt

	prompt = build_prompt(
		[Change(source_id="s", url="u", change_kind="updated", title="t", raw_diff="d")]
	)
	assert "group_key" in prompt
	assert "group_summary" in prompt


def test_parse_sets_group_fields_on_change():
	from android_watcher.triage.claude_cli import ClaudeCliTriager

	changes = [
		Change(source_id="s", url="u1", change_kind="updated", title="a", raw_diff="d", id=1),
		Change(source_id="s", url="u2", change_kind="updated", title="b", raw_diff="d", id=2),
	]
	inner = {
		"changes": [
			{
				"index": 1,
				"verdict": "substantive",
				"description": "x",
				"group_key": "gki",
				"group_summary": "GKI builds across branches",
			},
			{
				"index": 2,
				"verdict": "substantive",
				"description": "y",
				"group_key": "gki",
				"group_summary": "GKI builds across branches",
			},
		],
		"tldr": None,
	}
	envelope = json.dumps({"result": json.dumps(inner)})

	class _Proc:
		returncode = 0
		stdout = envelope
		stderr = ""

	orig = subprocess.run
	subprocess.run = lambda *a, **k: _Proc()
	try:
		ClaudeCliTriager().triage(changes, AIConfig())
	finally:
		subprocess.run = orig

	assert changes[0].group_key == "gki"
	assert changes[1].group_key == "gki"
	assert changes[0].group_summary == "GKI builds across branches"


# ---------------------------------------------------------------------------
# argv + stdin assertion
# ---------------------------------------------------------------------------


def test_subprocess_called_with_correct_argv_and_stdin():
	"""Triager must pass build_argv result and build_prompt result to subprocess.run."""
	changes = [_make_change(1)]
	config = AIConfig(model="claude-opus-4-8")

	inner = {
		"changes": [{"index": 1, "verdict": "substantive", "description": "ok"}],
		"tldr": None,
	}
	envelope = _load_envelope()
	envelope["result"] = json.dumps(inner)

	with patch("subprocess.run") as mock_run:
		mock_run.return_value = CompletedProcess(
			args=[], returncode=0, stdout=json.dumps(envelope), stderr=""
		)
		_make_triager().triage(changes, config)

	call_kwargs = mock_run.call_args
	called_argv = call_kwargs[0][0]
	assert called_argv == build_argv(config)
	assert call_kwargs[1]["capture_output"] is True
	assert call_kwargs[1]["text"] is True
	assert call_kwargs[1]["timeout"] == SUBPROCESS_TIMEOUT
	# stdin must be a non-empty string (the prompt)
	assert isinstance(call_kwargs[1]["input"], str)
	assert len(call_kwargs[1]["input"]) > 0
