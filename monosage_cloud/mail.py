"""Transactional email: an outbox in the database, delivered over SMTP when configured.

Without ``AIRA_CLOUD_SMTP_URL`` every message is stored with status ``captured``
(development and tests read it back from the outbox). Teams and Enterprise get
their organization's name and logo in the message; no tenant domain is used.

With SMTP a message is stored ``queued`` and delivered by the job queue (``mail.deliver``), never inside the request
that caused it: a slow or failing mail server does not slow a sign-up, and a failure is retried with backoff
(30 s, 2 min, 10 min, 10 min) before the message is marked ``failed``.

The outbox does not keep working links: a message with a link that grants access (an email confirmation, a password
reset, an invitation, an export download) records when the link expires, and the daily housekeeping masks the link
once it has; every message is deleted after ``mail_outbox_days``.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any
from urllib.parse import unquote, urlsplit

from monosage_cloud.config import CloudConfig, Clock
from monosage_cloud.db import ControlDB, loads, many, new_id, one, platform_scope

logger = logging.getLogger("monosage_cloud.mail")

MAIL_JOB = "mail.deliver"
MAIL_ATTEMPTS = 5
EXPORT_LINK_SECONDS = 30 * 86_400
REDACTED_LINK = "[link expired and removed]"

TEMPLATES: dict[str, tuple[str, str]] = {
    "verify_email": (
        "Confirm your email for {org}",
        "Hello {name},\n\nConfirm your email address to finish setting up {org}:\n{link}\n\nThe link works once and expires in 24 hours.",
    ),
    "invite": (
        "{inviter} invited you to {org} on Monosage",
        "Hello,\n\n{inviter} invited you to join {org} on Monosage Career Copilot as {role}.\nAccept the invitation: {link}\n\nThe link works once and expires on {expires}.",
    ),
    "password_reset": (
        "Reset your Monosage password",
        "Hello {name},\n\nSomeone asked to reset the password for your {org} account. If it was you, choose a new password here:\n{link}\n\nThe link works once and expires in 24 hours. If it was not you, ignore this message.",
    ),
    "payment_failed": (
        "Payment failed for {org}",
        "The latest payment for {org} did not go through. Automation has stopped. The organization becomes read-only on {read_only_on} unless payment is updated:\n{link}",
    ),
    "read_only": (
        "{org} is now read-only",
        "Payment for {org} is {days} days overdue, so data is read-only. Update the payment method to restore access:\n{link}\nThe organization is scheduled for deletion on {deletion_on} unless payment is restored.",
    ),
    "deletion_notice": (
        "Final notice: {org} will be deleted",
        "{org} is scheduled for deletion on {deletion_on}. Export your data and update billing before then:\n{link}",
    ),
    "trial_ending": (
        "Your {org} trial ends on {trial_end}",
        "The trial for {org} ends on {trial_end}. Pick a plan to keep your candidates' searches and applications running:\n{link}",
    ),
    "subscription_cancelled": (
        "Subscription cancelled for {org}",
        "The subscription for {org} is cancelled. Data stays readable and exportable until {export_until}:\n{link}",
    ),
    "support_request": (
        "Monosage support asks for access to {org}",
        "{operator} from Monosage support asked for time-limited access to {org}.\nReason: {reason}\nReview and approve or deny the request:\n{link}",
    ),
    "downgrade_pause": (
        "{org} is over its new plan limits",
        "After the plan change, {org} is over these limits: {limits}. Automation is paused until the organization is within them:\n{link}",
    ),
    "data_export_ready": (
        "Your {org} data export is ready",
        "Your export is ready to download:\n{link}",
    ),
    "account_recovery": (
        "Recovery of your second factor for {org} has started",
        "Hello {name},\n\nMonosage support started removing the second factor (authenticator) from your {org} owner "
        "account, because you told us you lost it and your recovery codes. Nothing changes until {available_on}; then "
        "support can remove it, and you sign in with your password and set up a new authenticator.\n\n"
        "If you did not ask for this, cancel it now:\n{link}",
    ),
    "account_recovered": (
        "Your second factor for {org} was removed",
        "Hello {name},\n\nMonosage support removed the second factor from your {org} owner account and signed you out "
        "everywhere. Sign in with your password and set up a new authenticator straight away:\n{link}",
    ),
}


class Mailer:
    def __init__(self, db: ControlDB, clock: Clock, config: CloudConfig) -> None:
        self.db = db
        self.clock = clock
        self.config = config
        self.branding: Callable[[str], dict[str, Any]] = lambda org_id: {}
        self.queue: Any = None  # the job queue (CloudApp sets it); without one, mail is delivered at once

    def link_ttl(self, kind: str) -> int | None:
        """How long the link in a message of this kind grants access (None: the message holds no such link)."""
        return {
            "verify_email": self.config.email_token_ttl,
            "password_reset": self.config.email_token_ttl,
            "invite": self.config.invite_ttl,
            "data_export_ready": EXPORT_LINK_SECONDS,
            "account_recovery": self.config.recovery_delay_seconds,
        }.get(kind)

    def send(self, org_id: str, to: str, kind: str, **context: Any) -> str:
        subject_template, body_template = TEMPLATES[kind]
        branding = self.branding(org_id) if org_id else {}
        org_name = str(context.get("org") or branding.get("name") or "Monosage")
        values = {"name": "there", "org": org_name, **context}
        subject = subject_template.format(**values)
        body = body_template.format(**values)
        if branding.get("logo_url"):
            body = f"[{org_name}] {branding['logo_url']}\n\n{body}"
        elif branding.get("branded"):
            body = f"[{org_name}]\n\n{body}"
        body += "\n\n—\nAira Career Copilot"
        mail_id = new_id()
        status, error = "captured", ""
        queued = bool(self.config.smtp_url and self.queue is not None)
        if queued:
            status = "queued"
        elif self.config.smtp_url:
            try:
                self._deliver(to, subject, body)
                status = "sent"
            except Exception as exc:  # noqa: BLE001 - a mail failure must not fail the action
                status, error = "failed", f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("Mail delivery failed: %s", error)
        now = self.clock.now()
        ttl = self.link_ttl(kind)
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO mail_outbox (mail_id, org_id, recipient, subject, body, kind, status, error, created_at, sent_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mail_id,
                    org_id,
                    to,
                    subject,
                    body,
                    kind,
                    status,
                    error,
                    now,
                    now if status == "sent" else None,
                    now + ttl if ttl else None,
                ),
            )
        if queued:
            self.queue.enqueue(
                org_id or PLATFORM_ORG,
                MAIL_JOB,
                payload={"mail_id": mail_id},
                priority=2,  # people are waiting for these (a confirmation, a reset)
                max_attempts=MAIL_ATTEMPTS,
            )
        return mail_id

    def deliver_queued(self, mail_id: str, *, last_attempt: bool = False) -> dict[str, Any]:
        """One delivery attempt of a queued message (the ``mail.deliver`` job). Raises to be retried."""
        with platform_scope(), self.db.read() as connection:
            row = one(
                connection, "SELECT * FROM mail_outbox WHERE mail_id = ?", (mail_id,)
            )
        if row is None or row["status"] != "queued":
            return {"skipped": True}
        try:
            self._deliver(row["recipient"], row["subject"], row["body"])
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
            with platform_scope(), self.db.write() as connection:
                connection.execute(
                    "UPDATE mail_outbox SET attempts = attempts + 1, error = ?, status = ? WHERE mail_id = ?",
                    (error, "failed" if last_attempt else "queued", mail_id),
                )
            logger.warning("Mail delivery failed (%s): %s", row["kind"], error)
            raise
        with platform_scope(), self.db.write() as connection:
            connection.execute(
                "UPDATE mail_outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1, error = '' WHERE mail_id = ?",
                (self.clock.now(), mail_id),
            )
        return {"sent": True}

    def housekeeping(self) -> int:
        """Mask the links of messages whose link has expired, and delete messages older than ``mail_outbox_days``."""
        now = self.clock.now()
        changed = 0
        with platform_scope(), self.db.write() as connection:
            rows = many(
                connection,
                "SELECT mail_id, body FROM mail_outbox WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            for row in rows:
                connection.execute(
                    "UPDATE mail_outbox SET body = ?, expires_at = NULL WHERE mail_id = ?",
                    (redact_links(row["body"]), row["mail_id"]),
                )
            changed += len(rows)
            days = max(1, int(self.config.mail_outbox_days))
            changed += max(
                0,
                connection.execute(
                    "DELETE FROM mail_outbox WHERE created_at < ?", (now - days * 86_400,)
                ).rowcount,
            )
        return changed

    def _deliver(self, to: str, subject: str, body: str) -> None:
        parts = urlsplit(self.config.smtp_url)
        message = EmailMessage()
        message["From"] = self.config.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        host, port = (
            parts.hostname or "localhost",
            parts.port or (465 if parts.scheme == "smtps" else 587),
        )
        if parts.scheme == "smtps":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, context=ssl.create_default_context(), timeout=15
            )
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
        with smtp:
            if parts.scheme == "smtp" and port != 25:
                smtp.starttls(context=ssl.create_default_context())
            if parts.username:
                smtp.login(unquote(parts.username), unquote(parts.password or ""))
            smtp.send_message(message)

    def outbox(
        self, *, org_id: str = "", recipient: str = "", kind: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        clauses, params = ["1 = 1"], []
        for column, value in (
            ("org_id", org_id),
            ("recipient", recipient),
            ("kind", kind),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        with self.db.read() as connection:
            return many(
                connection,
                f"SELECT * FROM mail_outbox WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*params, limit),
            )

    def latest(self, recipient: str, kind: str) -> dict[str, Any] | None:
        rows = self.outbox(recipient=recipient, kind=kind, limit=1)
        return rows[0] if rows else None

    def delete_org(self, org_id: str) -> None:
        with self.db.write() as connection:
            connection.execute("DELETE FROM mail_outbox WHERE org_id = ?", (org_id,))


# Queue jobs for mail that belongs to no organization (an operator's own) are filed under this id.
PLATFORM_ORG = "_platform"


def redact_links(body: str) -> str:
    """The message with every URL replaced: an expired link is not kept in the outbox."""
    return re.sub(r"https?://[^\s]+", REDACTED_LINK, body)


def link_from(mail: dict[str, Any] | None) -> str:
    """The first URL in a captured message (tests follow it like a person would)."""
    if not mail:
        return ""
    for token in mail["body"].split():
        if token.startswith(("http://", "https://")):
            return token.rstrip(".,")
    return ""


__all__ = ["Mailer", "TEMPLATES", "link_from", "loads", "one"]
