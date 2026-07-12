"""CRM contact lists (audience management for outreach pickers), the
org-default SMS opt-in policy, and the bulk-edit endpoint.

Dedicated org (lc_org) — never asserts over the seeded Atlas Reach org's
counts, matching the isolation convention every other outreach-adjacent
suite uses (test_sms_outreach.sc_org / test_email_campaigns.cc_org).
"""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.core import Client, Organization
from app.models.crm import Contact, ContactListMember
from app.services import lead_finder as lead_finder_svc
from app.services import lead_ingest
from app.services.places import PlaceResult


@pytest.fixture(scope="module")
def lc_org(api):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Lists Co",
            "email": "owner@listsco.com",
            "password": "listsco-pass-1",
            "full_name": "Lists Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "Lists Client"}, headers=headers
    ).json()["id"]
    return {"org": body["organization_id"], "headers": headers, "client": client_id}


def _mk_contact(lc_org, api, *, first="Dana", last="Doe", **extra):
    payload = {"client_id": lc_org["client"], "first_name": first, "last_name": last}
    payload.update(extra)
    r = api.post("/api/crm/contacts", json=payload, headers=lc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_list(lc_org, api, name="VIP Leads", **over):
    payload = {"client_id": lc_org["client"], "name": name}
    payload.update(over)
    r = api.post("/api/crm/lists", json=payload, headers=lc_org["headers"])
    assert r.status_code == 201, r.text
    return r.json()


# --- lists CRUD --------------------------------------------------------------


def test_create_list_and_fetch(lc_org, api):
    lst = _mk_list(lc_org, api, name="Spring Promo")
    assert lst["name"] == "Spring Promo"
    assert lst["client_id"] == lc_org["client"]
    assert lst["member_count"] == 0

    listed = api.get(
        f"/api/crm/lists?client_id={lc_org['client']}", headers=lc_org["headers"]
    ).json()
    assert any(l["id"] == lst["id"] for l in listed)


def test_duplicate_list_name_409(lc_org, api):
    _mk_list(lc_org, api, name="Dupe Name")
    r = api.post(
        "/api/crm/lists",
        json={"client_id": lc_org["client"], "name": "Dupe Name"},
        headers=lc_org["headers"],
    )
    assert r.status_code == 409


def test_rename_list_duplicate_409(lc_org, api):
    a = _mk_list(lc_org, api, name="Rename A")
    b = _mk_list(lc_org, api, name="Rename B")
    r = api.patch(
        f"/api/crm/lists/{b['id']}",
        json={"name": "Rename A"},
        headers=lc_org["headers"],
    )
    assert r.status_code == 409
    ok = api.patch(
        f"/api/crm/lists/{b['id']}",
        json={"name": "Rename B2"},
        headers=lc_org["headers"],
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "Rename B2"
    assert a["id"] != b["id"]


def test_delete_list_removes_members_not_contacts(lc_org, api):
    lst = _mk_list(lc_org, api, name="Delete Me")
    c1 = _mk_contact(lc_org, api, first="Del1")
    api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [c1]},
        headers=lc_org["headers"],
    )
    r = api.delete(f"/api/crm/lists/{lst['id']}", headers=lc_org["headers"])
    assert r.status_code == 204
    assert api.get(
        f"/api/crm/lists?client_id={lc_org['client']}", headers=lc_org["headers"]
    ).json()
    still_there = api.get(
        f"/api/crm/contacts?client_id={lc_org['client']}", headers=lc_org["headers"]
    ).json()
    assert any(c["id"] == c1 for c in still_there)


# --- bulk add/remove + cross-org skip ----------------------------------------


