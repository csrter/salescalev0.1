"""Instagram Messaging via the Meta Graph API (page-backed professional
accounts) — the only send/receive path the Outreach module has.

Compliance notes (STANDING GUARDRAILS + module spec):
- Every call here is an approved Graph endpoint; there is no browser
  automation or scraping fallback anywhere in this module.
- There is NO endpoint that initiates a conversation with a user who has
  never messaged/commented/mentioned the account — that's a platform rule,
  not a gap in this file. The engine (outreach_send.py) enforces the
  matching window semantics before any function here is called.
- Request shapes follow the Instagram Messaging / private-replies docs as of
  the Phase 6 leadgen verification pass. Per the repo guardrail, RE-VERIFY
  each shape against live docs when the operator's instagram_manage_messages
  App Review access lands (same per-platform gating as Phase 7b) — these
  calls are monkeypatched in tests and cannot run live until then anyway.

Uses the meta_api primitives so BYO-app credentials, version pinning, and
error taxonomy (MetaAuthError → reconnect banner) stay in one place.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from ..config import get_settings
from . import integration_creds
from .meta_api import _base, _get, _paginate, _post, MetaApiError, MetaAuthError

__all__ = ["MetaApiError", "MetaAuthError"]

# Scopes for the IG-messaging OAuth connect flow (superset of the ads scopes;
# a separate consent because messaging is a separate grant + App Review track).
IG_SCOPES = (
    "instagram_basic,instagram_manage_messages,instagram_manage_comments,"
    "pages_show_list,pages_manage_metadata,pages_read_engagement,business_management"
)

# Webhook fields the module consumes (subscribed on the backing page).
IG_WEBHOOK_FIELDS = "messages,messaging_postbacks,messaging_seen"


def build_ig_oauth_url(state: str) -> str:
    settings = get_settings()
    creds = integration_creds.current_meta()
    params = {
        "client_id": creds.app_id,
        "redirect_uri": settings.api_base_url + "/api/outreach/accounts/callback",
        "state": state,
        "scope": IG_SCOPES,
    }
    return (
        f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?"
        + urlencode(params)
    )


def fetch_page_ig_accounts(user_token: str) -> List[Dict[str, Any]]:
    """Pages the user manages, each with its own page access token and (when
    linked) the IG professional account. Rows without an IG account are
    filtered out by the caller."""
    return _paginate(
        f"{_base()}/me/accounts",
        {
            "access_token": user_token,
            "fields": "id,name,access_token,"
            "instagram_business_account{id,username,name,profile_picture_url}",
            "limit": 100,
        },
    )


def subscribe_page_webhooks(page_token: str, page_id: str) -> Dict[str, Any]:
    """Subscribe the backing page to the app's webhook so this account's
    messages/comments start flowing to /api/webhooks/meta/instagram."""
    return _post(
        f"{_base()}/{page_id}/subscribed_apps",
        {"access_token": page_token, "subscribed_fields": IG_WEBHOOK_FIELDS},
    )


def send_text(
    page_token: str,
    ig_account_id: str,
    recipient_igsid: str,
    text: str,
    tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Send one DM. tag=HUMAN_AGENT only ever arrives here from the manual
    inbox reply path — the automated engine never sets it."""
    data: Dict[str, Any] = {
        "access_token": page_token,
        "recipient": {"id": recipient_igsid},
        "message": {"text": text},
    }
    if tag:
        data["messaging_type"] = "MESSAGE_TAG"
        data["tag"] = tag
    else:
        data["messaging_type"] = "RESPONSE"
    return _post(f"{_base()}/{ig_account_id}/messages", _json_encode(data))


def send_private_reply(
    page_token: str, ig_account_id: str, comment_id: str, text: str
) -> Dict[str, Any]:
    """One private DM in reply to a comment (allowed once per comment, within
    PRIVATE_REPLY_WINDOW_DAYS) — the compliant 'comment keyword → DM' path."""
    data = {
        "access_token": page_token,
        "recipient": {"comment_id": comment_id},
        "message": {"text": text},
    }
    return _post(f"{_base()}/{ig_account_id}/messages", _json_encode(data))


def fetch_user_profile(page_token: str, igsid: str) -> Dict[str, Any]:
    """API-provided profile for an IG-scoped user who has engaged with the
    account — the only business-signal source the rule filters may use."""
    return _get(
        f"{_base()}/{igsid}",
        {
            "access_token": page_token,
            "fields": "name,username,profile_pic,follower_count,"
            "is_user_follow_business,is_business_follow_user,is_verified_user",
        },
    )


def business_discovery(
    page_token: str, ig_account_id: str, username: str
) -> Dict[str, Any]:
    """Public professional-account fields by handle (Business Discovery) —
    prospect validation + enrichment (category is not exposed here; bio,
    website, and follower counts are)."""
    field = (
        f"business_discovery.username({username})"
        "{id,username,name,biography,website,followers_count,media_count}"
    )
    data = _get(
        f"{_base()}/{ig_account_id}", {"access_token": page_token, "fields": field}
    )
    return data.get("business_discovery", {})


def ad_library_search(
    access_token: str, search_terms: str, country: str = "US", limit: int = 25
) -> List[Dict[str, Any]]:
    """Read-only Ad Library signal: businesses currently running ads for a
    term/geo. Purely a prospecting hint — output feeds the watch list."""
    data = _get(
        f"{_base()}/ads_archive",
        {
            "access_token": access_token,
            "search_terms": search_terms,
            "ad_reached_countries": f'["{country}"]',
            "ad_active_status": "ACTIVE",
            "fields": "page_id,page_name,ad_snapshot_url",
            "limit": limit,
        },
    )
    return data.get("data", [])


def _json_encode(data: Dict[str, Any]) -> Dict[str, Any]:
    # Graph form-encodes posts; nested recipient/message objects go as JSON
    # strings (same convention as meta_api._encode_fields).
    import json

    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else v
        for k, v in data.items()
    }
