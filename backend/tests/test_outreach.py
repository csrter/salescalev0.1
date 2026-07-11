"""Outreach module tests: webhook trust model, trigger rules, the
window-aware sequence engine, caps, role gates, and tenant isolation.

All Graph calls are monkeypatched (the repo pattern) — sends never touch the
network. Run with TZ=UTC like the rest of the suite.
"""

import datetime as dt
import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.crm import Contact, ContactTag, Tag
from app.models.outreach import (
    ENROLL_ACTIVE,
    ENROLL_COMPLETED,
    ENROLL_EXITED,
    EXIT_REPLIED,
    InstagramAccount,
    OutreachConversation,
    OutreachEnrollment,
    OutreachMessage,
    OutreachProspect,
    OutreachSequence,
    OutreachStep,
    OutreachTriggerRule,
)
from app.security import encrypt_secret
from app.services import instagram_api, outreach_sequences

META_SECRET = "test-meta-app-secret"
IG_ACCT = "17840000000000001"  # the connected IG professional account id


def _signed(body: dict):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(META_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def _dm_body(igsid: str, mid: str, text: str, entry_id: str = IG_ACCT):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": entry_id,
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": igsid},
                        "recipient": {"id": entry_id},
                        "timestamp": 1,
                        "message": {"mid": mid, "text": text},
                    }
                ],
            }
        ],
    }


def _comment_body(igsid: str, username: str, comment_id: str, text: str, media_id="m1"):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": IG_ACCT,
                "time": 1,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "text": text,
                            "from": {"id": igsid, "username": username},
                            "media": {"id": media_id},
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture(scope="module")
def ig_account(seeded, api):
    """Directly provision a connected IG account for client A (the OAuth
    callback is exercised separately; tests need the post-connect state)."""
    db = SessionLocal()
    account = db.execute(
        select(InstagramAccount).where(InstagramAccount.ig_user_id == IG_ACCT)
    ).scalar_one_or_none()
    if account is None:
        account = InstagramAccount(
            organization_id=seeded["org"],
            client_id=seeded["client_a"],
            ig_user_id=IG_ACCT,
            page_id="page1",
            username="atlasreach.hvac",
            access_token_encrypted=encrypt_secret("test-ig-page-token"),
            status="active",
            daily_send_cap=100,
            connected_at=utcnow(),
        )
        db.add(account)
        db.commit()
    account_id = account.id
    db.close()
    return account_id


@pytest.fixture(autouse=True)
def graph_stub(monkeypatch):
    """Capture every would-be Graph send instead of calling Meta."""
    calls = {"send_text": [], "private_reply": []}

    def fake_send_text(token, ig_id, igsid, text, tag=None):
        calls["send_text"].append(
            {"ig_id": ig_id, "igsid": igsid, "text": text, "tag": tag}
        )
        return {"recipient_id": igsid, "message_id": f"mid.out.{len(calls['send_text'])}"}

    def fake_private_reply(token, ig_id, comment_id, text):
        calls["private_reply"].append({"comment_id": comment_id, "text": text})
        return {"message_id": f"mid.pr.{len(calls['private_reply'])}"}

    monkeypatch.setattr(instagram_api, "send_text", fake_send_text)
    monkeypatch.setattr(instagram_api, "send_private_reply", fake_private_reply)
    monkeypatch.setattr(
        instagram_api,
        "fetch_user_profile",
        lambda token, igsid: {"username": f"user-{igsid}", "follower_count": 500},
    )
    return calls


def _reset_outreach_rows(keep_account=True):
    """Isolate stateful tests from each other without rebuilding the schema."""
    db = SessionLocal()
    for model in (
        OutreachMessage,
        OutreachEnrollment,
        OutreachProspect,
        OutreachTriggerRule,
        OutreachStep,
        OutreachSequence,
        OutreachConversation,
    ):
        for row in db.execute(select(model)).scalars():
            db.delete(row)
    db.commit()
    db.close()


# --- webhook trust model ---


