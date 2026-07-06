"""Create the agency, a team user, and (optionally) a demo client.

Usage:
    python -m scripts.seed --email you@atlasreach.com --password '...' \
        [--name 'Your Name'] [--demo-client 'Smith HVAC']
"""

import argparse

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models.core import ROLE_TEAM, Agency, Client, User
from app.security import hash_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Atlas Reach Admin")
    parser.add_argument("--demo-client", default=None)
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        agency = db.execute(select(Agency)).scalar_one_or_none()
        if agency is None:
            agency = Agency(name="Atlas Reach")
            db.add(agency)
            db.flush()

        email = args.email.lower()
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None:
            db.add(
                User(
                    email=email,
                    hashed_password=hash_password(args.password),
                    full_name=args.name,
                    role=ROLE_TEAM,
                )
            )
            print(f"Created team user {email}")
        else:
            print(f"User {email} already exists — skipping")

        if args.demo_client:
            db.add(Client(agency_id=agency.id, name=args.demo_client))
            print(f"Created client {args.demo_client}")

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
