"""Per-Organization platform API credentials (bring-your-own app).

Each agency configures its own Meta app / Google Ads OAuth client + developer
token, so connecting ad accounts is the tenant's responsibility, not the
platform operator's. Secret values are encrypted at rest with the same Fernet
key used for OAuth tokens; non-secret identifiers (app_id/client_id) are stored
plaintext so they can be shown back in the UI.
"""
import datetime as dt
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column


class IntegrationCredential(Base):
    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint("organization_id", "provider", name="uq_integration_org_provider"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # meta | google

    # Non-secret identifiers (safe to show): Meta app_id / Google client_id, and
    # Google's login customer id.
    public_id: Mapped[Optional[str]] = mapped_column(String(128))
    login_customer_id: Mapped[Optional[str]] = mapped_column(String(32))

    # Encrypted secrets: Meta app_secret; Google client_secret + developer_token.
    secret_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    secret2_encrypted: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = created_at_column()