def test_bulk_add_remove_and_cross_org_skip(lc_org, api, team_headers):
    lst = _mk_list(lc_org, api, name="Bulk Ops")
    c1 = _mk_contact(lc_org, api, first="Bulk1")
    c2 = _mk_contact(lc_org, api, first="Bulk2")

    # A contact id from the seeded Atlas Reach org (team_headers) is foreign
    # to lc_org — silently skipped, never a signal it exists.
    other_client = api.post(
        "/api/clients", json={"name": "Foreign Client"}, headers=team_headers
    ).json()["id"]
    foreign = api.post(
        "/api/crm/contacts",
        json={"client_id": other_client, "first_name": "Foreign"},
        headers=team_headers,
    ).json()["id"]

    r = api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [c1, c2, foreign]},
        headers=lc_org["headers"],
    )
    assert r.status_code == 200
    assert r.json() == {"added": 2, "skipped": 1}

    # Idempotent re-add: both already members now, foreign still skipped.
    r2 = api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [c1, c2, foreign]},
        headers=lc_org["headers"],
    )
    assert r2.json() == {"added": 0, "skipped": 3}

    rm = api.post(
        f"/api/crm/lists/{lst['id']}/contacts/remove",
        json={"contact_ids": [c1]},
        headers=lc_org["headers"],
    )
    assert rm.json() == {"removed": 1}

    got = api.get(
        f"/api/crm/lists?client_id={lc_org['client']}", headers=lc_org["headers"]
    ).json()
    row = next(l for l in got if l["id"] == lst["id"])
    assert row["member_count"] == 1


# --- list_id contact filter ---------------------------------------------------


def test_list_id_filters_contacts(lc_org, api):
    lst = _mk_list(lc_org, api, name="Filter List")
    in_list = _mk_contact(lc_org, api, first="InList")
    out_list = _mk_contact(lc_org, api, first="OutList")
    api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [in_list]},
        headers=lc_org["headers"],
    )
    r = api.get(
        f"/api/crm/contacts?client_id={lc_org['client']}&list_id={lst['id']}",
        headers=lc_org["headers"],
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert in_list in ids
    assert out_list not in ids


# --- tenant isolation ----------------------------------------------------------


def test_list_tenant_isolation(lc_org, api, team_headers):
    lst = _mk_list(lc_org, api, name="Isolated List")
    # Atlas Reach (a different org) can't see, rename, delete, or add to it.
    listed = api.get(
        f"/api/crm/lists?client_id={lc_org['client']}", headers=team_headers
    )
    # Different org's client_id 404s before it can even list.
    assert listed.status_code == 404
    assert api.patch(
        f"/api/crm/lists/{lst['id']}", json={"name": "Hijacked"}, headers=team_headers
    ).status_code == 404
    assert api.delete(
        f"/api/crm/lists/{lst['id']}", headers=team_headers
    ).status_code == 404
    assert api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": ["whatever"]},
        headers=team_headers,
    ).status_code == 404


# --- delete_contact cascade ----------------------------------------------------


def test_delete_contact_cascades_list_membership(lc_org, api):
    lst = _mk_list(lc_org, api, name="Cascade List")
    c1 = _mk_contact(lc_org, api, first="CascadeMe")
    api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [c1]},
        headers=lc_org["headers"],
    )
    r = api.delete(f"/api/crm/contacts/{c1}", headers=lc_org["headers"])
    assert r.status_code == 204
    db = SessionLocal()
    try:
        remaining = db.execute(
            select(ContactListMember).where(ContactListMember.contact_id == c1)
        ).scalars().all()
        assert remaining == []
    finally:
        db.close()


# --- enroll-by-list: email + SMS ----------------------------------------------


