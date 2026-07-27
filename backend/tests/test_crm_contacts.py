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


def test_csv_import_multi_address_email_cell_takes_first(api, cc_org):
    """A CSV email cell with two comma-joined addresses (two guesses at one
    person) imports the FIRST as the deliverable email and keeps both as
    candidates — not the un-sendable comma-joined string that SMTP 501s."""
    body = {
        "client_id": cc_org["client"],
        "mapping": {"Email": "email", "First": "first_name"},
        "rows": [
            {"First": "Carolina", "Email": "carolina@2atax.com, carolinasanto@2atax.com"},
        ],
    }
    r = api.post("/api/crm/contacts/import", json=body, headers=cc_org["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.first_name == "Carolina").one()
        assert c.email == "carolina@2atax.com"  # first, deliverable
        assert "," not in (c.email or "")
        cands = {x["email"] for x in (c.candidate_emails or [])}
        assert cands == {"carolina@2atax.com", "carolinasanto@2atax.com"}
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


def _import(api, org, rows, mapping, **extra):
    body = {"client_id": org["client"], "mapping": mapping, "rows": rows, **extra}
    r = api.post("/api/crm/contacts/import", json=body, headers=org["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def test_csv_import_normalizes_phone_and_state(api):
    org = _signup(api, "Import Norm", "owner@importnorm.com")
    out = _import(
        api,
        org,
        [{"First": "Pat", "Phone": "(480) 555-0100", "St": "California"}],
        {"First": "first_name", "Phone": "phone", "St": "state"},
    )
    assert out["created"] == 1
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.first_name == "Pat").one()
        assert c.phone == "+14805550100"  # E.164
        assert c.state == "CA"  # full name -> 2-letter
    finally:
        db.close()


def test_csv_import_create_or_update_fills_blanks_never_overwrites(api):
    org = _signup(api, "Upsert Email", "owner@upsertemail.com")
    _import(
        api,
        org,
        [{"Email": "dup@x.com", "First": "Original", "City": "Mesa"}],
        {"Email": "email", "First": "first_name", "City": "city"},
    )
    out = _import(
        api,
        org,
        [{"Email": "DUP@x.com", "First": "Changed", "City": "Tempe", "State": "AZ"}],
        {"Email": "email", "First": "first_name", "City": "city", "State": "state"},
        mode="create_or_update",
    )
    assert out["created"] == 0
    assert out["updated"] == 1
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == "dup@x.com").one()
        assert c.first_name == "Original"  # existing value never overwritten
        assert c.city == "Mesa"  # existing value never overwritten
        assert c.state == "AZ"  # blank filled
    finally:
        db.close()


def test_csv_import_phone_match_when_email_absent(api):
    org = _signup(api, "Upsert Phone", "owner@upsertphone.com")
    _import(
        api,
        org,
        [{"First": "Ivy", "Phone": "480-555-0199"}],
        {"First": "first_name", "Phone": "phone"},
    )
    out = _import(
        api,
        org,
        [{"Phone": "(480) 555-0199", "City": "Gilbert"}],
        {"Phone": "phone", "City": "city"},
        mode="create_or_update",
    )
    assert out["updated"] == 1 and out["created"] == 0
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.first_name == "Ivy").one()
        assert c.city == "Gilbert"
    finally:
        db.close()


def test_csv_import_update_mode_skips_unmatched(api):
    org = _signup(api, "Update Skip", "owner@updateskip.com")
    out = _import(
        api,
        org,
        [{"Email": "nobody@x.com", "First": "Ghost"}],
        {"Email": "email", "First": "first_name"},
        mode="update",
    )
    assert out["created"] == 0 and out["updated"] == 0
    assert out["skipped"] == 1
    assert out["failed"] == []
    db = SessionLocal()
    try:
        assert db.query(Contact).filter(Contact.email == "nobody@x.com").count() == 0
    finally:
        db.close()


def test_csv_import_in_file_duplicate_collapses(api):
    org = _signup(api, "Dup Collapse", "owner@dupcollapse.com")
    out = _import(
        api,
        org,
        [
            {"Email": "same@x.com", "First": "First", "City": "Mesa"},
            {"Email": "same@x.com", "First": "Second", "State": "AZ"},
        ],
        {"Email": "email", "First": "first_name", "City": "city", "State": "state"},
        mode="create_or_update",
    )
    assert out["created"] == 1
    assert out["updated"] == 1  # second row updates the first
    db = SessionLocal()
    try:
        rows = db.query(Contact).filter(Contact.email == "same@x.com").all()
        assert len(rows) == 1
        assert rows[0].first_name == "First"  # never overwritten
        assert rows[0].city == "Mesa"
        assert rows[0].state == "AZ"  # blank filled by row 2
    finally:
        db.close()


def test_csv_import_unchanged_counted(api):
    org = _signup(api, "Unchanged Count", "owner@unchangedcount.com")
    _import(
        api,
        org,
        [{"Email": "fixed@x.com", "First": "Set", "City": "Mesa"}],
        {"Email": "email", "First": "first_name", "City": "city"},
    )
    out = _import(
        api,
        org,
        [{"Email": "fixed@x.com", "First": "Other", "City": "Tempe"}],
        {"Email": "email", "First": "first_name", "City": "city"},
        mode="create_or_update",
    )
    assert out["created"] == 0
    assert out["updated"] == 0
    assert out["unchanged"] == 1  # matched, nothing to fill


def test_csv_import_website_to_company_domain(api):
    org = _signup(api, "Website Domain", "owner@websitedomain.com")
    _import(
        api,
        org,
        [{"Email": "w1@x.com", "Org": "Acme Co", "Site": "https://www.acme.com/contact"}],
        {"Email": "email", "Org": "company", "Site": "website"},
    )
    db = SessionLocal()
    try:
        co = db.query(Company).filter(Company.name == "Acme Co").one()
        assert co.domain == "acme.com"  # scheme/www/path stripped
        cid = co.id
    finally:
        db.close()
    # A later row's website does not overwrite an already-set domain.
    _import(
        api,
        org,
        [{"Email": "w2@x.com", "Org": "Acme Co", "Site": "other.com"}],
        {"Email": "email", "Org": "company", "Site": "website"},
    )
    db = SessionLocal()
    try:
        co = db.get(Company, cid)
        assert co.domain == "acme.com"  # unchanged
    finally:
        db.close()


def test_csv_import_notes_create_internal_activity(api):
    org = _signup(api, "Notes Activity", "owner@notesactivity.com")
    _import(
        api,
        org,
        [{"Email": "note@x.com", "Memo": "Met at the trade show"}],
        {"Email": "email", "Memo": "notes"},
    )
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == "note@x.com").one()
        act = db.query(Activity).filter(Activity.contact_id == c.id).one()
        assert act.type == "note"
        assert act.body == "Met at the trade show"
        assert act.is_internal is True
    finally:
        db.close()


