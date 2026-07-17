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
        headers={**team_headers, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Meta API error" in detail and "Graph API timed out" in detail
    # this handler is registered for the specific MetaApiError type (not the
    # base Exception class), so Starlette routes it through ExceptionMiddleware
    # — inside CORSMiddleware — and the header is correctly present.
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


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


def test_arbitrary_unhandled_exception_becomes_readable_500_with_cors(
    team_headers, seeded, monkeypatch
):
    """The three platform-error types above are only special-cased —
    anything else unhandled (a plain RuntimeError from any service call) must
    hit the generic catch-all and come back as a normal, CORS-safe 500, not a
    bare crash the browser reports as an opaque NetworkError. Vehicle: an
    unguarded service call (get_or_create_company) throwing inside contact
    create. (CSV import used to be the vehicle here, but it now guards every
    row in a savepoint and reports bad rows in `failed` instead of 500ing —
    see test_crm_contacts.test_csv_import_bad_row_isolated_not_500 — so it no
    longer reaches this catch-all.)

    Critically asserts the Access-Control-Allow-Origin header is actually
    present (with an Origin header on the request, mimicking a real
    cross-origin browser call) — an EARLIER version of this fix used
    app.add_exception_handler(Exception, ...), which produced a status-500/
    correct-body response that this exact test happily passed, while still
    being genuinely broken in the browser: Starlette special-cases a handler
    keyed on the base Exception class into ServerErrorMiddleware, which sits
    OUTSIDE CORSMiddleware, so that response never got CORS headers at all.
    TestClient doesn't enforce/simulate CORS the way a real browser does, so
    a test that only checks status/body — like the original version of this
    test — cannot catch that class of regression; asserting the header
    itself is the only way to.

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
            "/api/crm/contacts",
            json={
                "client_id": seeded["client_a"],
                "first_name": "Jane",
                "email": "jane-catchall@example.com",
                "company_name": "Acme",
            },
            headers={**team_headers, "Origin": "http://localhost:5173"},
        )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "An unexpected error occurred. Please try again."
    # never leaks the raw exception text to the client
    assert "unexpected failure unrelated" not in resp.text
    # the actual bug: this header must be present, or the browser blocks the
    # response entirely and reports an opaque CORS/network error instead
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
