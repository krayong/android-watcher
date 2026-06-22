"""Tests for notify/email.py: EmailNotifier with TLS-enforced SMTP."""

from __future__ import annotations

import smtplib
import ssl
from unittest.mock import MagicMock, patch

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
from android_watcher.notify.email import EmailNotifier
from android_watcher.notify.render import render_email

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_email_channel(**kwargs) -> EmailChannel:
	defaults = dict(
		enabled=True,
		smtp_host="smtp.example.com",
		smtp_port=465,
		username="user@example.com",
		password="secret",
		sender="sender@example.com",
		recipient="to@example.com",
	)
	defaults.update(kwargs)
	return EmailChannel(**defaults)


def _make_config(email: EmailChannel) -> Config:
	return Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=email,
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)


def _make_group(change_id: int | None = 42) -> DigestGroup:
	c = Change(
		source_id="src1",
		url="https://example.com/page",
		change_kind="updated",
		title="Page title",
		description="Something changed here.",
		verdict="substantive",
		id=change_id,
	)
	return DigestGroup(
		key="k1",
		title="Page title",
		summary=None,
		category="guides",
		source_id="src1",
		change_kind="updated",
		members=[c],
		score=10,
	)


def _make_digest(change_id: int | None = 42) -> Digest:
	return Digest(groups=[_make_group(change_id)])


def _make_smtp_instance() -> MagicMock:
	smtp = MagicMock()
	smtp.__enter__ = MagicMock(return_value=smtp)
	smtp.__exit__ = MagicMock(return_value=False)
	return smtp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmailNotifier465:
	"""Port 465 → SMTP_SSL with verifying context; login + send_message called."""

	def test_uses_smtp_ssl(self) -> None:
		ec = _make_email_channel(smtp_port=465)
		config = _make_config(ec)
		digest = _make_digest()

		smtp_instance = _make_smtp_instance()

		with (
			patch("smtplib.SMTP_SSL", return_value=smtp_instance) as mock_ssl,
			patch("smtplib.SMTP") as mock_plain,
		):
			EmailNotifier().send(digest, config)

			# SMTP_SSL must be called with host, port, and a verifying context
			assert mock_ssl.call_count == 1
			_args, _kwargs = mock_ssl.call_args
			assert _args[0] == ec.smtp_host
			assert _args[1] == ec.smtp_port
			ctx = _kwargs.get("context") or (_args[2] if len(_args) > 2 else None)
			assert isinstance(ctx, ssl.SSLContext)
			assert ctx.verify_mode == ssl.CERT_REQUIRED

			# plaintext SMTP must never be opened
			mock_plain.assert_not_called()

			# login and send_message called
			smtp_instance.login.assert_called_once_with(ec.username, ec.password)
			smtp_instance.send_message.assert_called_once()

	def test_message_has_html_and_plaintext_parts(self) -> None:
		ec = _make_email_channel(smtp_port=465)
		config = _make_config(ec)
		digest = _make_digest()

		html_expected, plain_expected = render_email(digest)

		smtp_instance = _make_smtp_instance()

		with patch("smtplib.SMTP_SSL", return_value=smtp_instance):
			EmailNotifier().send(digest, config)

		sent_msg = smtp_instance.send_message.call_args[0][0]
		content_types = [part.get_content_type() for part in sent_msg.walk()]
		assert "text/plain" in content_types
		assert "text/html" in content_types

		# Plaintext payload must contain the rendered plaintext
		plain_part = next(p for p in sent_msg.walk() if p.get_content_type() == "text/plain")
		assert plain_expected in plain_part.get_payload(decode=True).decode()

		# HTML payload must contain the rendered HTML
		html_part = next(p for p in sent_msg.walk() if p.get_content_type() == "text/html")
		assert html_expected in html_part.get_payload(decode=True).decode()

	def test_returns_all_group_member_ids(self) -> None:
		"""send() returns the set of all member ids across all groups."""
		ec = _make_email_channel(smtp_port=465)
		config = _make_config(ec)
		# Two groups, each with one member
		c1 = Change(source_id="s", url="u1", change_kind="new", id=10)
		c2 = Change(source_id="s", url="u2", change_kind="new", id=20)
		g1 = DigestGroup(
			key="k1",
			title="t1",
			summary=None,
			category="c",
			source_id="s",
			change_kind="new",
			members=[c1],
			score=5,
		)
		g2 = DigestGroup(
			key="k2",
			title="t2",
			summary=None,
			category="c",
			source_id="s",
			change_kind="new",
			members=[c2],
			score=4,
		)
		digest = Digest(groups=[g1, g2])

		smtp_instance = _make_smtp_instance()
		with patch("smtplib.SMTP_SSL", return_value=smtp_instance):
			ids = EmailNotifier().send(digest, config)

		assert ids == {10, 20}


