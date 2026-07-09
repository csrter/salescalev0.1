"""Platform registry + GET /api/platforms discovery endpoint."""

from types import SimpleNamespace

import pytest

from app import platforms as reg
from app.models.attribution import LandingEvent
from app.services import change_executor, insights_sync, metrics


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


def test_dispatch_seams_are_registry_driven():
    # The insights + change-execution seams are registries keyed by platform,
    # covering exactly the live reference implementations.
    assert set(change_executor.CHANGE_EXECUTORS) == {"meta", "google"}
    assert set(insights_sync.INSIGHTS_FETCHERS) == {"meta", "google"}


def test_change_executor_rejects_unregistered_platform():
    # A platform with no executor must raise, not silently route to Google's
    # (the pre-refactor `else` fallthrough hazard).
    acct = SimpleNamespace(platform="tiktok")
    with pytest.raises(change_executor.UnsupportedChange):
        change_executor.execute(None, None, acct, None)


def test_generic_click_id_attributes_a_new_platform():
    # A new platform's click ID (in the generic click_ids map) and its utm
    # alias both attribute without any edit to the attribution code.
    assert LandingEvent(click_ids={"msclkid": "x"}).click_id("msclkid") == "x"
    assert metrics._platform_from_landing(LandingEvent(click_ids={"msclkid": "x"})) == "msads"
    assert metrics._platform_from_landing(LandingEvent(utm_source="bing")) == "msads"
    assert metrics._platform_from_landing(LandingEvent(click_ids={"ttclid": "y"})) == "tiktok"


def test_meta_before_google_attribution_precedence_preserved():
    # fbclid wins over a google utm_source, exactly as the prior hardcoded
    # fbclid-then-gclid order did.
    assert metrics._platform_from_landing(LandingEvent(fbclid="fb", utm_source="google")) == "meta"
    assert metrics._platform_from_landing(LandingEvent(gclid="g")) == "google"
    assert metrics._platform_from_landing(LandingEvent(utm_source="organic")) is None