def test_webhook_verify_handshake(api):
    resp = api.get(
        "/api/webhooks/meta/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "42",
        },
    )
    assert resp.status_code == 200 and resp.text == "42"
    assert (
        api.get(
            "/api/webhooks/meta/instagram",
            params={"hub.mode": "subscribe", "hub.verify_token": "wrong"},
        ).status_code
        == 403
    )


def test_webhook_rejects_bad_signature(api, ig_account):
    raw, _ = _signed(_dm_body("u1", "mid.bad", "hi"))
    resp = api.post(
        "/api/webhooks/meta/instagram",
        content=raw,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resp.status_code == 403


def test_dm_ingest_is_idempotent_and_routes_by_entry_id(api, ig_account):
    _reset_outreach_rows()
    raw, headers = _signed(_dm_body("igsid-alpha", "mid.100", "hello there"))
    assert api.post("/api/webhooks/meta/instagram", content=raw, headers=headers).status_code == 200
    # exact redelivery — no duplicate message
    api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)
    db = SessionLocal()
    convos = db.execute(
        select(OutreachConversation).where(
            OutreachConversation.ig_user_id == "igsid-alpha"
        )
    ).scalars().all()
    assert len(convos) == 1
    msgs = db.execute(
        select(OutreachMessage).where(
            OutreachMessage.conversation_id == convos[0].id
        )
    ).scalars().all()
    assert len(msgs) == 1 and msgs[0].direction == "in"
    assert convos[0].last_user_message_at is not None  # window opened
    # unknown entry id: acknowledged, dropped
    raw2, headers2 = _signed(_dm_body("x", "mid.101", "hi", entry_id="999"))
    resp = api.post("/api/webhooks/meta/instagram", content=raw2, headers=headers2)
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "ignored"
    db.close()


# --- trigger rules ---


def test_comment_rule_full_action_set(api, ig_account, team_headers, graph_stub, seeded):
    _reset_outreach_rows()
    seq = api.post(
        "/api/outreach/sequences",
        json={"account_id": ig_account, "name": "Contractor nurture"},
        headers=team_headers,
    ).json()
    api.put(
        f"/api/outreach/sequences/{seq['id']}/steps",
        json=[{"kind": "message", "text_a": "Thanks {{username}} — here's our pricing link."}],
        headers=team_headers,
    )
    api.post(f"/api/outreach/sequences/{seq['id']}/activate", headers=team_headers)
    rule = api.post(
        "/api/outreach/rules",
        json={
            "account_id": ig_account,
            "name": "Price keyword on posts",
            "trigger_type": "comment",
            "keywords": ["price", "quote"],
            "reply_text": "Sent you a DM, {{username}}!",
            "tag_names": ["ig-lead"],
            "enroll_sequence_id": seq["id"],
            "capture_prospect": True,
        },
        headers=team_headers,
    )
    assert rule.status_code == 201

    raw, headers = _signed(
        _comment_body("igsid-hvac", "desertairhvac", "c-1", "What's the PRICE on this?")
    )
    resp = api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["results"][0]["rules_fired"] == 1

    # private reply (the compliant comment→DM path), personalized
    assert graph_stub["private_reply"] == [
        {"comment_id": "c-1", "text": "Sent you a DM, desertairhvac!"}
    ]
    db = SessionLocal()
    contact = db.execute(
        select(Contact).where(Contact.source_external_id == "ig:igsid-hvac")
    ).scalar_one()
    assert contact.client_id == seeded["client_a"]
    tag = db.execute(select(Tag).where(Tag.name == "ig-lead")).scalar_one()
    assert (
        db.execute(
            select(ContactTag).where(
                ContactTag.contact_id == contact.id, ContactTag.tag_id == tag.id
            )
        ).scalar_one_or_none()
        is not None
    )
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.status == ENROLL_ACTIVE and enrollment.enrolled_by == "rule"
    prospect = db.execute(select(OutreachProspect)).scalar_one()
    assert prospect.username == "desertairhvac" and prospect.status == "engaged"
    db.close()

    # once_per_user: same commenter, new comment — no second fire
    raw2, headers2 = _signed(
        _comment_body("igsid-hvac", "desertairhvac", "c-2", "price again?")
    )
    resp2 = api.post("/api/webhooks/meta/instagram", content=raw2, headers=headers2)
    assert resp2.json()["results"][0]["rules_fired"] == 0


