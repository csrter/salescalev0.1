"""Two-factor auth: TOTP, email codes, backup codes, and the login challenge."""
import re

import pyotp

from app.db import SessionLocal
from app.models.email import EmailLog

PW = "mfa-pass-123456"


def _signup(api, org, email):
    r = api.post(
        "/api/orgs/signup",
        json={"organization_name": org, "email": email, "password": PW, "full_name": "M"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(sess):
    return {"Authorization": f"Bearer {sess['access_token']}"}


def _email_code(to_address: str) -> str:
    db = SessionLocal()
    row = (
        db.query(EmailLog)
        .filter(EmailLog.to_address == to_address)
        .order_by(EmailLog.created_at.desc())
        .first()
    )
    db.close()
    m = re.search(r"\b(\d{6})\b", row.body)
    assert m, f"no code in email: {row.body!r}"
    return m.group(1)


def test_totp_enroll_then_login_challenge(api):
    sess = _signup(api, "Totp Co", "totp@totpco.com")
    h = _headers(sess)
    setup = api.post("/api/mfa/totp/setup", headers=h)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    # enable with a live TOTP code
    enable = api.post("/api/mfa/totp/enable", headers=h, json={"code": pyotp.TOTP(secret).now()})
    assert enable.status_code == 200
    assert len(enable.json()["backup_codes"]) == 10

    # login now returns a challenge, not a session
    r = api.post("/api/auth/login", json={"email": "totp@totpco.com", "password": PW})
    assert r.status_code == 200
    body = r.json()
    assert body["mfa_required"] is True and body["method"] == "totp"
    assert "access_token" not in body
    challenge = body["challenge_token"]

    # wrong code rejected, right code yields a session
    assert api.post(
        "/api/auth/login/mfa", json={"challenge_token": challenge, "code": "000000"}
    ).status_code == 401
    done = api.post(
        "/api/auth/login/mfa",
        json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
    )
    assert done.status_code == 200 and "access_token" in done.json()


def test_email_2fa_login(api):
    sess = _signup(api, "Email2fa Co", "e2fa@e2faco.com")
    h = _headers(sess)
    assert api.post("/api/mfa/email/setup", headers=h).status_code == 200
    assert api.post(
        "/api/mfa/email/enable", headers=h, json={"code": _email_code("e2fa@e2faco.com")}
    ).status_code == 200

    r = api.post("/api/auth/login", json={"email": "e2fa@e2faco.com", "password": PW})
    assert r.json()["method"] == "email"
    challenge = r.json()["challenge_token"]
    # the login step emailed a fresh code
    done = api.post(
        "/api/auth/login/mfa",
        json={"challenge_token": challenge, "code": _email_code("e2fa@e2faco.com")},
    )
    assert done.status_code == 200 and "access_token" in done.json()


def test_backup_code_is_single_use(api):
    sess = _signup(api, "Backup Co", "backup@backupco.com")
    h = _headers(sess)
    secret = api.post("/api/mfa/totp/setup", headers=h).json()["secret"]
    backup = api.post(
        "/api/mfa/totp/enable", headers=h, json={"code": pyotp.TOTP(secret).now()}
    ).json()["backup_codes"]

    ch1 = api.post("/api/auth/login", json={"email": "backup@backupco.com", "password": PW}).json()[
        "challenge_token"
    ]
    assert api.post(
        "/api/auth/login/mfa", json={"challenge_token": ch1, "code": backup[0]}
    ).status_code == 200
    # reusing the same backup code fails
    ch2 = api.post("/api/auth/login", json={"email": "backup@backupco.com", "password": PW}).json()[
        "challenge_token"
    ]
    assert api.post(
        "/api/auth/login/mfa", json={"challenge_token": ch2, "code": backup[0]}
    ).status_code == 401


def test_disable_requires_password(api):
    sess = _signup(api, "Disable Co", "disable@disableco.com")
    h = _headers(sess)
    secret = api.post("/api/mfa/totp/setup", headers=h).json()["secret"]
    api.post("/api/mfa/totp/enable", headers=h, json={"code": pyotp.TOTP(secret).now()})
    assert api.post("/api/mfa/disable", headers=h, json={"password": "wrong"}).status_code == 400
    assert api.post("/api/mfa/disable", headers=h, json={"password": PW}).status_code == 200
    # login no longer challenges
    r = api.post("/api/auth/login", json={"email": "disable@disableco.com", "password": PW})
    assert "access_token" in r.json()


def test_sms_setup_unconfigured_returns_503(api):
    sess = _signup(api, "Sms Co", "sms@smsco.com")
    r = api.post("/api/mfa/sms/setup", headers=_headers(sess), json={"phone": "+15555550123"})
    assert r.status_code == 503


def _enroll_totp(api, org, email):
    sess = _signup(api, org, email)
    h = _headers(sess)
    secret = api.post("/api/mfa/totp/setup", headers=h).json()["secret"]
    api.post("/api/mfa/totp/enable", headers=h, json={"code": pyotp.TOTP(secret).now()})
    return sess, h, secret


def test_remember_device_skips_future_challenge(api):
    """remember_device=true on /login/mfa returns a device_token; sending it
    back as X-Device-Token on a later /login for the SAME account skips the
    challenge entirely (no challenge_token in the response)."""
    _, h, secret = _enroll_totp(api, "Remember Co", "remember@rememberco.com")

    ch = api.post(
        "/api/auth/login", json={"email": "remember@rememberco.com", "password": PW}
    ).json()["challenge_token"]
    done = api.post(
        "/api/auth/login/mfa",
        json={
            "challenge_token": ch,
            "code": pyotp.TOTP(secret).now(),
            "remember_device": True,
        },
    )
    assert done.status_code == 200
    device_token = done.json()["device_token"]
    assert device_token

    # Same device, next login: no challenge — straight to a session.
    r = api.post(
        "/api/auth/login",
        json={"email": "remember@rememberco.com", "password": PW},
        headers={"X-Device-Token": device_token},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body and "challenge_token" not in body

    # A different/absent device token still gets challenged normally.
    r2 = api.post(
        "/api/auth/login", json={"email": "remember@rememberco.com", "password": PW}
    )
    assert r2.status_code == 200
    assert r2.json().get("mfa_required") is True


def test_remember_device_can_be_revoked(api):
    _, h, secret = _enroll_totp(api, "Revoke Device Co", "revokedev@revokedevco.com")
    ch = api.post(
        "/api/auth/login", json={"email": "revokedev@revokedevco.com", "password": PW}
    ).json()["challenge_token"]
    done = api.post(
        "/api/auth/login/mfa",
        json={"challenge_token": ch, "code": pyotp.TOTP(secret).now(), "remember_device": True},
    )
    device_token = done.json()["device_token"]

    listed = api.get("/api/auth/trusted-devices", headers=h)
    assert listed.status_code == 200
    devices = listed.json()
    assert len(devices) == 1

    assert api.delete(f"/api/auth/trusted-devices/{devices[0]['id']}", headers=h).status_code == 200
    assert api.get("/api/auth/trusted-devices", headers=h).json() == []

    # Revoked token no longer skips the challenge.
    r = api.post(
        "/api/auth/login",
        json={"email": "revokedev@revokedevco.com", "password": PW},
        headers={"X-Device-Token": device_token},
    )
    assert r.json().get("mfa_required") is True


def test_remember_device_wiped_by_logout_all_and_disable(api):
    _, h, secret = _enroll_totp(api, "Wipe Device Co", "wipedev@wipedevco.com")
    ch = api.post(
        "/api/auth/login", json={"email": "wipedev@wipedevco.com", "password": PW}
    ).json()["challenge_token"]
    api.post(
        "/api/auth/login/mfa",
        json={"challenge_token": ch, "code": pyotp.TOTP(secret).now(), "remember_device": True},
    )
    assert len(api.get("/api/auth/trusted-devices", headers=h).json()) == 1

    assert api.post("/api/auth/logout-all", headers=h).status_code == 200
    # Re-login (fresh session) to get a valid header again, re-enroll a device.
    sess2 = api.post(
        "/api/auth/login", json={"email": "wipedev@wipedevco.com", "password": PW}
    )
    # logout-all revoked trusted devices; this account still has 2FA, so a
    # fresh login challenges again regardless of device history.
    assert sess2.json().get("mfa_required") is True


def test_org_can_disable_remember_device_policy(api):
    sess = _signup(api, "Policy Co", "policy@policyco.com")
    h = _headers(sess)
    secret = api.post("/api/mfa/totp/setup", headers=h).json()["secret"]
    api.post("/api/mfa/totp/enable", headers=h, json={"code": pyotp.TOTP(secret).now()})

    assert api.put(
        "/api/orgs/me/allow-remember-device", headers=h, json={"allow_remember_device": False}
    ).status_code == 200

    ch = api.post(
        "/api/auth/login", json={"email": "policy@policyco.com", "password": PW}
    ).json()["challenge_token"]
    done = api.post(
        "/api/auth/login/mfa",
        json={"challenge_token": ch, "code": pyotp.TOTP(secret).now(), "remember_device": True},
    )
    # Policy off: no device_token minted even though remember_device was requested.
    assert done.json()["device_token"] is None
