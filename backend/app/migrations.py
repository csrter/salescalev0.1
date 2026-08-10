"""Run Alembic migrations programmatically (on startup, and reusable in CI).

Replacing the old `create_all` with `upgrade_to_head()` means every database —
a fresh SQLite file, a new Postgres/Supabase project, or an existing one — is
brought to the current schema the same way. This is what prevents the
"existing DB is missing a newly-added column" class of runtime errors.
"""
import logging
import os
import sys

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

log = logging.getLogger("salescale.migrations")


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
    try:
        command.upgrade(alembic_config(), "head")
    except CommandError as e:
        # "Can't locate revision" means the DATABASE is at a revision this
        # build has never heard of — i.e. a newer build migrated it. Only the
        # packaged desktop app hits this, because it ships a frozen copy of
        # alembic/versions and points at the same Supabase DB the web deploy
        # migrates. Alembic's own message says nothing about that, and this
        # raises out of a startup event where uvicorn exits 3 with the
        # traceback swallowed, so it has read as an unexplained crash three
        # separate times. Say the actual thing instead.
        if "Can't locate revision" in str(e):
            log.error(
                "This build is OLDER than the database it points at (%s). "
                "The schema was migrated by a newer build; update this app "
                "to one that includes that migration.",
                e,
            )
            raise RuntimeError(
                f"App build is older than the database: {e}. Update the app."
            ) from e
        log.error("Database migration failed: %s", e)
        raise