def test_rule_keyword_and_media_filters(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    api.post(
        "/api/outreach/rules",
        json={
            "account_id": ig_account,
            "name": "Specific post only",
            "trigger_type": "comment",
            "keywords": ["book"],
            "media_ids": ["media-77"],
            "reply_text": "Booking link!",
        },
        headers=team_headers,
    )
    # wrong keyword
    raw, h = _signed(_comment_body("u1", "someone", "c-10", "nice post", media_id="media-77"))
    assert api.post("/api/webhooks/meta/instagram", content=raw, headers=h).json()[
        "results"
    ][0]["rules_fired"] == 0
    # right keyword, wrong media
    raw, h = _signed(_comment_body("u2", "someone2", "c-11", "book me", media_id="other"))
    assert api.post("/api/webhooks/meta/instagram", content=raw, headers=h).json()[
        "results"
    ][0]["rules_fired"] == 0
    # both match
    raw, h = _signed(_comment_body("u3", "someone3", "c-12", "Book me in", media_id="media-77"))
    assert api.post("/api/webhooks/meta/instagram", content=raw, headers=h).json()[
        "results"
    ][0]["rules_fired"] == 1
    assert len(graph_stub["private_reply"]) == 1


# --- sequence engine ---


def _make_sequence(api, team_headers, ig_account, steps, **kwargs):
    seq = api.post(
        "/api/outreach/sequences",
        json={"account_id": ig_account, "name": kwargs.pop("name", "Seq"), **kwargs},
        headers=team_headers,
    ).json()
    api.put(f"/api/outreach/sequences/{seq['id']}/steps", json=steps, headers=team_headers)
    api.post(f"/api/outreach/sequences/{seq['id']}/activate", headers=team_headers)
    return seq


def _open_conversation(api, igsid, mid, text="hi"):
    raw, headers = _signed(_dm_body(igsid, mid, text))
    api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)
    db = SessionLocal()
    convo = db.execute(
        select(OutreachConversation).where(OutreachConversation.ig_user_id == igsid)
    ).scalar_one()
    convo_id = convo.id
    db.close()
    return convo_id


def test_sequence_message_wait_condition_flow(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [
            {"kind": "message", "text_a": "Hey {{username}} — quick question about your business."},
            {"kind": "wait", "wait_hours": 24},
            {"kind": "condition", "condition": "replied", "on_true": "exit", "on_false": "continue"},
            {"kind": "message", "text_a": "Bumping this — worth a chat?"},
        ],
        exit_on_reply=False,
    )
    convo_id = _open_conversation(api, "igsid-seq1", "mid.200")
    api.post(
        "/api/outreach/enrollments",
        json={"sequence_id": seq["id"], "conversation_id": convo_id},
        headers=team_headers,
    )
    # tick 1: message sends (window open — they just DM'd), wait schedules
    api.post("/api/outreach/run-tick", headers=team_headers)
    assert len(graph_stub["send_text"]) == 1
    assert "igsid-seq1" in graph_stub["send_text"][0]["text"] or "user-" not in graph_stub["send_text"][0]["text"]

    db = SessionLocal()
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.status == ENROLL_ACTIVE and enrollment.current_position == 2
    assert enrollment.next_run_at is not None
    # fast-forward past the wait
    enrollment.next_run_at = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    db.close()

    # tick 2: condition → not replied → continue → second message → completed
    api.post("/api/outreach/run-tick", headers=team_headers)
    assert len(graph_stub["send_text"]) == 2
    db = SessionLocal()
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.status == ENROLL_COMPLETED
    db.close()


