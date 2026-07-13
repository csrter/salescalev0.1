"""A platform-API failure during live refresh must surface as a structured
502 (with a readable detail), never an unhandled 500 — bare 500s are emitted
outside CORSMiddleware and reach the browser as an opaque NetworkError."""

from fastapi.testclient import TestClient

from app.main import app
from app.services import crm as crm_svc
from app.services import meta_api


def test_live_refresh_platform_error_becomes_502(api, team_headers, seeded, monkeypatch):
    def boom(token, external_id):
        raise meta_api.MetaApiError("Graph API timed out")

    monkeypatch.setattr(meta_api, "fetch_campaigns", boom)
    resp = api.get(
        f"/api/ad-accounts/{seeded['acct_a']}/campaigns?refresh=true",
        headers=team_headers,
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Meta API error" in detail and "Graph API timed out" in detail


def test_cached_read_unaffected_by_platform_error(
    api, team_headers, seeded, monkeypatch
):
    def boom(token, external_id):
        raise meta_api.MetaApiError("Graph API timed out")

    monkeypatch.setattr(meta_api, "fetch_campaigns", boom)
    resp = api.get(
        f"/api/ad-accounts/{seeded['acct_a']}/campaigns?refresh=false",
        headers=team_headers,
    )
    assert resp.status_code == 200


def test_arbitrary_unhandled_exception_becomes_readable_500(
    team_headers, seeded, monkeypatch
):
    """The three platform-error types above are only special-cased —
    anything else unhandled (a bug in CSV import, a plain RuntimeError from
    any service call) must hit the generic catch-all and come back as a
    normal, CORS-safe 500, not a bare crash the browser reports as an opaque
    NetworkError. Reproduces the actual CSV-import symptom: an unguarded
    service call (get_or_create_company) throwing mid-row.

    Uses a local TestClient with raise_server_exceptions=False — the shared
    `api` fixture defaults to True (Starlette re-raises in the TEST process
    even when a registered handler already sent a proper response, so bugs
    are loud in tests); a real browser only ever sees the ASGI response
    actually sent, which is what this verifies."""

    def boom(db, organization_id, client_id, name):
        raise RuntimeError("unexpected failure unrelated to any platform API")

    monkeypatch.setattr(crm_svc, "get_or_create_company", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/crm/contacts/import",
            json={
                "client_id": seeded["client_a"],
                "mapping": {"Name": "full_name", "Email": "email", "Org": "company"},
                "rows": [{"Name": "Jane Doe", "Email": "jane@example.com", "Org": "Acme"}],
            },
            headers=team_headers,
        )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "An unexpected error occurred. Please try again."
    # never leaks the raw exception text to the client
    assert "unexpected failure unrelated" not in resp.text
