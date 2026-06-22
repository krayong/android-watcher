"""Slack notifier: bot token (chat.postMessage + threaded file upload)."""

from __future__ import annotations

import logging

import httpx

from android_watcher.config import Config
from android_watcher.models import Digest, DigestGroup
from android_watcher.notify.base import NOTIFIERS, NotifyError
from android_watcher.notify.html import render_html
from android_watcher.notify.render import render_slack

logger = logging.getLogger(__name__)


def _member_ids(groups: list[DigestGroup]) -> set[int]:
	return {m.id for g in groups for m in g.members if m.id is not None}


def _targets(raw: str) -> list[str]:
	return [t.strip() for t in raw.split(",") if t.strip()]


@NOTIFIERS.register("slack")
class SlackNotifier:
	name = "slack"

	def send(self, digest: Digest, config: Config) -> set[int]:
		sl = config.slack
		if not (sl.bot_token and sl.channel):
			raise NotifyError("slack enabled but bot_token + channel not configured")
		payload = render_slack(digest, thread_page=True)
		delivered: set[int] = set()
		for target in _targets(sl.channel):
			logger.info("slack: posting message to %s then uploading digest page", target)
			channel_id, ts = self._send_bot(sl.bot_token, target, payload)
			logger.info(
				"slack: message posted (channel_id=%s ts=%s); uploading HTML page",
				channel_id,
				ts,
			)
			uploaded = self._deliver_page(sl.bot_token, channel_id, ts, digest)
			if uploaded:
				# Full HTML reached the channel: all groups (incl. carried) are visible.
				delivered |= _member_ids(digest.groups)
			else:
				# Upload failed entirely; only the capped message reached Slack.
				delivered |= _member_ids(digest.message_groups())
		return delivered

	def _send_bot(self, bot_token: str, channel: str, payload: dict) -> tuple[str, str]:
		try:
			resp = httpx.post(
				"https://slack.com/api/chat.postMessage",
				headers={"Authorization": f"Bearer {bot_token}"},
				json={"channel": channel, **payload},
				timeout=30.0,
			)
			resp.raise_for_status()
		except (httpx.HTTPStatusError, httpx.RequestError) as exc:
			raise NotifyError(f"slack send failed: {type(exc).__name__}") from exc
		body = resp.json()
		if not body.get("ok"):
			raise NotifyError(f"slack chat.postMessage failed: {body.get('error')}")
		return body["channel"], body["ts"]

	def _deliver_page(self, bot_token: str, channel_id: str, ts: str, digest: Digest) -> bool:
		"""Deliver the full-digest HTML page. Try a threaded reply first; if that
		fails, fall back to a standalone message in the channel so the page still
		lands. Non-fatal: the main message is already delivered, so a total failure
		logs and returns False (carried changes then retry next run)."""
		data = render_html(digest).encode("utf-8")
		if self._upload_page(bot_token, channel_id, data, thread_ts=ts):
			return True
		logger.warning(
			"slack: threaded page upload failed; retrying as a standalone channel message"
		)
		if self._upload_page(bot_token, channel_id, data, thread_ts=None):
			return True
		logger.error("slack: digest page upload failed (thread and standalone); page not delivered")
		return False

	def _upload_page(
		self, bot_token: str, channel_id: str, data: bytes, *, thread_ts: str | None
	) -> bool:
		"""One external-upload sequence (getUploadURLExternal -> PUT -> complete).
		Posts into the thread when thread_ts is set, else as a new channel message."""
		where = f"thread {thread_ts}" if thread_ts else "channel"
		try:
			headers = {"Authorization": f"Bearer {bot_token}"}
			r1 = httpx.get(
				"https://slack.com/api/files.getUploadURLExternal",
				headers=headers,
				params={"filename": "digest.html", "length": len(data)},
				timeout=30.0,
			)
			r1.raise_for_status()
			b1 = r1.json()
			if not b1.get("ok"):
				raise NotifyError(f"getUploadURLExternal: {b1.get('error')}")
			httpx.post(b1["upload_url"], content=data, timeout=30.0).raise_for_status()
			body: dict = {
				"files": [{"id": b1["file_id"], "title": "Android Watcher Digest"}],
				"channel_id": channel_id,
			}
			if thread_ts:
				body["thread_ts"] = thread_ts
			r2 = httpx.post(
				"https://slack.com/api/files.completeUploadExternal",
				headers=headers,
				json=body,
				timeout=30.0,
			)
			r2.raise_for_status()
			b2 = r2.json()
			if not b2.get("ok"):
				raise NotifyError(f"completeUploadExternal: {b2.get('error')}")
			logger.info("slack: digest page uploaded to %s", where)
			return True
		except (httpx.HTTPStatusError, httpx.RequestError, NotifyError, KeyError) as exc:
			logger.warning("slack: digest page upload to %s failed: %s", where, exc)
			return False
