"""Client portal access: provisioning a client-role account by invite, and the
two surfaces that account gets — the lead conversation and its own status.

Dedicated org (cp_org), per the isolation convention: the seeded Atlas Reach
org's contact counts feed test_metrics' exact arithmetic.

The load-bearing assertions here are the negative ones. A client portal user is
the only role that is NOT the agency, so every leak has a real-world victim:
another client's leads, or the agency's own internal traffic.
"""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.core import Client
from app.models.crm import Contact
from app.models.sms_outreach import (
    SMS_DIR_IN,
    SMS_DIR_OUT,
    SMS_KIND_CAMPAIGN,
    SMS_KIND_NOTIFICATION,
    SmsAccount,
    SmsMessage,
)


@pytest.fixture(scope="module")
def cp_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Portal Co",
            "email": "owner@portalco.com",
            "password": "portalco-pass-1",
            "full_name": "Portal Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    a = api.post("/api/clients", json={"name": "Alpha HVAC"}, headers=headers).json()
    b = api.post("/api/clients", json={"name": "Beta Spas"}, headers=headers).json()
    return {
        "org": body["organization_id"],
        "headers": headers,
        "client_a": a["id"],
        "client_b": b["id"],
    }


def _invite_portal_user(api, cp_org, email, client_id):
    """Send a client invite and redeem it; returns the new account's headers."""
    inv = api.post(
        "/api/orgs/me/invites",
        json={"email": email, "role": "client", "client_id": client_id},
        headers=cp_org["headers"],
    )
    assert inv.status_code == 201, inv.text
    link = inv.json()["invite_link"]  # no mail transport in tests
    token = link.split("invite=")[-1] if "invite=" in link else link.rsplit("/", 1)[-1]
    acc = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": token, "full_name": "Portal Person", "password": "portal-pw-1"},
    )
    assert acc.status_code == 201, acc.text
    return inv.json(), acc.json()


def test_client_invite_provisions_a_pinned_portal_account(api, cp_org):
    inv, acc = _invite_portal_user(api, cp_org, "ops@alphahvac.com", cp_org["client_a"])
    assert inv["role"] == "client"
    assert inv["client_id"] == cp_org["client_a"]
    assert inv["client_name"] == "Alpha HVAC"

    # The account is pinned to exactly that client...
    assert acc["role"] == "client"
    assert acc["client_id"] == cp_org["client_a"]
    # ...and gets NO organization membership: membership is the team-seat
    # record and drives the org switcher.
    db = SessionLocal()
    try:
        from app.models.core import User
        from app.models.team import OrganizationMembership

        user = db.execute(
            select(User).where(User.email == "ops@alphahvac.com")
        ).scalar_one()
        assert user.client_id == cp_org["client_a"]
        assert (
            db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id
                )
            ).scalar_one_or_none()
            is None
        )
    finally:
        db.close()

    mine = api.get(
        "/api/orgs/mine",
        headers={"Authorization": f"Bearer {acc['access_token']}"},
    )
    assert mine.status_code == 200 and len(mine.json()) == 1


def test_house_crm_and_team_accounts_are_refused(api, cp_org):
    house = api.get("/api/orgs/me/house-client", headers=cp_org["headers"]).json()
    # The house CRM is the agency's OWN prospect pipeline.
    r = api.post(
        "/api/orgs/me/invites",
        json={"email": "nope@x.com", "role": "client", "client_id": house["client_id"]},
        headers=cp_org["headers"],
    )
    assert r.status_code == 400

    # A client invite must name a client.
    r = api.post(
        "/api/orgs/me/invites",
        json={"email": "nope@x.com", "role": "client"},
        headers=cp_org["headers"],
    )
    assert r.status_code == 400

    # Another org's client id is a 404, not a 403 (no existence leak).
    other = SessionLocal()
    try:
        foreign = other.execute(
            select(Client).where(Client.organization_id != cp_org["org"])
        ).scalars().first()
    finally:
        other.close()
    if foreign is not None:
        r = api.post(
            "/api/orgs/me/invites",
            json={"email": "nope@x.com", "role": "client", "client_id": foreign.id},
            headers=cp_org["headers"],
        )
        assert r.status_code == 404

    # An existing TEAM account can't be demoted into a portal user.
    r = api.post(
        "/api/orgs/me/invites",
        json={
            "email": "owner@portalco.com",
            "role": "client",
            "client_id": cp_org["client_a"],
        },
        headers=cp_org["headers"],
    )
    assert r.status_code == 409


