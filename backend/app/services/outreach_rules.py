"""Inbound trigger engine: IF [event matches rule] THEN [actions].

Called by outreach_ingest after an event is stored and its conversation
upserted. Matching is pure data (keywords / media filter / business-signal
filters over API-provided profile fields); actions run through the same
gateways as everything else (outreach_send for replies, lead_ingest for
contacts) so guardrails hold no matter which path fired."""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.base import utcnow
from ..models.core import Client
from ..models.crm import Contact, ContactTag, Tag
from ..models.outreach import (
    EVENT_COMMENT,
    EVENT_LIVE_COMMENT,
    EVENT_MENTION,
    KIND_RULE,
    PROSPECT_ENGAGED,
    InstagramAccount,
    OutreachConversation,
    OutreachMessage,
    OutreachProspect,
    OutreachSequence,
    OutreachTriggerRule,
)
from ..security import decrypt_secret
from . import instagram_api, lead_ingest, outreach_send, outreach_sequences

log = logging.getLogger("salescale.outreach")


def _keyword_match(keywords: Optional[list], text: str) -> bool:
    if not keywords:
        return True  # empty = match every event of this trigger type
    lowered = (text or "").lower()
    return any((kw or "").lower() in lowered for kw in keywords)


def _media_match(media_ids: Optional[list], media_id: Optional[str]) -> bool:
    if not media_ids:
        return True
    return media_id is not None and str(media_id) in [str(m) for m in media_ids]


def _profile_match(
    db: Session,
    rule: OutreachTriggerRule,
    account: InstagramAccount,
    convo: OutreachConversation,
) -> bool:
    """Business-signal filters over the API-provided user profile. The
    profile is fetched once per conversation and cached on convo.peer."""
    filters = rule.filters or {}
    if not any(
        k in filters for k in ("min_followers", "max_followers", "verified_only")
    ):
        return True
    peer = convo.peer or {}
    if "follower_count" not in peer:
        try:
            token = decrypt_secret(account.access_token_encrypted)
            peer = {**peer, **instagram_api.fetch_user_profile(token, convo.ig_user_id)}
            convo.peer = peer
        except Exception as e:  # profile fetch is best-effort; fail the filter
            log.info("Profile fetch failed for %s: %s", convo.ig_user_id, e)
            return False
    followers = peer.get("follower_count") or 0
    if filters.get("min_followers") is not None and followers < filters["min_followers"]:
        return False
    if filters.get("max_followers") is not None and followers > filters["max_followers"]:
        return False
    if filters.get("verified_only") and not peer.get("is_verified_user"):
        return False
    return True


def _already_fired(db: Session, rule: OutreachTriggerRule, convo: OutreachConversation) -> bool:
    return (
        db.execute(
            select(OutreachMessage.id)
            .where(
                OutreachMessage.rule_id == rule.id,
                OutreachMessage.conversation_id == convo.id,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _apply_tags(db: Session, contact: Contact, tag_names: list):
    for name in tag_names:
        tag = db.execute(
            select(Tag).where(Tag.client_id == contact.client_id, Tag.name == name)
        ).scalar_one_or_none()
        if tag is None:
            tag = Tag(
                organization_id=contact.organization_id,
                client_id=contact.client_id,
                name=name,
            )
            db.add(tag)
            db.flush()
        exists = db.execute(
            select(ContactTag.id).where(
                ContactTag.contact_id == contact.id, ContactTag.tag_id == tag.id
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                ContactTag(
                    organization_id=contact.organization_id,
                    contact_id=contact.id,
                    tag_id=tag.id,
                )
            )


def _ensure_contact(
    db: Session, convo: OutreachConversation
) -> Optional[Contact]:
    """Create/link the CRM contact for this conversation (auto-dedupe by
    IG-scoped user id via source_external_id)."""
    if convo.contact_id:
        return db.get(Contact, convo.contact_id)
    client = db.get(Client, convo.client_id)
    peer = convo.peer or {}
    name = (peer.get("name") or peer.get("username") or "").strip()
    first, _, last = name.partition(" ")
    contact, _created = lead_ingest.upsert_contact(
        db,
        client,
        first_name=first or peer.get("username") or convo.ig_user_id,
        last_name=last or None,
        source="instagram_outreach",
        source_external_id=f"ig:{convo.ig_user_id}",
        source_detail={"ig_user_id": convo.ig_user_id, "username": peer.get("username", "")},
    )
    convo.contact_id = contact.id
    return contact


def _capture_prospect(db: Session, convo: OutreachConversation):
    peer = convo.peer or {}
    username = peer.get("username")
    if not username:
        return
    existing = db.execute(
        select(OutreachProspect).where(
            OutreachProspect.client_id == convo.client_id,
            OutreachProspect.username == username,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            OutreachProspect(
                organization_id=convo.organization_id,
                client_id=convo.client_id,
                account_id=convo.account_id,
                username=username,
                ig_user_id=convo.ig_user_id,
                source="engagement",
                status=PROSPECT_ENGAGED,
                conversation_id=convo.id,
                contact_id=convo.contact_id,
                engaged_at=utcnow(),
            )
        )


def run_rules(
    db: Session,
    account: InstagramAccount,
    convo: OutreachConversation,
    event_type: str,
    text: str,
    *,
    media_id: Optional[str] = None,
    comment_id: Optional[str] = None,
) -> int:
    """Evaluate every enabled rule for this account against one inbound
    event. Returns how many rules fired. Rule isolation: one rule's failure
    never blocks the next."""
    rules = (
        db.execute(
            select(OutreachTriggerRule).where(
                OutreachTriggerRule.account_id == account.id,
                OutreachTriggerRule.enabled.is_(True),
                OutreachTriggerRule.trigger_type == event_type,
            )
        )
        .scalars()
        .all()
    )
    fired = 0
    for rule in rules:
        try:
            if not _keyword_match(rule.keywords, text):
                continue
            if event_type in (EVENT_COMMENT, EVENT_LIVE_COMMENT, EVENT_MENTION):
                if not _media_match(rule.media_ids, media_id):
                    continue
            if rule.once_per_user and _already_fired(db, rule, convo):
                continue
            if not _profile_match(db, rule, account, convo):
                continue

            contact = None
            if rule.create_contact or rule.tag_names or rule.enroll_sequence_id:
                contact = _ensure_contact(db, convo)
            if rule.tag_names and contact is not None:
                _apply_tags(db, contact, rule.tag_names)
            if rule.capture_prospect:
                _capture_prospect(db, convo)
            if rule.reply_text:
                outreach_send.send(
                    db,
                    account,
                    convo,
                    rule.reply_text,
                    kind=KIND_RULE,
                    rule_id=rule.id,
                    # Comment-triggered rules answer via a private reply (the
                    # compliant comment→DM path); DM/story triggers answer in
                    # the (now open) standard window.
                    reply_to_comment_id=comment_id,
                )
            if rule.enroll_sequence_id:
                seq = db.get(OutreachSequence, rule.enroll_sequence_id)
                if seq is not None and seq.client_id == convo.client_id:
                    outreach_sequences.enroll(
                        db, seq, convo,
                        contact_id=convo.contact_id, enrolled_by="rule",
                    )
            fired += 1
        except Exception:
            log.exception("Rule %s failed on convo %s", rule.id, convo.id)
    return fired
