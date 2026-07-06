"""Phase 9 entitlement seams — the single place Phase 8's subscription
tiers will plug in.

Every feature gate introduced in Phase 9 goes through exactly one function
here, so wiring real tier data later means editing this file, not hunting
call sites. Until Phase 8 exists:

- white-labeling is available to every Organization (industry-wide, custom
  domains are usually an Agency-tier feature and logo/colors sometimes sit
  lower — that split is a Phase 8 decision to confirm with the user);
- AI insights get one global default monthly cap (config.ai_monthly_query_limit)
  applied to every Organization.
"""

from ..config import get_settings
from ..models.core import Organization


def can_use_white_labeling(org: Organization) -> bool:
    """May this Organization configure branding, custom domains, and branded
    email? Phase 8: return based on the org's subscription tier."""
    return True


def can_use_ai_insights(org: Organization) -> bool:
    """May this Organization use AI explanations/summaries at all?
    Phase 8: return based on the org's subscription tier."""
    return True


def ai_monthly_query_limit(org: Organization) -> int:
    """Monthly cap on AI queries (explanations + summaries) for this
    Organization. Phase 8: read the org's tier limit instead of the global
    default. NOTE for whoever prices the tiers: check actual cost per org in
    the ai_usage table before committing to a number — a cap that lets an
    org spend more on Claude API calls than its subscription price is a
    loss-maker, and this ledger exists so that's checkable, not guessed.
    """
    return get_settings().ai_monthly_query_limit
