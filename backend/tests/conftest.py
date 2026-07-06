import os
import tempfile

# Environment must be pinned before any app module is imported: db.py builds
# its engine from settings at import time.
_tmpdir = tempfile.mkdtemp(prefix="salescale-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["JWT_SECRET"] = "test-secret-0123456789abcdef0123456789abcdef"
os.environ["TOKEN_ENCRYPTION_KEY"] = (
    "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # test-only Fernet key
)

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models.ads import Campaign
from app.models.attribution import LandingEvent
from app.models.base import utcnow
from app.models.core import (
    ROLE_CLIENT,
    ROLE_TEAM,
    AdAccount,
    Agency,
    Client,
    PlatformConnection,
    User,
)
from app.security import hash_password


@pytest.fixture(scope="session")
def seeded():
    """Two clients with their own users, accounts, campaigns, and landing
    events — the fixture every isolation test runs against."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    agency = Agency(name="Atlas Reach")
    db.add(agency)
    db.flush()

    client_a = Client(
        agency_id=agency.id,
        name="Alpha HVAC",
        internal_notes="INTERNAL: margin notes for Alpha",
    )
    client_b = Client(
        agency_id=agency.id,
        name="Bravo Heating",
        internal_notes="INTERNAL: margin notes for Bravo",
    )
    db.add_all([client_a, client_b])
    db.flush()

    team_user = User(
        email="team@atlasreach.com",
        hashed_password=hash_password("team-pass"),
        full_name="Team Member",
        role=ROLE_TEAM,
    )
    client_a_user = User(
        email="owner@alphahvac.com",
        hashed_password=hash_password("client-pass"),
        full_name="Alpha Owner",
        role=ROLE_CLIENT,
        client_id=client_a.id,
    )
    db.add_all([team_user, client_a_user])

    conn_a = PlatformConnection(client_id=client_a.id, platform="meta")
    conn_b = PlatformConnection(client_id=client_b.id, platform="meta")
    db.add_all([conn_a, conn_b])
    db.flush()

    acct_a = AdAccount(
        client_id=client_a.id,
        connection_id=conn_a.id,
        platform="meta",
        external_id="act_111",
        name="Alpha Meta Account",
    )
    acct_b = AdAccount(
        client_id=client_b.id,
        connection_id=conn_b.id,
        platform="meta",
        external_id="act_222",
        name="Bravo Meta Account",
    )
    db.add_all([acct_a, acct_b])
    db.flush()

    camp_a = Campaign(
        client_id=client_a.id,
        ad_account_id=acct_a.id,
        platform="meta",
        external_id="c_111",
        name="Alpha Campaign",
    )
    camp_b = Campaign(
        client_id=client_b.id,
        ad_account_id=acct_b.id,
        platform="meta",
        external_id="c_222",
        name="Bravo Campaign",
    )
    db.add_all([camp_a, camp_b])

    db.add_all(
        [
            LandingEvent(
                client_id=client_a.id,
                session_key="sess-a",
                utm_source="facebook",
                occurred_at=utcnow(),
            ),
            LandingEvent(
                client_id=client_b.id,
                session_key="sess-b",
                utm_source="google",
                occurred_at=utcnow(),
            ),
        ]
    )
    db.commit()

    ids = {
        "client_a": client_a.id,
        "client_b": client_b.id,
        "acct_a": acct_a.id,
        "acct_b": acct_b.id,
        "camp_a": camp_a.id,
        "camp_b": camp_b.id,
    }
    db.close()
    return ids


@pytest.fixture(scope="session")
def api(seeded):
    with TestClient(app) as tc:
        yield tc


def _login(api, email, password):
    resp = api.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="session")
def team_headers(api):
    return _login(api, "team@atlasreach.com", "team-pass")


@pytest.fixture(scope="session")
def client_a_headers(api):
    return _login(api, "owner@alphahvac.com", "client-pass")
