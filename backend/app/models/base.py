import datetime as dt
import uuid

from sqlalchemy import DateTime, String
from sqlalchemy.orm import mapped_column


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def id_column():
    return mapped_column(String(36), primary_key=True, default=new_id)


def created_at_column():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
