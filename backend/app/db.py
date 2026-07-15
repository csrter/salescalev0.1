from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _normalize_url(url: str):
    """Coerce a plain Postgres URL onto the psycopg (v3) driver.

    Supabase hands out connection strings like `postgres://...` or
    `postgresql://...`; SQLAlchemy would route those to psycopg2, which we
    don't install. Forcing the psycopg3 dialect lets a pasted Supabase URL
    work verbatim.
    """
    u = make_url(url)
    if u.drivername in ("postgres", "postgresql"):
        u = u.set(drivername="postgresql+psycopg")
    return u


def _make_engine(*, pool_size: Optional[int] = None, max_overflow: Optional[int] = None):
    url = _normalize_url(get_settings().database_url)

    if url.get_backend_name() == "sqlite":
        return create_engine(url, connect_args={"check_same_thread": False})

    if url.get_backend_name() == "postgresql":
        connect_args = {}
        # Supabase requires TLS; default it on unless the URL already sets it.
        if "sslmode" not in url.query:
            connect_args["sslmode"] = "require"
        # The Supavisor transaction pooler (port 6543) can't do server-side
        # prepared statements. Disabling them keeps every Supabase connection
        # mode working — direct, session pooler, and transaction pooler.
        connect_args["prepare_threshold"] = None
        kwargs = {"connect_args": connect_args, "pool_pre_ping": True}
        # Cloud Postgres drops idle connections; validate one before use.
        if pool_size is not None:
            kwargs["pool_size"] = pool_size
        if max_overflow is not None:
            kwargs["max_overflow"] = max_overflow
        return create_engine(url, **kwargs)

    return create_engine(url)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Background schedulers (IG outreach sequences, cold-email/SMS campaign sends,
# IMAP reply sync) run long chains of blocking network I/O — each SMTP send or
# IMAP poll has its own multi-second timeout — while holding whatever session
# they were given checked out the entire time. Sharing `engine`'s pool with
# request-serving code meant a slow mailbox could hold a connection long
# enough to starve unrelated requests (e.g. login) waiting on the same pool.
# A separate, small, dedicated pool means a stalled scheduler tick can never
# block real HTTP traffic — it can only ever starve itself.
scheduler_engine = _make_engine(
    pool_size=get_settings().scheduler_db_pool_size,
    max_overflow=get_settings().scheduler_db_max_overflow,
)
SchedulerSessionLocal = sessionmaker(
    bind=scheduler_engine, autoflush=False, expire_on_commit=False
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
