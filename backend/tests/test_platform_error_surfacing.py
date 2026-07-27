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


def test_oversized_body_413_carries_cors_headers(api, team_headers, seeded):
    """An oversized request body must 413 WITH Access-Control-Allow-Origin.

    The body-size middleware was originally registered AFTER
    add_middleware(CORSMiddleware, ...), which makes it the OUTERMOST layer in
    Starlette — so its 413 never flowed back through CORS and reached the
    browser with no allow-origin header. A real 600-row CSV import then failed
    as an opaque "NetworkError when attempting to fetch resource" instead of
    showing the actual "Request body too large" message. Same ordering trap the
    unhandled-exception middleware above documents; asserting the HEADER (not
    just the status) is the only thing that catches a regression, since
    TestClient does not enforce CORS on its own.
    """
    from app.main import _MAX_BODY_BYTES

    oversized = "x" * (_MAX_BODY_BYTES + 1024)
    resp = api.post(
        "/api/crm/contacts",
        json={
            "client_id": seeded["client_a"],
            "first_name": "Jane",
            "email": "jane-oversize@example.com",
            "notes": oversized,
        },
        headers={**team_headers, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body too large"
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_import_sized_batch_is_accepted(api, team_headers, seeded):
    """A fully-populated import batch must fit under the body cap.

    Guards the actual regression: at 512KB a legitimate wide-row batch 413'd.
    Builds a 200-row batch (the frontend's BATCH_SIZE) with every column
    populated and asserts the request is not rejected for size.
    """
    row = {
        "first_name": "Firstname",
        "last_name": "Lastname",
        "email": "someone.longish@example.com",
        "phone": "+14805551234",
        "city": "Scottsdale",
        "state": "AZ",
        "zip": "85251",
        "company": "Some Fairly Long Business Name LLC",
        "notes": "A realistic note field with a sentence of length to it.",
    }
    resp = api.post(
        "/api/crm/contacts/import",
        json={
            "client_id": seeded["client_a"],
            "mode": "create_or_update",
            "rows": [dict(row) for _ in range(200)],
            "mapping": {k: k for k in row},
        },
        headers={**team_headers, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code != 413, "a 200-row wide batch must fit under the body cap"


def test_track_endpoints_allow_any_origin(api, seeded):
    """/api/track/* is embedded on CLIENTS' OWN landing pages — origins the
    frontend_origins() allowlist can never enumerate. The dedicated capture
    CORS layer must answer preflights and stamp a wildcard origin, while
    every other route keeps the strict policy."""
    foreign = {"Origin": "https://some-client-site.example"}

    r = api.options(
        "/api/track/landing",
        headers={
            **foreign,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert "POST" in r.headers["access-control-allow-methods"]

    r = api.post(
        "/api/track/landing",
        json={
            "client_id": seeded["client_a"],
            "session_key": "cors-embed-check-1",
            "landing_url": "https://some-client-site.example/lp",
            "gclid": "cors-gclid-1",
        },
        headers=foreign,
    )
    assert r.status_code in (200, 201), r.text
    assert r.headers["access-control-allow-origin"] == "*"

    # A non-track route from a foreign origin gets NO wildcard grant.
    r = api.get("/api/health", headers=foreign)
    assert r.headers.get("access-control-allow-origin") != "*"
