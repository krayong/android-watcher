"""Email notifier: sends digest via SMTP with TLS enforced (fail closed)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from android_watcher.config import Config
from android_watcher.models import Digest
from android_watcher.notify.base import NOTIFIERS, NotifyError
from android_watcher.notify.render import render_email


@NOTIFIERS.register("email")
class EmailNotifier:
	name = "email"

	def send(self, digest: Digest, config: Config) -> set[int]:
		ec = config.email
		html, plaintext = render_email(digest)

		msg = EmailMessage()
		msg["From"] = ec.sender
		msg["To"] = ec.recipient
		msg["Subject"] = "android-watcher digest"
		msg.set_content(plaintext)
		msg.add_alternative(html, subtype="html")

		context = ssl.create_default_context()
		try:
			if ec.smtp_port == 465:
				with smtplib.SMTP_SSL(ec.smtp_host, ec.smtp_port, context=context) as s:
					s.login(ec.username, ec.password)
					s.send_message(msg)
			else:
				with smtplib.SMTP(ec.smtp_host, ec.smtp_port) as s:
					s.ehlo()
					if not s.has_extn("starttls"):
						raise NotifyError(
							f"SMTP server {ec.smtp_host}:{ec.smtp_port} does not advertise "
							"STARTTLS; refusing to send over plaintext"
						)
					s.starttls(context=context)
					s.ehlo()
					s.login(ec.username, ec.password)
					s.send_message(msg)
		except NotifyError:
			raise
		except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
			raise NotifyError(f"email send failed: {exc}") from exc
		return {m.id for g in digest.groups for m in g.members if m.id is not None}
