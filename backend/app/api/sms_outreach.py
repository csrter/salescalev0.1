"""SMS outreach API — framework surface: accounts (BYO Twilio, creds
write-only), suppression, usage. Campaign/step/enrollment routes are built by
the campaign-engine work on top of this file — mirror api/email_outreach.py's
shapes exactly (same paths under /api/sms, same status codes, 1-indexed step
positions, PUT /steps upsert-in-place with stable ids, activate guard,
archive endpoint).

Gates mirror email: require_team reads, require_admin config, client role
locked out entirely (require_team refuses it). Isolation is the org-scoped
_scoped_get pattern (404-not-403).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantScope, get_scope, require_admin, require_team
from ..models.base import utcnow
from ..models.core import Organization, User
from ..models.sms_outreach import (
    SMS_ACCOUNT_ACTIVE,
    SMS_ACCOUNT_ERROR,
    SMS_SUPPRESS_MANUAL,
    SmsAccount,
    SmsCampaign,
    SmsSuppression,
)
from ..security import encrypt_secret
from ..services import entitlements, sms_consent, sms_send

router = APIRouter(prefix="/api/sms", tags=["sms-outreach"])


def _org(db: Session, user: User) -> Organization:
    return db.get(Organization, user.organization_id)


def _scoped_get(db: Session, scope: TenantScope, model, object_id: str):
    obj = db.get(model, object_id)
    if obj is None or obj.organization_id != scope.organization_id:
        raise HTTPException(404, "Not found")
    return obj


# --- serialization (auth token never included) ---


def _account_out(db: Session, a: SmsAccount) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "provider": a.provider,
        "account_sid": a.account_sid,
        "from_number": a.from_number,
        "messaging_service_sid": a.messaging_service_sid,
        "status": a.status,
        "error_detail": a.error_detail,
        "daily_send_cap": a.daily_send_cap,
        "sends_today": sms_send.sends_today(db, a),
        "created_at": a.created_at.isoformat(),
    }


# --- accounts ---


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_sid: str = Field(min_length=10, max_length=64)
    auth_token: str = Field(min_length=8, max_length=200)
    from_number: Optional[str] = Field(default=None, max_length=20)
    messaging_service_sid: Optional[str] = Field(default=None, max_length=64)
    daily_send_cap: int = Field(default=200, ge=1, le=5000)


class AccountPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    auth_token: Optional[str] = Field(default=None, min_length=8, max_length=200)
    from_number: Optional[str] = Field(default=None, max_length=20)
    messaging_service_sid: Optional[str] = Field(default=None, max_length=64)
    daily_send_cap: Optional[int] = Field(default=None, ge=1, le=5000)


@router.get("/accounts")
def list_accounts(
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(SmsAccount).where(
            SmsAccount.organization_id == scope.organization_id
        )
    ).scalars()
    return [_account_out(db, a) for a in rows]


@router.post("/accounts", status_code=201)
def create_account(
    body: AccountIn,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    if not body.from_number and not body.messaging_service_sid:
        raise HTTPException(
            422, "Provide a from number or a Messaging Service SID."
        )
    account = SmsAccount(
        organization_id=scope.organization_id,
        name=body.name.strip(),
        account_sid=body.account_sid.strip(),
        auth_token_encrypted=encrypt_secret(body.auth_token.strip()),
        from_number=sms_consent.normalize_phone(body.from_number),
        messaging_service_sid=(body.messaging_service_sid or "").strip() or None,
        daily_send_cap=body.daily_send_cap,
    )
    ok, detail = sms_send.verify_credentials(account)
    account.status = SMS_ACCOUNT_ACTIVE if ok else SMS_ACCOUNT_ERROR
    account.error_detail = None if ok else detail
    db.add(account)
    db.commit()
    return _account_out(db, account)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    body: AccountPatch,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = _scoped_get(db, scope, SmsAccount, account_id)
    if body.name is not None:
        account.name = body.name.strip()
    if body.auth_token is not None:
        account.auth_token_encrypted = encrypt_secret(body.auth_token.strip())
    if body.from_number is not None:
        account.from_number = sms_consent.normalize_phone(body.from_number)
    if body.messaging_service_sid is not None:
        account.messaging_service_sid = body.messaging_service_sid.strip() or None
    if body.daily_send_cap is not None:
        account.daily_send_cap = body.daily_send_cap
    db.commit()
    return _account_out(db, account)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = _scoped_get(db, scope, SmsAccount, account_id)
    in_use = db.execute(
        select(SmsCampaign.id).where(SmsCampaign.account_id == account.id).limit(1)
    ).scalar_one_or_none()
    if in_use is not None:
        raise HTTPException(
            409, "A campaign still uses this number — archive it first."
        )
    db.delete(account)
    db.commit()


@router.post("/accounts/{account_id}/test")
def test_account(
    account_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    account = _scoped_get(db, scope, SmsAccount, account_id)
    ok, detail = sms_send.verify_credentials(account)
    account.status = SMS_ACCOUNT_ACTIVE if ok else SMS_ACCOUNT_ERROR
    account.error_detail = None if ok else detail
    db.commit()
    return {"ok": ok, "detail": detail}


# --- suppression ---


class SuppressIn(BaseModel):
    phone: str = Field(min_length=7, max_length=25)
    detail: Optional[str] = Field(default=None, max_length=300)


@router.get("/suppression")
def list_suppression(
    user: User = Depends(require_team),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(SmsSuppression)
        .where(SmsSuppression.organization_id == scope.organization_id)
        .order_by(SmsSuppression.created_at.desc())
    ).scalars()
    return [
        {
            "id": s.id,
            "phone_e164": s.phone_e164,
            "reason": s.reason,
            "detail": s.detail,
            "created_at": s.created_at.isoformat(),
        }
        for s in rows
    ]


@router.post("/suppression", status_code=201)
def add_suppression(
    body: SuppressIn,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    number = sms_consent.normalize_phone(body.phone)
    if not number:
        raise HTTPException(422, "Not a usable phone number.")
    sms_consent.record_opt_out(
        db, scope.organization_id, number, SMS_SUPPRESS_MANUAL, detail=body.detail
    )
    db.commit()
    return {"ok": True, "phone_e164": number}


@router.delete("/suppression/{suppression_id}", status_code=204)
def delete_suppression(
    suppression_id: str,
    user: User = Depends(require_admin),
    scope: TenantScope = Depends(get_scope),
    db: Session = Depends(get_db),
):
    row = _scoped_get(db, scope, SmsSuppression, suppression_id)
    db.delete(row)
    db.commit()


# --- usage ---


@router.get("/usage")
def usage(user: User = Depends(require_team), db: Session = Depends(get_db)):
    org = _org(db, user)
    return {"sends": entitlements.sms_outreach_usage(db, org), "plan": org.plan}
