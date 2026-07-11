from typing import Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models.core import (
    ADMIN_ROLES,
    ORG_SUSPENDED,
    ROLE_CLIENT,
    ROLE_OWNER,
    TEAM_ROLES,
    Organization,
    User,
)
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    # Session revocation: a password reset or logout-all bumps token_version,
    # invalidating every token issued before it. (Tokens minted before this
    # feature carry no tv and default to 0, matching a fresh user.)
    if payload.get("tv", 0) != user.token_version:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Session expired — please log in again"
        )
    # Per-device session: reject a token whose session was individually revoked
    # (session viewing / revoke). Legacy tokens carry no sid and skip this.
    sid = payload.get("sid")
    if sid is not None:
        from .services import sessions

        sess = sessions.get_active(db, sid, user.id)
        if sess is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This session was ended")
        if sessions.touch(db, sess):
            db.commit()
    request.state.session_id = sid
    # Enforce org suspension on every request, not just at login — otherwise a
    # suspended org's existing tokens keep working until they expire (up to
    # jwt_expire_minutes). Super-admins (operators) are exempt.
    if not is_superadmin(user):
        org = db.get(Organization, user.organization_id)
        if org is None or org.status == ORG_SUSPENDED:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization suspended")
    return user


def require_team(user: User = Depends(get_current_user)) -> User:
    """Gate for write endpoints and Organization-internal data. The Client
    role is visibility-only; it never passes this gate."""
    if user.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Team role required")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Owner/Admin gate: managing clients, platform connections, and team
    members. Members do day-to-day campaign work but not this."""
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin or Owner role required")
    return user


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Owner-only gate: team role changes (and billing, from Phase 8)."""
    if user.role != ROLE_OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    return user


def require_verified_email(user: User = Depends(get_current_user)) -> User:
    """Phase 12 task 13: with email verification enforced, unverified accounts
    are blocked from inviting members and connecting ad accounts. The login
    gate alone doesn't cover sessions issued before the flag was flipped (it
    only runs at login), so the sensitive actions re-check here. No-op when
    require_email_verification is off (dev/desktop default), same as login."""
    if (
        get_settings().require_email_verification
        and not user.email_verified
        and not is_superadmin(user)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Verify your email address to use this feature",
        )
    return user


def is_superadmin(user: User) -> bool:
    """Platform super-admin — a Salescale operator, not a tenant role. Derived
    solely from the SUPERADMIN_EMAILS allowlist; it cannot be set via signup or
    any API, so no request can escalate into it."""
    return user.email.lower() in get_settings().superadmin_email_set()


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    """Gate for the cross-tenant /api/admin surface. This is the ONE place that
    deliberately reads across Organizations — every endpoint behind it is
    platform-operator tooling, never reachable by a tenant user."""
    if not is_superadmin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super-admin required")
    return user


class TenantScope:
    """Data-access-layer tenant filter, enforced at both levels.

    Every user is pinned to exactly one organization_id — there is no role
    that sees across Organizations. Team roles see everything inside their
    Organization; client users are additionally pinned to their client_id.
    Every tenant-owned query goes through `filter()` (or `get_or_404()` for
    single-object access). A client user without a client_id is a
    misconfiguration and gets no access rather than all access.
    """

    def __init__(self, user: User):
        self.user = user
        self.is_team = user.role in TEAM_ROLES
        self.organization_id: str = user.organization_id
        if not self.organization_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tenant scope")
        if self.is_team:
            self.client_id: Optional[str] = None
        else:
            if user.role != ROLE_CLIENT or not user.client_id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "No tenant scope")
            self.client_id = user.client_id

    def filter(self, stmt, model):
        stmt = stmt.where(model.organization_id == self.organization_id)
        if not self.is_team:
            stmt = stmt.where(model.client_id == self.client_id)
        return stmt

    def check_organization_id(self, organization_id: str) -> None:
        """404 (not 403) on cross-organization access so existence of other
        tenants' objects is not leaked."""
        if organization_id != self.organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    def check_client_id(self, client_id: str) -> None:
        """Client-level pin, same 404-not-403 principle."""
        if not self.is_team and client_id != self.client_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    def get_or_404(self, db: Session, model, object_id: str):
        obj = db.get(model, object_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        self.check_organization_id(obj.organization_id)
        self.check_client_id(obj.client_id)
        return obj


def get_scope(user: User = Depends(get_current_user)) -> TenantScope:
    return TenantScope(user)


def mfa_gate(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> None:
    """Hard server-side enforcement of the org 2FA policy. Applied at the
    router level to the app-data routers: a team member of an org that requires
    2FA is blocked (403) until they enable it. The enrollment path
    (/api/mfa, /api/auth, /api/orgs/me) is deliberately NOT gated so they can
    comply. Super-admins are exempt (operator access).

    Tolerant of unauthenticated requests so it's safe on routers that also carry
    a public endpoint (e.g. Stripe/CRM webhooks): with no/invalid token it does
    nothing and the route's own auth (or lack of it) applies.
    """
    if credentials is None:
        return
    try:
        payload = decode_access_token(credentials.credentials)
    except pyjwt.PyJWTError:
        return  # the endpoint's own auth dependency will reject it
    user = db.get(User, payload.get("sub"))
    if user is None or is_superadmin(user):
        return
    if user.role in TEAM_ROLES and not user.mfa_method:
        org = db.get(Organization, user.organization_id)
        if org is not None and org.require_mfa:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Two-factor authentication is required by your organization — "
                "enable it to continue.",
            )


def bind_integration_creds(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    """Resolve this Organization's platform API credentials (its own, or the
    operator's global fallback) into the request context, so the platform-API
    services use the right ones. Applied to every router that talks to Meta or
    Google."""
    from .services import integration_creds

    integration_creds.bind(db, user.organization_id)