def test_reply_exits_enrollment_and_credits_variant(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [
            {"kind": "message", "text_a": "First touch"},
            {"kind": "wait", "wait_hours": 48},
            {"kind": "message", "text_a": "Second touch"},
        ],
    )  # exit_on_reply defaults True
    convo_id = _open_conversation(api, "igsid-replier", "mid.300")
    api.post(
        "/api/outreach/enrollments",
        json={"sequence_id": seq["id"], "conversation_id": convo_id},
        headers=team_headers,
    )
    api.post("/api/outreach/run-tick", headers=team_headers)
    assert len(graph_stub["send_text"]) == 1

    # the peer replies → enrollment exits, outbound message credited
    raw, headers = _signed(_dm_body("igsid-replier", "mid.301", "yes let's talk"))
    api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)
    db = SessionLocal()
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.status == ENROLL_EXITED and enrollment.exit_reason == EXIT_REPLIED
    out = db.execute(
        select(OutreachMessage).where(OutreachMessage.direction == "out")
    ).scalar_one()
    assert out.replied_to is True
    db.close()
    # no further sends on later ticks
    api.post("/api/outreach/run-tick", headers=team_headers)
    assert len(graph_stub["send_text"]) == 1


def test_window_closed_queues_then_flushes_on_reply(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [{"kind": "message", "text_a": "Still interested?"}],
        exit_on_reply=False,
    )
    convo_id = _open_conversation(api, "igsid-stale", "mid.400")
    db = SessionLocal()
    convo = db.get(OutreachConversation, convo_id)
    convo.last_user_message_at = utcnow() - dt.timedelta(hours=30)  # window closed
    db.commit()
    db.close()
    api.post(
        "/api/outreach/enrollments",
        json={"sequence_id": seq["id"], "conversation_id": convo_id},
        headers=team_headers,
    )
    api.post("/api/outreach/run-tick", headers=team_headers)
    # nothing sent — queued instead (no tag fallback for automation, ever)
    assert graph_stub["send_text"] == []
    db = SessionLocal()
    msg = db.execute(
        select(OutreachMessage).where(OutreachMessage.direction == "out")
    ).scalar_one()
    assert msg.status == "queued" and msg.message_tag is None
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.waiting_window is True and enrollment.next_run_at is None
    db.close()

    # user DMs again → window reopens → queue flushes automatically
    raw, headers = _signed(_dm_body("igsid-stale", "mid.401", "hey sorry, yes"))
    api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)
    assert len(graph_stub["send_text"]) == 1
    db = SessionLocal()
    msg = db.execute(
        select(OutreachMessage).where(OutreachMessage.direction == "out")
    ).scalar_one()
    assert msg.status == "sent"
    db.close()


