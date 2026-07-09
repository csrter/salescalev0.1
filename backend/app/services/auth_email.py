"""Compose the account-lifecycle emails (verification, password reset).

Delivery goes through services.email (real SMTP when configured, otherwise
recorded in email_log — dev mode). The caller owns the transaction/commit.
Links point at the web app, which reads the token from the query string.
"""
from html import escape

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.core import Organization, User
from ..security import create_action_token, password_fingerprint
from . import branding
from . import email as email_service

VERIFY_PURPOSE = "verify_email"
RESET_PURPOSE = "password_reset"


def _html(product: str, heading: str, intro: str, button: str, link: str, note: str) -> str:
    """A minimal, client-safe (inline-styled, table-based) transactional email."""
    return f"""\
<!doctype html><html><body style="margin:0;background:#f5f6fb;padding:24px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#111530">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
    <table role="presentation" width="460" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e7e9f2;border-radius:14px;overflow:hidden">
      <tr><td style="padding:28px 32px 8px;font-size:18px;font-weight:700;color:#111530">◆ {escape(product)}</td></tr>
      <tr><td style="padding:0 32px"><h1 style="font-size:22px;margin:12px 0 6px">{escape(heading)}</h1>
        <p style="color:#5b6280;font-size:15px;line-height:1.5;margin:0 0 20px">{escape(intro)}</p>
        <a href="{escape(link)}" style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;font-weight:600;padding:11px 22px;border-radius:10px">{escape(button)}</a>
        <p style="color:#98a0b8;font-size:12px;line-height:1.5;margin:22px 0 0">{escape(note)}</p>
        <p style="color:#98a0b8;font-size:12px;word-break:break-all;margin:8px 0 28px">Or paste this link: {escape(link)}</p>
      </td></tr>
    </table>
  </td></tr></table>
</body></html>"""


def send_verification_email(db: Session, org: Organization, user: User) -> None:
    token = create_action_token(VERIFY_PURPOSE, user.id, minutes=60 * 24)
    link = f"{get_settings().app_base_url}/?verify={token}"
    product = branding.merged(org).get("product_name", "Salescale")
    email_service.send_email(
        db,
        org,
        user.email,
        "Confirm your email",
        f"Welcome to {product}. Confirm your email address:\n\n{link}\n\n"
        "This link expires in 24 hours.",
        html=_html(
            product,
            "Confirm your email",
            f"Welcome to {product} — confirm this address to finish setting up your account.",
            "Confirm email",
            link,
            "This link expires in 24 hours.",
        ),
    )


def send_reset_email(db: Session, org: Organization, user: User) -> None:
    # Fingerprint the current password hash into the token so it stops working
    # the moment the password changes — effectively single-use, and it also
    # invalidates any older outstanding reset links.
    token = create_action_token(
        RESET_PURPOSE,
        user.id,
        minutes=30,
        extra={"pw": password_fingerprint(user.hashed_password)},
    )
    link = f"{get_settings().app_base_url}/?reset={token}"
    product = branding.merged(org).get("product_name", "Salescale")
    email_service.send_email(
        db,
        org,
        user.email,
        "Reset your password",
        f"Reset your {product} password:\n\n{link}\n\n"
        "This link expires in 30 minutes. If you didn't request it, ignore this email.",
        html=_html(
            product,
            "Reset your password",
            "Click below to set a new password. If you didn't request this, you can ignore this email.",
            "Reset password",
            link,
            "This link expires in 30 minutes.",
        ),
    )