def test_csv_import_over_cap_error_names_column(api):
    org = _signup(api, "Cap Error", "owner@caperror.com")
    out = _import(
        api,
        org,
        [{"Email": "z@x.com", "Billing City": "x" * 200}],
        {"Email": "email", "Billing City": "city"},
    )
    assert out["created"] == 0
    assert len(out["failed"]) == 1
    assert out["failed"][0]["row"] == 0
    assert out["failed"][0]["error"] == "'Billing City' is too long (max 120 characters)"


def test_csv_import_new_field_cap_soft_skips(api, monkeypatch):
    from fastapi import HTTPException

    from app.api import crm as crm_api

    def _at_cap(db, org):
        raise HTTPException(402, "Your plan allows 0 active custom fields.")

    monkeypatch.setattr(
        crm_api.entitlements, "enforce_can_add_custom_field", _at_cap
    )
    org = _signup(api, "Cap Soft", "owner@capsoft.com")
    out = _import(
        api,
        org,
        [{"Email": "cap@x.com", "First": "Al", "Extra": "value"}],
        {"Email": "email", "First": "first_name", "Extra": "new"},
        new_fields=[{"column": "Extra", "label": "Extra", "field_type": "text"}],
    )
    assert out["created"] == 1  # import proceeds
    assert out["created_fields"] == []
    assert out["skipped_fields"] == [
        {"column": "Extra", "reason": "Your plan allows 0 active custom fields."}
    ]
    db = SessionLocal()
    try:
        c = db.query(Contact).filter(Contact.email == "cap@x.com").one()
        assert not (c.custom_fields or {})  # the dropped column stored nothing
    finally:
        db.close()


