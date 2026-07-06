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
    ROLE_MEMBER,
    ROLE_OWNER,
    AdAccount,
    Client,
    Organization,
    PlatformConnection,
    User,
)
from app.security import encrypt_secret, hash_password


@pytest.fixture(scope="session")
def seeded():
    """Organization #1 (Atlas Reach) with two clients, their own users,
    accounts, campaigns, and landing events — the fixture every isolation
    test runs against. Organization #2 is created separately, through the
    public signup API (see org2_headers), per the Phase 1 definition of done.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    org = Organization(name="Atlas Reach")
    db.add(org)
    db.flush()

    client_a = Client(
        organization_id=org.id,
        name="Alpha HVAC",
        internal_notes="INTERNAL: margin notes for Alpha",
    )
    client_b = Client(
        organization_id=org.id,
        name="Bravo Heating",
        internal_notes="INTERNAL: margin notes for Bravo",
    )
    db.add_all([client_a, client_b])
    db.flush()

    owner_user = User(
        organization_id=org.id,
        email="owner@atlasreach.com",
        hashed_password=hash_password("owner-pass"),
        full_name="Org Owner",
        role=ROLE_OWNER,
    )
    member_user = User(
        organization_id=org.id,
        email="member@atlasreach.com",
        hashed_password=hash_password("member-pass"),
        full_name="Org Member",
        role=ROLE_MEMBER,
    )
    client_a_user = User(
        organization_id=org.id,
        email="owner@alphahvac.com",
        hashed_password=hash_password("client-pass"),
        full_name="Alpha Owner",
        role=ROLE_CLIENT,
        client_id=client_a.id,
    )
    db.add_all([owner_user, member_user, client_a_user])

    # Encrypted fake tokens so executor-path tests can run with the platform
    # API functions monkeypatched (get_access_token requires a stored token).
    conn_a = PlatformConnection(
        organization_id=org.id,
        client_id=client_a.id,
        platform="meta",
        access_token_encrypted=encrypt_secret("test-meta-token"),
    )
    conn_b = PlatformConnection(
        organization_id=org.id,
        client_id=client_b.id,
        platform="meta",
        access_token_encrypted=encrypt_secret("test-meta-token"),
    )
    db.add_all([conn_a, conn_b])
    db.flush()

    acct_a = AdAccount(
        organization_id=org.id,
        client_id=client_a.id,
        connection_id=conn_a.id,
        platform="meta",
        external_id="act_111",
        name="Alpha Meta Account",
    )
    acct_b = AdAccount(
        organization_id=org.id,
        client_id=client_b.id,
        connection_id=conn_b.id,
        platform="meta",
        external_id="act_222",
        name="Bravo Meta Account",
    )
    db.add_all([acct_a, acct_b])
    db.flush()

    camp_a = Campaign(
        organization_id=org.id,
        client_id=client_a.id,
        ad_account_id=acct_a.id,
        platform="meta",
        external_id="c_111",
        name="Alpha Campaign",
    )
    camp_b = Campaign(
        organization_id=org.id,
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
                organization_id=org.id,
                client_id=client_a.id,
                session_key="sess-a",
                utm_source="facebook",
                occurred_at=utcnow(),
            ),
            LandingEvent(
                organization_id=org.id,
                client_id=client_b.id,
                session_key="sess-b",
                utm_source="google",
                occurred_at=utcnow(),
            ),
        ]
    )
    db.commit()

    ids = {
        "org": org.id,
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
    """Org #1 Owner — full team access within Atlas Reach."""
    return _login(api, "owner@atlasreach.com", "owner-pass")


@pytest.fixture(scope="session")
def member_headers(api):
    """Org #1 Member — campaign work only, no client/team management."""
    return _login(api, "member@atlasreach.com", "member-pass")


@pytest.fixture(scope="session")
def client_a_headers(api):
    return _login(api, "owner@alphahvac.com", "client-pass")


@pytest.fixture(scope="session")
def org2(api):
    """A second Organization created through the public self-serve signup
    flow — the exact same path any agency uses, no special-casing."""
    resp = api.post(
        "/api/orgs/signup",
        json={
            "organization_name": "Rival Agency",
            "email": "owner@rivalagency.com",
            "password": "rival-pass-123",
            "full_name": "Rival Owner",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    # Give org #2 a client of its own so scoped-list tests have data.
    client_resp = api.post(
        "/api/clients", json={"name": "Rival Client Co"}, headers=headers
    )
    assert client_resp.status_code == 201, client_resp.text
    return {
        "organization_id": body["organization_id"],
        "headers": headers,
        "client_id": client_resp.json()["id"],
    }


@pytest.fixture(scope="session")
def org2_headers(org2):
    return org2["headers"]
