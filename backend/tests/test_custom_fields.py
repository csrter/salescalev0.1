"""Phase 14 — Custom CRM fields.

Covers the acceptance checks:
- an org creates one field of every type, populates them, and sees them
  render, filter, and sort in list views;
- rename preserves values; archive hides but preserves; delete scrubs and
  says so first; removed select options never silently destroy data;
- CSV import maps to custom fields (incl. create-during-mapping), and bad
  values report per-row instead of failing the file;
- a visible_to_clients=false field never appears in any Client-portal render
  or client-scoped API response;
- two Organizations define identically-labelled fields with zero interaction;
  definitions are org-isolated.
"""

import pytest

CLIENT_KEY = "client_a"


def _mkfield(api, headers, **body):
    body.setdefault("entity_type", "contact")
    resp = api.post("/api/crm/custom-fields", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mkcontact(api, headers, client_id, **body):
    body["client_id"] = client_id
    resp = api.post("/api/crm/contacts", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def cf_client(api, team_headers):
    """A dedicated client so contacts created here don't perturb other suites
    (test_metrics asserts exact arithmetic on the seeded clients)."""
    resp = api.post(
        "/api/clients", json={"name": "CustomFields Co"}, headers=team_headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture(scope="module")
def cf_client_headers(api, cf_client, seeded):
    """A client-role login pinned to cf_client — built directly like conftest's
    client user, so the client-visibility test never has to write to a seeded
    client that metrics depends on."""
    from app.db import SessionLocal
    from app.models.core import ROLE_CLIENT, User
    from app.security import hash_password

    db = SessionLocal()
    user = User(
        organization_id=seeded["org"],
        email="portal@customfields.co",
        hashed_password=hash_password("portal-pass"),
        full_name="CF Portal",
        role=ROLE_CLIENT,
        client_id=cf_client,
    )
    db.add(user)
    db.commit()
    db.close()
    resp = api.post(
        "/api/auth/login",
        json={"email": "portal@customfields.co", "password": "portal-pass"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


_fresh_counter = [0]


def _fresh_org(api):
    """A brand-new org (starter tier) with one client — used by tests whose
    field defs would otherwise pollute the shared org (required fields apply
    org-wide to manual contact creation)."""
    _fresh_counter[0] += 1
    n = _fresh_counter[0]
    resp = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": f"Fresh CF Org {n}",
            "email": f"owner@freshcf{n}.com",
            "password": "fresh-pass-123",
            "full_name": "Fresh Owner",
        },
    )
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    cr = api.post("/api/clients", json={"name": "Fresh Client"}, headers=headers)
    assert cr.status_code == 201, cr.text
    return headers, cr.json()["id"]


# --- Part A: every type populates, renders, filters, sorts ---


def test_every_field_type_roundtrips_and_filters(api, team_headers, cf_client):
    text = _mkfield(api, team_headers, label="Account Notes", field_type="text")
    number = _mkfield(api, team_headers, label="Number of Trucks", field_type="number")
    boolean = _mkfield(api, team_headers, label="Under Contract", field_type="boolean")
    date = _mkfield(api, team_headers, label="Renewal Date", field_type="date")
    url = _mkfield(api, team_headers, label="Website", field_type="url")
    select = _mkfield(
        api,
        team_headers,
        label="Tier",
        field_type="select",
        options=[{"label": "Gold"}, {"label": "Silver"}],
    )
    multi = _mkfield(
        api,
        team_headers,
        label="Regions",
        field_type="multi_select",
        options=[{"label": "Northeast"}, {"label": "Southwest"}],
    )

    # Keys are generated from labels, never colliding with system fields.
    assert number["key"] == "number_of_trucks"
    gold = select["options"][0]["key"]
    silver = select["options"][1]["key"]
    ne = multi["options"][0]["key"]
    sw = multi["options"][1]["key"]

    big = _mkcontact(
        api,
        team_headers,
        cf_client,
        first_name="Big",
        custom_fields={
            text["key"]: "  whale account  ",
            number["key"]: "20",  # string coerces to number
            boolean["key"]: "yes",
            date["key"]: "2026-09-01",
            url["key"]: "acme.com",  # scheme added
            select["key"]: "Gold",  # label resolves to key
            multi["key"]: ["Northeast", "Southwest"],
        },
    )
    small = _mkcontact(
        api,
        team_headers,
        cf_client,
        first_name="Small",
        custom_fields={
            number["key"]: 3,
            select["key"]: silver,
            multi["key"]: [ne],
        },
    )

    # Stored/coerced shapes.
    assert big["custom_fields"][text["key"]] == "whale account"
    assert big["custom_fields"][number["key"]] == 20
    assert big["custom_fields"][boolean["key"]] is True
    assert big["custom_fields"][url["key"]].startswith("https://")
    assert big["custom_fields"][select["key"]] == gold
    assert set(big["custom_fields"][multi["key"]]) == {ne, sw}

    # Filter: trucks >= 5 returns only Big.
    import json as _json

    filt = _json.dumps([{"key": number["key"], "op": "gte", "value": 5}])
    resp = api.get(
        f"/api/crm/contacts?client_id={cf_client}&cf_filter={filt}",
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    names = {c["first_name"] for c in resp.json()}
    assert names == {"Big"}

    # Sort ascending by number: Small (3) before Big (20).
    resp = api.get(
        f"/api/crm/contacts?client_id={cf_client}&sort={number['key']}&sort_dir=asc",
        headers=team_headers,
    )
    ordered = [c["first_name"] for c in resp.json() if c["id"] in (big["id"], small["id"])]
    assert ordered == ["Small", "Big"]

    # Select any-of and multi_select any-of.
    filt = _json.dumps([{"key": select["key"], "op": "any_of", "value": [gold]}])
    resp = api.get(
        f"/api/crm/contacts?client_id={cf_client}&cf_filter={filt}", headers=team_headers
    )
    assert {c["first_name"] for c in resp.json()} == {"Big"}

    filt = _json.dumps([{"key": multi["key"], "op": "any_of", "value": [sw]}])
    resp = api.get(
        f"/api/crm/contacts?client_id={cf_client}&cf_filter={filt}", headers=team_headers
    )
    assert {c["first_name"] for c in resp.json()} == {"Big"}


def test_unknown_key_and_bad_value_rejected(api, team_headers, cf_client):
    resp = api.post(
        "/api/crm/contacts",
        json={"client_id": cf_client, "first_name": "X", "custom_fields": {"nope": 1}},
        headers=team_headers,
    )
    assert resp.status_code == 400
    assert "unknown" in resp.json()["detail"].lower()

    num = _mkfield(api, team_headers, label="Score", field_type="number")
    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": cf_client,
            "first_name": "Y",
            "custom_fields": {num["key"]: "not-a-number"},
        },
        headers=team_headers,
    )
    assert resp.status_code == 400
    assert "number" in resp.json()["detail"].lower()


def test_required_enforced_on_create_only(api):
    # Own org: a required field applies org-wide to manual creates, so isolate it.
    headers, client_id = _fresh_org(api)
    req = _mkfield(api, headers, label="Mandatory Ref", field_type="text", required=True)
    # Missing required -> rejected on create.
    resp = api.post(
        "/api/crm/contacts",
        json={"client_id": client_id, "first_name": "NoRef"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()
    # With value -> ok. Then a partial PATCH not touching it is allowed.
    c = _mkcontact(
        api, headers, client_id, first_name="HasRef", custom_fields={req["key"]: "REF-1"}
    )
    resp = api.patch(
        f"/api/crm/contacts/{c['id']}", json={"phone": "555-1000"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["custom_fields"][req["key"]] == "REF-1"


# --- Part B: lifecycle ---


def test_rename_is_label_only_and_preserves_values(api, team_headers, cf_client):
    f = _mkfield(api, team_headers, label="Old Label", field_type="text")
    key = f["key"]
    c = _mkcontact(
        api, team_headers, cf_client, first_name="Keep", custom_fields={key: "v1"}
    )
    resp = api.patch(
        f"/api/crm/custom-fields/{f['id']}",
        json={"label": "New Label"},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "New Label"
    assert resp.json()["key"] == key  # key immutable
    # Value still attached under the same key.
    got = api.get(f"/api/crm/contacts/{c['id']}", headers=team_headers).json()
    assert got["custom_fields"][key] == "v1"


def test_archive_hides_but_preserves_and_filters(api, team_headers, cf_client):
    f = _mkfield(api, team_headers, label="Archivable", field_type="text")
    key = f["key"]
    c = _mkcontact(
        api, team_headers, cf_client, first_name="Arch", custom_fields={key: "kept"}
    )
    resp = api.post(f"/api/crm/custom-fields/{f['id']}/archive", headers=team_headers)
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None
    # Hidden from the default definition list...
    active = api.get("/api/crm/custom-fields", headers=team_headers).json()
    assert f["id"] not in [d["id"] for d in active]
    # ...but present under include_archived, and value preserved on the contact.
    arch = api.get(
        "/api/crm/custom-fields?include_archived=true", headers=team_headers
    ).json()
    assert f["id"] in [d["id"] for d in arch]
    got = api.get(f"/api/crm/contacts/{c['id']}", headers=team_headers).json()
    assert got["custom_fields"][key] == "kept"


def test_hard_delete_scrubs_values(api, team_headers, cf_client):
    f = _mkfield(api, team_headers, label="Deletable", field_type="text")
    key = f["key"]
    c = _mkcontact(
        api, team_headers, cf_client, first_name="Del", custom_fields={key: "bye"}
    )
    resp = api.delete(f"/api/crm/custom-fields/{f['id']}", headers=team_headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    # Background scrub runs before the TestClient call returns — value is gone.
    got = api.get(f"/api/crm/contacts/{c['id']}", headers=team_headers).json()
    assert key not in (got.get("custom_fields") or {})


def test_removed_select_option_blocks_then_keeps_or_remaps(api, team_headers, cf_client):
    f = _mkfield(
        api,
        team_headers,
        label="Plan",
        field_type="select",
        options=[{"label": "A"}, {"label": "B"}],
    )
    a_key = f["options"][0]["key"]
    b_key = f["options"][1]["key"]
    c = _mkcontact(
        api, team_headers, cf_client, first_name="Opt", custom_fields={f["key"]: a_key}
    )
    # Removing option A (in use) with no decision -> 409, never silent loss.
    resp = api.patch(
        f"/api/crm/custom-fields/{f['id']}",
        json={"options": [{"key": b_key, "label": "B"}]},
        headers=team_headers,
    )
    assert resp.status_code == 409
    assert a_key in resp.json()["detail"]["in_use"]
    # Remap A -> B rewrites stored values.
    resp = api.patch(
        f"/api/crm/custom-fields/{f['id']}",
        json={
            "options": [{"key": b_key, "label": "B"}],
            "option_remap": {a_key: b_key},
        },
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    got = api.get(f"/api/crm/contacts/{c['id']}", headers=team_headers).json()
    assert got["custom_fields"][f["key"]] == b_key


def test_cap_blocks_over_limit(api):
    """A fresh org is on the default (starter) tier: cap = 20 active fields.
    Uses its own org so filling the cap doesn't perturb other suites. Archiving
    frees a slot."""
    resp = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Capped Agency",
            "email": "owner@cappedagency.com",
            "password": "capped-pass-123",
            "full_name": "Capped Owner",
        },
    )
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    created, last, first_id = 0, None, None
    for i in range(25):
        r = api.post(
            "/api/crm/custom-fields",
            json={"label": f"Field {i}", "field_type": "text"},
            headers=headers,
        )
        if r.status_code == 402:
            last = r
            break
        assert r.status_code == 201, r.text
        if first_id is None:
            first_id = r.json()["id"]
        created += 1
    assert created == 20
    assert last is not None and last.status_code == 402

    # Usage endpoint reflects the cap.
    usage = api.get("/api/crm/custom-fields/usage", headers=headers).json()
    assert usage == {"used": 20, "limit": 20}

    # Archiving one frees a slot; a new field can then be created.
    assert (
        api.post(f"/api/crm/custom-fields/{first_id}/archive", headers=headers).status_code
        == 200
    )
    assert (
        api.post(
            "/api/crm/custom-fields",
            json={"label": "After archive", "field_type": "text"},
            headers=headers,
        ).status_code
        == 201
    )


# --- Part C: client visibility & isolation ---


def test_client_visibility_flag_hides_field(
    api, team_headers, cf_client_headers, cf_client
):
    client_id = cf_client
    client_a_headers = cf_client_headers  # client-role user pinned to cf_client
    hidden = _mkfield(
        api, team_headers, label="Internal Margin", field_type="text",
        visible_to_clients=False,
    )
    shown = _mkfield(
        api, team_headers, label="Shared Ref", field_type="text",
        visible_to_clients=True,
    )
    c = _mkcontact(
        api,
        team_headers,
        client_id,
        first_name="Visible",
        custom_fields={hidden["key"]: "secret", shown["key"]: "ok-to-see"},
    )
    # Team sees both.
    team_view = api.get(f"/api/crm/contacts/{c['id']}", headers=team_headers).json()
    assert team_view["custom_fields"][hidden["key"]] == "secret"

    # Client-role read: hidden field absent, shown field present.
    client_view = api.get(
        f"/api/crm/contacts/{c['id']}", headers=client_a_headers
    ).json()
    assert hidden["key"] not in client_view["custom_fields"]
    assert client_view["custom_fields"][shown["key"]] == "ok-to-see"

    # Client cannot filter on a hidden field to probe it — filter is dropped.
    import json as _json

    filt = _json.dumps([{"key": hidden["key"], "op": "contains", "value": "secret"}])
    resp = api.get(
        f"/api/crm/contacts?client_id={client_id}&cf_filter={filt}",
        headers=client_a_headers,
    )
    assert resp.status_code == 200
    # Filter ignored (not applied), so the contact still lists; and its payload
    # still hides the field.
    payloads = [x for x in resp.json() if x["id"] == c["id"]]
    assert payloads and hidden["key"] not in payloads[0]["custom_fields"]


def test_definitions_are_org_isolated(api, team_headers, org2_headers):
    # Both orgs can define identically-labelled fields with no interaction.
    a = _mkfield(api, team_headers, label="Shared Name", field_type="text")
    b = _mkfield(api, org2_headers, label="Shared Name", field_type="text")
    assert a["key"] == b["key"] == "shared_name"  # same slug, different orgs

    a_ids = {d["id"] for d in api.get("/api/crm/custom-fields", headers=team_headers).json()}
    b_ids = {d["id"] for d in api.get("/api/crm/custom-fields", headers=org2_headers).json()}
    assert a["id"] in a_ids and a["id"] not in b_ids
    assert b["id"] in b_ids and b["id"] not in a_ids

    # Cross-org mutation is a 404, not a 403 (no existence leak).
    resp = api.patch(
        f"/api/crm/custom-fields/{b['id']}",
        json={"label": "Hijack"},
        headers=team_headers,
    )
    assert resp.status_code == 404


def test_client_role_cannot_manage_fields(api, client_a_headers):
    resp = api.post(
        "/api/crm/custom-fields",
        json={"label": "Nope", "field_type": "text"},
        headers=client_a_headers,
    )
    assert resp.status_code == 403


# --- Part C: CSV import ---


def test_csv_import_maps_and_reports_bad_rows(api, team_headers, cf_client):
    existing = _mkfield(api, team_headers, label="Existing Score", field_type="number")
    body = {
        "client_id": cf_client,
        "mapping": {
            "Email": "email",
            "First": "first_name",
            "Score": f"custom:{existing['key']}",
            "Segment": "new",  # created inline below
            "Ignore": "skip",
        },
        "new_fields": [
            {
                "column": "Segment",
                "label": "Segment",
                "field_type": "select",
                "options": [{"label": "SMB"}, {"label": "Enterprise"}],
            }
        ],
        "rows": [
            {"Email": "a@x.com", "First": "A", "Score": "10", "Segment": "SMB", "Ignore": "z"},
            {"Email": "b@x.com", "First": "B", "Score": "bad", "Segment": "SMB"},  # bad number
            {"First": "C", "Score": "5", "Segment": "Unknown"},  # bad option + has identity
            {"Ignore": "only"},  # no identity mapped
        ],
    }
    resp = api.post("/api/crm/contacts/import", json=body, headers=team_headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["imported"] == 1  # only the first row is clean
    assert len(out["failed"]) == 3
    failed_rows = {f["row"] for f in out["failed"]}
    assert failed_rows == {1, 2, 3}
    assert out["created_fields"][0]["label"] == "Segment"

    # The created field exists and the imported contact carries mapped values.
    seg_key = out["created_fields"][0]["key"]
    listing = api.get(
        f"/api/crm/contacts?client_id={cf_client}", headers=team_headers
    ).json()
    imported = [c for c in listing if c["email"] == "a@x.com"]
    assert imported and imported[0]["custom_fields"][existing["key"]] == 10
    assert imported[0]["custom_fields"][seg_key]  # SMB option key


def test_client_role_cannot_import(api, client_a_headers, seeded):
    resp = api.post(
        "/api/crm/contacts/import",
        json={"client_id": seeded[CLIENT_KEY], "mapping": {}, "rows": []},
        headers=client_a_headers,
    )
    assert resp.status_code == 403