def test_csv_import_new_field_reused_not_duplicated(api):
    # Re-importing a file whose column maps to "new" must reuse the field the
    # first import created (matched by normalized label), never mint
    # lead_score_2. Guards against a stale client re-sending "new".
    from app.models.crm import CustomFieldDefinition

    org = _signup(api, "Reuse Field", "owner@reusefield.com")
    new_fields = [{"column": "Lead Score", "label": "Lead Score", "field_type": "number"}]
    first = _import(
        api,
        org,
        [{"Email": "rf1@x.com", "Lead Score": "87"}],
        {"Email": "email", "Lead Score": "new"},
        new_fields=new_fields,
    )
    assert len(first["created_fields"]) == 1
    key = first["created_fields"][0]["key"]

    # Same "new" mapping again (as a stale client would send it).
    second = _import(
        api,
        org,
        [{"Email": "rf2@x.com", "Lead Score": "64"}],
        {"Email": "email", "Lead Score": "new"},
        new_fields=new_fields,
    )
    assert second["created_fields"] == []  # reused, not recreated

    db = SessionLocal()
    try:
        defs = (
            db.query(CustomFieldDefinition)
            .filter(
                CustomFieldDefinition.organization_id == org["org"],
                CustomFieldDefinition.label == "Lead Score",
            )
            .all()
        )
        assert len(defs) == 1  # exactly one definition, not two
        c2 = db.query(Contact).filter(Contact.email == "rf2@x.com").one()
        assert c2.custom_fields == {key: 64}  # only the reused key, no _2
    finally:
        db.close()


def test_csv_import_writes_audit_entry(api):
    org = _signup(api, "Import Audit", "owner@importaudit.com")
    _import(
        api,
        org,
        [{"Email": "audited@x.com", "First": "Aud"}],
        {"Email": "email", "First": "first_name"},
        file_name="leads-q3.csv",
    )
    db = SessionLocal()
    try:
        audit = db.query(AuditLogEntry).filter(
            AuditLogEntry.organization_id == org["org"],
            AuditLogEntry.action == "contacts.imported",
        ).one()
        assert audit.entity_type == "contact"
        assert audit.entity_name == "leads-q3.csv"
        summary = {r["field"]: r["after"] for r in audit.diff}
        assert summary["created"] == 1
        assert summary["file"] == "leads-q3.csv"
    finally:
        db.close()


def test_csv_import_default_mode_plain_inserts(api):
    org = _signup(api, "Default Insert", "owner@defaultinsert.com")
    out = _import(
        api,
        org,
        [
            {"Email": "same2@x.com", "First": "One"},
            {"Email": "same2@x.com", "First": "Two"},
        ],
        {"Email": "email", "First": "first_name"},
    )
    # Back-compat: create mode always inserts, no dedupe, even in-file.
    assert out["created"] == 2
    assert out["imported"] == 2
    db = SessionLocal()
    try:
        assert db.query(Contact).filter(Contact.email == "same2@x.com").count() == 2
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


def test_gdpr_export_bundles_everything_and_is_admin_gated(api, cc_org):
    """The export mirrors the deletion cascade's table list, so disclosure
    and erasure can't drift; admin-gated + org-scoped + audit-logged."""
    r = api.post(
        "/api/crm/contacts",
        json={
            "client_id": cc_org["client"],
            "first_name": "Expo",
            "last_name": "Subject",
            "email": "expo.subject@example.com",
        },
        headers=cc_org["headers"],
    )
    cid = r.json()["id"]
    api.post(
        "/api/crm/activities",
        json={"contact_id": cid, "type": "note", "body": "GDPR export test note"},
        headers=cc_org["headers"],
    )

    r = api.get(f"/api/crm/contacts/{cid}/export", headers=cc_org["headers"])
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["contact"]["email"] == "expo.subject@example.com"
    for key in (
        "activities", "tasks", "deals", "landing_events", "conversion_events",
        "email_messages", "email_suppressions", "sms_messages",
        "sms_enrollments", "email_enrollments", "email_threads",
    ):
        assert key in bundle, key
    assert any(
        "GDPR export test note" in (a.get("body") or "")
        for a in bundle["activities"]
    )

    # Cross-org id → 404 (existence not leaked).
    other = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Export Snoop Co",
            "email": "owner@exportsnoop.com",
            "password": "exportsnoop-pass-1",
            "full_name": "Snoop",
        },
    ).json()
    r = api.get(
        f"/api/crm/contacts/{cid}/export",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert r.status_code == 404
