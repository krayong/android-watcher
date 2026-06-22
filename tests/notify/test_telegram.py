"""Tests for notify/telegram.py and render_telegram."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
	load_config,
)
from android_watcher.models import Change, Digest, DigestGroup, NotifyError
from android_watcher.notify.render import render_telegram
from android_watcher.notify.telegram import TelegramNotifier
from android_watcher.tui.configio import validate_config

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_CHAT_ID = "-100123456789"


def _make_config(*, enabled: bool = True, token: str = _TOKEN, chat_id: str = _CHAT_ID) -> Config:
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(enabled=enabled, bot_token=token, chat_id=chat_id),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def _make_group(
	*,
	title: str = "New page",
	description: str = "Details here.",
	url: str = "https://developer.android.com/page",
	change_id: int | None = 7,
) -> DigestGroup:
	c = Change(
		source_id="src1",
		url=url,
		change_kind="new",
		title=title,
		description=description,
		verdict="substantive",
		id=change_id,
	)
	return DigestGroup(
		key="k1",
		title=title,
		summary=None,
		category="guides",
		source_id="src1",
		change_kind="new",
		members=[c],
		score=5,
	)


def _make_digest(*, title: str = "New page", description: str = "Details here.") -> Digest:
	return Digest(groups=[_make_group(title=title, description=description)])


def _make_empty_digest() -> Digest:
	return Digest(groups=[])


def test_sends_to_each_chat_id_in_comma_separated_list() -> None:
	"""A comma-separated chat_id list delivers one message per chat."""
	cfg = _make_config(chat_id="111, 222 ,333")
	mock_resp = MagicMock()
	mock_resp.raise_for_status = MagicMock()
	with patch("httpx.post", return_value=mock_resp) as mock_post:
		TelegramNotifier().send(_make_digest(), cfg)
	sent = [c.kwargs["json"]["chat_id"] for c in mock_post.call_args_list]
	assert sent == ["111", "222", "333"]


# ---------------------------------------------------------------------------
# TelegramNotifier.send — success
# ---------------------------------------------------------------------------


class TestTelegramNotifierSuccess:
	def test_posts_to_correct_url_with_payload(self) -> None:
		config = _make_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.raise_for_status = MagicMock()

		with patch("httpx.post", return_value=mock_resp) as mock_post:
			TelegramNotifier().send(digest, config)

		mock_post.assert_called_once()
		call_args, call_kwargs = mock_post.call_args
		expected_url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
		assert call_args[0] == expected_url
		payload = call_kwargs.get("json", {})
		assert payload["chat_id"] == _CHAT_ID
		assert payload["parse_mode"] == "HTML"
		assert payload["disable_web_page_preview"] is True
		assert "text" in payload
		assert "timeout" in call_kwargs
		mock_resp.raise_for_status.assert_called_once()

	def test_text_matches_render_telegram(self) -> None:
		config = _make_config()
		digest = _make_digest()
		expected_text = render_telegram(digest)

		mock_resp = MagicMock()
		mock_resp.raise_for_status = MagicMock()

		with patch("httpx.post", return_value=mock_resp) as mock_post:
			TelegramNotifier().send(digest, config)

		payload = mock_post.call_args[1].get("json", {})
		assert payload["text"] == expected_text

	def test_no_exception_on_200(self) -> None:
		config = _make_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.raise_for_status = MagicMock()

		with patch("httpx.post", return_value=mock_resp):
			TelegramNotifier().send(digest, config)  # must not raise

	def test_returns_message_group_ids(self) -> None:
		"""send() returns member ids from message_groups() only (capped message)."""
		config = _make_config()
		groups = [
			DigestGroup(
				key=f"k{i}",
				title=f"t{i}",
				summary=None,
				category="guides",
				source_id="s",
				change_kind="new",
				members=[Change(source_id="s", url=f"u{i}", change_kind="new", id=i)],
				score=100 - i,
			)
			for i in range(12)
		]
		digest = Digest(groups=groups, max_items=10)

		mock_resp = MagicMock()
		mock_resp.raise_for_status = MagicMock()

		with patch("httpx.post", return_value=mock_resp):
			ids = TelegramNotifier().send(digest, config)

		# Only message_groups() (first 10) ids returned; carried (10, 11) not included
		assert ids == set(range(10))


# ---------------------------------------------------------------------------
# TelegramNotifier.send — error paths
# ---------------------------------------------------------------------------


class TestTelegramNotifierNon2xx:
	def test_http_status_error_raises_notifyerror(self) -> None:
		config = _make_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.status_code = 400
		mock_resp.text = "Bad Request"
		mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"400 Bad Request",
			request=MagicMock(),
			response=mock_resp,
		)

		with patch("httpx.post", return_value=mock_resp):
			with pytest.raises(NotifyError) as exc_info:
				TelegramNotifier().send(digest, config)

		assert "400" in str(exc_info.value)

	def test_notifyerror_does_not_contain_bot_token(self) -> None:
		"""The bot token must never appear in a NotifyError message (it is in the URL)."""
		config = _make_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.status_code = 403
		mock_resp.text = "Forbidden"
		mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"403 Forbidden",
			request=MagicMock(),
			response=mock_resp,
		)

		with patch("httpx.post", return_value=mock_resp):
			with pytest.raises(NotifyError) as exc_info:
				TelegramNotifier().send(digest, config)

		assert _TOKEN not in str(exc_info.value)


class TestTelegramNotifierNetworkError:
	def test_connect_error_raises_notifyerror(self) -> None:
		config = _make_config()
		digest = _make_digest()

		with patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
			with pytest.raises(NotifyError):
				TelegramNotifier().send(digest, config)

	def test_notifyerror_does_not_contain_bot_token_on_network_error(self) -> None:
		"""Token must not leak into NotifyError on network failure either."""
		config = _make_config()
		digest = _make_digest()

		with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
			with pytest.raises(NotifyError) as exc_info:
				TelegramNotifier().send(digest, config)

		assert _TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# render_telegram
# ---------------------------------------------------------------------------


class TestRenderTelegram:
	def test_renders_item_with_link_and_source(self) -> None:
		digest = _make_digest(title="New Guide", description="Guide description.")
		text = render_telegram(digest)
		assert "New Guide" in text
		assert "developer.android.com" in text
		assert "src1" in text
		# render_telegram shows summary (not description); description is absent here

	def test_renders_ai_unavailable_banner(self) -> None:
		digest = _make_digest()
		digest.ai_unavailable = "claude not found"
		text = render_telegram(digest)
		assert "claude not found" in text
		assert "<b>" in text

	def test_renders_tldr_is_absent(self) -> None:
		"""render_telegram does not render tldr; ai_unavailable is the banner."""
		digest = _make_digest()
		digest.tldr = "Short summary"
		text = render_telegram(digest)
		# tldr is not rendered in the Telegram format
		assert "Short summary" not in text

	def test_empty_digest_renders_nothing_notable(self) -> None:
		digest = _make_empty_digest()
		text = render_telegram(digest)
		assert "Nothing notable" in text

	def test_html_escapes_title(self) -> None:
		digest = _make_digest(title="<script>alert('xss')</script>")
		text = render_telegram(digest)
		assert "<script>" not in text
		assert "&lt;script&gt;" in text

	def test_html_escapes_summary(self) -> None:
		"""render_telegram shows group summary (when set); it must be html-escaped."""
		c = Change(
			source_id="src1",
			url="https://developer.android.com/page",
			change_kind="new",
			title="Title",
			verdict="substantive",
		)
		g = DigestGroup(
			key="k1",
			title="Title",
			summary="Use a > b & c < d",
			category="guides",
			source_id="src1",
			change_kind="new",
			members=[c],
			score=5,
		)
		digest = Digest(groups=[g])
		text = render_telegram(digest)
		assert "Use a &gt; b &amp; c &lt; d" in text

	def test_message_under_4096_chars_untruncated(self) -> None:
		digest = _make_digest()
		text = render_telegram(digest)
		assert len(text) <= 4096

	def test_truncation_when_exceeds_4096(self) -> None:
		"""When message_groups() would push over 4096 chars, excess are dropped."""
		# Use max_items large enough that all 100 groups are in message_groups(),
		# with enough text per group to exceed 4096 chars total.
		groups = [
			DigestGroup(
				key=f"k{i}",
				title="A" * 40,
				summary="S" * 60,
				category="guides",
				source_id="src1",
				change_kind="new",
				members=[
					Change(
						source_id="src1",
						url=f"https://developer.android.com/page-{i}",
						change_kind="new",
						title="A" * 40,
						verdict="substantive",
					)
				],
				score=i,
			)
			for i in range(100)
		]
		digest = Digest(groups=groups, max_items=100)
		text = render_telegram(digest)
		assert len(text) <= 4096
		assert "more)" in text  # truncation note appended


# ---------------------------------------------------------------------------
# Config: ${ENV} interpolation for bot_token
# ---------------------------------------------------------------------------


class TestTelegramConfigInterpolation:
	def test_bot_token_expands_with_expand_true(
		self, tmp_path, monkeypatch: pytest.MonkeyPatch
	) -> None:
		monkeypatch.setenv("TG_TOKEN", "real-token-value")
		cfg_file = tmp_path / "config.toml"
		cfg_file.write_text(
			'[channels.telegram]\nenabled = true\nbot_token = "${TG_TOKEN}"\nchat_id = "123"\n',
			encoding="utf-8",
		)
		loaded = load_config(str(cfg_file), expand=True)
		assert loaded.telegram.bot_token == "real-token-value"

	def test_bot_token_preserved_with_expand_false(
		self, tmp_path, monkeypatch: pytest.MonkeyPatch
	) -> None:
		monkeypatch.setenv("TG_TOKEN", "real-token-value")
		cfg_file = tmp_path / "config.toml"
		cfg_file.write_text(
			'[channels.telegram]\nenabled = true\nbot_token = "${TG_TOKEN}"\nchat_id = "123"\n',
			encoding="utf-8",
		)
		loaded = load_config(str(cfg_file), expand=False)
		assert loaded.telegram.bot_token == "${TG_TOKEN}"

	def test_bot_token_ref_unset_with_expand_false_does_not_raise(
		self, tmp_path, monkeypatch: pytest.MonkeyPatch
	) -> None:
		monkeypatch.delenv("TG_TOKEN", raising=False)
		cfg_file = tmp_path / "config.toml"
		cfg_file.write_text(
			'[channels.telegram]\nenabled = true\nbot_token = "${TG_TOKEN}"\nchat_id = "123"\n',
			encoding="utf-8",
		)
		loaded = load_config(str(cfg_file), expand=False)
		assert loaded.telegram.bot_token == "${TG_TOKEN}"


# ---------------------------------------------------------------------------
# validate_config: enabled telegram with missing fields
# ---------------------------------------------------------------------------


class TestValidateTelegram:
	def _base_config(self) -> Config:
		return Config(
			schedule=ScheduleConfig(),
			ai=AIConfig(),
			digest=DigestConfig(),
			sort={},
			email=EmailChannel(),
			slack=SlackChannel(),
			telegram=TelegramChannel(),
			custom_sources=[],
			enabled_source_ids=set(),
		)

	def test_enabled_with_missing_bot_token_is_error(self) -> None:
		cfg = self._base_config()
		cfg.telegram.enabled = True
		cfg.telegram.bot_token = ""
		cfg.telegram.chat_id = "123"
		errors = validate_config(cfg)
		assert any("bot_token" in e for e in errors)

	def test_enabled_with_missing_chat_id_is_error(self) -> None:
		cfg = self._base_config()
		cfg.telegram.enabled = True
		cfg.telegram.bot_token = "sometoken"
		cfg.telegram.chat_id = ""
		errors = validate_config(cfg)
		assert any("chat_id" in e for e in errors)

	def test_enabled_with_both_fields_is_valid(self) -> None:
		cfg = self._base_config()
		cfg.telegram.enabled = True
		cfg.telegram.bot_token = "sometoken"
		cfg.telegram.chat_id = "123"
		errors = validate_config(cfg)
		assert errors == []

	def test_disabled_with_empty_fields_is_valid(self) -> None:
		cfg = self._base_config()
		cfg.telegram.enabled = False
		# Another channel must be enabled for the config to be complete.
		cfg.slack.enabled = True
		cfg.slack.bot_token = "xoxb-test"
		cfg.slack.channel = "#updates"
		errors = validate_config(cfg)
		assert errors == []
