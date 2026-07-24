"""Contact deletion vs the outreach tables + the purge-the-CRM endpoint.

The original delete_contact cascade predated the SMS/email outreach modules,
so any lead ever enrolled or messaged hit a NOT-NULL FK on Postgres and the
delete 500'd ("can't delete all leads"). SQLite doesn't enforce FKs, so these
tests assert ROW-LEVEL outcomes (children deleted, ledgers detached), not
just "no exception" — that keeps them meaningful on the test DB.

Referencing rows are fabricated directly (SQLite ignores the dangling
campaign/account FKs) — the cascade only keys off contact_id, which is what's
under test. Dedicated org (pg_org), per the isolation convention.
"""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.crm import Activity, Contact, ContactListMember, Deal
from app.models.email_outreach import (
    EmailEnrollment,
    EmailMessage,
    EmailSuppression,
    EmailThread,
)
from app.models.audit import AuditLogEntry
from app.models.sms_outreach import SmsEnrollment, SmsMessage


@pytest.fixture(scope="module")
def pg_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Purge Co",
            "email": "owner@purgeco.com",
            "password": "purgeco-pass-1",
            "full_name": "Purge Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Purge Client"}, headers=headers
    ).json()["id"]
    other_client = api.post(
        "/api/clients", json={"name": "Purge Other Client"}, headers=headers
    ).json()["id"]
    return {
        "org": body["organization_id"],
        "headers": headers,
        "client": client_id,
        "other_client": other_client,
    }


