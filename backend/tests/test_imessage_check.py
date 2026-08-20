"""iMessage capability lookups (services/imessage_check).

The relay is stubbed at the httpx boundary — these assert the RULES the
module exists to enforce, not that BlueBubbles works:
  - a failed lookup must never be persisted as "not on iMessage"
  - the same number is looked up once, however many contacts share it
  - a fresh verdict is reused; a stale one is re-checked
"""

import datetime as dt

import pytest

from app.db import SessionLocal
from app.security import encrypt_secret
from app.models.base import utcnow
from app.models.core import Client
from app.models.crm import Contact
from app.models.sms_outreach import SmsAccount
from app.services import imessage_check


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _available(flag):
    return _Resp(200, {"status": 200, "data": {"available": flag}})


@pytest.fixture(scope="module")
def imc_org(api):
    """Own org: run_check sweeps every contact in the org, so a shared
    fixture would pull in other modules' seeded contacts. NOTE the signup
    email must be unique across the whole suite — test_imessage_outreach.py
    already claims owner@imessageco.com on the shared module-scoped DB."""
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "iMessage Check Co",
            "email": "owner@imsgcheckco.com",
            "password": "imsgcheckco-pass-1",
            "full_name": "iMessage Check Owner",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client_id = api.post(
        "/api/clients", json={"name": "iMessage Check Client"}, headers=headers
    ).json()["id"]
    return {
        "headers": headers,
        "org_id": body["organization_id"],
        "client_id": client_id,
    }


def _account(db, imc_org, name, from_number):
    acct = SmsAccount(
        organization_id=imc_org["org_id"],
        name=name,
        provider="bluebubbles",
        # BlueBubbles has no SID; the column is NOT NULL, and the real
        # connect endpoint stores "" for this provider.
        account_sid="",
        relay_url="https://relay.example",
        auth_token_encrypted=encrypt_secret("relay-pw"),
        from_number=from_number,
        status="active",
    )
    db.add(acct)
    db.commit()
    return acct


def _contact(db, imc_org, phone, **kw):
    c = Contact(
        organization_id=imc_org["org_id"],
        client_id=imc_org["client_id"],
        first_name=kw.pop("first_name", "Test"),
        phone=phone,
        **kw,
    )
    db.add(c)
    db.commit()
    return c


def test_failed_lookup_is_not_recorded_as_not_capable(imc_org, monkeypatch):
    """A relay blip must leave the flag None. Writing False here would
    silently misroute the contact to green-bubble SMS forever."""
    with SessionLocal() as db:
        _account(db, imc_org, "relay1", "+14805550100")
        cid = _contact(db, imc_org, "+14805550111").id

    calls = []

    def failing_get(url, **kw):
        calls.append(kw.get("params", {}).get("address"))
        return _Resp(500)

    monkeypatch.setattr(imessage_check.httpx, "get", failing_get)
    monkeypatch.setattr(imessage_check.time, "sleep", lambda s: None)
    imessage_check.run_check(imc_org["org_id"], [cid])

    # The lookup must actually have been attempted — otherwise this test
    # would pass vacuously on any early crash in run_check.
    assert calls == ["+14805550111"]
    with SessionLocal() as db:
        refreshed = db.get(Contact, cid)
        assert refreshed.imessage_capable is None
        assert refreshed.imessage_checked_at is None


def test_records_verdicts_and_dedupes_shared_numbers(imc_org, monkeypatch):
    with SessionLocal() as db:
        _account(db, imc_org, "relay2", "+14805550200")
        blue = _contact(db, imc_org, "+14805550222").id
        green = _contact(db, imc_org, "+14805550333").id
        dupe = _contact(db, imc_org, "+14805550222", first_name="Same Number").id

    calls = []

    def fake_get(url, **kw):
        addr = kw.get("params", {}).get("address")
        calls.append(addr)
        return _available(addr == "+14805550222")

    monkeypatch.setattr(imessage_check.httpx, "get", fake_get)
    monkeypatch.setattr(imessage_check.time, "sleep", lambda s: None)
    imessage_check.run_check(imc_org["org_id"], [blue, green, dupe])

    with SessionLocal() as db:
        assert db.get(Contact, blue).imessage_capable is True
        assert db.get(Contact, green).imessage_capable is False
        # Both rows carrying the same number get the verdict, from ONE lookup.
        assert db.get(Contact, dupe).imessage_capable is True
    assert sorted(calls) == ["+14805550222", "+14805550333"]


def test_fresh_verdict_reused_stale_rechecked(imc_org, monkeypatch):
    with SessionLocal() as db:
        _account(db, imc_org, "relay3", "+14805550300")
        fresh = _contact(db, imc_org, "+14805550444")
        fresh.imessage_capable = True
        fresh.imessage_checked_at = utcnow()
        stale = _contact(db, imc_org, "+14805550555")
        stale.imessage_capable = True
        stale.imessage_checked_at = utcnow() - dt.timedelta(
            days=imessage_check.RECHECK_AFTER_DAYS + 1
        )
        db.commit()
        fresh_id, stale_id = fresh.id, stale.id

    calls = []

    def fake_get(url, **kw):
        calls.append(kw.get("params", {}).get("address"))
        return _available(False)

    monkeypatch.setattr(imessage_check.httpx, "get", fake_get)
    monkeypatch.setattr(imessage_check.time, "sleep", lambda s: None)
    imessage_check.run_check(imc_org["org_id"], [fresh_id, stale_id])

    assert calls == ["+14805550555"]
    with SessionLocal() as db:
        assert db.get(Contact, fresh_id).imessage_capable is True
        assert db.get(Contact, stale_id).imessage_capable is False


