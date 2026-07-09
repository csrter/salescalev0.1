"""Login-session (device) tracking behind the JWT.

Each login creates a UserSession row whose id rides in the access token as
`sid`. get_current_user validates it every request (missing/revoked ⇒ 401) and
refreshes last_seen. Powers the sessions list, per-device revoke, and
logout-everywhere. Legacy tokens with no `sid` still work until they expire.
"""
import datetime as dt
from typing import List, Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.base import utcnow
from ..models.core import User, UserSession

# Don't write last_seen on every request — only when it's this stale.
_TOUCH_INTERVAL = dt.timedelta(seconds=60)


def _client_ip(request: Request) -> Optional[str]:
    if get_settings().trust_forwarded_for:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def create(db: Session, user: User, request: Request) -> str:
    """Create a session for a fresh login and return its id (the token's sid)."""
    sess = UserSession(
        user_id=user.id,
        user_agent=(request.headers.get("user-agent") or "")[:400] or None,
        ip=_client_ip(request),
        last_seen_at=utcnow(),
    )
    db.add(sess)
    db.flush()
    return sess.id


def get_active(db: Session, sid: str, user_id: str) -> Optional[UserSession]:
    sess = db.get(UserSession, sid)
    if sess is None or sess.revoked or sess.user_id != user_id:
        return None
    return sess


def touch(db: Session, sess: UserSession) -> bool:
    """Refresh last_seen if it's stale. Returns True if it wrote (so the caller
    knows to commit)."""
    last = sess.last_seen_at
    if last.tzinfo is None:  # SQLite hands back naive datetimes
        last = last.replace(tzinfo=dt.timezone.utc)
    if utcnow() - last > _TOUCH_INTERVAL:
        sess.last_seen_at = utcnow()
        return True
    return False


def list_for_user(db: Session, user_id: str) -> List[UserSession]:
    return (
        db.execute(
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked.is_(False))
            .order_by(UserSession.last_seen_at.desc())
        )
        .scalars()
        .all()
    )


def revoke_one(db: Session, sid: str, user_id: str) -> bool:
    sess = db.get(UserSession, sid)
    if sess is None or sess.user_id != user_id:
        return False
    sess.revoked = True
    return True


def revoke_all(db: Session, user_id: str, except_sid: Optional[str] = None) -> None:
    for sess in (
        db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked.is_(False)
            )
        )
        .scalars()
        .all()
    ):
        if sess.id != except_sid:
            sess.revoked = True