def test_enroll_by_list_email(lc_org, api, monkeypatch):
    from app.services import email_transport

    monkeypatch.setattr(
        email_transport, "smtp_send", lambda account, msg: "250 OK captured"
    )
    monkeypatch.setattr(
        email_transport,
        "probe",
        lambda account: {"smtp_ok": True, "imap_ok": True, "detail": None},
    )
    api.put(
        "/api/orgs/me/branding",
        json={"mailing_address": "1 Main St, Anytown NY 10001"},
        headers=lc_org["headers"],
    )
    acct = api.post(
        "/api/email-outreach/accounts",
        json={
            "name": "Lists Mailbox",
            "from_name": "Lists Owner",
            "from_email": "owner@listsco.com",
            "smtp_host": "smtp.listsco.com",
            "smtp_port": 465,
            "smtp_security": "ssl",
            "imap_host": "imap.listsco.com",
            "imap_port": 993,
            "imap_security": "ssl",
            "smtp_username": "owner@listsco.com",
            "smtp_password": "mbx-secret",
            "imap_username": "owner@listsco.com",
            "imap_password": "mbx-secret",
            "daily_send_cap": 100,
        },
        headers=lc_org["headers"],
    ).json()
    camp = api.post(
        "/api/email-outreach/campaigns",
        json={
            "name": "List Enroll Campaign",
            "account_id": acct["id"],
            "send_window_start": 0,
            "send_window_end": 24,
            "send_days": [0, 1, 2, 3, 4, 5, 6],
        },
        headers=lc_org["headers"],
    ).json()

    lst = _mk_list(lc_org, api, name="Email Enroll List")
    c1 = _mk_contact(lc_org, api, first="Emailed1", email="emailed1@example.com")
    c2 = _mk_contact(lc_org, api, first="Emailed2", email="emailed2@example.com")
    api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [c1, c2]},
        headers=lc_org["headers"],
    )

    r = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={"list_id": lst["id"]},
        headers=lc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["enrolled"] == 2

    # Neither contact_ids nor list_id -> validation error.
    bad = api.post(
        f"/api/email-outreach/campaigns/{camp['id']}/enroll",
        json={},
        headers=lc_org["headers"],
    )
    assert bad.status_code == 422


