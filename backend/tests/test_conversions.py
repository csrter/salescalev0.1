"""Phase 5: server-side conversion tracking.

Covers the four things that must be exactly right:
- PII normalization/hashing to each platform's published spec (asserted
  against independently-computed SHA-256 values, not the code's own
  helpers).
- Browser↔server deduplication: the pixel's eventID is what the server
  event carries.
- Click-ID capture: a landing ping's fbclid/fbp/gclid ride through to the
  server-side sends (fbc derived in Meta's cookie format).
- Tenant isolation + role gates on every new route, and per-platform
  failure isolation on dispatch.

Platform calls are monkeypatched (no live credentials in CI); the live
verification path is the test-send endpoint + each platform's own tooling.
"""

import hashlib
import re

import pytest

from app.db import SessionLocal
from app.models.attribution import LandingEvent
from app.models.conversions import (
    ConversionConfig,
    ConversionDispatch,
    ConversionEvent,
)
from app.models.core import PlatformConnection
from app.models.crm import Contact
from app.security import encrypt_secret
from app.services import google_conversions, meta_capi, pii


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def _clean_up_created_rows(seeded):
    """The seeded DB is session-scoped and test_metrics' arithmetic depends
    on exact lead counts — snapshot what exists, and remove everything this
    module creates (contacts, landing events, conversion rows) on the way
    out so later modules see the fixture untouched."""
    db = SessionLocal()
    before_contacts = {c.id for c in db.query(Contact).all()}
    before_landings = {e.id for e in db.query(LandingEvent).all()}
    db.close()
    yield
    db = SessionLocal()
    db.query(ConversionDispatch).delete()
    db.query(ConversionEvent).delete()
    for event in db.query(LandingEvent).all():
        if event.id not in before_landings:
            db.delete(event)
    for contact in db.query(Contact).all():
        if contact.id not in before_contacts:
            db.delete(contact)
    db.commit()
    db.close()


# --- hashing/normalization spec (verified against live docs 2026-07-06) ---


def test_meta_email_normalization():
    # Trim + lowercase only — Meta does NOT strip gmail dots/plus.
    assert pii.meta_email("  John_Smith@Gmail.com ") == sha("john_smith@gmail.com")
    assert pii.meta_email("j.s+tag@gmail.com") == sha("j.s+tag@gmail.com")
    assert pii.meta_email(None) is None
    assert pii.meta_email("   ") is None


def test_google_email_normalization():
    # Google additionally strips dots and +suffixes for gmail domains only.
    assert pii.google_email("J.S+tag@Gmail.com") == sha("js@gmail.com")
    assert pii.google_email("J.S+tag@googlemail.com") == sha("js@googlemail.com")
    assert pii.google_email("J.S+tag@example.com") == sha("j.s+tag@example.com")


def test_meta_phone_normalization():
    # Digits only, no leading zeros, country code included (US default).
    assert pii.meta_phone("(650) 555-1212") == sha("16505551212")
    assert pii.meta_phone("+1 650-555-1212") == sha("16505551212")
    assert pii.meta_phone("0049 30 1234567") == sha("49301234567")
    assert pii.meta_phone("") is None


def test_google_phone_e164():
    # E.164 with the leading + before hashing.
    assert pii.google_phone("(650) 555-1212") == sha("+16505551212")
    assert pii.google_phone("+16505551212") == sha("+16505551212")


def test_meta_geo_fields():
    assert pii.meta_name("  O'Brien ") == sha("obrien")
    assert pii.meta_city("New York") == sha("newyork")
    assert pii.meta_state("NY") == sha("ny")
    assert pii.meta_zip("94025-1234") == sha("94025")
    assert pii.meta_zip("SW1A 1AA") == sha("sw1a1aa")  # non-US kept whole
    assert pii.meta_country("US") == sha("us")
    assert pii.meta_country("USA") is None  # alpha-2 only


def test_never_hash_fields_stay_raw():
    user_data, keys = meta_capi.build_user_data(
        {
            "email": "a@b.com",
            "client_ip_address": "203.0.113.7",
            "client_user_agent": "Mozilla/5.0",
        },
        fbc="fb.1.1700000000000.abc",
        fbp="fb.1.1700000000000.999",
    )
    assert user_data["client_ip_address"] == "203.0.113.7"
    assert user_data["client_user_agent"] == "Mozilla/5.0"
    assert user_data["fbc"] == "fb.1.1700000000000.abc"
    assert user_data["fbp"] == "fb.1.1700000000000.999"
    assert user_data["em"] == [sha("a@b.com")]
    assert set(keys) == {"em", "client_ip_address", "client_user_agent", "fbc", "fbp"}


