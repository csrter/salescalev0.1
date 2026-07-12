"""Cold-email Outreach module — Phase 1 foundation.

The SMTP/IMAP transport is monkeypatched (no live mail server): outbound sends
capture the built MIME message; IMAP sync is fed canned RFC822. The tests pin
the compliance-critical plumbing — the one send gateway's ordered guards
(suppression / verified-invalid / cap), the CAN-SPAM footer + List-Unsubscribe
headers + per-message tokens, threaded replies, IMAP reply/bounce/unsubscribe
classification and idempotency, the open pixel + one-click unsubscribe public
endpoints, tenant isolation, and the client-role lockout.

Contact-creating work runs against a dedicated org (ce_org), never the seeded
Atlas Reach org whose contact counts the metrics suite asserts over.
"""

import datetime as dt
import email

import pytest

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.core import Organization
from app.models.crm import Contact
from app.models.email_outreach import (
    DIR_IN,
    DIR_OUT,
    MSG_BOUNCED,
    MSG_SENT,
    EmailAccount,
    EmailMessage,
    EmailThread,
)
from app.services import email_outreach_send as gateway
from app.services import email_outreach_sync as sync
from app.services import email_transport


# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def probe_ok(monkeypatch):
    monkeypatch.setattr(
        email_transport, "probe",
        lambda account: {"smtp_ok": True, "imap_ok": True, "detail": None},
    )


@pytest.fixture()
def captured_sends(monkeypatch):
    """Capture every MIME message the gateway hands to SMTP; return a list of
    email.message.Message objects."""
    sent = []

    def _fake_smtp_send(account, msg):
        sent.append(msg)
        return "250 OK captured"

    monkeypatch.setattr(email_transport, "smtp_send", _fake_smtp_send)
    return sent


