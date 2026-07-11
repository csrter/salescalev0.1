"""Phase 13 — invites, multi-org membership, seats, and the membership audit
trail. Every acceptance check in PHASE_13_TEAMS_SEATS.md has a test here or
in test_team_roles.py; UI hiding is never load-bearing."""
import re

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.core import Organization, User
from app.models.crm import CrmTask
from app.models.email import EmailLog
from app.models.team import (
    MembershipAuditEntry,
    OrganizationInvite,
    OrganizationMembership,
)

PW = "invite-pass-123"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": org,
            "email": email,
            "password": PW,
            "full_name": "T",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(body):
    return {"Authorization": f"Bearer {body['access_token']}"}


def _login(api, email, password=PW):
    r = api.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _last_invite_token(to_address):
    """The raw token exists only in the email — read it back from email_log
    the way the invitee reads their inbox."""
    db = SessionLocal()
    entry = (
        db.query(EmailLog)
        .filter(EmailLog.to_address == to_address)
        .order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
        .first()
    )
    db.close()
    assert entry is not None, f"no invite email logged for {to_address}"
    m = re.search(r"\?invite=([\w\-~.]+)", entry.body)
    assert m, entry.body
    return m.group(1)


def _audit_actions(org_id):
    db = SessionLocal()
    rows = (
        db.query(MembershipAuditEntry)
        .filter(MembershipAuditEntry.organization_id == org_id)
        .order_by(MembershipAuditEntry.created_at)
        .all()
    )
    actions = [(r.action, r.target_email) for r in rows]
    db.close()
    return actions


# --- happy path: new user ---


def test_invite_new_user_full_flow(api):
    owner = _signup(api, "Invite Co", "owner@inviteco.com")
    h = _headers(owner)

    r = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "Newbie@inviteco.com", "role": "member"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "newbie@inviteco.com"  # normalized
    assert r.json()["status"] == "pending"

    token = _last_invite_token("newbie@inviteco.com")

    # Public lookup drives the accept page: no account yet → signup path.
    look = api.get(f"/api/orgs/invites/lookup?token={token}")
    assert look.status_code == 200
    assert look.json() == {
        "organization_name": "Invite Co",
        "email": "newbie@inviteco.com",
        "role": "member",
        "status": "pending",
        "account_exists": False,
    }

    r = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": token, "full_name": "New Member", "password": PW},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "member"
    assert body["organization_id"] == owner["organization_id"]
    # Token possession proves inbox control — the account starts verified.
    assert body["email_verified"] is True

    # The invite is single-use.
    again = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": token, "full_name": "Imposter", "password": PW},
    )
    assert again.status_code == 400

    members = api.get("/api/orgs/me/members", headers=h).json()
    assert {"owner@inviteco.com", "newbie@inviteco.com"} == {
        m["email"] for m in members
    }

    actions = _audit_actions(owner["organization_id"])
    assert ("invite_sent", "newbie@inviteco.com") in actions
    assert ("invite_accepted", "newbie@inviteco.com") in actions


# --- happy path: existing user + org switcher ---


