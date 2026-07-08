"""Per-Organization platform API credentials (bring-your-own app).

Owner/Admin enter their own Meta app + Google Ads OAuth client/developer token
here; the connect flows then use them. Secrets are write-only (encrypted at
rest, never returned) — reads report only whether a provider is configured and
by whom (this org's own credentials vs the operator's global fallback).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models.base import utcnow
from ..models.core import User
from ..models.integrations import IntegrationCredential
from ..security import encrypt_secret
from ..services import integration_creds

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class MetaCredentialsIn(BaseModel):
    app_id: str
    app_secret: str


class GoogleCredentialsIn(BaseModel):
    client_id: str
    client_secret: str
    developer_token: str
    login_customer_id: Optional[str] = None


class IntegrationStatusOut(BaseModel):
    provider: str
    configured: bool
    source: str  # organization | global | none
    public_id: Optional[str] = None  # non-secret identifier when org-configured


def _status(db: Session, org_id: str, provider: str) -> IntegrationStatusOut:
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == org_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row and row.public_id and row.secret_encrypted:
        return IntegrationStatusOut(
            provider=provider, configured=True, source="organization", public_id=row.public_id
        )
    resolved = (
        integration_creds.resolve_meta(db, org_id)
        if provider == "meta"
        else integration_creds.resolve_google(db, org_id)
    )
    if resolved.configured:
        return IntegrationStatusOut(provider=provider, configured=True, source="global")
    return IntegrationStatusOut(provider=provider, configured=False, source="none")


def _upsert(db: Session, org_id: str, provider: str, **fields) -> None:
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == org_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row is None:
        row = IntegrationCredential(organization_id=org_id, provider=provider)
        db.add(row)
    for k, v in fields.items():
        setattr(row, k, v)
    row.updated_at = utcnow()
    db.commit()


@router.get("", response_model=List[IntegrationStatusOut])
def list_integrations(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_status(db, user.organization_id, p) for p in ("meta", "google")]


@router.put("/meta", response_model=IntegrationStatusOut)
def set_meta(
    body: MetaCredentialsIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _upsert(
        db,
        user.organization_id,
        "meta",
        public_id=body.app_id.strip(),
        secret_encrypted=encrypt_secret(body.app_secret.strip()),
        secret2_encrypted=None,
        login_customer_id=None,
    )
    return _status(db, user.organization_id, "meta")


@router.put("/google", response_model=IntegrationStatusOut)
def set_google(
    body: GoogleCredentialsIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _upsert(
        db,
        user.organization_id,
        "google",
        public_id=body.client_id.strip(),
        secret_encrypted=encrypt_secret(body.client_secret.strip()),
        secret2_encrypted=encrypt_secret(body.developer_token.strip()),
        login_customer_id=(body.login_customer_id or "").strip() or None,
    )
    return _status(db, user.organization_id, "google")


@router.delete("/{provider}", response_model=IntegrationStatusOut)
def delete_integration(
    provider: str, user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    row = db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.organization_id == user.organization_id,
            IntegrationCredential.provider == provider,
        )
    ).scalar_one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return _status(db, user.organization_id, provider)
