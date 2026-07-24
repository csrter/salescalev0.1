"""Auto-enroll a brand-new lead into its client's SMS outreach campaign(s).

The outreach counterpart to services/lead_notify.py: fired at the same
lead-creation call sites, right after the team alert. Where lead_notify texts
the agency's OWN ops phones, this enrolls the LEAD itself into any active SMS
campaign that (a) is scoped to the lead's client and (b) has
auto_enroll_new_leads turned on — so a fresh lead starts receiving the
campaign's qualifying-question sequence within one scheduler tick.

Guarantees (mirror lead_notify.notify_new_lead):
- Best-effort side effect of lead creation. Never commits or rolls back the
  session — the caller's own commit, right after this returns, persists the
  SmsEnrollment rows this adds. Never lets a failure propagate: a broken
  campaign config must not cost the lead that was just created.
- The consent gate is NOT bypassed. Enrollment routes through
  sms_campaigns.enroll_contacts → sms_consent.sendable, so a lead with no
  recorded SMS opt-in (or one already suppressed) is simply skipped, never
  force-texted. This is compliance-correct, not a bug: TCPA opt-in must exist
  before the first send (the org's sms_opt_in_default, applied at lead
  creation, is what makes inbound leads eligible).
- Idempotent per campaign: enroll_contacts de-dups on (campaign, contact), so
  a lead that somehow reaches this path twice is enrolled once.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.core import Client
from ..models.crm import Contact
from ..models.sms_outreach import SMS_CAMPAIGN_ACTIVE, SmsCampaign
from . import sms_campaigns

log = logging.getLogger("salescale.lead_autoenroll")


def auto_enroll_new_lead(db: Session, client: Client, contact: Contact) -> None:
    """Enroll `contact` into every active, auto-enroll SMS campaign scoped to
    `client`. No-op (silently) when the client has no such campaign."""
    try:
        campaigns = (
            db.execute(
                select(SmsCampaign).where(
                    SmsCampaign.organization_id == client.organization_id,
                    SmsCampaign.client_id == client.id,
                    SmsCampaign.status == SMS_CAMPAIGN_ACTIVE,
                    SmsCampaign.auto_enroll_new_leads.is_(True),
                )
            )
            .scalars()
            .all()
        )
        for campaign in campaigns:
            result = sms_campaigns.enroll_contacts(
                db,
                campaign,
                [contact.id],
                source="auto_new_lead",
                # The lead's own capture source (landing_page_webhook, meta
                # lead form, …) — the attribution the Audience tab shows.
                source_detail=contact.source,
            )
            if result["enrolled"]:
                log.info(
                    "auto-enrolled lead %s into sms campaign %s (client %s)",
                    contact.id,
                    campaign.id,
                    client.id,
                )
            else:
                # skipped (no_consent/no_number/suppressed/already) — expected,
                # not an error; log at debug for traceability.
                log.debug(
                    "lead %s not auto-enrolled into campaign %s: %s",
                    contact.id,
                    campaign.id,
                    result["skipped"],
                )
    except Exception:
        log.exception(
            "auto-enroll failed for org=%s, client=%s, contact=%s",
            client.organization_id,
            client.id,
            contact.id,
        )
