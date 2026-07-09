"""Platform registry + GET /api/platforms discovery endpoint."""

from app import platforms as reg


def test_registry_has_reference_and_scaffolded_platforms():
    ids = reg.all_ids()
    # Reference implementations are live and connectable.
    for pid in ("meta", "google"):
        spec = reg.get(pid)
        assert spec is not None and spec.status == reg.STATUS_LIVE
        assert spec.connectable and spec.supports_conversions
    # Phase 7 platforms are registered (visible) but not yet connectable.
    for pid in ("msads", "linkedin", "snapchat", "reddit", "tiktok", "pinterest", "nextdoor"):
        assert pid in ids
        assert reg.get(pid).status == reg.STATUS_STUB
        assert not reg.get(pid).connectable


def test_derived_maps_only_expose_conversion_and_byo_where_supported():
    # Only platforms with a server-side sender may be conversion targets.
    assert reg.conversion_platform_ids() == frozenset({"meta", "google"})
    # BYO-cred management is scoped to platforms that support it, in order.
    assert reg.byo_creds_platform_ids() == ["meta", "google"]


def test_platforms_endpoint_lists_registry(api, team_headers):
    resp = api.get("/api/platforms", headers=team_headers)
    assert resp.status_code == 200
    body = {p["id"]: p for p in resp.json()}
    assert body["meta"]["connectable"] is True
    assert body["tiktok"]["coming_soon"] is True
    assert body["tiktok"]["connectable"] is False


def test_platforms_endpoint_requires_auth(api):
    assert api.get("/api/platforms").status_code == 401
