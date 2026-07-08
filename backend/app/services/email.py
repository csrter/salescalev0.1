"""Phase 9 transactional email with white-labeled sender resolution.

The rule (CLAUDE.md / phase 9 task 4): an Organization that configured
branded email NEVER has client-facing mail go out under the vendor's name;
an Organization that didn't gets the neutral platform default. Sender
resolution lives in exactly one function (resolve_sender) so that rule
can't drift per call site.

Transport: real SMTP only when configured (settings.smtp_host); otherwise
dev mode — the email is composed and recorded in email_log but not sent.
Either way the resolved sender is persisted, which is what the tests (and
any later compliance question) assert against.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from typing import Tuple

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.core import Organization
from ..models.email import EmailLog
from . import branding, entitlements

log = logging.getLogger("salescale.email")


def _send_via_resend(
    api_key: str,
    from_name: str,
    from_address: str,
    to_address: str,
    subject: str,
    text: str,
    html: str | None = None,
) -> bool:
    """Deliver through the Resend API. Returns True on success; never raises."""
    try:
        payload = {
            "from": f"{from_name} <{from_address}>",
            "to": [to_address],
            "subject": subject,
            "text": text,
        }
        if html:
            payload["html"] = html
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=15,
        )
        if resp.status_code // 100 == 2:
            return True
        log.warning("Resend delivery failed (%s): %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("Resend delivery error: %s", e)
        return False


def _send_via_smtp(
    settings, from_name, from_address, to_address, subject, text, html=None
) -> bool:
    try:
        msg = MIMEText(html, "html") if html else MIMEText(text, "plain")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_address}>"
        msg["To"] = to_address
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(from_address, [to_address], msg.as_string())
        return True
    except Exception as e:
        log.warning("SMTP delivery error: %s", e)
        return False


def resolve_sender(org: Organization) -> Tuple[str, str]:
    """(from_name, from_address) for mail sent on behalf of this
    Organization. Branded when configured and entitled; neutral default
    otherwise."""
    settings = get_settings()
    b = branding.merged(org)
    if entitlements.can_use_white_labeling(org):
        name = b.get("email_from_name")
        address = b.get("email_from_address")
        if address:
            return (name or b["product_name"], address)
    return (settings.email_default_from_name, settings.email_default_from_address)


def send_email(
    db: Session,
    org: Organization,
    to_address: str,
    subject: str,
    body: str,
    html: str | None = None,
) -> EmailLog:
    """Compose, (maybe) deliver, and always log. Never raises on transport
    failure — email is a side effect, not a request outcome; the log row
    records whether delivery happened. `body` is the plain-text part; `html`
    (optional) is the rich version."""
    settings = get_settings()
    from_name, from_address = resolve_sender(org)

    # Resend is preferred; SMTP is the fallback; neither configured = dev mode
    # (composed and logged, not delivered).
    delivered = False
    if settings.resend_api_key:
        delivered = _send_via_resend(
            settings.resend_api_key, from_name, from_address, to_address, subject, body, html
        )
    elif settings.smtp_host:
        delivered = _send_via_smtp(
            settings, from_name, from_address, to_address, subject, body, html
        )

    entry = EmailLog(
        organization_id=org.id,
        to_address=to_address,
        from_name=from_name,
        from_address=from_address,
        subject=subject,
        body=body,
        delivered=delivered,
    )
    db.add(entry)
    # Caller owns the transaction (commit happens with the triggering write).
    return entry
