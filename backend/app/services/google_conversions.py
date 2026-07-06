"""Google Ads server-side conversions: Enhanced Conversions for Leads and
gclid-based click-conversion import, both via ConversionUploadService.

Verified against live docs 2026-07-06 (Google Ads API v24, same pin as
google_ads_api.py):
- UploadClickConversions with a ClickConversion per lead. Include the gclid
  whenever we captured one — Google recommends gclid + identifiers together;
  a conversion with identifiers but no gclid is matched against ad-click
  data by Google (that's the "for Leads" flow).
- user_identifiers: oneof hashed_email / hashed_phone_number (E.164 →
  SHA-256 hex; gmail dot/plus normalization — see pii.py), max 5 per
  conversion, user_identifier_source=FIRST_PARTY.
- conversion_date_time format "yyyy-mm-dd HH:mm:ss±HH:MM" (offset colon
  required — strftime %z needs patching).
- consent.ad_user_data should be populated; default GRANTED, configurable
  per client.
- Account prerequisites: customer.accepted_customer_data_terms and
  customer.enhanced_conversions_for_leads_enabled must both be true, and
  the conversion action must be type UPLOAD_CLICKS — checked by
  check_readiness() so misconfiguration surfaces in the UI instead of as
  silent upload failures.
- partial_failure is required on this service; errors come back on
  partial_failure_error, not as exceptions.
"""

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from . import pii
from .google_ads_api import GoogleApiError, _client, _search, _wrap_auth_errors


def format_conversion_datetime(when: dt.datetime) -> str:
    """"yyyy-mm-dd HH:mm:ss+HH:MM" — %z emits +0000, Google wants +00:00."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    stamp = when.strftime("%Y-%m-%d %H:%M:%S%z")
    return f"{stamp[:-2]}:{stamp[-2:]}"


def build_identifiers(lead: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Hashed user identifiers for Enhanced Conversions for Leads, plus the
    PII-free match-key list for the dispatch log."""
    identifiers: List[Dict[str, str]] = []
    match_keys: List[str] = []
    email = pii.google_email(lead.get("email"))
    if email:
        identifiers.append({"hashed_email": email})
        match_keys.append("hashed_email")
    phone = pii.google_phone(lead.get("phone"))
    if phone:
        identifiers.append({"hashed_phone_number": phone})
        match_keys.append("hashed_phone_number")
    return identifiers, match_keys


def upload_click_conversion(
    refresh_token: str,
    customer_id: str,
    conversion_action_id: str,
    occurred_at: dt.datetime,
    gclid: Optional[str] = None,
    identifiers: Optional[List[Dict[str, str]]] = None,
    value: Optional[float] = None,
    currency: Optional[str] = None,
    order_id: Optional[str] = None,
    ad_user_data_consent: str = "GRANTED",
) -> Dict[str, Any]:
    """One ClickConversion upload. Caller guarantees gclid and/or
    identifiers is non-empty — with neither there is nothing to match on."""

    def fn(client):
        conversion = client.get_type("ClickConversion")
        conversion.conversion_action = client.get_service(
            "ConversionActionService"
        ).conversion_action_path(customer_id, conversion_action_id)
        conversion.conversion_date_time = format_conversion_datetime(occurred_at)
        if gclid:
            conversion.gclid = gclid
        if value is not None:
            conversion.conversion_value = value
            conversion.currency_code = currency or "USD"
        if order_id:
            conversion.order_id = order_id
        for ident in identifiers or []:
            ui = client.get_type("UserIdentifier")
            if "hashed_email" in ident:
                ui.hashed_email = ident["hashed_email"]
            elif "hashed_phone_number" in ident:
                ui.hashed_phone_number = ident["hashed_phone_number"]
            ui.user_identifier_source = (
                client.enums.UserIdentifierSourceEnum.FIRST_PARTY
            )
            conversion.user_identifiers.append(ui)
        conversion.consent.ad_user_data = client.enums.ConsentStatusEnum[
            ad_user_data_consent
        ]

        svc = client.get_service("ConversionUploadService")
        request = client.get_type("UploadClickConversionsRequest")
        request.customer_id = customer_id
        request.conversions.append(conversion)
        request.partial_failure = True  # required by this service
        resp = svc.upload_click_conversions(request=request)
        if resp.partial_failure_error and resp.partial_failure_error.code:
            raise GoogleApiError(resp.partial_failure_error.message)
        result = resp.results[0] if resp.results else None
        return {
            "gclid": result.gclid if result else None,
            "conversion_action": result.conversion_action if result else None,
            "conversion_date_time": (
                result.conversion_date_time if result else None
            ),
        }

    def call():
        return fn(_client(refresh_token))

    return _wrap_auth_errors(call)


def check_readiness(refresh_token: str, customer_id: str) -> Dict[str, Any]:
    """Both flags must be true before Enhanced Conversions for Leads uploads
    will be accepted; surfaced in the config UI."""
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT customer.id, customer.conversion_tracking_setting."
        "accepted_customer_data_terms, customer.conversion_tracking_setting."
        "enhanced_conversions_for_leads_enabled FROM customer",
    )
    if not rows:
        raise GoogleApiError(f"No customer row for {customer_id}")
    setting = rows[0].customer.conversion_tracking_setting
    return {
        "accepted_customer_data_terms": bool(setting.accepted_customer_data_terms),
        "enhanced_conversions_for_leads_enabled": bool(
            setting.enhanced_conversions_for_leads_enabled
        ),
    }


def list_conversion_actions(
    refresh_token: str, customer_id: str
) -> List[Dict[str, Any]]:
    """Upload-eligible conversion actions for the config UI dropdown."""
    rows = _search(
        refresh_token,
        customer_id,
        "SELECT conversion_action.id, conversion_action.name, "
        "conversion_action.type, conversion_action.status "
        "FROM conversion_action WHERE conversion_action.status = 'ENABLED'",
    )
    return [
        {
            "id": str(r.conversion_action.id),
            "name": r.conversion_action.name,
            "type": r.conversion_action.type_.name,
            "uploadable": r.conversion_action.type_.name == "UPLOAD_CLICKS",
        }
        for r in rows
    ]
