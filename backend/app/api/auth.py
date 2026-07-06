from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models.core import Organization, User
from ..schemas import LoginRequest, TokenResponse
from ..security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(
        body.password, user.hashed_password
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    org = db.get(Organization, user.organization_id)
    token = create_access_token(user.id, user.role, user.organization_id, user.client_id)
    return TokenResponse(
        access_token=token,
        role=user.role,
        organization_id=user.organization_id,
        organization_name=org.name,
        client_id=user.client_id,
        full_name=user.full_name,
    )