def test_invite_existing_user_and_org_switch(api):
    org_a = _signup(api, "Agency A", "owner@agency-a.com")
    org_b = _signup(api, "Agency B", "owner@agency-b.com")

    r = api.post(
        "/api/orgs/me/invites",
        headers=_headers(org_a),
        json={"email": "owner@agency-b.com", "role": "admin"},
    )
    assert r.status_code == 201, r.text
    token = _last_invite_token("owner@agency-b.com")

    look = api.get(f"/api/orgs/invites/lookup?token={token}").json()
    assert look["account_exists"] is True

    # Accepting while logged in attaches and switches the active org.
    r = api.post(
        "/api/orgs/invites/accept",
        headers=_headers(org_b),
        json={"token": token},
    )
    assert r.status_code == 200, r.text
    joined = r.json()
    assert joined["organization_id"] == org_a["organization_id"]
    assert joined["role"] == "admin"
    bh = _headers(joined)

    # Both memberships are visible, with per-org roles.
    mine = api.get("/api/orgs/mine", headers=bh).json()
    by_org = {m["organization_id"]: m for m in mine}
    assert by_org[org_a["organization_id"]]["role"] == "admin"
    assert by_org[org_b["organization_id"]]["role"] == "owner"
    assert by_org[org_a["organization_id"]]["is_active_org"] is True

    # Switch back home: role and tenant scope follow the membership.
    r = api.post(
        "/api/orgs/switch",
        headers=bh,
        json={"organization_id": org_b["organization_id"]},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "owner"
    assert r.json()["organization_name"] == "Agency B"

    # Switching into an org they don't belong to → 404, existence not leaked.
    stranger = _signup(api, "Agency C", "owner@agency-c.com")
    r = api.post(
        "/api/orgs/switch",
        headers=_headers(stranger),
        json={"organization_id": org_a["organization_id"]},
    )
    assert r.status_code == 404


def test_invite_email_must_match_accepting_account(api):
    org = _signup(api, "Match Co", "owner@matchco.com")
    outsider = _signup(api, "Outsider Co", "owner@outsiderco.com")
    api.post(
        "/api/orgs/me/invites",
        headers=_headers(org),
        json={"email": "invited@matchco.com", "role": "member"},
    )
    token = _last_invite_token("invited@matchco.com")
    r = api.post(
        "/api/orgs/invites/accept", headers=_headers(outsider), json={"token": token}
    )
    assert r.status_code == 403
    assert "invited@matchco.com" in r.json()["detail"]


# --- send-side rules ---


def test_invite_role_gates(api, member_headers, client_a_headers):
    # Members and client users can't touch the invite surface at all.
    assert (
        api.post(
            "/api/orgs/me/invites",
            headers=member_headers,
            json={"email": "x@x.com", "role": "member"},
        ).status_code
        == 403
    )
    assert (
        api.get("/api/orgs/me/invites", headers=client_a_headers).status_code == 403
    )

    # Admins invite members, but only the Owner invites admins.
    org = _signup(api, "Gate Co", "owner@gateco.com")
    oh = _headers(org)
    api.post(
        "/api/orgs/me/invites",
        headers=oh,
        json={"email": "admin@gateco.com", "role": "admin"},
    )
    token = _last_invite_token("admin@gateco.com")
    admin = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": token, "full_name": "Admin", "password": PW},
    ).json()
    ah = _headers(admin)
    assert (
        api.post(
            "/api/orgs/me/invites",
            headers=ah,
            json={"email": "m@gateco.com", "role": "member"},
        ).status_code
        == 201
    )
    assert (
        api.post(
            "/api/orgs/me/invites",
            headers=ah,
            json={"email": "a2@gateco.com", "role": "admin"},
        ).status_code
        == 403
    )


def test_inviting_existing_member_blocked_with_clear_message(api):
    org = _signup(api, "Dup Co", "owner@dupco.com")
    h = _headers(org)
    r = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "owner@dupco.com", "role": "member"},
    )
    assert r.status_code == 409
    assert "already a member" in r.json()["detail"]


def test_reinvite_supersedes_old_invite(api):
    org = _signup(api, "Supersede Co", "owner@ssco.com")
    h = _headers(org)
    api.post(
        "/api/orgs/me/invites", headers=h, json={"email": "p@ssco.com", "role": "member"}
    )
    first_token = _last_invite_token("p@ssco.com")
    api.post(
        "/api/orgs/me/invites", headers=h, json={"email": "p@ssco.com", "role": "member"}
    )
    second_token = _last_invite_token("p@ssco.com")
    assert first_token != second_token

    # Old token is dead; seat reservation didn't double.
    r = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": first_token, "full_name": "P", "password": PW},
    )
    assert r.status_code == 400
    seats = api.get("/api/orgs/me/seats", headers=h).json()
    assert seats["pending_invites"] == 1

    r = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": second_token, "full_name": "P", "password": PW},
    )
    assert r.status_code == 201


def test_revoke_and_resend(api):
    org = _signup(api, "Revoke Co", "owner@revokeco.com")
    h = _headers(org)
    inv = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "r@revokeco.com", "role": "member"},
    ).json()
    token = _last_invite_token("r@revokeco.com")

    r = api.delete(f"/api/orgs/me/invites/{inv['id']}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert (
        api.post(
            "/api/orgs/invites/accept-signup",
            json={"token": token, "full_name": "R", "password": PW},
        ).status_code
        == 400
    )

    # Resend of a revoked invite is refused; a fresh invite works, and its
    # resend rotates the token (old link dies).
    assert (
        api.post(f"/api/orgs/me/invites/{inv['id']}/resend", headers=h).status_code
        == 400
    )
    inv2 = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "r@revokeco.com", "role": "member"},
    ).json()
    old_token = _last_invite_token("r@revokeco.com")
    assert (
        api.post(f"/api/orgs/me/invites/{inv2['id']}/resend", headers=h).status_code
        == 200
    )
    new_token = _last_invite_token("r@revokeco.com")
    assert old_token != new_token
    assert (
        api.post(
            "/api/orgs/invites/accept-signup",
            json={"token": old_token, "full_name": "R", "password": PW},
        ).status_code
        == 400
    )
    assert (
        api.post(
            "/api/orgs/invites/accept-signup",
            json={"token": new_token, "full_name": "R", "password": PW},
        ).status_code
        == 201
    )


