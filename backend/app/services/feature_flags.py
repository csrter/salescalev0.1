"""Operator-level feature-visibility flags (beta honesty gates).

Not entitlements — tier gating stays in services/entitlements via the stub.
These hide features that are structurally unavailable to external
Organizations today, so a beta org never sees a door that dead-ends:

- ig_outreach: the Instagram Outreach module go-live is gated on Meta App
  Review; until that clears, only dogfood orgs (dev-mode API access) can
  use it.
- bluebubbles: the self-hosted BlueBubbles iMessage relay is a dev path
  that requires a Mac + Apple ID the operator controls — an external org
  must not be invited to wire a personal Apple ID into bulk sends.

Each flag is a comma-separated allowlist of Organization ids in env
(IG_OUTREACH_ORG_IDS / BLUEBUBBLES_ORG_IDS), or "*" for everyone — the
default, which keeps dev/test behavior unchanged; production sets the
allowlists in backend/.env. UI hiding is never load-bearing (project
rule): BlueBubbles is also enforced server-side at SMS account creation.
"""

from ..config import get_settings


def _allowed(csv: str, org_id: str) -> bool:
    csv = (csv or "").strip()
    if csv == "*":
        return True
    return org_id in {part.strip() for part in csv.split(",") if part.strip()}


def ig_outreach_allowed(org_id: str) -> bool:
    return _allowed(get_settings().ig_outreach_org_ids, org_id)


def bluebubbles_allowed(org_id: str) -> bool:
    return _allowed(get_settings().bluebubbles_org_ids, org_id)


def for_org(org_id: str) -> dict:
    """The feature map serialized into TokenResponse — what the frontend
    gates nav/provider visibility on."""
    return {
        "ig_outreach": ig_outreach_allowed(org_id),
        "bluebubbles": bluebubbles_allowed(org_id),
    }
