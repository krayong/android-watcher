"""Tests for notify/slack.py: SlackNotifier via bot token."""

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
)
from android_watcher.models import Change, Digest, DigestGroup, NotifyError
from android_watcher.notify.render import render_slack
from android_watcher.notify.slack import SlackNotifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOT_TOKEN = "xoxb-test-token-abc"
_CHANNEL = "#updates"


def _ok_resp(sent: dict | None = None) -> MagicMock:
	"""Return a mock response with raise_for_status() and .json() → {"ok": True}."""
	r = MagicMock()
	r.raise_for_status = MagicMock()
	r.json.return_value = {"ok": True}
	if sent is not None:
		sent["called"] = True
	return r


def _make_bot_config() -> Config:
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(enabled=True, bot_token=_BOT_TOKEN, channel=_CHANNEL),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def _make_group(key: str, url: str, change_id: int | None = None) -> DigestGroup:
	c = Change(
		source_id="src1",
		url=url,
		change_kind="new",
		title="New page",
		description="A brand new page appeared.",
		verdict="substantive",
		id=change_id,
	)
	return DigestGroup(
		key=key,
		title="New page",
		summary=None,
		category="guides",
		source_id="src1",
		change_kind="new",
		members=[c],
		score=5,
	)


def _make_digest() -> Digest:
	return Digest(groups=[_make_group("k1", "https://example.com/page", change_id=1)])


def _make_empty_digest() -> Digest:
	return Digest(groups=[])


# ---------------------------------------------------------------------------
# Bot-token path tests
# ---------------------------------------------------------------------------