def test_google_conversion_datetime_offset_colon():
    import datetime as dt

    when = dt.datetime(2026, 7, 6, 19, 32, 45, tzinfo=dt.timezone.utc)
    assert google_conversions.format_conversion_datetime(when) == (
        "2026-07-06 19:32:45+00:00"
    )


# --- per-client config CRUD + gates ---


def _meta_config(dataset_id="ds_123", **extra):
    return {"enabled": True, "settings": {"dataset_id": dataset_id, **extra}}


def test_admin_sets_config_member_cannot(api, seeded, team_headers, member_headers):
    resp = api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/meta",
        json=_meta_config(test_event_code="TEST123"),
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings"]["dataset_id"] == "ds_123"

    resp = api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/meta",
        json=_meta_config(),
        headers=member_headers,
    )
    assert resp.status_code == 403

    # member can still read
    resp = api.get(
        f"/api/clients/{seeded['client_a']}/conversion-configs",
        headers=member_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_config_validation(api, seeded, team_headers):
    resp = api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/meta",
        json={"enabled": True, "settings": {}},
        headers=team_headers,
    )
    assert resp.status_code == 400
    resp = api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/google",
        json={"enabled": True, "settings": {"customer_id": "123"}},
        headers=team_headers,
    )
    assert resp.status_code == 400  # missing conversion_action_id
    resp = api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/tiktok",
        json=_meta_config(),
        headers=team_headers,
    )
    assert resp.status_code == 400  # unknown platform


def test_config_org_isolation(api, seeded, org2_headers):
    # Another Organization can neither write nor read this client's configs.
    resp = api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/meta",
        json=_meta_config(dataset_id="evil"),
        headers=org2_headers,
    )
    assert resp.status_code == 404
    resp = api.get(
        f"/api/clients/{seeded['client_a']}/conversion-configs",
        headers=org2_headers,
    )
    assert resp.status_code == 404


def test_client_role_cannot_touch_conversion_surface(api, seeded, client_a_headers):
    for path in (
        f"/api/clients/{seeded['client_a']}/conversion-configs",
        f"/api/conversions/log?client_id={seeded['client_a']}",
        f"/api/conversions/emq?client_id={seeded['client_a']}",
    ):
        assert api.get(path, headers=client_a_headers).status_code == 403


# --- lead capture → conversion send flow (platform calls mocked) ---


@pytest.fixture()
def google_setup(seeded, api, team_headers):
    """client_a gets a google connection + both platform configs."""
    db = SessionLocal()
    existing = (
        db.query(PlatformConnection)
        .filter_by(client_id=seeded["client_a"], platform="google")
        .one_or_none()
    )
    if existing is None:
        db.add(
            PlatformConnection(
                organization_id=seeded["org"],
                client_id=seeded["client_a"],
                platform="google",
                refresh_token_encrypted=encrypt_secret("test-google-refresh"),
            )
        )
        db.commit()
    db.close()
    api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/meta",
        json=_meta_config(test_event_code="TEST123"),
        headers=team_headers,
    )
    api.put(
        f"/api/clients/{seeded['client_a']}/conversion-configs/google",
        json={
            "enabled": True,
            "settings": {"customer_id": "1234567890", "conversion_action_id": "987"},
        },
        headers=team_headers,
    )


