"""Phase 6 — Salescale CRM.

Covers the definition of done:
- native-form leads (Meta Instant Form / Google Lead Form webhooks) appear
  automatically with attribution attached, idempotently on redelivery;
- the pipeline board exists per client with per-client-customizable stages,
  and a stage change is a plain PATCH (what the kanban drag calls);
- marking a lead qualified — via checklist OR dragging into a qualified
  stage — updates LQA-CPL and the guarantee tracker with no further step;
- qualified-lead criteria are per-Organization (a second org configures
  different/no criteria without touching Atlas Reach's);
- external CRM sync (opt-in per client) works both ways without duplicates;
- the Client role sees its own pipeline read-only, with internal-only data
  filtered at the API, and cannot write or cross tenants.
"""

import hashlib
import hmac
import json

import pytest

from app.api import lead_webhooks
from app.db import SessionLocal
from app.models.core import PlatformConnection
from app.security import encrypt_secret
from app.services import external_sync as external_sync_svc

META_APP_SECRET = b"test-meta-app-secret"


@pytest.fixture(scope="module")
def crm_client(api, team_headers, seeded):
    """A dedicated client for the lead/deal-creating tests, so the exact
    per-contact arithmetic test_metrics asserts for client_a stays intact.
    Gets a Meta connection so the leadgen webhook's lead fetch has a token."""
    resp = api.post(
        "/api/clients", json={"name": "CRM Test Plumbing"}, headers=team_headers
    )
    assert resp.status_code == 201, resp.text
    client_id = resp.json()["id"]
    db = SessionLocal()
    db.add(
        PlatformConnection(
            organization_id=seeded["org"],
            client_id=client_id,
            platform="meta",
            access_token_encrypted=encrypt_secret("test-meta-token"),
        )
    )
    db.commit()
    db.close()
    return client_id