def test_expired_invite_rejected(api):
    org = _signup(api, "Expiry Co", "owner@expiryco.com")
    h = _headers(org)
    inv = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "late@expiryco.com", "role": "member"},
    ).json()
    token = _last_invite_token("late@expiryco.com")

    db = SessionLocal()
    row = db.get(OrganizationInvite, inv["id"])
    row.expires_at = utcnow().replace(year=2000)
    db.commit()
    db.close()

    r = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": token, "full_name": "Late", "password": PW},
    )
    assert r.status_code == 400
    assert "expired" in r.json()["detail"]
    listed = api.get("/api/orgs/me/invites", headers=h).json()
    assert next(i for i in listed if i["id"] == inv["id"])["status"] == "expired"


# --- seats ---


def test_pending_invites_count_against_seats(api):
    h = _headers(_signup(api, "Seatful Co", "owner@seatful.com"))
    # starter = 5 seats; owner occupies 1 → 4 invites fill the plan.
    for i in range(4):
        assert (
            api.post(
                "/api/orgs/me/invites",
                headers=h,
                json={"email": f"i{i}@seatful.com", "role": "member"},
            ).status_code
            == 201
        )
    r = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "i5@seatful.com", "role": "member"},
    )
    assert r.status_code == 402
    seats = api.get("/api/orgs/me/seats", headers=h).json()
    assert seats == {"used": 1, "pending_invites": 4, "limit": 5, "plan": "starter"}


def test_accept_blocked_when_seats_filled_after_send(api):
    h = _headers(_signup(api, "Squeeze Co", "owner@squeeze.com"))
    inv = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "late@squeeze.com", "role": "member"},
    )
    assert inv.status_code == 201
    token = _last_invite_token("late@squeeze.com")

    # Seats fill up after the invite went out: 3 direct-adds are allowed (the
    # pending invite reserves the 5th seat)…
    for i in range(3):
        assert (
            api.post(
                "/api/orgs/me/members",
                headers=h,
                json={
                    "email": f"d{i}@squeeze.com",
                    "password": PW,
                    "full_name": f"D{i}",
                    "role": "member",
                },
            ).status_code
            == 201
        )
    # …and the send-gate correctly refuses a 4th while the invite is out.
    assert (
        api.post(
            "/api/orgs/me/members",
            headers=h,
            json={
                "email": "d4@squeeze.com",
                "password": PW,
                "full_name": "D4",
                "role": "member",
            },
        ).status_code
        == 402
    )
    # Simulate losing the race anyway (e.g. a concurrent accept landed first):
    # a 5th membership appears outside this request path.
    from app.security import hash_password

    db = SessionLocal()
    org_id = api.get("/api/orgs/me", headers=h).json()["id"]
    racer = User(
        organization_id=org_id,
        email="racer@squeeze.com",
        hashed_password=hash_password(PW),
        full_name="Racer",
        role="member",
    )
    db.add(racer)
    db.flush()
    db.add(
        OrganizationMembership(
            organization_id=org_id, user_id=racer.id, role="member"
        )
    )
    db.commit()
    db.close()

    # Accept must block rather than over-provision, with a message pointing
    # at the org admin.
    r = api.post(
        "/api/orgs/invites/accept-signup",
        json={"token": token, "full_name": "Late", "password": PW},
    )
    assert r.status_code == 402
    assert "admin" in r.json()["detail"].lower()


# --- last-Owner protection & ownership transfer ---


