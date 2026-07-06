from typing import Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models.core import ROLE_CLIENT, ROLE_TEAM, User
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
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
    return user


def require_team(user: User = Depends(get_current_user)) -> User:
    """Gate for every write endpoint and Atlas Reach-internal data. The
    Client role is visibility-only; it never passes this gate."""
    if user.role != ROLE_TEAM:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Atlas Reach team role required")
    return user


class TenantScope:
    """Data-access-layer tenant filter.

    Every tenant-owned query goes through `filter()` (or `check_client_id()`
    for single-object access). Team users see everything; client users are
    pinned to their own client_id. A client user without a client_id is a
    misconfiguration and gets no access rather than all access.
    """

    def __init__(self, user: User):
        self.user = user
        self.is_team = user.role == ROLE_TEAM
        if self.is_team:
            self.client_id: Optional[str] = None
        else:
            if user.role != ROLE_CLIENT or not user.client_id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "No tenant scope")
            self.client_id = user.client_id

    def filter(self, stmt, model):
        if self.is_team:
            return stmt
        return stmt.where(model.client_id == self.client_id)

    def check_client_id(self, client_id: str) -> None:
        """404 (not 403) on cross-tenant access so existence of other
        tenants' objects is not leaked."""
        if not self.is_team and client_id != self.client_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    def get_or_404(self, db: Session, model, object_id: str):
        obj = db.get(model, object_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        self.check_client_id(obj.client_id)
        return obj


def get_scope(user: User = Depends(get_current_user)) -> TenantScope:
    return TenantScope(user)