def _meta_signed(body: dict):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(META_APP_SECRET, raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def _board(api, headers, client_id):
    resp = api.get(f"/api/crm/board?client_id={client_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _contacts(api, headers, client_id):
    resp = api.get(f"/api/crm/contacts?client_id={client_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _qualified_total(api, headers, client_id) -> int:
    resp = api.get(
        f"/api/metrics/lead-quality-adjusted-cpl?client_id={client_id}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["total_qualified_leads"]


# --- pipeline board & per-client stages ---


def test_default_pipeline_created_on_first_board_view(api, team_headers, seeded):
    board = _board(api, team_headers, seeded["client_a"])
    names = [s["name"] for s in board["stages"]]
    assert names == ["New", "Contacted", "Qualified", "Negotiation"]
    assert sum(1 for s in board["stages"] if s["is_qualified_stage"]) == 1
    assert board["read_only"] is False


def test_stages_customizable_per_client_admin_only(
    api, team_headers, member_headers, seeded
):
    board = _board(api, team_headers, seeded["client_a"])
    pipeline_id = board["pipeline"]["id"]
    stages = board["stages"]
    new_stages = [
        {"id": stages[0]["id"], "name": "Inbound", "is_qualified_stage": False},
        {"id": stages[1]["id"], "name": "Contacted", "is_qualified_stage": False},
        {
            "id": stages[2]["id"],
            "name": "Trial Booked",
            "is_qualified_stage": True,
        },
        {"id": stages[3]["id"], "name": "Negotiation", "is_qualified_stage": False},
        {"name": "Install Scheduled", "is_qualified_stage": False},
    ]
    # Stage design is client setup — member (campaign work only) may not.
    resp = api.put(
        f"/api/crm/pipelines/{pipeline_id}/stages",
        json={"stages": new_stages},
        headers=member_headers,
    )
    assert resp.status_code == 403
    resp = api.put(
        f"/api/crm/pipelines/{pipeline_id}/stages",
        json={"stages": new_stages},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    names = [s["name"] for s in resp.json()["stages"]]
    assert names == [
        "Inbound",
        "Contacted",
        "Trial Booked",
        "Negotiation",
        "Install Scheduled",
    ]
    # Another client of the same org keeps its own default stage set —
    # stages are per client, not per organization.
    board_b = _board(api, team_headers, seeded["client_b"])
    assert [s["name"] for s in board_b["stages"]] == [
        "New",
        "Contacted",
        "Qualified",
        "Negotiation",
    ]


# --- lead ingestion: Meta Instant Forms ---


def test_meta_webhook_verification_handshake(api):
    resp = api.get(
        "/api/webhooks/meta/leadgen",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "1158201444",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "1158201444"
    resp = api.get(
        "/api/webhooks/meta/leadgen",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "x",
        },
    )
    assert resp.status_code == 403


def test_meta_instant_form_lead_ingested_idempotently(
    api, team_headers, crm_client, monkeypatch
):
    # Route page 777 to client A (admin config).
    resp = api.put(
        f"/api/clients/{crm_client}/lead-forms/meta",
        json={"external_key": "777"},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text

    monkeypatch.setattr(
        lead_webhooks.meta_leadgen,
        "fetch_lead",
        lambda token, leadgen_id: {
            "id": leadgen_id,
            "campaign_id": "23840000000",
            "ad_id": "23849999999",
            "form_id": "555",
            "field_data": [
                {"name": "full_name", "values": ["Rita Book"]},
                {"name": "email", "values": ["rita@example.com"]},
                {"name": "phone_number", "values": ["+15550104477"]},
            ],
        },
    )
    envelope = {
        "object": "page",
        "entry": [
            {
                "id": "777",
                "time": 1730000000,
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": "lead-888",
                            "page_id": "777",
                            "form_id": "555",
                            "ad_id": "23849999999",
                            "created_time": 1730000000,
                        },
                    }
                ],
            }
        ],
    }
    raw, headers = _meta_signed(envelope)

    # Bad signature is rejected before anything is read.
    resp = api.post(
        "/api/webhooks/meta/leadgen",
        content=raw,
        headers={**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resp.status_code == 403

    resp = api.post("/api/webhooks/meta/leadgen", content=raw, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] == "created"

    # The lead is in the CRM with source attribution + the platform's ad
    # linkage — no manual step in between.
    contacts = _contacts(api, team_headers, crm_client)
    lead = next(c for c in contacts if c["email"] == "rita@example.com")
    assert lead["source"] == "meta_instant_form"
    assert lead["first_name"] == "Rita"
    assert lead["source_detail"]["campaign_id"] == "23840000000"
    assert lead["attribution"]["platform"] == "meta"

    # Meta redelivers webhooks; same leadgen_id must not duplicate.
    resp = api.post("/api/webhooks/meta/leadgen", content=raw, headers=headers)
    assert resp.json()["results"][0]["status"] == "updated"
    contacts = _contacts(api, team_headers, crm_client)
    assert sum(1 for c in contacts if c["email"] == "rita@example.com") == 1


# --- lead ingestion: Google Lead Form ads ---


def test_google_lead_form_lead_ingested_with_gclid(api, team_headers, crm_client):
    resp = api.put(
        f"/api/clients/{crm_client}/lead-forms/google",
        json={"external_key": "gkey-secret-1"},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text

    payload = {
        "lead_id": "glead-001",
        "api_version": "1.0",
        "form_id": 100200,
        "campaign_id": 300400,
        "gcl_id": "EAIaIQ-test-gclid",
        "google_key": "gkey-secret-1",
        "is_test": False,
        "user_column_data": [
            {"column_id": "FULL_NAME", "string_value": "Paige Turner"},
            {"column_id": "EMAIL", "string_value": "paige@example.com"},
            {"column_id": "PHONE_NUMBER", "string_value": "+15550107788"},
        ],
    }
    url = f"/api/webhooks/google/lead-form/{crm_client}"
    # Wrong key → 403 (non-retryable for Google, nothing ingested).
    resp = api.post(url, json={**payload, "google_key": "wrong"})
    assert resp.status_code == 403

    resp = api.post(url, json=payload)
    assert resp.status_code == 200, resp.text

    contacts = _contacts(api, team_headers, crm_client)
    lead = next(c for c in contacts if c["email"] == "paige@example.com")
    assert lead["source"] == "google_lead_form"
    # gcl_id became a first-class attribution row: platform resolves via the
    # click id, not just the form source.
    assert lead["attribution"]["platform"] == "google"
    assert lead["attribution"]["has_click_id"] is True

    # Redelivery of the same lead_id updates in place.
    resp = api.post(url, json=payload)
    assert resp.status_code == 200
    contacts = _contacts(api, team_headers, crm_client)
    assert sum(1 for c in contacts if c["email"] == "paige@example.com") == 1

    # Google's console test leads are acknowledged but never become CRM rows.
    resp = api.post(
        url, json={**payload, "lead_id": "glead-test", "is_test": True}
    )
    assert resp.status_code == 200
    contacts = _contacts(api, team_headers, crm_client)
    assert not any(c["source_external_id"] == "glead-test" for c in contacts)


# --- lead ingestion: landing-page path updates instead of duplicating ---


def test_landing_lead_resubmission_updates_existing_contact(api, crm_client):
    body = {
        "client_id": crm_client,
        "session_key": "crm-sess-1",
        "email": "repeat@example.com",
        "first_name": "Ray",
    }
    first = api.post("/api/track/lead", json=body)
    assert first.status_code == 201, first.text
    second = api.post(
        "/api/track/lead",
        json={**body, "session_key": "crm-sess-2", "last_name": "Peat"},
    )
    assert second.status_code == 201
    assert second.json()["contact_id"] == first.json()["contact_id"]


# --- qualified-lead workflow: one status change, every consumer updates ---


def test_checklist_qualification_feeds_lqa_cpl_and_guarantee(
    api, team_headers, crm_client
):
    # Atlas Reach's own criteria (Organization data, editable any time).
    resp = api.put(
        "/api/orgs/me/qualified-lead-criteria",
        json={
            "criteria": [
                {"key": "contact_made", "label": "Spoke with the lead"},
                {"key": "in_service_area", "label": "Inside the service area"},
            ]
        },
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = api.put(
        f"/api/clients/{crm_client}/guarantee",
        json={
            "name": "14-Day Trial Sprint",
            "metric": "qualified_leads",
            "target": 10,
            "window_days": 14,
        },
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": crm_client,
            "first_name": "Quinn",
            "email": "quinn@example.com",
        },
        headers=team_headers,
    )
    assert resp.status_code == 201, resp.text
    contact_id = resp.json()["id"]

    lqa_before = _qualified_total(api, team_headers, crm_client)
    g_before = api.get(
        f"/api/metrics/guarantee?client_id={crm_client}",
        headers=team_headers,
    ).json()["progress"]

    # Half the checklist → still not qualified.
    resp = api.put(
        f"/api/crm/contacts/{contact_id}/qualification",
        json={"checklist": {"contact_made": True}},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["qualified"] is False
    assert _qualified_total(api, team_headers, crm_client) == lqa_before

    # Completing the checklist IS the qualifying event — no other call.
    resp = api.put(
        f"/api/crm/contacts/{contact_id}/qualification",
        json={"checklist": {"in_service_area": True}},
        headers=team_headers,
    )
    assert resp.json()["qualified"] is True
    assert resp.json()["transition"] == "qualified"

    assert (
        _qualified_total(api, team_headers, crm_client) == lqa_before + 1
    )
    g_after = api.get(
        f"/api/metrics/guarantee?client_id={crm_client}",
        headers=team_headers,
    ).json()
    assert g_after["name"] == "14-Day Trial Sprint"
    assert g_after["progress"] == g_before + 1

    # Unknown criterion keys are rejected — the checklist is structured.
    resp = api.put(
        f"/api/crm/contacts/{contact_id}/qualification",
        json={"checklist": {"vibes": True}},
        headers=team_headers,
    )
    assert resp.status_code == 400

    # Clean up the guarantee so later phases' tests see their own config.
    api.delete(
        f"/api/clients/{crm_client}/guarantee", headers=team_headers
    )


def test_kanban_drag_into_qualified_stage_is_the_same_event(
    api, team_headers, crm_client
):
    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": crm_client,
            "first_name": "Kanban",
            "email": "kanban@example.com",
        },
        headers=team_headers,
    )
    contact_id = resp.json()["id"]
    resp = api.post(
        "/api/crm/deals",
        json={
            "client_id": crm_client,
            "contact_id": contact_id,
            "value_cents": 250000,
        },
        headers=team_headers,
    )
    assert resp.status_code == 201, resp.text
    deal = resp.json()

    board = _board(api, team_headers, crm_client)
    qualified_stage = next(s for s in board["stages"] if s["is_qualified_stage"])
    assert deal["stage_id"] != qualified_stage["id"]
    assert deal["id"] in [
        d["id"] for d in board["deals_by_stage"][deal["stage_id"]]
    ]

    lqa_before = _qualified_total(api, team_headers, crm_client)
    resp = api.patch(
        f"/api/crm/deals/{deal['id']}",
        json={"stage_id": qualified_stage["id"]},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage_id"] == qualified_stage["id"]

    board = _board(api, team_headers, crm_client)
    assert deal["id"] in [
        d["id"] for d in board["deals_by_stage"][qualified_stage["id"]]
    ]
    # The drag was the status change — contact qualified, metric moved.
    assert board["contacts"][contact_id]["qualified_at"] is not None
    assert (
        _qualified_total(api, team_headers, crm_client) == lqa_before + 1
    )


def test_qualified_criteria_are_per_organization(
    api, team_headers, org2, org2_headers
):
    # Org 2 sets its own, different checklist.
    resp = api.put(
        "/api/orgs/me/qualified-lead-criteria",
        json={"criteria": [{"key": "demo_attended", "label": "Attended a demo"}]},
        headers=org2_headers,
    )
    assert resp.status_code == 200

    # Atlas Reach's criteria are untouched by org 2's write.
    mine = api.get(
        "/api/orgs/me/qualified-lead-criteria", headers=team_headers
    ).json()["criteria"]
    assert [c["key"] for c in mine] == ["contact_made", "in_service_area"]

    # And an organization with NO criteria uses a plain qualified toggle.
    resp = api.put(
        "/api/orgs/me/qualified-lead-criteria",
        json={"criteria": []},
        headers=org2_headers,
    )
    assert resp.status_code == 200
    resp = api.post(
        "/api/crm/contacts",
        json={"client_id": org2["client_id"], "first_name": "Solo"},
        headers=org2_headers,
    )
    contact_id = resp.json()["id"]
    resp = api.put(
        f"/api/crm/contacts/{contact_id}/qualification",
        json={"checklist": {"anything": True}},
        headers=org2_headers,
    )
    assert resp.status_code == 400  # no criteria → checklist is meaningless
    resp = api.put(
        f"/api/crm/contacts/{contact_id}/qualification",
        json={"qualified": True},
        headers=org2_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["qualified"] is True


# --- optional external CRM sync ---


def test_external_sync_two_way_without_duplicates(
    api, team_headers, crm_client, monkeypatch
):
    resp = api.put(
        f"/api/clients/{crm_client}/external-sync",
        json={
            "enabled": True,
            "url": "https://external-crm.example/hooks/salescale",
            "secret": "shared-secret-123",
        },
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    # The secret is write-only — reads never echo it.
    got = api.get(
        f"/api/clients/{crm_client}/external-sync", headers=team_headers
    ).json()
    assert "secret" not in got and got["configured"] is True

    sent = []

    def fake_post(url, content=None, headers=None, timeout=None):
        sent.append({"url": url, "content": content, "headers": headers})

        class R:
            status_code = 200

        return R()

    monkeypatch.setattr(external_sync_svc.httpx, "post", fake_post)

    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": crm_client,
            "first_name": "Synco",
            "email": "synco@example.com",
        },
        headers=team_headers,
    )
    contact_id = resp.json()["id"]

    # Outbound: qualifying pushes the status change to the external CRM,
    # signed with the shared secret.
    resp = api.put(
        f"/api/crm/contacts/{contact_id}/qualification",
        json={"checklist": {"contact_made": True, "in_service_area": True}},
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(sent) == 1
    body = json.loads(sent[0]["content"])
    assert body["event"] == "lead.qualified"
    assert body["contact"]["salescale_contact_id"] == contact_id
    expected_sig = (
        "sha256="
        + hmac.new(
            b"shared-secret-123", sent[0]["content"], hashlib.sha256
        ).hexdigest()
    )
    assert sent[0]["headers"]["X-Salescale-Signature-256"] == expected_sig

    # Inbound: wrong secret is rejected; right secret applies the change to
    # the SAME contact (matched by email, external id learned) — no dup.
    url = f"/api/crm/external-sync/{crm_client}"
    inbound = {
        "external_contact_id": "ghl-42",
        "email": "synco@example.com",
        "qualified": False,
    }
    resp = api.post(url, json=inbound, headers={"X-Salescale-Secret": "nope"})
    assert resp.status_code == 403
    before = len(_contacts(api, team_headers, crm_client))
    resp = api.post(
        url, json=inbound, headers={"X-Salescale-Secret": "shared-secret-123"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "contact_id": contact_id,
        "created": False,
        "applied": ["unqualified"],
    }
    # Replay: matched by the learned external id now; still no duplicate.
    resp = api.post(
        url, json=inbound, headers={"X-Salescale-Secret": "shared-secret-123"}
    )
    assert resp.json()["contact_id"] == contact_id
    assert len(_contacts(api, team_headers, crm_client)) == before

    contacts = _contacts(api, team_headers, crm_client)
    synco = next(c for c in contacts if c["id"] == contact_id)
    assert synco["external_crm_id"] == "ghl-42"
    assert synco["qualified_at"] is None  # the inbound unqualify applied

    # Opt-out again so later tests never attempt outbound HTTP.
    resp = api.delete(
        f"/api/clients/{crm_client}/external-sync", headers=team_headers
    )
    assert resp.status_code == 204


# --- activities & tasks ---


def test_activities_and_tasks(api, team_headers, member_headers, crm_client):
    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": crm_client,
            "first_name": "Acty",
            "email": "acty@example.com",
        },
        headers=team_headers,
    )
    contact_id = resp.json()["id"]

    resp = api.post(
        "/api/crm/activities",
        json={
            "contact_id": contact_id,
            "type": "call",
            "body": "Spoke about the spring tune-up offer",
        },
        headers=member_headers,  # members do day-to-day CRM work
    )
    assert resp.status_code == 201, resp.text
    resp = api.post(
        "/api/crm/activities",
        json={
            "contact_id": contact_id,
            "type": "haiku",
            "body": "not a real type",
        },
        headers=team_headers,
    )
    assert resp.status_code == 400

    members = api.get("/api/orgs/me/members", headers=team_headers).json()
    member_id = next(m["id"] for m in members if m["role"] == "member")
    resp = api.post(
        "/api/crm/tasks",
        json={
            "client_id": crm_client,
            "contact_id": contact_id,
            "title": "Follow up on trial quote",
            "assigned_to_user_id": member_id,
        },
        headers=team_headers,
    )
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]

    open_tasks = api.get(
        f"/api/crm/tasks?client_id={crm_client}", headers=member_headers
    ).json()
    assert task_id in [t["id"] for t in open_tasks]

    resp = api.patch(
        f"/api/crm/tasks/{task_id}",
        json={"completed": True},
        headers=member_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["completed_at"] is not None
    open_tasks = api.get(
        f"/api/crm/tasks?client_id={crm_client}", headers=member_headers
    ).json()
    assert task_id not in [t["id"] for t in open_tasks]


# --- client-facing pipeline view: read-only, field-filtered, tenant-pinned ---


def test_client_role_pipeline_view(api, team_headers, client_a_headers, seeded):
    # A contact with both an internal-only and a client-visible activity.
    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": seeded["client_a"],
            "first_name": "Vista",
            "email": "vista@example.com",
        },
        headers=team_headers,
    )
    contact_id = resp.json()["id"]
    api.post(
        "/api/crm/activities",
        json={
            "contact_id": contact_id,
            "type": "note",
            "body": "INTERNAL: thin margins on this one, keep discount low",
            "is_internal": True,
        },
        headers=team_headers,
    )
    api.post(
        "/api/crm/activities",
        json={
            "contact_id": contact_id,
            "type": "email",
            "body": "Sent the welcome packet",
        },
        headers=team_headers,
    )

    # Read access to their own pipeline…
    board = _board(api, client_a_headers, seeded["client_a"])
    assert board["read_only"] is True
    # …but not to any other client's.
    resp = api.get(
        f"/api/crm/board?client_id={seeded['client_b']}", headers=client_a_headers
    )
    assert resp.status_code == 404

    # Field-level filtering on the backend: the org-internal workflow fields
    # aren't nulled, they don't exist in the response shape at all.
    contacts = _contacts(api, client_a_headers, seeded["client_a"])
    vista = next(c for c in contacts if c["id"] == contact_id)
    for internal_field in ("qualification", "source_detail", "external_crm_id"):
        assert internal_field not in vista
    assert "qualified_at" in vista  # the status itself is theirs to see

    # Internal-only activities are excluded by the query, not the UI.
    detail = api.get(
        f"/api/crm/contacts/{contact_id}", headers=client_a_headers
    ).json()
    bodies = [a["body"] for a in detail["activities"]]
    assert "Sent the welcome packet" in bodies
    assert all("INTERNAL" not in (b or "") for b in bodies)
    assert "tasks" not in detail  # tasks are team work management
    team_detail = api.get(
        f"/api/crm/contacts/{contact_id}", headers=team_headers
    ).json()
    assert len(team_detail["activities"]) == 2

    # No write action, anywhere.
    denied = [
        api.post(
            "/api/crm/contacts",
            json={"client_id": seeded["client_a"], "first_name": "X"},
            headers=client_a_headers,
        ),
        api.post(
            "/api/crm/deals",
            json={"client_id": seeded["client_a"], "contact_id": contact_id},
            headers=client_a_headers,
        ),
        api.put(
            f"/api/crm/contacts/{contact_id}/qualification",
            json={"qualified": True},
            headers=client_a_headers,
        ),
        api.post(
            "/api/crm/activities",
            json={"contact_id": contact_id, "type": "note", "body": "hi"},
            headers=client_a_headers,
        ),
        api.get(
            f"/api/crm/tasks?client_id={seeded['client_a']}",
            headers=client_a_headers,
        ),
        api.get("/api/orgs/me/qualified-lead-criteria", headers=client_a_headers),
    ]
    assert [r.status_code for r in denied] == [403] * len(denied)

    # Remove this test's client_a contact so test_metrics' exact per-contact
    # arithmetic for client_a (5 seeded leads) still holds.
    db = SessionLocal()
    from app.models.crm import Activity, Contact

    for a in (
        db.query(Activity).filter(Activity.contact_id == contact_id).all()
    ):
        db.delete(a)
    db.delete(db.get(Contact, contact_id))
    db.commit()
    db.close()


def test_crm_org_isolation(
    api, org2_headers, seeded, team_headers, org2, crm_client
):
    # Org 2 cannot see or touch Atlas Reach's CRM at all (404, not 403 —
    # existence is not leaked), and vice versa.
    resp = api.get(
        f"/api/crm/board?client_id={seeded['client_a']}", headers=org2_headers
    )
    assert resp.status_code == 404
    resp = api.get(
        f"/api/crm/contacts?client_id={seeded['client_a']}", headers=org2_headers
    )
    assert resp.status_code == 404
    resp = api.get(
        f"/api/crm/board?client_id={org2['client_id']}", headers=team_headers
    )
    assert resp.status_code == 404

    # Cross-org contact access via detail route.
    mine = _contacts(api, team_headers, crm_client)
    resp = api.get(f"/api/crm/contacts/{mine[0]['id']}", headers=org2_headers)
    assert resp.status_code == 404
