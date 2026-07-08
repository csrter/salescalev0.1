import os
import sys
from logging.config import fileConfig

from alembic import context

# Make the backend package importable regardless of where alembic is invoked
# from (CLI cwd, or the bundled binary's temp dir).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Reuse the app's engine/URL logic so migrations use the exact same driver
# (psycopg3), TLS, and pooler handling as the app — including for Supabase.
from app.config import get_settings
from app.db import Base, engine, _normalize_url
from app import models  # noqa: F401 — registers all tables on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url",
    _normalize_url(get_settings().database_url).render_as_string(hide_password=False),
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
