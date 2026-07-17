"""Contact identity/location fields, company linkage, CSV import extensions,
and contact deletion with cascade.

Contact-creating tests run against dedicated orgs (cc_org / cc_org2), never the
seeded Atlas Reach org: the metrics/isolation suites assert over Atlas Reach's
exact contact counts, and rows landing there would silently shift them.
"""

import uuid

import pytest

from app.db import SessionLocal
from app.models.audit import AuditLogEntry
from app.models.crm import Activity, Company, Contact, CrmTask, Deal


def _signup(api, name, email):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": name,
            "email": email,
            "password": "contacts-pass-1",
            "full_name": "CC Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": f"{name} Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


@pytest.fixture(scope="module")
def cc_org(api):
    return _signup(api, "Contacts Co", "owner@contactsco.com")


@pytest.fixture(scope="module")
def cc_org2(api):
    return _signup(api, "Contacts Two", "owner@contactstwo.com")


def _create_contact(api, cc_org, **fields):
    payload = {"client_id": cc_org["client"], **fields}
    r = api.post("/api/crm/contacts", json=payload, headers=cc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_patch_identity_city_state_company(api, cc_org):
    created = _create_contact(
        api,
        cc_org,
        first_name="Dana",
        city="Scottsdale",
        state="AZ",
        zip="85251",
        company_name="Acme LLC",
    )
    assert created["city"] == "Scottsdale"
    assert created["state"] == "AZ"
    assert created["zip"] == "85251"
    assert created["company_name"] == "Acme LLC"
    cid = created["id"]

    # Case-insensitive match reuses the same Company on a second contact.
    other = _create_contact(api, cc_org, first_name="Reed", company_name="acme llc")
    db = SessionLocal()
    try:
        c1 = db.get(Contact, cid)
        c2 = db.get(Contact, other["id"])
        assert c1.company_id and c1.company_id == c2.company_id
        n = db.query(Company).filter(
            Company.client_id == cc_org["client"],
            Company.name.ilike("acme llc"),
        ).count()
        assert n == 1
    finally:
        db.close()

    # Rename identity + move to a new company; job_title round-trips too.
    r = api.patch(
        f"/api/crm/contacts/{cid}",
        json={
            "first_name": "Dana R",
            "city": "Mesa",
            "zip": "85201",
            "company_name": "Beta Inc",
            "job_title": "Marketing Director",
        },
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_name"] == "Dana R"
    assert body["city"] == "Mesa"
    assert body["state"] == "AZ"
    assert body["zip"] == "85201"
    assert body["company_name"] == "Beta Inc"
    assert body["job_title"] == "Marketing Director"

    # Empty job_title clears it.
    r = api.patch(
        f"/api/crm/contacts/{cid}",
        json={"job_title": ""},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["job_title"] is None

    # Empty company_name clears the link.
    r = api.patch(
        f"/api/crm/contacts/{cid}",
        json={"company_name": ""},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] is None
    db = SessionLocal()
    try:
        assert db.get(Contact, cid).company_id is None
    finally:
        db.close()


def test_csv_import_city_state_company_full_name(api, cc_org):
    body = {
        "client_id": cc_org["client"],
        "mapping": {
            "Name": "full_name",
            "Email": "email",
            "Title": "job_title",
            "Town": "city",
            "Region": "state",
            "Postal": "zip",
            "Org": "company",
        },
        "rows": [
            {
                "Name": "John A. Smith",
                "Email": "john@example.com",
                "Title": "Owner",
                "Town": "Tempe",
                "Region": "AZ",
                "Postal": "85281",
                "Org": "Widgets LLC",
            },
            {
                "Name": "Mary Jones",
                "Email": "mary@example.com",
                "Town": "Tempe",
                "Region": "AZ",
                "Org": "widgets llc",
            },
            {
                "Name": "Sam",
                "Email": "sam@example.com",
                "Town": "Gilbert",
                "Region": "AZ",
                "Org": "Other Co",
            },
        ],
    }
    r = api.post(
        "/api/crm/contacts/import", json=body, headers=cc_org["headers"]
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["imported"] == 3
    assert result["failed"] == []

    db = SessionLocal()
    try:
        john = db.query(Contact).filter(Contact.email == "john@example.com").one()
        assert john.first_name == "John"
        assert john.last_name == "A. Smith"
        assert john.job_title == "Owner"
        assert john.city == "Tempe"
        assert john.state == "AZ"
        assert john.zip == "85281"
        # "Widgets LLC" / "widgets llc" dedupe to one Company.
        widgets = db.query(Company).filter(
            Company.client_id == cc_org["client"],
            Company.name.ilike("widgets llc"),
        ).all()
        assert len(widgets) == 1
        mary = db.query(Contact).filter(Contact.email == "mary@example.com").one()
        assert mary.company_id == john.company_id == widgets[0].id
    finally:
        db.close()


def test_csv_import_explicit_name_columns_win_over_full_name(api, cc_org):
    body = {
        "client_id": cc_org["client"],
        "mapping": {
            "Full": "full_name",
            "First": "first_name",
            "Email": "email",
        },
        "rows": [
            {"Full": "Ignore Me", "First": "Explicit", "Email": "explicit@example.com"},
        ],
    }
    r = api.post(
        "/api/crm/contacts/import", json=body, headers=cc_org["headers"]
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == "explicit@example.com").one()
        assert c.first_name == "Explicit"
        # full_name still supplies last_name (not explicitly mapped).
        assert c.last_name == "Me"
    finally:
        db.close()


def test_csv_import_bad_row_isolated_not_500(api, cc_org, monkeypatch):
    """A row that raises while its DB writes flush (e.g. a value longer than a
    Postgres column cap — SQLite ignores caps, so we inject the driver error)
    must land in `failed` and let the good rows import, never abort the whole
    request with a bare 500. Regression for the prod CSV-import "unexpected
    error"."""
    from app.api import crm as crm_api

    real = crm_api.custom_fields_svc.validate_and_merge

    def _boom(db, org_id, contact, custom, enforce_required=True):
        if contact.email == "boom-badrow@example.com":
            raise RuntimeError("value too long for type character varying(20)")
        return real(db, org_id, contact, custom, enforce_required=enforce_required)

    monkeypatch.setattr(crm_api.custom_fields_svc, "validate_and_merge", _boom)

    body = {
        "client_id": cc_org["client"],
        "mapping": {"Email": "email"},
        "rows": [
            {"Email": "goodrow1@example.com"},
            {"Email": "boom-badrow@example.com"},
            {"Email": "goodrow2@example.com"},
        ],
    }
    r = api.post("/api/crm/contacts/import", json=body, headers=cc_org["headers"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["imported"] == 2
    assert [f["row"] for f in out["failed"]] == [1]
    assert "RuntimeError" in out["failed"][0]["error"]
    db = SessionLocal()
    try:
        assert db.query(Contact).filter(Contact.email == "goodrow1@example.com").count() == 1
        assert db.query(Contact).filter(Contact.email == "goodrow2@example.com").count() == 1
        assert db.query(Contact).filter(Contact.email == "boom-badrow@example.com").count() == 0
    finally:
        db.close()


def test_delete_contact_cascades_and_audits(api, cc_org):
    created = _create_contact(api, cc_org, first_name="Gone", last_name="Soon")
    cid = created["id"]
    # An activity, a task, and a deal all pointing at the contact.
    assert api.post(
        "/api/crm/activities",
        json={"contact_id": cid, "type": "note", "body": "hi"},
        headers=cc_org["headers"],
    ).status_code == 201
    assert api.post(
        "/api/crm/tasks",
        json={"client_id": cc_org["client"], "contact_id": cid, "title": "follow up"},
        headers=cc_org["headers"],
    ).status_code == 201
    assert api.post(
        "/api/crm/deals",
        json={"client_id": cc_org["client"], "contact_id": cid, "name": "Deal"},
        headers=cc_org["headers"],
    ).status_code == 201

    r = api.delete(f"/api/crm/contacts/{cid}", headers=cc_org["headers"])
    assert r.status_code == 204, r.text

    assert api.get(
        f"/api/crm/contacts/{cid}", headers=cc_org["headers"]
    ).status_code == 404

    db = SessionLocal()
    try:
        assert db.get(Contact, cid) is None
        assert db.query(Activity).filter(Activity.contact_id == cid).count() == 0
        assert db.query(CrmTask).filter(CrmTask.contact_id == cid).count() == 0
        assert db.query(Deal).filter(Deal.contact_id == cid).count() == 0
        audit = db.query(AuditLogEntry).filter(
            AuditLogEntry.entity_type == "contact",
            AuditLogEntry.action == "contact.deleted",
            AuditLogEntry.entity_external_id == cid,
        ).one()
        assert audit.organization_id == cc_org["org"]
        assert audit.entity_name == "Gone Soon"
    finally:
        db.close()


def test_bulk_delete_skips_out_of_org_ids(api, cc_org, cc_org2):
    a = _create_contact(api, cc_org, first_name="A")["id"]
    b = _create_contact(api, cc_org, first_name="B")["id"]
    # A contact that belongs to another org — must not be touched.
    foreign = _create_contact(api, cc_org2, first_name="Foreign")["id"]

    r = api.post(
        "/api/crm/contacts/bulk-delete",
        json={"contact_ids": [a, b, foreign, str(uuid.uuid4())]},
        headers=cc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 2

    db = SessionLocal()
    try:
        assert db.get(Contact, a) is None
        assert db.get(Contact, b) is None
        assert db.get(Contact, foreign) is not None
    finally:
        db.close()


def test_client_role_cannot_delete(api, client_a_headers, cc_org):
    victim = _create_contact(api, cc_org, first_name="Safe")["id"]
    assert api.delete(
        f"/api/crm/contacts/{victim}", headers=client_a_headers
    ).status_code == 403
    assert api.post(
        "/api/crm/contacts/bulk-delete",
        json={"contact_ids": [victim]},
        headers=client_a_headers,
    ).status_code == 403
    # Still there.
    db = SessionLocal()
    try:
        assert db.get(Contact, victim) is not None
    finally:
        db.close()