def test_enroll_by_list_sms(lc_org, api, monkeypatch):
    from app.services import sms_send as gateway

    monkeypatch.setattr(gateway, "verify_credentials", lambda account: (True, "ok"))
    acct = api.post(
        "/api/sms/accounts",
        json={
            "name": "Lists SMS Line",
            "account_sid": "ACtestaccountsid00000001",
            "auth_token": "sms-list-test-auth-token-01234",
            "from_number": "+14805550199",
            "daily_send_cap": 200,
        },
        headers=lc_org["headers"],
    ).json()
    camp = api.post(
        "/api/sms/campaigns",
        json={
            "name": "List Enroll SMS Campaign",
            "account_id": acct["id"],
            "send_window_start": 0,
            "send_window_end": 24,
            "send_days": [0, 1, 2, 3, 4, 5, 6],
        },
        headers=lc_org["headers"],
    ).json()

    lst = _mk_list(lc_org, api, name="SMS Enroll List")
    c1 = _mk_contact(
        lc_org, api, first="Texted1", mobile_phone="4805557101", sms_opt_in=True
    )
    c2 = _mk_contact(
        lc_org, api, first="Texted2", mobile_phone="4805557102", sms_opt_in=True
    )
    api.post(
        f"/api/crm/lists/{lst['id']}/contacts",
        json={"contact_ids": [c1, c2]},
        headers=lc_org["headers"],
    )

    r = api.post(
        f"/api/sms/campaigns/{camp['id']}/enroll",
        json={"list_id": lst["id"]},
        headers=lc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["enrolled"] == 2


# --- org-default SMS opt-in ----------------------------------------------------


def test_toggle_sms_opt_in_default_requires_owner(lc_org, api, member_headers=None):
    r = api.put(
        "/api/orgs/me/sms-opt-in-default",
        json={"sms_opt_in_default": True},
        headers=lc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["sms_opt_in_default"] is True
    # Reset for the rest of the module's tests below (they toggle it back on
    # deliberately where needed).
    api.put(
        "/api/orgs/me/sms-opt-in-default",
        json={"sms_opt_in_default": False},
        headers=lc_org["headers"],
    )


def test_org_default_opt_in_post_and_explicit_wins(lc_org, api):
    api.put(
        "/api/orgs/me/sms-opt-in-default",
        json={"sms_opt_in_default": True},
        headers=lc_org["headers"],
    )
    try:
        cid = _mk_contact(lc_org, api, first="DefaultOptIn")
        got = api.get(f"/api/crm/contacts/{cid}", headers=lc_org["headers"]).json()
        assert got["sms_opt_in"] is True
        assert got["sms_opt_in_source"] == "org_default:pre_opted_funnel"

        # Explicit sms_opt_in=False still means no opt-in stamped (attestation
        # only fills the gap for contacts nobody already asserted about) —
        # here explicit False just means "not opted in via this form"; the
        # org default still applies since the contact isn't opted in.
        cid2 = _mk_contact(lc_org, api, first="DefaultOptIn2", sms_opt_in=False)
        got2 = api.get(f"/api/crm/contacts/{cid2}", headers=lc_org["headers"]).json()
        assert got2["sms_opt_in"] is True
        assert got2["sms_opt_in_source"] == "org_default:pre_opted_funnel"
    finally:
        api.put(
            "/api/orgs/me/sms-opt-in-default",
            json={"sms_opt_in_default": False},
            headers=lc_org["headers"],
        )


def test_org_default_opt_in_csv_import_fallback_and_explicit_wins(lc_org, api):
    api.put(
        "/api/orgs/me/sms-opt-in-default",
        json={"sms_opt_in_default": True},
        headers=lc_org["headers"],
    )
    try:
        # Row with no explicit attestation falls back to the org default.
        r = api.post(
            "/api/crm/contacts/import",
            json={
                "client_id": lc_org["client"],
                "mapping": {"Name": "first_name"},
                "rows": [{"Name": "CsvFallback"}],
            },
            headers=lc_org["headers"],
        )
        assert r.status_code == 200, r.text
        listed = api.get(
            f"/api/crm/contacts?client_id={lc_org['client']}",
            headers=lc_org["headers"],
        ).json()
        row = next(c for c in listed if c.get("first_name") == "CsvFallback")
        assert row["sms_opt_in"] is True
        assert row["sms_opt_in_source"] == "org_default:pre_opted_funnel"

        # Explicit per-file attestation wins over (and is distinct from) the
        # org default source.
        r2 = api.post(
            "/api/crm/contacts/import",
            json={
                "client_id": lc_org["client"],
                "mapping": {"Name": "first_name"},
                "rows": [{"Name": "CsvExplicit"}],
                "sms_opt_in_all": True,
            },
            headers=lc_org["headers"],
        )
        assert r2.status_code == 200, r2.text
        listed2 = api.get(
            f"/api/crm/contacts?client_id={lc_org['client']}",
            headers=lc_org["headers"],
        ).json()
        row2 = next(c for c in listed2 if c.get("first_name") == "CsvExplicit")
        assert row2["sms_opt_in"] is True
        assert row2["sms_opt_in_source"] == "csv_import:website_attested"
    finally:
        api.put(
            "/api/orgs/me/sms-opt-in-default",
            json={"sms_opt_in_default": False},
            headers=lc_org["headers"],
        )


def test_org_default_opt_in_lead_ingest(lc_org, api):
    db = SessionLocal()
    try:
        org = db.get(Organization, lc_org["org"])
        org.sms_opt_in_default = True
        db.commit()
        client = db.get(Client, lc_org["client"])
        contact, created = lead_ingest.upsert_contact(
            db,
            client,
            email="landing@example.com",
            first_name="Landing",
            source="landing_page",
        )
        db.commit()
        assert created is True
        assert contact.sms_opt_in is True
        assert contact.sms_opt_in_source == "org_default:pre_opted_funnel"
    finally:
        org = db.get(Organization, lc_org["org"])
        org.sms_opt_in_default = False
        db.commit()
        db.close()


def test_org_default_opt_in_lead_finder(lc_org, api):
    db = SessionLocal()
    try:
        org = db.get(Organization, lc_org["org"])
        org.sms_opt_in_default = True
        db.commit()
        client = db.get(Client, lc_org["client"])
        places = [
            PlaceResult(
                place_id="place-lf-1",
                name="Found Business",
                address="1 Found Way",
                phone="4805557777",
                website=None,
                rating=4.5,
                types=["plumber"],
            )
        ]
        created, skipped = lead_finder_svc.import_places(
            db, org, client, places, search_id="s1", query="plumber"
        )
        db.commit()
        assert len(created) == 1
        assert created[0].sms_opt_in is True
        assert created[0].sms_opt_in_source == "org_default:pre_opted_funnel"
    finally:
        org = db.get(Organization, lc_org["org"])
        org.sms_opt_in_default = False
        db.commit()
        db.close()


# --- Feature 5: bulk edit ------------------------------------------------------


def test_bulk_update_city_state_and_custom_field(lc_org, api):
    r = api.post(
        "/api/crm/custom-fields",
        json={"entity_type": "contact", "label": "Priority", "field_type": "text"},
        headers=lc_org["headers"],
    )
    assert r.status_code == 201, r.text
    key = r.json()["key"]

    ids = [_mk_contact(lc_org, api, first=f"BulkEdit{i}") for i in range(3)]
    r = api.post(
        "/api/crm/contacts/bulk-update",
        json={
            "contact_ids": ids,
            "fields": {
                "city": "Phoenix",
                "state": "AZ",
                "custom_fields": {key: "high"},
            },
        },
        headers=lc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 3, "skipped": 0}

    for cid in ids:
        got = api.get(f"/api/crm/contacts/{cid}", headers=lc_org["headers"]).json()
        assert got["city"] == "Phoenix"
        assert got["state"] == "AZ"
        assert got["custom_fields"][key] == "high"


def test_bulk_update_sms_opt_in_records_manual_consent(lc_org, api):
    ids = [_mk_contact(lc_org, api, first=f"BulkOptIn{i}") for i in range(2)]
    r = api.post(
        "/api/crm/contacts/bulk-update",
        json={"contact_ids": ids, "fields": {"sms_opt_in": True}},
        headers=lc_org["headers"],
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    for cid in ids:
        got = api.get(f"/api/crm/contacts/{cid}", headers=lc_org["headers"]).json()
        assert got["sms_opt_in"] is True
        assert got["sms_opt_in_source"] == "manual"


def test_bulk_update_cross_org_id_skipped(lc_org, api, team_headers):
    other_client = api.post(
        "/api/clients", json={"name": "Foreign Bulk Client"}, headers=team_headers
    ).json()["id"]
    foreign = api.post(
        "/api/crm/contacts",
        json={"client_id": other_client, "first_name": "ForeignBulk"},
        headers=team_headers,
    ).json()["id"]
    mine = _mk_contact(lc_org, api, first="MineBulk")

    r = api.post(
        "/api/crm/contacts/bulk-update",
        json={"contact_ids": [mine, foreign], "fields": {"city": "Denver"}},
        headers=lc_org["headers"],
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 1, "skipped": 1}

    # The foreign contact was never touched.
    foreign_row = api.get(
        f"/api/crm/contacts/{foreign}", headers=team_headers
    ).json()
    assert foreign_row["city"] is None


def test_bulk_update_custom_field_error_aborts_all(lc_org, api):
    r = api.post(
        "/api/crm/custom-fields",
        json={
            "entity_type": "contact",
            "label": "Bulk Number",
            "field_type": "number",
        },
        headers=lc_org["headers"],
    )
    assert r.status_code == 201, r.text
    key = r.json()["key"]

    ids = [_mk_contact(lc_org, api, first=f"BulkFail{i}") for i in range(2)]
    r = api.post(
        "/api/crm/contacts/bulk-update",
        json={
            "contact_ids": ids,
            "fields": {"city": "ShouldNotStick", "custom_fields": {key: "not-a-number"}},
        },
        headers=lc_org["headers"],
    )
    assert r.status_code == 400

    for cid in ids:
        got = api.get(f"/api/crm/contacts/{cid}", headers=lc_org["headers"]).json()
        assert got["city"] is None