class TestEmailNotifierStartTLS:
	"""Non-465 port → plaintext SMTP + mandatory STARTTLS upgrade."""

	def test_starttls_available_upgrades_then_sends(self) -> None:
		ec = _make_email_channel(smtp_port=587)
		config = _make_config(ec)
		digest = _make_digest()

		smtp_instance = _make_smtp_instance()
		smtp_instance.has_extn.return_value = True  # STARTTLS advertised

		with (
			patch("smtplib.SMTP", return_value=smtp_instance) as mock_plain,
			patch("smtplib.SMTP_SSL") as mock_ssl,
		):
			EmailNotifier().send(digest, config)

			mock_plain.assert_called_once()
			mock_ssl.assert_not_called()

			# Order: ehlo → starttls → ehlo → login → send_message
			call_names = [c[0] for c in smtp_instance.method_calls]
			assert "ehlo" in call_names
			starttls_idx = next(i for i, n in enumerate(call_names) if n == "starttls")
			login_idx = next(i for i, n in enumerate(call_names) if n == "login")
			send_idx = next(i for i, n in enumerate(call_names) if n == "send_message")
			assert starttls_idx < login_idx < send_idx

			# starttls called with a verifying SSL context
			starttls_kwargs = smtp_instance.starttls.call_args[1]
			ctx = starttls_kwargs.get("context")
			assert isinstance(ctx, ssl.SSLContext)
			assert ctx.verify_mode == ssl.CERT_REQUIRED

	def test_no_starttls_raises_notifyerror_and_never_logs_in(self) -> None:
		ec = _make_email_channel(smtp_port=587)
		config = _make_config(ec)
		digest = _make_digest()

		smtp_instance = _make_smtp_instance()
		smtp_instance.has_extn.return_value = False  # STARTTLS NOT advertised

		with patch("smtplib.SMTP", return_value=smtp_instance):
			with pytest.raises(NotifyError, match="STARTTLS"):
				EmailNotifier().send(digest, config)

		# Must never proceed to login or send over the plaintext channel
		smtp_instance.login.assert_not_called()
		smtp_instance.send_message.assert_not_called()


class TestEmailNotifierErrorWrapping:
	"""SMTPException, OSError, ssl.SSLError → NotifyError."""

	def test_smtp_exception_wrapped(self) -> None:
		ec = _make_email_channel(smtp_port=465)
		config = _make_config(ec)
		digest = _make_digest()

		smtp_instance = _make_smtp_instance()
		smtp_instance.send_message.side_effect = smtplib.SMTPException("nope")

		with patch("smtplib.SMTP_SSL", return_value=smtp_instance):
			with pytest.raises(NotifyError, match="nope"):
				EmailNotifier().send(digest, config)

	def test_oserror_wrapped(self) -> None:
		ec = _make_email_channel(smtp_port=465)
		config = _make_config(ec)
		digest = _make_digest()

		with patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")):
			with pytest.raises(NotifyError, match="connection refused"):
				EmailNotifier().send(digest, config)

	def test_ssl_error_wrapped(self) -> None:
		ec = _make_email_channel(smtp_port=465)
		config = _make_config(ec)
		digest = _make_digest()

		with patch("smtplib.SMTP_SSL", side_effect=ssl.SSLError("cert verify failed")):
			with pytest.raises(NotifyError, match="cert verify failed"):
				EmailNotifier().send(digest, config)
