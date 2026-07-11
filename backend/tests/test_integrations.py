"""Per-Organization platform API credentials (bring-your-own app)."""
from app.db import SessionLocal
from app.services import integration_creds


def test_status_none_before_config(api, team_headers):
    rows = api.get("/api/integrations", headers=team_headers).json()
    by = {r["provider"]: r for r in rows}
    assert by["meta"]["configured"] is False and by["meta"]["source"] == "none"
    assert by["google"]["configured"] is False


def test_only_admins_can_manage(api, member_headers, client_a_headers):
    assert api.get("/api/integrations", headers=member_headers).status_code == 403
    assert (
        api.put(
            "/api/integrations/meta",
            headers=member_headers,
            json={"app_id": "x", "app_secret": "y"},
        ).status_code
        == 403
    )
    # client-role users have no business here either
    assert api.get("/api/integrations", headers=client_a_headers).status_code == 403


def test_set_meta_then_resolves_and_connect_uses_it(api, team_headers, seeded):
    r = api.put(
        "/api/integrations/meta",
        headers=team_headers,
        json={"app_id": "123appid", "app_secret": "supersecret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["source"] == "organization"
    assert body["public_id"] == "123appid"
    # secret is write-only — never echoed back
    assert "secret" not in str(body).lower() or "supersecret" not in str(body)

    # the resolver decrypts the org's own credentials
    db = SessionLocal()
    creds = integration_creds.resolve_meta(db, seeded["org"])
    db.close()
    assert creds.app_id == "123appid" and creds.app_secret == "supersecret"

    # and the connect flow now builds a URL with the org's app id (no 503)
    start = api.get(
        f"/api/connect/meta/start?client_id={seeded['client_a']}", headers=team_headers
    )
    assert start.status_code == 200
    assert "client_id=123appid" in start.json()["url"]


def test_set_google_stores_developer_token(api, team_headers, seeded):
    r = api.put(
        "/api/integrations/google",
        headers=team_headers,
        json={
            "client_id": "gclient",
            "client_secret": "gsecret",
            "developer_token": "devtoken123",
            "login_customer_id": "1234567890",
        },
    )
    assert r.status_code == 200 and r.json()["public_id"] == "gclient"
    db = SessionLocal()
    creds = integration_creds.resolve_google(db, seeded["org"])
    db.close()
    assert creds.client_id == "gclient"
    assert creds.developer_token == "devtoken123"
    assert creds.login_customer_id == "1234567890"
    assert creds.configured is True


def test_delete_reverts_to_none(api, team_headers, seeded):
    api.put(
        "/api/integrations/meta",
        headers=team_headers,
        json={"app_id": "temp", "app_secret": "temp"},
    )
    r = api.delete("/api/integrations/meta", headers=team_headers)
    assert r.status_code == 200
    assert r.json()["configured"] is False
    # connect start now 503s again (no org creds, no global in tests)
    start = api.get(
        f"/api/connect/meta/start?client_id={seeded['client_a']}", headers=team_headers
    )
    assert start.status_code == 503


def test_credentials_are_org_scoped(api, team_headers, org2_headers, org2):
    # org1 sets meta creds; org2 must not see them and stays unconfigured
    api.put(
        "/api/integrations/meta",
        headers=team_headers,
        json={"app_id": "org1app", "app_secret": "org1secret"},
    )
    rows = api.get("/api/integrations", headers=org2_headers).json()
    meta = next(r for r in rows if r["provider"] == "meta")
    assert meta["configured"] is False  # org2 has none of its own
    db = SessionLocal()
    creds = integration_creds.resolve_meta(db, org2["organization_id"])
    db.close()
    assert creds.app_id != "org1app"


def test_redirect_uris_listed_for_admin(api, team_headers, member_headers):
    """The exact redirect URIs to register on the OAuth apps — connect and
    sign-in use different callback paths and BOTH must be registered, or the
    provider fails with redirect_uri_mismatch."""
    r = api.get("/api/integrations/redirect-uris", headers=team_headers)
    assert r.status_code == 200, r.text
    by_key = {(u["provider"], u["purpose"]): u["uri"] for u in r.json()}
    assert by_key[("google", "connect")].endswith("/api/connect/google/callback")
    assert by_key[("google", "signin")].endswith("/api/auth/oauth/google/callback")
    assert by_key[("meta", "connect")].endswith("/api/connect/meta/callback")
    assert by_key[("meta", "signin")].endswith("/api/auth/oauth/meta/callback")
    # Members don't manage integrations.
    assert (
        api.get("/api/integrations/redirect-uris", headers=member_headers).status_code
        == 403
    )
