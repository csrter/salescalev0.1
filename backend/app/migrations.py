"""Run Alembic migrations programmatically (on startup, and reusable in CI).

Replacing the old `create_all` with `upgrade_to_head()` means every database —
a fresh SQLite file, a new Postgres/Supabase project, or an existing one — is
brought to the current schema the same way. This is what prevents the
"existing DB is missing a newly-added column" class of runtime errors.
"""
import os
import sys

from alembic import command
from alembic.config import Config


def _base_dir() -> str:
    # A PyInstaller one-file build unpacks bundled data under sys._MEIPASS;
    # in a normal checkout the alembic/ dir sits next to the app package.
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def alembic_config() -> Config:
    base = _base_dir()
    cfg = Config(os.path.join(base, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(base, "alembic"))
    return cfg


def upgrade_to_head() -> None:
    command.upgrade(alembic_config(), "head")