@pytest.fixture(scope="module")
def ce_org(api):
    """Dedicated Organization for the cold-email tests (see module doc)."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Cold Email Co",
            "email": "owner@coldemailco.com",
            "password": "coldemail-pass-1",
            "full_name": "CE Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "CE Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _make_contact(ce_org, api, *, email_addr, first="Pat", last="Prospect"):
    r = api.post(
        "/api/crm/contacts",
        json={
            "client_id": ce_org["client"],
            "first_name": first,
            "last_name": last,
            "email": email_addr,
        },
        headers=ce_org["headers"],
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _account_payload(**over):
    base = {
        "name": "Sales Mailbox",
        "from_name": "Sam Sales",
        "from_email": "sam@coldemailco.com",
        "smtp_host": "smtp.coldemailco.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.coldemailco.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_username": "sam@coldemailco.com",
        "smtp_password": "mailbox-secret",
        "imap_username": "sam@coldemailco.com",
        "imap_password": "mailbox-secret",
        "daily_send_cap": 100,
        "signature": "Sam Sales\nCold Email Co",
    }
    base.update(over)
    return base


def _create_account(ce_org, api, **over):
    r = api.post(
        "/api/email-outreach/accounts",
        json=_account_payload(**over),
        headers=ce_org["headers"],
    )
    assert r.status_code == 201, r.text
    return r.json()


def _account_row(account_id):
    db = SessionLocal()
    try:
        return db.get(EmailAccount, account_id)
    finally:
        db.close()


# --- account connect / probe -----------------------------------------------


def test_probe_failure_400_and_password_never_stored(ce_org, api, monkeypatch):
    monkeypatch.setattr(
        email_transport, "probe",
        lambda account: {"smtp_ok": False, "imap_ok": False, "detail": "SMTP: auth failed"},
    )
    r = api.post(
        "/api/email-outreach/accounts",
        json=_account_payload(from_email="probefail@coldemailco.com"),
        headers=ce_org["headers"],
    )
    assert r.status_code == 400
    assert "auth failed" in r.json()["detail"]


def test_create_account_encrypts_password_and_never_serializes_it(
    ce_org, api, probe_ok
):
    acct = _create_account(ce_org, api, from_email="store@coldemailco.com")
    # The serialization must not leak either password / encrypted blob.
    assert "smtp_password" not in acct and "smtp_password_encrypted" not in acct
    assert "imap_password" not in acct and "imap_password_encrypted" not in acct
    assert acct["status"] == "active"
    assert acct["sends_today"] == 0
    assert acct["effective_daily_cap"] == acct["daily_send_cap"]

    row = _account_row(acct["id"])
    assert row.smtp_password_encrypted and row.smtp_password_encrypted != "mailbox-secret"
    assert row.imap_password_encrypted and row.imap_password_encrypted != "mailbox-secret"
    from app.security import decrypt_secret

    assert decrypt_secret(row.smtp_password_encrypted) == "mailbox-secret"
    assert decrypt_secret(row.imap_password_encrypted) == "mailbox-secret"

    # List response also omits secrets.
    listed = api.get("/api/email-outreach/accounts", headers=ce_org["headers"]).json()
    assert all(
        "smtp_password" not in a and "smtp_password_encrypted" not in a
        and "imap_password" not in a and "imap_password_encrypted" not in a
        for a in listed
    )


# --- gateway guards ---------------------------------------------------------


def _send_via_gateway(account_id, contact_id, *, kind="manual", subject="Hi", body="Hello there"):
    db = SessionLocal()
    try:
        account = db.get(EmailAccount, account_id)
        contact = db.get(Contact, contact_id)
        code, msg = gateway.send(
            db, account, to_contact=contact, subject=subject, body_text=body, kind=kind
        )
        db.commit()
        return code, (msg.id if msg else None)
    finally:
        db.close()


def test_gateway_sent_has_footer_headers_and_tokens(
    ce_org, api, probe_ok, captured_sends
):
    # Configure a mailing address so the CAN-SPAM footer carries it.
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "500 Market St, Denver CO 80202"},
        headers=ce_org["headers"],
    )
    acct = _create_account(ce_org, api, from_email="sender@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="lead1@example.com")

    code, msg_id = _send_via_gateway(
        acct["id"], contact_id, body="Quick question about your roofing crews."
    )
    assert code == gateway.SENT
    assert len(captured_sends) == 1
    mime = captured_sends[0]

    assert mime["From"] == "Sam Sales <sender@coldemailco.com>"
    assert mime["To"] == "lead1@example.com"
    assert mime["Message-ID"]
    # List-Unsubscribe + one-click headers present on a cold send.
    assert mime["List-Unsubscribe"].startswith("<http")
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    body = mime.get_content()
    assert "500 Market St, Denver CO 80202" in body  # mailing address in footer
    assert "Cold Email Co" in body  # org name in footer
    assert "Unsubscribe:" in body
    assert "Sam Sales" in body  # account signature above footer

    # Tokens persisted on the row.
    db = SessionLocal()
    try:
        row = db.get(EmailMessage, msg_id)
        assert row.open_token and row.unsubscribe_token
        assert row.status == MSG_SENT and row.sent_at is not None
        assert row.smtp_response == "250 OK captured"
        thread = db.get(EmailThread, row.thread_id)
        assert thread.message_count == 1
    finally:
        db.close()


def test_gateway_unsubscribe_token_rendered_in_place(
    ce_org, api, probe_ok, captured_sends
):
    acct = _create_account(ce_org, api, from_email="inplace@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="lead-inplace@example.com")
    code, _ = _send_via_gateway(
        acct["id"], contact_id,
        body="Hello!\n\nOpt out any time: {{unsubscribe_url}}",
    )
    assert code == gateway.SENT
    body = captured_sends[-1].get_content()
    assert "{{unsubscribe_url}}" not in body
    assert "Opt out any time: http" in body
    # Rendered in place → no appended footer block.
    assert "\n--\n" not in body


def test_gateway_suppressed(ce_org, api, probe_ok, captured_sends):
    acct = _create_account(ce_org, api, from_email="supp@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="blocked@example.com")
    # Suppress the address via the admin endpoint.
    api.post(
        "/api/email-outreach/suppression",
        json={"emails": ["blocked@example.com"]},
        headers=ce_org["headers"],
    )
    code, msg_id = _send_via_gateway(acct["id"], contact_id)
    assert code == gateway.SUPPRESSED
    assert msg_id is None
    assert captured_sends == []  # never handed to SMTP


def test_gateway_blocked_on_verified_invalid(ce_org, api, probe_ok, captured_sends):
    acct = _create_account(ce_org, api, from_email="blk@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="invalid-verify@example.com")
    db = SessionLocal()
    try:
        c = db.get(Contact, contact_id)
        c.verification_status = "invalid"
        db.commit()
    finally:
        db.close()
    code, _ = _send_via_gateway(acct["id"], contact_id)
    assert code == gateway.BLOCKED
    assert captured_sends == []


def test_gateway_cap_reached(ce_org, api, probe_ok, captured_sends):
    acct = _create_account(ce_org, api, from_email="cap@coldemailco.com", daily_send_cap=1)
    c1 = _make_contact(ce_org, api, email_addr="cap1@example.com")
    c2 = _make_contact(ce_org, api, email_addr="cap2@example.com")
    assert _send_via_gateway(acct["id"], c1)[0] == gateway.SENT
    code, msg_id = _send_via_gateway(acct["id"], c2)
    assert code == gateway.CAP_REACHED
    assert msg_id is None
    assert len(captured_sends) == 1


# --- compose + threaded reply ----------------------------------------------


def test_compose_then_threaded_reply_sets_in_reply_to(
    ce_org, api, probe_ok, captured_sends
):
    acct = _create_account(ce_org, api, from_email="convo@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="convo-lead@example.com")

    r = api.post(
        "/api/email-outreach/compose",
        json={
            "account_id": acct["id"],
            "contact_id": contact_id,
            "subject": "Roofing leads for Q3",
            "body": "Are you booking new roofing jobs?",
        },
        headers=ce_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    thread_id = r.json()["thread_id"]
    first_mid = captured_sends[-1]["Message-ID"]

    # Reply on the same thread → In-Reply-To/References the first message.
    r2 = api.post(
        f"/api/email-outreach/threads/{thread_id}/reply",
        json={"body": "Following up on my note."},
        headers=ce_org["headers"],
    )
    assert r2.status_code == 200, r2.text
    reply_mime = captured_sends[-1]
    assert reply_mime["In-Reply-To"] == first_mid
    assert reply_mime["References"] == first_mid
    assert reply_mime["Subject"].startswith("Re: Roofing leads")


# --- IMAP sync --------------------------------------------------------------


def _rfc822(headers: dict, body: str = "") -> bytes:
    lines = [f"{k}: {v}" for k, v in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()


def _seed_outbound(account_id, contact_id, subject="Original outreach"):
    """Send a first message so an inbound reply has an outbound to thread onto,
    returns (message_id_header, thread_id)."""
    db = SessionLocal()
    try:
        account = db.get(EmailAccount, account_id)
        contact = db.get(Contact, contact_id)
        code, msg = gateway.send(
            db, account, to_contact=contact, subject=subject,
            body_text="Hi there", kind="manual",
        )
        db.commit()
        return msg.message_id_header, msg.thread_id
    finally:
        db.close()


def test_sync_inbound_reply_creates_message_unread_and_is_idempotent(
    ce_org, api, probe_ok, captured_sends, monkeypatch
):
    acct = _create_account(ce_org, api, from_email="sync1@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="replier@example.com")
    out_mid, thread_id = _seed_outbound(acct["id"], contact_id)

    inbound = _rfc822(
        {
            "From": "Replier <replier@example.com>",
            "To": "sync1@coldemailco.com",
            "Subject": "Re: Original outreach",
            "Message-ID": "<reply-001@example.com>",
            "In-Reply-To": out_mid,
        },
        "Yes, tell me more.",
    )
    monkeypatch.setattr(
        email_transport, "fetch_new", lambda account, last_uid: [(11, inbound)]
    )

    db = SessionLocal()
    try:
        account = db.get(EmailAccount, acct["id"])
        result = sync.sync_account(db, account)
        db.commit()
        assert result["outcomes"].get("reply") == 1
        thread = db.get(EmailThread, thread_id)
        assert thread.unread is True
        assert thread.last_inbound_at is not None
        inbound_rows = [
            m for m in db.query(EmailMessage)
            .filter_by(thread_id=thread_id, direction=DIR_IN).all()
        ]
        assert len(inbound_rows) == 1
        assert account.last_imap_uid == 11
    finally:
        db.close()

    # Re-sync the same message (UID reset so it's re-delivered) → idempotent.
    db = SessionLocal()
    try:
        account = db.get(EmailAccount, acct["id"])
        account.last_imap_uid = 0
        db.commit()
        sync.sync_account(db, account)
        db.commit()
        inbound_rows = db.query(EmailMessage).filter_by(
            thread_id=thread_id, direction=DIR_IN
        ).all()
        assert len(inbound_rows) == 1  # not duplicated
    finally:
        db.close()


def test_sync_dsn_marks_bounced_suppresses_and_invalidates_contact(
    ce_org, api, probe_ok, captured_sends, monkeypatch
):
    acct = _create_account(ce_org, api, from_email="sync2@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="bouncer@example.com")
    out_mid, _ = _seed_outbound(acct["id"], contact_id)

    # A minimal DSN: multipart/report; report-type=delivery-status, quoting the
    # original Message-ID in the embedded part.
    boundary = "b0undary"
    dsn = (
        f"From: MAILER-DAEMON@example.com\r\n"
        f"To: sync2@coldemailco.com\r\n"
        f"Subject: Delivery Status Notification (Failure)\r\n"
        f"Message-ID: <dsn-001@example.com>\r\n"
        f'Content-Type: multipart/report; report-type=delivery-status; boundary="{boundary}"\r\n'
        f"\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/plain\r\n\r\n"
        f"Your message could not be delivered.\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: message/rfc822\r\n\r\n"
        f"Message-ID: {out_mid}\r\n\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    monkeypatch.setattr(
        email_transport, "fetch_new", lambda account, last_uid: [(21, dsn)]
    )
    db = SessionLocal()
    try:
        account = db.get(EmailAccount, acct["id"])
        result = sync.sync_account(db, account)
        db.commit()
        assert result["outcomes"].get("bounced") == 1
        original = db.query(EmailMessage).filter_by(
            message_id_header=out_mid
        ).one()
        assert original.status == MSG_BOUNCED and original.bounced_at is not None
        contact = db.get(Contact, contact_id)
        assert contact.verification_status == "invalid"
    finally:
        db.close()

    # Suppressed → a further send is blocked.
    assert gateway.SUPPRESSED == _send_via_gateway(acct["id"], contact_id)[0]


def test_sync_unsubscribe_reply_suppresses(
    ce_org, api, probe_ok, captured_sends, monkeypatch
):
    acct = _create_account(ce_org, api, from_email="sync3@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="optout@example.com")
    out_mid, _ = _seed_outbound(acct["id"], contact_id)

    inbound = _rfc822(
        {
            "From": "optout@example.com",
            "To": "sync3@coldemailco.com",
            "Subject": "Re: Original outreach",
            "Message-ID": "<optout-001@example.com>",
            "In-Reply-To": out_mid,
        },
        "Please unsubscribe me from this list.",
    )
    monkeypatch.setattr(
        email_transport, "fetch_new", lambda account, last_uid: [(31, inbound)]
    )
    db = SessionLocal()
    try:
        account = db.get(EmailAccount, acct["id"])
        sync.sync_account(db, account)
        db.commit()
    finally:
        db.close()
    assert gateway.SUPPRESSED == _send_via_gateway(acct["id"], contact_id)[0]


def test_sync_transport_error_sets_account_error_and_does_not_raise(
    ce_org, api, probe_ok, monkeypatch
):
    acct = _create_account(ce_org, api, from_email="sync4@coldemailco.com")

    def _boom(account, last_uid):
        raise email_transport.EmailTransportError("connection refused")

    monkeypatch.setattr(email_transport, "fetch_new", _boom)
    db = SessionLocal()
    try:
        account = db.get(EmailAccount, acct["id"])
        result = sync.sync_account(db, account)  # must not raise
        db.commit()
        assert "error" in result
        assert account.status == "error"
        assert "connection refused" in account.last_sync_error
    finally:
        db.close()


# --- public pixel + unsubscribe --------------------------------------------


def test_open_pixel_records_once_increments_and_bad_token_200(
    ce_org, api, probe_ok, captured_sends
):
    acct = _create_account(ce_org, api, from_email="pixel@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="opener@example.com")
    _, msg_id = _send_via_gateway(acct["id"], contact_id)
    db = SessionLocal()
    try:
        token = db.get(EmailMessage, msg_id).open_token
    finally:
        db.close()

    r = api.get(f"/api/email-outreach/o/{token}.gif")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/gif"
    r2 = api.get(f"/api/email-outreach/o/{token}.gif")
    assert r2.status_code == 200

    db = SessionLocal()
    try:
        row = db.get(EmailMessage, msg_id)
        assert row.opened_at is not None
        assert row.open_count == 2
    finally:
        db.close()

    # Unknown token still 200 + a GIF (no enumeration).
    bad = api.get("/api/email-outreach/o/not-a-real-token.gif")
    assert bad.status_code == 200
    assert bad.headers["content-type"] == "image/gif"


def test_unsubscribe_get_and_post(ce_org, api, probe_ok, captured_sends):
    acct = _create_account(ce_org, api, from_email="unsub@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="unsub-lead@example.com")
    _, msg_id = _send_via_gateway(acct["id"], contact_id)
    db = SessionLocal()
    try:
        token = db.get(EmailMessage, msg_id).unsubscribe_token
    finally:
        db.close()

    # GET renders a branded confirmation page.
    r = api.get(f"/api/email-outreach/unsubscribe/{token}")
    assert r.status_code == 200
    assert "unsubscribed" in r.text.lower()
    # And the address is now suppressed.
    assert gateway.SUPPRESSED == _send_via_gateway(acct["id"], contact_id)[0]

    # POST one-click on an unknown token is still 200 (no enumeration).
    r2 = api.post("/api/email-outreach/unsubscribe/nope-nope-nope")
    assert r2.status_code == 200


# --- tenant isolation + role gate ------------------------------------------


def test_tenant_isolation_other_org_404(ce_org, api, probe_ok, team_headers):
    """Atlas Reach (team_headers) must not see or fetch Cold Email Co's
    mailbox or threads."""
    acct = _create_account(ce_org, api, from_email="iso@coldemailco.com")
    contact_id = _make_contact(ce_org, api, email_addr="iso-lead@example.com")
    _, msg_id = _send_via_gateway(acct["id"], contact_id)
    db = SessionLocal()
    try:
        thread_id = db.get(EmailMessage, msg_id).thread_id
    finally:
        db.close()

    # Org B's list never contains org A's mailbox.
    listed = api.get("/api/email-outreach/accounts", headers=team_headers).json()
    assert all(a["id"] != acct["id"] for a in listed)

    # Direct fetches 404.
    assert api.patch(
        f"/api/email-outreach/accounts/{acct['id']}",
        json={"name": "hijack"},
        headers=team_headers,
    ).status_code == 404
    assert api.get(
        f"/api/email-outreach/threads/{thread_id}/messages", headers=team_headers
    ).status_code == 404
    assert api.post(
        f"/api/email-outreach/threads/{thread_id}/reply",
        json={"body": "nope"},
        headers=team_headers,
    ).status_code == 404


def test_client_role_forbidden(api, client_a_headers):
    """The client role is locked out of every Outreach surface."""
    assert api.get(
        "/api/email-outreach/accounts", headers=client_a_headers
    ).status_code == 403
    assert api.get(
        "/api/email-outreach/inbox", headers=client_a_headers
    ).status_code == 403
    assert api.get(
        "/api/email-outreach/suppression", headers=client_a_headers
    ).status_code == 403


def test_member_cannot_manage_accounts_but_can_view(ce_org, api, probe_ok):
    """Member (team, non-admin) sees the inbox but can't connect mailboxes."""
    # Add a member to the ce_org via the team endpoint.
    r = api.post(
        "/api/orgs/me/members",
        json={
            "email": "member@coldemailco.com",
            "password": "member-pass-1",
            "full_name": "CE Member",
            "role": "member",
        },
        headers=ce_org["headers"],
    )
    assert r.status_code in (200, 201), r.text
    login = api.post(
        "/api/auth/login",
        json={"email": "member@coldemailco.com", "password": "member-pass-1"},
    ).json()
    mh = {"Authorization": f"Bearer {login['access_token']}"}

    assert api.get("/api/email-outreach/accounts", headers=mh).status_code == 200
    assert api.post(
        "/api/email-outreach/accounts",
        json=_account_payload(from_email="member-try@coldemailco.com"),
        headers=mh,
    ).status_code == 403