class TestSlackNotifierBotToken:
	"""chat.postMessage path: correct URL, headers, payload, and ok-field check."""

	def _post_ok(self, channel_id="#updates", ts="1234.5678") -> MagicMock:
		r = MagicMock()
		r.raise_for_status = MagicMock()
		r.json.return_value = {"ok": True, "channel": channel_id, "ts": ts}
		return r

	def _get_ok(
		self, upload_url: str = "https://upload.slack.com/x", file_id: str = "F123"
	) -> MagicMock:
		r = MagicMock()
		r.raise_for_status = MagicMock()
		r.json.return_value = {"ok": True, "upload_url": upload_url, "file_id": file_id}
		return r

	def _complete_ok(self) -> MagicMock:
		r = MagicMock()
		r.raise_for_status = MagicMock()
		r.json.return_value = {"ok": True}
		return r

	def test_posts_to_chat_post_message(self) -> None:
		config = _make_bot_config()
		digest = _make_digest()
		# Bot-token mode attaches the HTML page in a thread, so the rendered
		# message advertises it (thread_page=True).
		expected_blocks = render_slack(digest, thread_page=True)

		with (
			patch("httpx.post", return_value=self._post_ok()) as mock_post,
			patch("httpx.get", return_value=self._get_ok()),
		):
			SlackNotifier().send(digest, config)

		first_call = mock_post.call_args_list[0]
		call_args, call_kwargs = first_call
		assert call_args[0] == "https://slack.com/api/chat.postMessage"
		assert call_kwargs["headers"] == {"Authorization": f"Bearer {_BOT_TOKEN}"}
		sent_json = call_kwargs["json"]
		assert sent_json["channel"] == _CHANNEL
		assert sent_json["blocks"] == expected_blocks["blocks"]
		assert "timeout" in call_kwargs

	def test_thread_upload_failure_falls_back_to_standalone_channel_message(self) -> None:
		"""If the threaded page upload fails, the page is retried as a standalone
		channel message (no thread_ts) so it still lands."""
		config = _make_bot_config()
		digest = _make_digest()

		def post_side_effect(url, *args, **kwargs):
			r = MagicMock()
			r.raise_for_status = MagicMock()
			if url == "https://slack.com/api/chat.postMessage":
				r.json.return_value = {"ok": True, "channel": "#updates", "ts": "1.2"}
			elif url == "https://slack.com/api/files.completeUploadExternal":
				threaded = "thread_ts" in kwargs.get("json", {})
				r.json.return_value = (
					{"ok": False, "error": "thread_not_found"} if threaded else {"ok": True}
				)
			else:  # PUT to the upload URL
				r.json.return_value = {}
			return r

		with (
			patch("httpx.post", side_effect=post_side_effect) as mock_post,
			patch("httpx.get", return_value=self._get_ok()),
		):
			SlackNotifier().send(digest, config)

		complete = [
			c
			for c in mock_post.call_args_list
			if c[0][0] == "https://slack.com/api/files.completeUploadExternal"
		]
		assert len(complete) == 2  # threaded attempt, then standalone fallback
		assert "thread_ts" in complete[0].kwargs["json"]
		assert "thread_ts" not in complete[1].kwargs["json"]

	def test_posts_to_each_channel_or_dm_in_list(self) -> None:
		"""A comma-separated channel list (channels and user DMs) sends to each target."""
		config = _make_bot_config()
		config.slack.channel = "#updates, U0123DM ,#android"
		digest = _make_digest()

		with (
			patch("httpx.post", return_value=self._post_ok()) as mock_post,
			patch("httpx.get", return_value=self._get_ok()),
		):
			SlackNotifier().send(digest, config)

		# First call per target is chat.postMessage; filter those.
		post_msg_calls = [
			c
			for c in mock_post.call_args_list
			if c[0][0] == "https://slack.com/api/chat.postMessage"
		]
		sent = [c.kwargs["json"]["channel"] for c in post_msg_calls]
		assert sent == ["#updates", "U0123DM", "#android"]

	def test_ok_false_raises_notifyerror(self) -> None:
		"""HTTP 200 with ok=false must raise NotifyError."""
		config = _make_bot_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.raise_for_status = MagicMock()
		mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}

		with patch("httpx.post", return_value=mock_resp):
			with pytest.raises(NotifyError, match="channel_not_found"):
				SlackNotifier().send(digest, config)

	def test_ok_false_error_does_not_contain_token(self) -> None:
		"""The raised NotifyError must not leak the bot_token."""
		config = _make_bot_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.raise_for_status = MagicMock()
		mock_resp.json.return_value = {"ok": False, "error": "not_authed"}

		with patch("httpx.post", return_value=mock_resp):
			with pytest.raises(NotifyError) as exc_info:
				SlackNotifier().send(digest, config)
		assert _BOT_TOKEN not in str(exc_info.value)

	def test_http_status_error_raises_notifyerror_no_token_leak(self) -> None:
		config = _make_bot_config()
		digest = _make_digest()

		mock_resp = MagicMock()
		mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
			"403 Forbidden",
			request=MagicMock(),
			response=mock_resp,
		)

		with patch("httpx.post", return_value=mock_resp):
			with pytest.raises(NotifyError) as exc_info:
				SlackNotifier().send(digest, config)
		assert _BOT_TOKEN not in str(exc_info.value)

	def test_network_error_raises_notifyerror(self) -> None:
		config = _make_bot_config()
		digest = _make_digest()

		with patch(
			"httpx.post",
			side_effect=httpx.ConnectError("connection refused"),
		):
			with pytest.raises(NotifyError):
				SlackNotifier().send(digest, config)

	def test_no_creds_raises_notifyerror(self) -> None:
		"""slack enabled with no bot_token+channel → NotifyError."""
		config = Config(
			schedule=ScheduleConfig(),
			ai=AIConfig(),
			digest=DigestConfig(),
			sort={},
			email=EmailChannel(),
			slack=SlackChannel(enabled=True),
			telegram=TelegramChannel(),
			custom_sources=[],
			enabled_source_ids=set(),
		)
		digest = _make_digest()

		with pytest.raises(NotifyError, match="bot_token"):
			SlackNotifier().send(digest, config)

	def test_bot_token_returns_all_group_member_ids(self) -> None:
		"""Bot-token path returns all groups' member ids (full HTML in thread)."""
		config = _make_bot_config()
		groups = [
			DigestGroup(
				key=f"k{i}",
				title=f"t{i}",
				summary=None,
				category="guides",
				source_id="s",
				change_kind="updated",
				members=[Change(source_id="s", url=f"u{i}", change_kind="updated", id=i)],
				score=100 - i,
			)
			for i in range(5)
		]
		digest = Digest(groups=groups, max_items=3)

		with (
			patch("httpx.post", return_value=self._post_ok()),
			patch("httpx.get", return_value=self._get_ok()),
		):
			ids = SlackNotifier().send(digest, config)

		# All 5 group members returned, not just the 3 in message_groups
		assert ids == {0, 1, 2, 3, 4}

	def test_bot_token_upload_calls_use_thread_ts(self) -> None:
		"""Upload flow: getUploadURLExternal -> PUT to upload_url -> completeUploadExternal."""
		config = _make_bot_config()
		digest = _make_digest()

		upload_url = "https://upload.slack.com/test-upload"
		file_id = "FTEST99"
		channel_id = "#updates"
		ts = "9999.1111"

		post_responses = [
			self._post_ok(channel_id=channel_id, ts=ts),  # chat.postMessage
			MagicMock(raise_for_status=MagicMock()),  # PUT to upload_url
			self._complete_ok(),  # completeUploadExternal
		]
		get_resp = self._get_ok(upload_url=upload_url, file_id=file_id)

		with (
			patch("httpx.post", side_effect=post_responses) as mock_post,
			patch("httpx.get", return_value=get_resp) as mock_get,
		):
			SlackNotifier().send(digest, config)

		# getUploadURLExternal called with filename + length
		get_call = mock_get.call_args
		assert "files.getUploadURLExternal" in get_call[0][0]
		assert get_call[1]["params"]["filename"] == "digest.html"

		# PUT to upload_url called
		put_call = mock_post.call_args_list[1]
		assert put_call[0][0] == upload_url

		# completeUploadExternal called with correct thread_ts, channel_id, file_id
		complete_call = mock_post.call_args_list[2]
		assert "files.completeUploadExternal" in complete_call[0][0]
		complete_json = complete_call[1]["json"]
		assert complete_json["thread_ts"] == ts
		assert complete_json["channel_id"] == channel_id
		assert complete_json["files"][0]["id"] == file_id

	def test_upload_failure_is_nonfatal(self) -> None:
		"""Upload failure after chat.postMessage: does not raise, delivery still returns ids."""
		config = _make_bot_config()
		digest = _make_digest()

		# chat.postMessage succeeds; getUploadURLExternal fails
		with (
			patch("httpx.post", return_value=self._post_ok()),
			patch("httpx.get", side_effect=httpx.ConnectError("upload down")),
		):
			ids = SlackNotifier().send(digest, config)

		# Single-group digest: that group is in message_groups, so id 1 is returned
		# even when upload fails (message reached Slack; only carried groups are at risk).
		assert ids == {1}

	def test_upload_success_returns_all_group_ids_including_carried(self) -> None:
		"""Bot-token + successful upload: all group member ids returned (carried in thread)."""
		config = _make_bot_config()
		groups = [
			DigestGroup(
				key=f"k{i}",
				title=f"t{i}",
				summary=None,
				category="guides",
				source_id="s",
				change_kind="updated",
				members=[Change(source_id="s", url=f"u{i}", change_kind="updated", id=i)],
				score=100 - i,
			)
			for i in range(5)
		]
		# max_items=3: groups 3 and 4 are carried (not in the capped message)
		digest = Digest(groups=groups, max_items=3)

		with (
			patch(
				"httpx.post",
				side_effect=[
					self._post_ok(),  # chat.postMessage
					MagicMock(raise_for_status=MagicMock()),  # PUT to upload_url
					self._complete_ok(),  # completeUploadExternal
				],
			),
			patch("httpx.get", return_value=self._get_ok()),
		):
			ids = SlackNotifier().send(digest, config)

		# Upload succeeded: all 5 members (including carried groups 3+4) are delivered.
		assert ids == {0, 1, 2, 3, 4}

	def test_upload_failure_with_carried_groups_returns_only_message_group_ids(self) -> None:
		"""Bot-token + upload failure + carried groups: only message_groups() ids returned.

		Carried groups never reached Slack (thread upload failed), so they must NOT
		get a delivery row — they stay in the backlog for the next run.
		"""
		config = _make_bot_config()
		groups = [
			DigestGroup(
				key=f"k{i}",
				title=f"t{i}",
				summary=None,
				category="guides",
				source_id="s",
				change_kind="updated",
				members=[Change(source_id="s", url=f"u{i}", change_kind="updated", id=i)],
				score=100 - i,
			)
			for i in range(5)
		]
		# max_items=3: groups 3 and 4 are carried (ids 3 and 4)
		digest = Digest(groups=groups, max_items=3)

		# chat.postMessage succeeds; getUploadURLExternal fails → upload returns False
		with (
			patch("httpx.post", return_value=self._post_ok()),
			patch("httpx.get", side_effect=httpx.ConnectError("upload down")),
		):
			ids = SlackNotifier().send(digest, config)

		# Only the 3 message_group members (ids 0, 1, 2) are returned.
		# Carried members (ids 3, 4) are absent: they reached Slack nowhere.
		assert ids == {0, 1, 2}
		assert 3 not in ids
		assert 4 not in ids