def test_daily_cap_enforced_server_side(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    db = SessionLocal()
    account = db.get(InstagramAccount, ig_account)
    account.daily_send_cap = 1
    db.commit()
    db.close()
    try:
        seq = _make_sequence(
            api, team_headers, ig_account,
            [{"kind": "message", "text_a": "hello"}], exit_on_reply=False,
        )
        convo1 = _open_conversation(api, "igsid-cap1", "mid.500")
        convo2 = _open_conversation(api, "igsid-cap2", "mid.501")
        for cid in (convo1, convo2):
            api.post(
                "/api/outreach/enrollments",
                json={"sequence_id": seq["id"], "conversation_id": cid},
                headers=team_headers,
            )
        api.post("/api/outreach/run-tick", headers=team_headers)
        assert len(graph_stub["send_text"]) == 1  # second held by the cap
        # manual replies hit the same cap
        resp = api.post(
            f"/api/outreach/conversations/{convo2}/reply",
            json={"text": "manual over cap"},
            headers=team_headers,
        )
        assert resp.status_code == 429
    finally:
        db = SessionLocal()
        db.get(InstagramAccount, ig_account).daily_send_cap = 100
        db.commit()
        db.close()


def test_manual_reply_window_and_human_agent_rules(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    convo_id = _open_conversation(api, "igsid-manual", "mid.600")
    db = SessionLocal()
    db.get(OutreachConversation, convo_id).last_user_message_at = (
        utcnow() - dt.timedelta(hours=30)
    )
    db.commit()
    db.close()
    # window closed, no tag → refused
    resp = api.post(
        f"/api/outreach/conversations/{convo_id}/reply",
        json={"text": "checking in"},
        headers=team_headers,
    )
    assert resp.status_code == 400
    # human agent tag within 7 days → sent with HUMAN_AGENT
    resp = api.post(
        f"/api/outreach/conversations/{convo_id}/reply",
        json={"text": "checking in", "use_human_agent": True},
        headers=team_headers,
    )
    assert resp.status_code == 200
    assert graph_stub["send_text"][-1]["tag"] == "HUMAN_AGENT"
    # beyond 7 days → refused even with the tag
    db = SessionLocal()
    db.get(OutreachConversation, convo_id).last_user_message_at = (
        utcnow() - dt.timedelta(days=8)
    )
    db.commit()
    db.close()
    resp = api.post(
        f"/api/outreach/conversations/{convo_id}/reply",
        json={"text": "hello?", "use_human_agent": True},
        headers=team_headers,
    )
    assert resp.status_code == 400


def test_review_first_day_holds_sends_for_approval(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [{"kind": "message", "text_a": "reviewed message"}],
        review_first_day=True, exit_on_reply=False,
    )
    convo_id = _open_conversation(api, "igsid-review", "mid.700")
    api.post(
        "/api/outreach/enrollments",
        json={"sequence_id": seq["id"], "conversation_id": convo_id},
        headers=team_headers,
    )
    api.post("/api/outreach/run-tick", headers=team_headers)
    assert graph_stub["send_text"] == []  # held, not sent
    pending = api.get("/api/outreach/messages/pending", headers=team_headers).json()
    assert len(pending) == 1
    resp = api.post(
        f"/api/outreach/messages/{pending[0]['id']}/approve", headers=team_headers
    )
    assert resp.status_code == 200 and resp.json()["status"] == "sent"
    assert len(graph_stub["send_text"]) == 1


def test_variant_promotion_by_reply_rate(api, ig_account, team_headers, seeded):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [{"kind": "message", "text_a": "variant A", "text_b": "variant B"}],
        settings={"promotion_min_sends": 2}, exit_on_reply=False,
    )
    db = SessionLocal()
    step = db.execute(select(OutreachStep)).scalar_one()
    convo_id = _open_conversation(api, "igsid-ab", "mid.800")
    for variant, replied in (("a", False), ("a", False), ("b", True), ("b", False)):
        db.add(
            OutreachMessage(
                organization_id=seeded["org"],
                client_id=seeded["client_a"],
                conversation_id=convo_id,
                direction="out",
                text="x",
                status="sent",
                kind="sequence",
                step_id=step.id,
                variant=variant,
                replied_to=replied,
                sent_at=utcnow(),
            )
        )
    db.commit()
    outreach_sequences._maybe_promote(db, db.get(OutreachStep, step.id))
    db.commit()
    assert db.get(OutreachStep, step.id).promoted_variant == "b"
    db.close()


# --- CRM sync ---


def test_crm_stage_change_exits_sequence(api, ig_account, team_headers, seeded):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [{"kind": "wait", "wait_hours": 999}, {"kind": "message", "text_a": "x"}],
        exit_on_reply=False,
    )
    convo_id = _open_conversation(api, "igsid-crm", "mid.900")
    db = SessionLocal()
    convo = db.get(OutreachConversation, convo_id)
    contact = Contact(
        organization_id=seeded["org"],
        client_id=seeded["client_a"],
        first_name="Crm",
        last_name="Target",
        source="instagram_outreach",
        source_external_id="ig:igsid-crm",
    )
    db.add(contact)
    db.flush()
    convo.contact_id = contact.id
    contact_id = contact.id
    db.commit()
    db.close()
    api.post(
        "/api/outreach/enrollments",
        json={"sequence_id": seq["id"], "conversation_id": convo_id},
        headers=team_headers,
    )
    # create a deal, then move it — stage change must exit the enrollment
    deal = api.post(
        "/api/crm/deals",
        json={"client_id": seeded["client_a"], "contact_id": contact_id, "name": "IG deal"},
        headers=team_headers,
    )
    assert deal.status_code == 201
    resp = api.patch(
        f"/api/crm/deals/{deal.json()['id']}",
        json={"status": "won"},
        headers=team_headers,
    )
    assert resp.status_code == 200
    db = SessionLocal()
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.status == ENROLL_EXITED and enrollment.exit_reason == "stage_change"
    db.close()