def test_transfer_ownership_and_last_owner_guards(api):
    org = _signup(api, "Handover Co", "founder@handover.com")
    oh = _headers(org)
    mid = api.post(
        "/api/orgs/me/members",
        headers=oh,
        json={
            "email": "successor@handover.com",
            "password": PW,
            "full_name": "Successor",
            "role": "member",
        },
    ).json()["id"]

    # The only Owner can't be removed, demoted, or deactivated by any path.
    founder_id = next(
        m["id"]
        for m in api.get("/api/orgs/me/members", headers=oh).json()
        if m["role"] == "owner"
    )
    sh = _login(api, "successor@handover.com")
    assert (
        api.request("DELETE", f"/api/orgs/me/members/{founder_id}", headers=sh).status_code
        == 403  # members can't manage at all
    )
    assert (
        api.patch(
            f"/api/orgs/me/members/{founder_id}", headers=oh, json={"role": "member"}
        ).status_code
        == 400  # self-change blocked
    )

    r = api.post(
        "/api/orgs/me/transfer-ownership", headers=oh, json={"member_id": mid}
    )
    assert r.status_code == 200, r.text
    roles = {
        m["email"]: m["role"] for m in api.get("/api/orgs/me/members", headers=oh).json()
    }
    assert roles["successor@handover.com"] == "owner"
    assert roles["founder@handover.com"] == "admin"
    assert ("ownership_transferred", "successor@handover.com") in _audit_actions(
        org["organization_id"]
    )

    # New owner (fresh token: the old one predates the role change... the
    # mirror is read from the DB, so the existing login keeps working).
    nh = _login(api, "successor@handover.com")

    # Now the successor is the only Owner: demote/deactivate/remove all block.
    assert (
        api.patch(
            f"/api/orgs/me/members/{mid}", headers=nh, json={"role": "member"}
        ).status_code
        == 400  # self
    )
    demote = api.patch(
        f"/api/orgs/me/members/{mid}", headers=_login(api, "founder@handover.com"), json={"role": "member"}
    )
    assert demote.status_code == 403  # admins never change roles

    # Co-owner path: keep ownership while promoting, then demoting one works.
    co = api.post(
        "/api/orgs/me/transfer-ownership",
        headers=nh,
        json={"member_id": founder_id, "demote_self": False},
    )
    assert co.status_code == 200
    roles = {
        m["email"]: m["role"] for m in api.get("/api/orgs/me/members", headers=nh).json()
    }
    assert roles == {
        "founder@handover.com": "owner",
        "successor@handover.com": "owner",
    }
    # Two owners → demoting one is fine; demoting the last is not.
    assert (
        api.patch(
            f"/api/orgs/me/members/{founder_id}", headers=nh, json={"role": "admin"}
        ).status_code
        == 200
    )
    assert (
        api.patch(
            f"/api/orgs/me/members/{mid}",
            headers=_login(api, "founder@handover.com"),
            json={"role": "member"},
        ).status_code
        == 403  # founder is admin again — role changes are Owner-only
    )
    r = api.request(
        "DELETE",
        f"/api/orgs/me/members/{mid}",
        headers=_login(api, "founder@handover.com"),
    )
    assert r.status_code == 400  # owners are never removable


# --- member removal ---


def test_remove_member_kills_sessions_and_reassigns_records(api, team_headers, seeded):
    # Atlas Reach (agency plan, has clients) — create a member with open work.
    r = api.post(
        "/api/orgs/me/members",
        headers=team_headers,
        json={
            "email": "leaver@atlasreach.com",
            "password": PW,
            "full_name": "Leaver",
            "role": "member",
        },
    )
    assert r.status_code == 201, r.text
    leaver_id = r.json()["id"]
    leaver_headers = _login(api, "leaver@atlasreach.com")

    db = SessionLocal()
    task = CrmTask(
        organization_id=seeded["org"],
        client_id=seeded["client_a"],
        title="Follow up with lead",
        assigned_to_user_id=leaver_id,
    )
    done = CrmTask(
        organization_id=seeded["org"],
        client_id=seeded["client_a"],
        title="Already done",
        assigned_to_user_id=leaver_id,
        completed_at=utcnow(),
    )
    db.add_all([task, done])
    db.commit()
    task_id, done_id = task.id, done.id
    db.close()

    # The leaver has a live session before removal…
    assert api.get("/api/orgs/me", headers=leaver_headers).status_code == 200

    r = api.request(
        "DELETE", f"/api/orgs/me/members/{leaver_id}", headers=team_headers
    )
    assert r.status_code == 200, r.text

    # …and a dead one immediately after.
    assert api.get("/api/orgs/me", headers=leaver_headers).status_code == 401

    members = api.get("/api/orgs/me/members", headers=team_headers).json()
    assert "leaver@atlasreach.com" not in {m["email"] for m in members}

    db = SessionLocal()
    owner_id = (
        db.query(User).filter(User.email == "owner@atlasreach.com").one().id
    )
    open_task = db.get(CrmTask, task_id)
    done_task = db.get(CrmTask, done_id)
    # Open work reassigned to the remover; history untouched; nothing deleted.
    assert open_task.assigned_to_user_id == owner_id
    assert done_task.assigned_to_user_id == leaver_id
    # Sole-org user with no memberships left can't log in.
    leaver = db.query(User).filter(User.email == "leaver@atlasreach.com").one()
    assert leaver.is_active is False
    db.close()
    assert (
        api.post(
            "/api/auth/login",
            json={"email": "leaver@atlasreach.com", "password": PW},
        ).status_code
        == 401
    )
    assert ("member_removed", "leaver@atlasreach.com") in _audit_actions(seeded["org"])