def test_lead_flow_end_to_end(api, seeded, team_headers, google_setup, monkeypatch):
    """Landing ping (click IDs captured) → lead submit → both platforms get
    a server-side event carrying the right identifiers and dedup key."""
    meta_calls, google_calls = [], []
    monkeypatch.setattr(
        meta_capi,
        "send_events",
        lambda token, ds, events, test_event_code=None: meta_calls.append(
            {"dataset": ds, "events": events, "code": test_event_code}
        )
        or {"events_received": 1},
    )
    monkeypatch.setattr(
        google_conversions,
        "upload_click_conversion",
        lambda *a, **kw: google_calls.append(kw) or {},
    )

    resp = api.post(
        "/api/track/landing",
        json={
            "client_id": seeded["client_a"],
            "session_key": "sess-lead-1",
            "landing_url": "https://alphahvac.com/offer?fbclid=CLICK123",
            "utm_source": "facebook",
            "utm_campaign": "summer",
            "fbclid": "CLICK123",
            "fbp": "fb.1.1700000000000.42",
            "gclid": "GCLID456",
        },
    )
    assert resp.status_code == 201

    resp = api.post(
        "/api/track/lead",
        json={
            "client_id": seeded["client_a"],
            "session_key": "sess-lead-1",
            "email": " John_Smith@Gmail.com ",
            "phone": "(650) 555-1212",
            "first_name": "John",
            "event_id": "pixel-evt-abc",  # the browser pixel's eventID
            "event_source_url": "https://alphahvac.com/offer",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_id"] == "pixel-evt-abc"
    statuses = {d["platform"]: d["status"] for d in body["dispatched"]}
    assert statuses == {"meta": "sent", "google": "sent"}

    # Meta payload: dedup key is the pixel's eventID; PII hashed to spec;
    # fbc derived from the captured fbclid in cookie format; fbp forwarded.
    [meta_call] = meta_calls
    [event] = meta_call["events"]
    assert event["event_id"] == "pixel-evt-abc"
    assert event["event_name"] == "Lead"
    assert event["action_source"] == "website"
    assert meta_call["code"] is None  # never a test code on production sends
    ud = event["user_data"]
    assert ud["em"] == [sha("john_smith@gmail.com")]
    assert ud["ph"] == [sha("16505551212")]
    assert re.fullmatch(r"fb\.1\.\d+\.CLICK123", ud["fbc"])
    assert ud["fbp"] == "fb.1.1700000000000.42"

    # Google upload: gclid from the landing event + hashed identifiers,
    # order_id doubles as the dedup key. (Gmail rule strips periods and
    # +suffixes — underscores stay.)
    [google_call] = google_calls
    assert google_call["gclid"] == "GCLID456"
    assert {"hashed_email": sha("john_smith@gmail.com")} in google_call["identifiers"]
    assert {"hashed_phone_number": sha("+16505551212")} in google_call["identifiers"]
    assert google_call["order_id"] == "pixel-evt-abc"

    # One record per lead: contact linked back onto the landing event.
    events_resp = api.get(
        f"/api/attribution/landing-events?client_id={seeded['client_a']}",
        headers=team_headers,
    )
    row = next(
        e for e in events_resp.json() if e["session_key"] == "sess-lead-1"
    )
    assert row["contact_id"] == body["contact_id"]
    assert row["fbclid"] == "CLICK123" and row["gclid"] == "GCLID456"

    # And the dispatch log shows both sends.
    log = api.get(
        f"/api/conversions/log?client_id={seeded['client_a']}",
        headers=team_headers,
    ).json()
    ours = [e for e in log if e["event_id"] == "pixel-evt-abc"]
    assert {e["dispatch"]["platform"] for e in ours} == {"meta", "google"}
    assert all(e["dispatch"]["status"] == "sent" for e in ours)
    meta_log = next(e for e in ours if e["dispatch"]["platform"] == "meta")
    # Log records which keys were matched on — never the values.
    assert "em" in meta_log["dispatch"]["match_keys"]


def test_lead_without_landing_ping_still_captures(api, seeded, google_setup, monkeypatch):
    """Direct submit with no prior landing ping: click IDs posted with the
    form create the landing row — capture degrades, never disappears."""
    monkeypatch.setattr(
        meta_capi, "send_events", lambda *a, **kw: {"events_received": 1}
    )
    monkeypatch.setattr(
        google_conversions, "upload_click_conversion", lambda *a, **kw: {}
    )
    resp = api.post(
        "/api/track/lead",
        json={
            "client_id": seeded["client_a"],
            "session_key": "sess-no-ping",
            "email": "late@example.com",
            "gclid": "LATE_GCLID",
            "utm_source": "google",
        },
    )
    assert resp.status_code == 201
    db = SessionLocal()
    from app.models.attribution import LandingEvent

    row = (
        db.query(LandingEvent)
        .filter_by(client_id=seeded["client_a"], session_key="sess-no-ping")
        .one()
    )
    assert row.gclid == "LATE_GCLID"
    assert row.contact_id is not None
    db.close()


def test_dispatch_failure_isolation(api, seeded, google_setup, monkeypatch):
    """Meta down never blocks the Google send for the same lead."""

    def boom(*a, **kw):
        raise RuntimeError("meta 500")

    monkeypatch.setattr(meta_capi, "send_events", boom)
    monkeypatch.setattr(
        google_conversions, "upload_click_conversion", lambda *a, **kw: {}
    )
    resp = api.post(
        "/api/track/lead",
        json={
            "client_id": seeded["client_a"],
            "session_key": "sess-iso",
            "email": "iso@example.com",
        },
    )
    assert resp.status_code == 201
    statuses = {d["platform"]: d["status"] for d in resp.json()["dispatched"]}
    assert statuses["meta"] == "failed"
    assert statuses["google"] == "sent"  # EC-for-Leads path: email, no gclid


def test_google_skipped_without_anything_to_match(api, seeded, google_setup, monkeypatch):
    monkeypatch.setattr(
        meta_capi, "send_events", lambda *a, **kw: {"events_received": 1}
    )
    resp = api.post(
        "/api/track/lead",
        json={
            "client_id": seeded["client_a"],
            "session_key": "sess-nothing",
            "first_name": "Anon",  # Meta can match on fn; Google EC cannot
        },
    )
    assert resp.status_code == 201
    statuses = {d["platform"]: d["status"] for d in resp.json()["dispatched"]}
    assert statuses["google"] == "skipped"
    assert statuses["meta"] == "sent"


def test_unknown_client_404(api):
    resp = api.post(
        "/api/track/lead",
        json={"client_id": "nope", "session_key": "s", "email": "a@b.com"},
    )
    assert resp.status_code == 404


# --- EMQ + test send ---


def test_emq_endpoint(api, seeded, team_headers, google_setup, monkeypatch):
    monkeypatch.setattr(
        meta_capi,
        "fetch_event_match_quality",
        lambda token, ds: [
            {
                "event_name": "Lead",
                "composite_score": 7.2,
                "match_keys": [{"identifier": "email", "coverage_pct": 100}],
            }
        ],
    )
    resp = api.get(
        f"/api/conversions/emq?client_id={seeded['client_a']}",
        headers=team_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dataset_id"] == "ds_123"
    assert body["events"][0]["composite_score"] == 7.2

    # EMQ for another org's client: 404, not someone else's score.
    resp = api.get(
        f"/api/conversions/emq?client_id={seeded['client_a']}",
        headers={"Authorization": "Bearer bogus"},
    )
    assert resp.status_code == 401


def test_emq_org_isolation(api, seeded, org2_headers):
    resp = api.get(
        f"/api/conversions/emq?client_id={seeded['client_a']}",
        headers=org2_headers,
    )
    assert resp.status_code == 404


def test_test_send_uses_test_event_code(api, seeded, team_headers, google_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(
        meta_capi,
        "send_events",
        lambda token, ds, events, test_event_code=None: calls.append(
            test_event_code
        )
        or {"events_received": 1},
    )
    resp = api.post(
        "/api/conversions/test-send",
        json={
            "client_id": seeded["client_a"],
            "platform": "meta",
            "email": "test@example.com",
        },
        headers=team_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["results"][0]["status"] == "sent"
    assert calls == ["TEST123"]  # the configured test_event_code rode along

    # Test sends are flagged in the log.
    log = api.get(
        f"/api/conversions/log?client_id={seeded['client_a']}",
        headers=team_headers,
    ).json()
    assert any(e["dispatch"]["is_test"] for e in log)


def test_test_send_org2_isolated(api, seeded, org2_headers):
    resp = api.post(
        "/api/conversions/test-send",
        json={"client_id": seeded["client_a"], "platform": "meta"},
        headers=org2_headers,
    )
    assert resp.status_code == 404  # other org: invisible, not forbidden


def test_conversion_event_org_scoping_in_db(seeded, google_setup):
    """Every Phase 5 row carries the two-level tenant keys."""
    db = SessionLocal()
    for model in (ConversionConfig, ConversionEvent):
        for row in db.query(model).all():
            assert row.organization_id == seeded["org"]
            assert row.client_id == seeded["client_a"]
    db.close()