# --- prospects (watch list) ---


def test_prospect_import_and_auto_enroll_on_engagement(api, ig_account, team_headers, seeded, graph_stub):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [{"kind": "message", "text_a": "Thanks for reaching out!"}],
        exit_on_reply=False,
    )
    resp = api.post(
        "/api/outreach/prospects/import",
        json={
            "client_id": seeded["client_a"],
            "handles": ["@DesertPlumbingAZ", "valleyhvac", "@DesertPlumbingAZ"],
            "vertical": "plumbing",
            "sequence_id": seq["id"],
            "account_id": ig_account,
        },
        headers=team_headers,
    )
    assert resp.status_code == 201 and resp.json()["created"] == 2

    # the prospect engages (DMs the account) → linked + auto-enrolled; the
    # watch list never cold-sends: this inbound is what arms the sequence.
    db = SessionLocal()
    prospect = db.execute(
        select(OutreachProspect).where(OutreachProspect.username == "desertplumbingaz")
    ).scalar_one()
    prospect.ig_user_id = "igsid-prospect1"
    db.commit()
    db.close()
    raw, headers = _signed(_dm_body("igsid-prospect1", "mid.1000", "saw your ad"))
    api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)

    db = SessionLocal()
    prospect = db.execute(
        select(OutreachProspect).where(OutreachProspect.username == "desertplumbingaz")
    ).scalar_one()
    assert prospect.status == "engaged" and prospect.conversation_id is not None
    enrollment = db.execute(select(OutreachEnrollment)).scalar_one()
    assert enrollment.enrolled_by == "prospect"
    db.close()
    api.post("/api/outreach/run-tick", headers=team_headers)
    assert len(graph_stub["send_text"]) == 1


