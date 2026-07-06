"""Create an Organization with its Owner user, and (optionally) a demo client.

Goes through the same generic signup path as any self-serve tenant — Atlas
Reach (tenant #1) gets no special-casing.

Usage:
    python -m scripts.seed --org 'Atlas Reach' --email you@atlasreach.com \
        --password '...' [--name 'Your Name'] [--demo-client 'Smith HVAC']
"""

import argparse

from sqlalchemy import select

from app.api.orgs import signup
from app.db import Base, SessionLocal, engine
from app.models.core import Client, Organization, User
from app.schemas import OrgSignupRequest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="Atlas Reach")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Owner")
    parser.add_argument("--demo-client", default=None)
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        email = args.email.lower()
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None:
            # The same code path as POST /api/orgs/signup — no special-casing.
            signup(
                OrgSignupRequest(
                    organization_name=args.org,
                    email=email,
                    password=args.password,
                    full_name=args.name,
                ),
                db,
            )
            user = db.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            print(f"Created organization {args.org!r} with owner {email}")
        else:
            print(f"User {email} already exists — skipping signup")

        if args.demo_client:
            org = db.get(Organization, user.organization_id)
            db.add(Client(organization_id=org.id, name=args.demo_client))
            db.commit()
            print(f"Created client {args.demo_client!r} under {org.name!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