def test_removed_multi_org_user_keeps_other_org(api):
    org_a = _signup(api, "Keep A", "owner@keepa.com")
    org_b = _signup(api, "Keep B", "owner@keepb.com")
    api.post(
        "/api/orgs/me/invites",
        headers=_headers(org_a),
        json={"email": "owner@keepb.com", "role": "member"},
    )
    token = _last_invite_token("owner@keepb.com")
    joined = api.post(
        "/api/orgs/invites/accept", headers=_headers(org_b), json={"token": token}
    ).json()
    b_user_id = None
    members = api.get("/api/orgs/me/members", headers=_headers(org_a)).json()
    b_user_id = next(m["id"] for m in members if m["email"] == "owner@keepb.com")

    r = api.request(
        "DELETE", f"/api/orgs/me/members/{b_user_id}", headers=_headers(org_a)
    )
    assert r.status_code == 200

    # Sessions died org-wide, but the account survives: log back in and land
    # in the remaining org.
    assert api.get("/api/orgs/me", headers=_headers(joined)).status_code == 401
    back = api.post(
        "/api/auth/login", json={"email": "owner@keepb.com", "password": PW}
    )
    assert back.status_code == 200
    assert back.json()["organization_id"] == org_b["organization_id"]
    assert back.json()["role"] == "owner"


def test_reassignment_target_must_be_team_member(api, team_headers):
    r = api.post(
        "/api/orgs/me/members",
        headers=team_headers,
        json={
            "email": "shortlived@atlasreach.com",
            "password": PW,
            "full_name": "SL",
            "role": "member",
        },
    )
    mid = r.json()["id"]
    r = api.request(
        "DELETE",
        f"/api/orgs/me/members/{mid}",
        headers=team_headers,
        json={"reassign_to_user_id": "not-a-member"},
    )
    assert r.status_code == 400
    r = api.request(
        "DELETE",
        f"/api/orgs/me/members/{mid}",
        headers=team_headers,
        json={"reassign_to_user_id": mid},
    )
    assert r.status_code == 400


# --- tenant isolation on the new tables ---


def test_invites_and_audit_are_org_scoped(api, org2_headers):
    org = _signup(api, "Scoped Co", "owner@scopedco.com")
    h = _headers(org)
    inv = api.post(
        "/api/orgs/me/invites",
        headers=h,
        json={"email": "s@scopedco.com", "role": "member"},
    ).json()

    # Another org can't see, revoke, or resend it — 404, existence not leaked.
    other = api.get("/api/orgs/me/invites", headers=org2_headers).json()
    assert inv["id"] not in {i["id"] for i in other}
    assert (
        api.delete(f"/api/orgs/me/invites/{inv['id']}", headers=org2_headers).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/orgs/me/invites/{inv['id']}/resend", headers=org2_headers
        ).status_code
        == 404
    )

    # Audit trail likewise stays home.
    for entry in api.get("/api/orgs/me/membership-audit", headers=org2_headers).json():
        assert entry["target_email"] != "s@scopedco.com"


def test_seats_and_audit_role_gates(api, member_headers, client_a_headers, team_headers):
    # Usage is self-service for the whole team; the audit trail is admin+.
    assert api.get("/api/orgs/me/seats", headers=member_headers).status_code == 200
    assert api.get("/api/orgs/me/seats", headers=client_a_headers).status_code == 403
    assert (
        api.get("/api/orgs/me/membership-audit", headers=member_headers).status_code
        == 403
    )
    assert (
        api.get("/api/orgs/me/membership-audit", headers=team_headers).status_code
        == 200
    )


def test_org_invite_rate_limit_bucket(monkeypatch):
    """Per-org invite sends share one bucket regardless of caller IP."""
    from app import ratelimit
    from app.config import get_settings
    from fastapi import HTTPException

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    key = "org_invites:test-org-bucket"
    for _ in range(30):
        ratelimit.enforce_bucket(key, 30, 3600)
    try:
        ratelimit.enforce_bucket(key, 30, 3600)
        assert False, "31st send in the window should have been limited"
    except HTTPException as e:
        assert e.status_code == 429