# --- transport wall-clock deadline -------------------------------------------
#
# Found via live browser verification, not by these mocks: socket timeouts
# (smtplib/imaplib `timeout=`) bound connect()/recv() *after* a socket exists,
# but never bound getaddrinfo() — a mistyped or unreachable mail host can hang
# a request-handling thread forever. email_transport._run_with_deadline wraps
# every connect+auth attempt in a hard wall-clock ceiling so that can't happen.


def test_transport_deadline_bounds_a_hang(monkeypatch):
    import time

    from app.services import email_transport

    monkeypatch.setattr(email_transport, "_DEADLINE", 0.2)
    start = time.monotonic()
    with pytest.raises(email_transport.EmailTransportError, match="timed out"):
        email_transport._run_with_deadline(
            "stalled.example.com", lambda: time.sleep(5)
        )
    assert time.monotonic() - start < 2


def test_transport_deadline_passes_through_fast_result():
    from app.services import email_transport

    assert email_transport._run_with_deadline("ok.example.com", lambda: 42) == 42


def test_transport_deadline_propagates_original_exception():
    from app.services import email_transport

    def _boom():
        raise ConnectionRefusedError("nope")

    with pytest.raises(ConnectionRefusedError):
        email_transport._run_with_deadline("refused.example.com", _boom)
