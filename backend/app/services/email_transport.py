"""stdlib SMTP/IMAP transport for the cold-email Outreach module.

Cold email sends through each Organization's OWN connected mailbox (per-account
SMTP credentials), never the operator's transactional SMTP (services/email.py)
— deliverability and sender reputation belong to the sending domain, and the
CAN-SPAM identity must be the agency's. Everything here is stdlib (smtplib /
imaplib / email); no new dependency.

Every failure normalizes to EmailTransportError so the callers (the send
gateway, the IMAP sync, the connect/probe endpoint) branch on one exception.
Callers reach these through the module namespace (email_transport.smtp_send,
email_transport.fetch_new, …) so the test suite can monkeypatch them without a
live server.
"""

import concurrent.futures
import email
import imaplib
import smtplib
import ssl
from email.message import Message
from typing import List, Optional, Tuple

from ..models.email_outreach import SEC_SSL, SEC_STARTTLS
from ..security import decrypt_secret

# Conservative socket timeout — bounds connect()/recv() once a socket exists.
_TIMEOUT = 20

# Wall-clock ceiling for an entire connect+auth attempt. socket timeouts above
# do NOT bound DNS resolution — getaddrinfo() has no timeout in the stdlib, so
# a mistyped or unreachable hostname can otherwise hang the calling thread
# (and a request-handling worker) forever. Slightly above _TIMEOUT so a
# legitimate slow-but-working connect isn't falsely killed by the wall clock
# racing the socket timeout.
_DEADLINE = 25


class EmailTransportError(Exception):
    """Any SMTP/IMAP connect/auth/protocol failure, normalized."""


def _run_with_deadline(host: str, fn, *args, **kwargs):
    """Run fn(*args, **kwargs) in a worker thread with a hard wall-clock
    deadline, so a stuck DNS lookup can't wedge the caller. If fn raises, that
    exception propagates unchanged (concurrent.futures re-raises the original
    object) so callers' existing except clauses still see smtplib/OSError/
    ssl.SSLError as before. On timeout the worker thread is abandoned to run
    down on its own — Python can't forcibly interrupt a blocked syscall, but
    letting one thread leak briefly is far better than wedging a request."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=_DEADLINE)
    except concurrent.futures.TimeoutError:
        raise EmailTransportError(
            f"Connection to {host} timed out after {_DEADLINE}s "
            "(host unreachable or DNS did not resolve)"
        )
    finally:
        executor.shutdown(wait=False)


def _account_password(account) -> str:
    if not account.password_encrypted:
        raise EmailTransportError("No stored password for this mailbox")
    try:
        return decrypt_secret(account.password_encrypted)
    except Exception as e:  # bad/rotated key — treat as a transport failure
        raise EmailTransportError(f"Could not decrypt mailbox password: {e}") from e


def _smtp_connect(account) -> smtplib.SMTP:
    if account.smtp_security == SEC_SSL:
        server = smtplib.SMTP_SSL(
            account.smtp_host, account.smtp_port, timeout=_TIMEOUT,
            context=ssl.create_default_context(),
        )
    else:
        server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=_TIMEOUT)
        if account.smtp_security == SEC_STARTTLS:
            server.starttls(context=ssl.create_default_context())
    return server


def smtp_login(account) -> None:
    """Connect + authenticate + hang up, without sending. The probe/test path."""
    def _do():
        server = _smtp_connect(account)
        try:
            server.login(account.username, _account_password(account))
        finally:
            try:
                server.quit()
            except Exception:
                pass

    try:
        _run_with_deadline(account.smtp_host, _do)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise EmailTransportError(str(e)) from e


def smtp_send(account, msg: Message) -> str:
    """Deliver a fully-built message via the account's SMTP. Returns a short
    server response string on success; raises EmailTransportError otherwise."""
    recipients = [a for a in [msg.get("To")] if a]

    def _do():
        server = _smtp_connect(account)
        try:
            server.login(account.username, _account_password(account))
            refused = server.send_message(msg, account.from_email, recipients)
            if refused:
                raise EmailTransportError(f"Recipients refused: {refused}")
            return "250 OK"
        finally:
            try:
                server.quit()
            except Exception:
                pass

    try:
        return _run_with_deadline(account.smtp_host, _do)
    except EmailTransportError:
        raise
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise EmailTransportError(str(e)) from e


def imap_connect(account) -> imaplib.IMAP4:
    """A logged-in IMAP connection (INBOX not yet selected). Caller closes it."""
    def _do():
        if account.imap_security == SEC_SSL:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                account.imap_host, account.imap_port, timeout=_TIMEOUT,
                ssl_context=ssl.create_default_context(),
            )
        else:
            conn = imaplib.IMAP4(account.imap_host, account.imap_port, timeout=_TIMEOUT)
            if account.imap_security == SEC_STARTTLS:
                conn.starttls(ssl.create_default_context())
        conn.login(account.username, _account_password(account))
        return conn

    try:
        return _run_with_deadline(account.imap_host, _do)
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
        raise EmailTransportError(str(e)) from e


def fetch_new(account, last_uid: int) -> List[Tuple[int, bytes]]:
    """Return [(uid, rfc822_bytes)] for every INBOX message with UID greater
    than last_uid, oldest first. This is the one IMAP seam the sync service
    drives, so a test can monkeypatch it with canned RFC822 instead of a live
    server. Raises EmailTransportError on any transport failure."""
    conn = imap_connect(account)
    out: List[Tuple[int, bytes]] = []
    try:
        conn.select("INBOX")
        typ, data = conn.uid("search", None, f"UID {int(last_uid) + 1}:*")
        if typ != "OK" or not data or not data[0]:
            return out
        for raw_uid in data[0].split():
            try:
                uid = int(raw_uid)
            except ValueError:
                continue
            if uid <= last_uid:
                # "n:*" always yields at least the highest UID even when it is
                # below n — filter those out so we don't reprocess.
                continue
            ftyp, fdata = conn.uid("fetch", raw_uid, "(RFC822)")
            if ftyp != "OK" or not fdata or not fdata[0] or not isinstance(fdata[0], tuple):
                continue
            out.append((uid, fdata[0][1]))
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
        raise EmailTransportError(str(e)) from e
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    out.sort(key=lambda t: t[0])
    return out


def probe(account) -> dict:
    """Test both legs of a mailbox before saving it. Never raises — returns
    {smtp_ok, imap_ok, detail} so the connect endpoint can 400 with a reason."""
    smtp_ok = imap_ok = False
    detail: Optional[str] = None

    try:
        smtp_login(account)
        smtp_ok = True
    except EmailTransportError as e:
        detail = f"SMTP: {e}"

    try:
        conn = imap_connect(account)
        try:
            conn.logout()
        except Exception:
            pass
        imap_ok = True
    except EmailTransportError as e:
        detail = (f"{detail}; " if detail else "") + f"IMAP: {e}"

    return {"smtp_ok": smtp_ok, "imap_ok": imap_ok, "detail": detail}


def parse_message(raw: bytes) -> Message:
    """RFC822 bytes → email.message.Message (compat32 policy)."""
    return email.message_from_bytes(raw)