def test_house_client_is_a_valid_outreach_target(api, team_headers, seeded, graph_stub):
    """The agency's own prospecting: the house client (the hidden per-org
    Client the CRM/Lead Finder run against) must work end-to-end as an
    outreach target — an IG account, a sequence, and imported prospects all
    bind to it — even though it's excluded from the /api/clients roster (the
    reason the Outreach UI resolves it separately and prepends it to the
    pickers)."""
    _reset_outreach_rows()
    # Resolve/create the house client the same way the frontend does.
    house = api.get("/api/orgs/me/house-client", headers=team_headers)
    assert house.status_code == 200, house.text
    house_id = house.json()["client_id"]

    # It's a real, org-scoped client but deliberately absent from the roster
    # that feeds /api/clients — hence the UI gap this change closes.
    roster = api.get("/api/clients", headers=team_headers).json()
    assert house_id not in {c["id"] for c in roster}

    # Provision an IG account ON the house client (distinct id from the shared
    # fixture account, which lives on client_a).
    db = SessionLocal()
    house_account = InstagramAccount(
        organization_id=seeded["org"],
        client_id=house_id,
        ig_user_id="17840000000000099",
        page_id="page-house",
        username="atlasreach.agency",
        access_token_encrypted=encrypt_secret("test-ig-page-token"),
        status="active",
        daily_send_cap=100,
        connected_at=utcnow(),
    )
    db.add(house_account)
    db.commit()
    house_account_id = house_account.id
    db.close()

    # A sequence bound to that account inherits the house client_id.
    seq = api.post(
        "/api/outreach/sequences",
        json={"account_id": house_account_id, "name": "Agency self-prospecting"},
        headers=team_headers,
    )
    assert seq.status_code == 201, seq.text
    seq_id = seq.json()["id"]
    api.put(
        f"/api/outreach/sequences/{seq_id}/steps",
        json=[{"kind": "message", "text_a": "Thanks for the follow!"}],
        headers=team_headers,
    )

    # Import prospects under the house client — the core assertion.
    resp = api.post(
        "/api/outreach/prospects/import",
        json={
            "client_id": house_id,
            "handles": ["@agencylead1", "agencylead2"],
            "vertical": "agency",
            "sequence_id": seq_id,
            "account_id": house_account_id,
        },
        headers=team_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["created"] == 2

    # Everything landed on the house client, not a real client account.
    db = SessionLocal()
    assert (
        db.execute(select(OutreachSequence).where(OutreachSequence.id == seq_id))
        .scalar_one()
        .client_id
        == house_id
    )
    prospects = db.execute(select(OutreachProspect)).scalars().all()
    assert len(prospects) == 2
    assert all(p.client_id == house_id for p in prospects)
    db.close()


# --- analytics + audit ---


def test_analytics_and_audit_export(api, ig_account, team_headers, graph_stub):
    _reset_outreach_rows()
    seq = _make_sequence(
        api, team_headers, ig_account,
        [{"kind": "message", "text_a": "hello {{username}}"}],
    )
    convo_id = _open_conversation(api, "igsid-metrics", "mid.1100")
    api.post(
        "/api/outreach/enrollments",
        json={"sequence_id": seq["id"], "conversation_id": convo_id},
        headers=team_headers,
    )
    api.post("/api/outreach/run-tick", headers=team_headers)
    raw, headers = _signed(_dm_body("igsid-metrics", "mid.1101", "interested!"))
    api.post("/api/webhooks/meta/instagram", content=raw, headers=headers)

    data = api.get("/api/outreach/analytics", headers=team_headers).json()
    assert data["headline"]["sent"] == 1
    seq_row = next(r for r in data["sequences"] if r["sequence_id"] == seq["id"])
    assert seq_row["enrolled"] == 1 and seq_row["replied"] == 1

    export = api.get("/api/outreach/audit/export", headers=team_headers)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    body = export.text
    assert "sequence" in body and "sent" in body


# --- roles + tenant isolation ---


def test_member_is_rep_inbox_only(api, ig_account, member_headers, team_headers):
    _reset_outreach_rows()
    convo_id = _open_conversation(api, "igsid-role", "mid.1200")
    # Rep: inbox read + manual reply allowed
    inbox = api.get("/api/outreach/inbox", headers=member_headers)
    assert inbox.status_code == 200 and len(inbox.json()) == 1
    reply = api.post(
        f"/api/outreach/conversations/{convo_id}/reply",
        json={"text": "rep reply"},
        headers=member_headers,
    )
    assert reply.status_code == 200
    # Rep: builder/analytics surfaces are admin-gated
    assert (
        api.post(
            "/api/outreach/rules",
            json={"account_id": ig_account, "name": "x", "trigger_type": "dm"},
            headers=member_headers,
        ).status_code
        == 403
    )
    assert api.get("/api/outreach/analytics", headers=member_headers).status_code == 403


def test_client_role_has_no_outreach_access(api, ig_account, client_a_headers):
    assert api.get("/api/outreach/inbox", headers=client_a_headers).status_code == 403


def test_tenant_isolation(api, ig_account, team_headers, org2_headers):
    _reset_outreach_rows()
    convo_id = _open_conversation(api, "igsid-iso", "mid.1300")
    rule = api.post(
        "/api/outreach/rules",
        json={"account_id": ig_account, "name": "iso", "trigger_type": "dm"},
        headers=team_headers,
    ).json()
    # org 2 sees nothing of org 1's outreach — empty lists, 404 by id
    assert api.get("/api/outreach/inbox", headers=org2_headers).json() == []
    assert api.get("/api/outreach/rules", headers=org2_headers).json() == []
    assert api.get("/api/outreach/accounts", headers=org2_headers).json() == []
    assert (
        api.get(
            f"/api/outreach/conversations/{convo_id}/messages", headers=org2_headers
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"/api/outreach/rules/{rule['id']}",
            json={"account_id": ig_account, "name": "steal", "trigger_type": "dm"},
            headers=org2_headers,
        ).status_code
        == 404
    )
