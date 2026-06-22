"""Telegram notifier — delivers digests via the Telegram Bot API."""

from __future__ import annotations

import httpx

from android_watcher.config import Config
from android_watcher.models import Digest, NotifyError
from android_watcher.notify.base import NOTIFIERS
from android_watcher.notify.render import render_telegram


def _chat_ids(raw: str) -> list[str]:
	"""Split a comma-separated list of chat ids into individual targets."""
	return [c.strip() for c in raw.split(",") if c.strip()]


@NOTIFIERS.register("telegram")
class TelegramNotifier:
	name = "telegram"

	def send(self, digest: Digest, config: Config) -> set[int]:
		token = config.telegram.bot_token
		text = render_telegram(digest)
		url = f"https://api.telegram.org/bot{token}/sendMessage"
		for chat_id in _chat_ids(config.telegram.chat_id):
			try:
				resp = httpx.post(
					url,
					json={
						"chat_id": chat_id,
						"text": text,
						"parse_mode": "HTML",
						"disable_web_page_preview": True,
					},
					timeout=30.0,
				)
				resp.raise_for_status()
			except httpx.HTTPStatusError as exc:
				raise NotifyError(
					f"telegram send failed: {exc.response.status_code} {exc.response.text[:200]}"
				) from exc
			except httpx.RequestError as exc:
				detail = exc.args[0] if exc.args else "request error"
				raise NotifyError(f"telegram send failed: {type(exc).__name__}: {detail}") from exc
		return {m.id for g in digest.message_groups() for m in g.members if m.id is not None}
