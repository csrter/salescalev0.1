"""Platform discovery — the frontend renders connect buttons and the dashboard
platform filter from this instead of a hardcoded list, so a platform newly
registered in app/platforms.py appears in the UI with no frontend edit."""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import platforms as platform_registry
from ..deps import get_current_user
from ..models.core import User

router = APIRouter(prefix="/api/platforms", tags=["platforms"])


class PlatformOut(BaseModel):
    id: str
    name: str
    status: str
    coming_soon: bool
    connectable: bool
    supports_conversions: bool
    supports_lead_forms: bool
    supports_byo_creds: bool


@router.get("", response_model=List[PlatformOut])
def list_platforms(_: User = Depends(get_current_user)) -> List[PlatformOut]:
    return [
        PlatformOut(
            id=p.id,
            name=p.name,
            status=p.status,
            coming_soon=p.coming_soon,
            connectable=p.connectable,
            supports_conversions=p.supports_conversions,
            supports_lead_forms=p.supports_lead_forms,
            supports_byo_creds=p.supports_byo_creds,
        )
        for p in platform_registry.PLATFORMS.values()
    ]