def _mk_contact(pg_org, api, client_id=None, **extra):
    payload = {
        "client_id": client_id or pg_org["client"],
        "first_name": extra.pop("first", "Purgee"),
    }
    payload.update(extra)
    r = api.post("/api/crm/contacts", json=payload, headers=pg_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _attach_outreach_refs(org_id: str, contact_id: str) -> dict:
    """Fabricate every outreach row class that references a contact — the
    exact shapes that made enrolled leads undeletable before the fix."""
    with SessionLocal() as db:
        sms_enr = SmsEnrollment(
            organization_id=org_id, campaign_id="sms-camp-x", contact_id=contact_id
        )
        thread = EmailThread(
            organization_id=org_id, account_id="eml-acct-x", contact_id=contact_id
        )
        eml_enr = EmailEnrollment(
            organization_id=org_id, campaign_id="eml-camp-x", contact_id=contact_id
        )
        db.add_all([sms_enr, thread, eml_enr])
        db.flush()
        sms_msg = SmsMessage(
            organization_id=org_id,
            account_id="sms-acct-x",
            contact_id=contact_id,
            enrollment_id=sms_enr.id,
            direction="out",
            to_number="+14805550000",
            body="hello",
            status="sent",
        )
        eml_msg = EmailMessage(
            organization_id=org_id,
            account_id="eml-acct-x",
            contact_id=contact_id,
            thread_id=thread.id,
            direction="out",
            status="sent",
        )
        suppression = EmailSuppression(
            organization_id=org_id,
            email=f"{contact_id[:8]}@purge.example.com",
            reason="unsubscribe",
            contact_id=contact_id,
        )
        db.add_all([sms_msg, eml_msg, suppression])
        db.commit()
        return {
            "sms_msg": sms_msg.id,
            "eml_msg": eml_msg.id,
            "suppression": suppression.id,
        }


def _assert_refs_cleared(org_id: str, contact_id: str, ids: dict) -> None:
    with SessionLocal() as db:
        assert db.get(Contact, contact_id) is None
        assert (
            db.execute(
                select(SmsEnrollment.id).where(SmsEnrollment.contact_id == contact_id)
            ).first()
            is None
        )
        assert (
            db.execute(
                select(EmailEnrollment.id).where(
                    EmailEnrollment.contact_id == contact_id
                )
            ).first()
            is None
        )
        assert (
            db.execute(
                select(EmailThread.id).where(EmailThread.contact_id == contact_id)
            ).first()
            is None
        )
        # Ledgers survive, detached from the contact/enrollment/thread.
        sms_msg = db.get(SmsMessage, ids["sms_msg"])
        assert sms_msg is not None
        assert sms_msg.contact_id is None
        assert sms_msg.enrollment_id is None
        eml_msg = db.get(EmailMessage, ids["eml_msg"])
        assert eml_msg is not None
        assert eml_msg.contact_id is None
        assert eml_msg.thread_id is None
        # Compliance: the suppression row outlives the contact.
        sup = db.get(EmailSuppression, ids["suppression"])
        assert sup is not None
        assert sup.contact_id is None


def test_delete_enrolled_contact_cascades_outreach_tables(pg_org, api):
    cid = _mk_contact(pg_org, api, first="EnrolledLead", phone="4805551001")
    ids = _attach_outreach_refs(pg_org["org"], cid)
    r = api.delete(f"/api/crm/contacts/{cid}", headers=pg_org["headers"])
    assert r.status_code == 204, r.text
    _assert_refs_cleared(pg_org["org"], cid, ids)


def test_purge_requires_typed_confirm(pg_org, api):
    r = api.post(
        "/api/crm/contacts/purge",
        json={"client_id": pg_org["client"], "confirm": "delete"},
        headers=pg_org["headers"],
    )
    assert r.status_code == 400
    assert "DELETE" in r.json()["detail"]


def test_purge_deletes_every_lead_and_isolates_other_clients(pg_org, api):
    # Client A: three leads — one fully enrolled/messaged, one in a list
    # with a deal + note, one plain. Client B: one lead that must survive.
    enrolled = _mk_contact(pg_org, api, first="PurgeEnrolled", phone="4805551002")
    ids = _attach_outreach_refs(pg_org["org"], enrolled)
    listed = _mk_contact(pg_org, api, first="PurgeListed", phone="4805551003")
    plain = _mk_contact(pg_org, api, first="PurgePlain", phone="4805551004")
    survivor = _mk_contact(
        pg_org, api, client_id=pg_org["other_client"], first="Survivor"
    )
    lst = api.post(
        "/api/crm/lists",
        json={"client_id": pg_org["client"], "name": "Purge List"},
        headers=pg_org["headers"],
    ).json()
    api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [listed]},
        headers=pg_org["headers"],
    )
    api.post(
        "/api/crm/deals",
        json={"client_id": pg_org["client"], "contact_id": listed, "name": "Deal"},
        headers=pg_org["headers"],
    )

    r = api.post(
        "/api/crm/contacts/purge",
        json={"client_id": pg_org["client"], "confirm": "DELETE"},
        headers=pg_org["headers"],
    )
    assert r.status_code == 200, r.text
    # >= : test_delete_enrolled_contact's client may hold other rows if tests
    # ever share contacts; assert the three we created are definitely counted.
    assert r.json()["deleted"] >= 3

    _assert_refs_cleared(pg_org["org"], enrolled, ids)
    with SessionLocal() as db:
        for cid in (listed, plain):
            assert db.get(Contact, cid) is None
        assert (
            db.execute(
                select(ContactListMember.id).where(
                    ContactListMember.contact_id == listed
                )
            ).first()
            is None
        )
        assert (
            db.execute(select(Deal.id).where(Deal.contact_id == listed)).first()
            is None
        )
        assert (
            db.execute(
                select(Activity.id).where(Activity.contact_id == listed)
            ).first()
            is None
        )
        # The other client's lead is untouched.
        assert db.get(Contact, survivor) is not None
        # One audit entry records the purge with the count.
        audit = db.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.organization_id == pg_org["org"],
                AuditLogEntry.action == "contacts.purged",
            )
        ).scalars().all()
        assert len(audit) == 1
        assert any(d.get("field") == "deleted" for d in audit[0].diff)

    # Idempotent: a second purge finds nothing.
    again = api.post(
        "/api/crm/contacts/purge",
        json={"client_id": pg_org["client"], "confirm": "DELETE"},
        headers=pg_org["headers"],
    )
    assert again.json()["deleted"] == 0


def test_purge_cross_org_client_404(pg_org, api, team_headers):
    other_org_client = api.post(
        "/api/clients", json={"name": "Foreign Purge Client"}, headers=team_headers
    ).json()["id"]
    r = api.post(
        "/api/crm/contacts/purge",
        json={"client_id": other_org_client, "confirm": "DELETE"},
        headers=pg_org["headers"],
    )
    assert r.status_code == 404
