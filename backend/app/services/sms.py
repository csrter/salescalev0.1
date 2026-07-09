"""SMS delivery via Twilio, for phone-based 2FA.

Pluggable and unconfigured-safe: without Twilio credentials, sms_configured()
is False and callers surface a 503 rather than silently dropping codes. TOTP
and email 2FA don't touch this module.
"""
import httpx

from ..config import get_settings


def sms_configured() -> bool:
    s = get_settings()
    return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number)


def send_sms(to: str, message: str) -> None:
    """Send an SMS via Twilio. Raises RuntimeError if unconfigured or on a
    provider error — callers translate that to a user-facing failure."""
    s = get_settings()
    if not sms_configured():
        raise RuntimeError("SMS is not configured")
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
        auth=(s.twilio_account_sid, s.twilio_auth_token),
        data={"From": s.twilio_from_number, "To": to, "Body": message},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"SMS send failed ({resp.status_code})")