def test_portal_user_sees_only_its_own_leads_conversation(api, cp_org):
    _, acc = _invite_portal_user(api, cp_org, "ops@betaspas.com", cp_org["client_b"])
    ch = {"Authorization": f"Bearer {acc['access_token']}"}

    lead_b = api.post(
        "/api/crm/contacts",
        json={
            "client_id": cp_org["client_b"],
            "first_name": "Bea",
            "phone": "+14805550111",
        },
        headers=cp_org["headers"],
    ).json()
    lead_a = api.post(
        "/api/crm/contacts",
        json={
            "client_id": cp_org["client_a"],
            "first_name": "Alan",
            "phone": "+14805550222",
        },
        headers=cp_org["headers"],
    ).json()

    db = SessionLocal()
    try:
        account = SmsAccount(
            organization_id=cp_org["org"],
            name="Portal Co SMS",
            provider="twilio",
            account_sid="AC_portal",
            auth_token_encrypted="x",
            from_number="+14805550999",
            status="active",
        )
        db.add(account)
        db.flush()
        db.add_all(
            [
                SmsMessage(
                    organization_id=cp_org["org"],
                    account_id=account.id,
                    contact_id=lead_b["id"],
                    direction=SMS_DIR_OUT,
                    kind=SMS_KIND_CAMPAIGN,
                    to_number="+14805550111",
                    body="Hi Bea, following up on your quote",
                    status="delivered",
                ),
                SmsMessage(
                    organization_id=cp_org["org"],
                    account_id=account.id,
                    contact_id=lead_b["id"],
                    direction=SMS_DIR_IN,
                    kind="inbound",
                    to_number="+14805550999",
                    body="Yes please call me",
                    status="received",
                ),
                # The agency's own ops alert. Same org, same ledger — and it
                # must NEVER appear in a client's view.
                SmsMessage(
                    organization_id=cp_org["org"],
                    account_id=account.id,
                    contact_id=None,
                    direction=SMS_DIR_OUT,
                    kind=SMS_KIND_NOTIFICATION,
                    to_number="+14807207351",
                    body="*NEW LEAD* internal ops alert",
                    status="sent",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    msgs = api.get(f"/api/crm/contacts/{lead_b['id']}/messages", headers=ch)
    assert msgs.status_code == 200
    bodies = [m["body"] for m in msgs.json()]
    assert bodies == ["Hi Bea, following up on your quote", "Yes please call me"]
    assert all("ops alert" not in b for b in bodies)
    # Ordered oldest-first, and each side is labelled.
    assert [m["direction"] for m in msgs.json()] == ["out", "in"]

    # Another client's lead does not exist as far as this account is concerned.
    assert (
        api.get(f"/api/crm/contacts/{lead_a['id']}/messages", headers=ch).status_code
        == 404
    )

    # The team sees the same conversation through the same endpoint.
    assert (
        api.get(
            f"/api/crm/contacts/{lead_b['id']}/messages", headers=cp_org["headers"]
        ).status_code
        == 200
    )


def test_notification_only_lead_returns_an_empty_conversation(api, cp_org):
    """Defence in depth: notification rows carry contact_id=None today, so the
    contact filter already excludes them. Pin a notification TO the lead and
    prove the kind allowlist independently keeps it out."""
    _, acc = _invite_portal_user(api, cp_org, "ops2@alphahvac.com", cp_org["client_a"])
    ch = {"Authorization": f"Bearer {acc['access_token']}"}
    lead = api.post(
        "/api/crm/contacts",
        json={"client_id": cp_org["client_a"], "first_name": "Nina"},
        headers=cp_org["headers"],
    ).json()

    db = SessionLocal()
    try:
        account = db.execute(
            select(SmsAccount).where(SmsAccount.organization_id == cp_org["org"])
        ).scalars().first()
        db.add(
            SmsMessage(
                organization_id=cp_org["org"],
                account_id=account.id,
                contact_id=lead["id"],
                direction=SMS_DIR_OUT,
                kind=SMS_KIND_NOTIFICATION,
                to_number="+14807207351",
                body="*NEW LEAD* Nina — internal",
                status="sent",
            )
        )
        db.commit()
    finally:
        db.close()

    r = api.get(f"/api/crm/contacts/{lead['id']}/messages", headers=ch)
    assert r.status_code == 200 and r.json() == []


def test_client_status_is_not_qualification(api, cp_org):
    _, acc = _invite_portal_user(api, cp_org, "ops3@alphahvac.com", cp_org["client_a"])
    ch = {"Authorization": f"Bearer {acc['access_token']}"}
    lead = api.post(
        "/api/crm/contacts",
        json={"client_id": cp_org["client_a"], "first_name": "Quinn"},
        headers=cp_org["headers"],
    ).json()

    assert (
        api.put(
            f"/api/crm/contacts/{lead['id']}/client-status",
            json={"status": "bad_lead"},
            headers=ch,
        ).status_code
        == 200
    )
    # The guarantee-tracker flag stays team-only and untouched.
    assert (
        api.put(
            f"/api/crm/contacts/{lead['id']}/qualification",
            json={"qualified": True},
            headers=ch,
        ).status_code
        == 403
    )
    db = SessionLocal()
    try:
        row = db.get(Contact, lead["id"])
        assert row.client_status == "bad_lead"
        assert row.client_status_at is not None
        assert row.qualified_at is None
    finally:
        db.close()

    # Clearing it removes the timestamp too.
    api.put(
        f"/api/crm/contacts/{lead['id']}/client-status", json={"status": None},
        headers=ch,
    )
    db = SessionLocal()
    try:
        row = db.get(Contact, lead["id"])
        assert row.client_status is None and row.client_status_at is None
    finally:
        db.close()
