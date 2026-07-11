"""A platform-API failure during live refresh must surface as a structured
502 (with a readable detail), never an unhandled 500 — bare 500s are emitted
outside CORSMiddleware and reach the browser as an opaque NetworkError."""

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
