"""Desktop-mode behavior: the packaged Electron backend must not run the
background outreach schedulers by default — it typically shares a database
with a server deployment whose schedulers are already live, and the due-row
scans have no cross-process claim, so a second instance can double-send real
email/SMS. DESKTOP_RUN_SCHEDULERS=1 re-enables them for standalone installs.
"""
from app.config import Settings


def test_desktop_mode_disables_schedulers_by_default():
    # Explicit base flags: the test env exports OUTREACH_SCHEDULER_ENABLED=0,
    # and this test is about desktop_mode's gate, not the env override.
    s = Settings(
        desktop_mode=True,
        outreach_scheduler_enabled=True,
        email_outreach_scheduler_enabled=True,
    )
    assert not s.run_schedulers()


def test_desktop_standalone_can_opt_back_in():
    s = Settings(desktop_mode=True, desktop_run_schedulers=True)
    assert s.run_schedulers()


def test_server_mode_runs_schedulers():
    s = Settings(desktop_mode=False)
    assert s.run_schedulers()