def test_endpoint_400s_without_a_bluebubbles_account(api):
    """Twilio/Telnyx/Sendblue can't answer an Apple-side question, so the
    endpoint refuses rather than queueing a job that can only fail. Own org
    — the other tests in this module connect a relay to theirs."""
    body = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "No Relay Co",
            "email": "owner@norelayco.com",
            "password": "norelayco-pass-1",
            "full_name": "No Relay Owner",
        },
    ).json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    r = api.post(
        "/api/crm/contacts/imessage-check",
        json={},
        headers=headers,
    )
    assert r.status_code == 400
    assert "BlueBubbles" in r.json()["detail"]


def test_summary_endpoint_is_not_shadowed_by_the_contact_detail_route(
    api, imc_org
):
    """Regression: GET /contacts/imessage-summary is a literal path that has
    to be registered ABOVE GET /contacts/{contact_id}, or the catch-all
    swallows it and returns 404. Caught in the browser, not by a unit test —
    hence this one."""
    r = api.get(
        "/api/crm/contacts/imessage-summary", headers=imc_org["headers"]
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "total",
        "with_number",
        "checked",
        "imessage",
        "sms_only",
        "unchecked",
    }


def test_summary_scoped_to_one_client(api, imc_org):
    """Regression: scoping by client must not 500. TenantScope.get_or_404
    asserts obj.client_id, which a Client row has no attribute for."""
    r = api.get(
        f"/api/crm/contacts/imessage-summary?client_id={imc_org['client_id']}",
        headers=imc_org["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["with_number"] >= 0


def test_whole_crm_sweep_respects_the_client_scope(imc_org, monkeypatch):
    """The coverage card counts one client's leads, so its sweep must check
    that same population — not the whole org."""
    other = None
    with SessionLocal() as db:
        _account(db, imc_org, "relay4", "+14805550400")
        mine = _contact(db, imc_org, "+14805550666").id
        # contacts.client_id is NOT NULL — a real second client is the only
        # way to model "belongs to a different scope".
        second = Client(
            organization_id=imc_org["org_id"], name="iMessage Second Client"
        )
        db.add(second)
        db.commit()
        other_c = Contact(
            organization_id=imc_org["org_id"],
            client_id=second.id,
            first_name="Other client lead",
            phone="+14805550777",
        )
        db.add(other_c)
        db.commit()
        other = other_c.id

    calls = []

    def fake_get(url, **kw):
        calls.append(kw.get("params", {}).get("address"))
        return _available(True)

    monkeypatch.setattr(imessage_check.httpx, "get", fake_get)
    monkeypatch.setattr(imessage_check.time, "sleep", lambda s: None)
    imessage_check.run_check(imc_org["org_id"], None, client_id=imc_org["client_id"])

    assert "+14805550666" in calls
    assert "+14805550777" not in calls
    with SessionLocal() as db:
        assert db.get(Contact, mine).imessage_capable is True
        assert db.get(Contact, other).imessage_capable is None


def test_list_scoped_sweep_and_coverage(api, imc_org, monkeypatch):
    """A list is its own population: the sweep checks exactly its members,
    and /imessage/lists reports that list's numbers."""
    with SessionLocal() as db:
        _account(db, imc_org, "relay5", "+14805550500")
        inside = _contact(db, imc_org, "+14805550888").id
        outside = _contact(db, imc_org, "+14805550999").id

    lst = api.post(
        "/api/crm/lists",
        json={"client_id": imc_org["client_id"], "name": "iMessage Target List"},
        headers=imc_org["headers"],
    )
    assert lst.status_code in (200, 201), lst.text
    list_id = lst.json()["id"]
    add = api.post(
        f"/api/crm/lists/{list_id}/contacts",
        json={"contact_ids": [inside]},
        headers=imc_org["headers"],
    )
    assert add.status_code == 200, add.text

    calls = []

    def fake_get(url, **kw):
        calls.append(kw.get("params", {}).get("address"))
        return _available(True)

    monkeypatch.setattr(imessage_check.httpx, "get", fake_get)
    monkeypatch.setattr(imessage_check.time, "sleep", lambda s: None)
    imessage_check.run_check(imc_org["org_id"], None, list_id=list_id)

    assert calls == ["+14805550888"]
    with SessionLocal() as db:
        assert db.get(Contact, inside).imessage_capable is True
        assert db.get(Contact, outside).imessage_capable is None

    cov = api.get(
        f"/api/crm/imessage/lists?client_id={imc_org['client_id']}",
        headers=imc_org["headers"],
    )
    assert cov.status_code == 200, cov.text
    row = next(l for l in cov.json()["lists"] if l["id"] == list_id)
    assert row["with_number"] == 1
    assert row["imessage"] == 1
    assert row["unchecked"] == 0
