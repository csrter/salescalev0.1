import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_at_column, id_column

# JSONB on Postgres (indexable, the production target), plain JSON on SQLite
# (dev/test). One column type, one migration, both dialects — see the Phase 14
# migration for the GIN index that only lands on Postgres.
JsonB = JSON().with_variant(JSONB(), "postgresql")

# Salescale CRM entities. The UI/workflows arrive in Phase 6, but the schema
# ships now so ad-side tables (landing_events, insights) can reference leads
# from day one. Every table carries organization_id (and client_id where the
# entity belongs to one client) for two-level tenant scoping.


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(String(300))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    # Lead-Finder enrichment firmographics. `description` comes from the
    # business's OWN site (meta/og description) or the org's connected data
    # provider; `estimated_revenue` / `employee_count` come ONLY from a
    # licensed provider (never guessed — guardrail 7). All best-effort and
    # nullable; a human edit always wins (enrichment never overwrites a
    # non-empty value).
    description: Mapped[Optional[str]] = mapped_column(Text)
    estimated_revenue: Mapped[Optional[str]] = mapped_column(String(60))
    employee_count: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = created_at_column()


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        # Webhook idempotency: the platform's own lead id (leadgen_id /
        # lead_id) — a retried delivery finds this row instead of duplicating.
        UniqueConstraint(
            "client_id", "source_external_id", name="uq_contact_source_external"
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    company_id: Mapped[Optional[str]] = mapped_column(ForeignKey("companies.id"))
    first_name: Mapped[Optional[str]] = mapped_column(String(150))
    last_name: Mapped[Optional[str]] = mapped_column(String(150))
    email: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    # Direct/mobile line for the person (vs `phone`, which for imported
    # businesses is usually the main office number). Filled by licensed
    # provider enrichment only — never scraped (guardrail 6). Team-only in
    # API payloads.
    mobile_phone: Mapped[Optional[str]] = mapped_column(String(50))
    # The person's role at the company ("Owner", "Marketing Director") — the
    # pitch target. Filled by licensed provider enrichment (fill-blanks-only)
    # or typed in; also a CSV-import target.
    job_title: Mapped[Optional[str]] = mapped_column(String(150))
    city: Mapped[Optional[str]] = mapped_column(String(120))
    state: Mapped[Optional[str]] = mapped_column(String(64))
    # Where the lead came from: meta_instant_form | google_lead_form |
    # landing_page | manual — attribution details live on the landing event.
    source: Mapped[Optional[str]] = mapped_column(String(50))
    source_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    # Native-form linkage the platform sent with the lead (campaign_id, ad_id,
    # form_id …) — kept verbatim for audit; metric attribution still comes
    # from the landing event / source rules in services/metrics.py.
    source_detail: Mapped[Optional[dict]] = mapped_column(JSON)
    # Phase 6 qualified-lead workflow. `qualification` holds the checklist
    # state ({criterion_key: bool}) against the Organization's configured
    # criteria; `qualified_at` is THE qualified flag every consumer reads
    # (LQA-CPL, guarantee tracker, client pipeline view) — one status change,
    # many places it shows up.
    qualification: Mapped[Optional[dict]] = mapped_column(JSON)
    qualified_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    # External CRM sync mapping (optional per-client) — the other system's
    # contact id, so two-way sync updates in place instead of duplicating.
    external_crm_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    # Phase 14 custom fields: a single JSONB bag keyed by CustomFieldDefinition
    # .key (never an EAV table — keeps list-view reads one query). Values are
    # validated/coerced at the data-access layer (services/custom_fields.py),
    # never trusted as sent. GIN-indexed (jsonb_path_ops) on Postgres so
    # filtered list views stay fast at 40k+ contacts.
    custom_fields: Mapped[Optional[dict]] = mapped_column(JsonB)
    # Phase 12 email verification. Status is a provider verdict about
    # `email` (models/lead_finder.VERIFICATION_STATUSES); any change to
    # `email` must reset it to "unverified" — services/email_verification
    # owns that rule. verified_at is when the verdict was issued.
    verification_status: Mapped[str] = mapped_column(
        String(20), default="unverified", server_default="unverified", nullable=False
    )
    verified_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    # Phase 12 enrichment: candidate contact emails discovered from the
    # business's own website or the org's connected provider, pending
    # verification — [{"email", "source", "found_at"}]. Never treated as a
    # verified address; promotion into `email` happens explicitly.
    candidate_emails: Mapped[Optional[list]] = mapped_column(JSON)
    # SMS consent (TCPA prior-express-written-consent record). The consent
    # GATE for every SMS send is services/sms_consent.assert_can_sms — it
    # requires sms_opt_in AND checks suppression; these three fields are the
    # compliance record of WHERE/WHEN consent was captured (website form,
    # attested CSV import, manual). Changing the contact's phone does NOT
    # reset consent (consent attaches to the person, not the number), but a
    # STOP suppression on the number always wins over sms_opt_in.
    sms_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    sms_opt_in_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    sms_opt_in_source: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = created_at_column()


# System contact fields whose names a generated custom-field key must never
# collide with. Kept next to the model deliberately (task 8): when a new
# first-class contact column is added, add its name here so a later custom
# field can't shadow it in the JSONB bag or in API/CSV mapping. `custom_fields`
# itself and the qualification/attribution machinery are reserved too.
RESERVED_CONTACT_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "organization_id",
        "client_id",
        "company_id",
        "first_name",
        "last_name",
        "name",
        "email",
        "phone",
        "mobile_phone",
        "job_title",
        "city",
        "state",
        "source",
        "source_external_id",
        "source_detail",
        "sms_opt_in",
        "sms_opt_in_at",
        "sms_opt_in_source",
        "qualification",
        "qualified",
        "qualified_at",
        "verification_status",
        "verified_at",
        "candidate_emails",
        "external_crm_id",
        "custom_fields",
        "created_at",
        "tags",
        "attribution",
        # Company firmographics injected into contact payloads (enrichment).
        "company_description",
        "company_estimated_revenue",
        "company_employee_count",
    }
)


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "position", name="uq_stage_position"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Marks the stage that counts as "qualified" for the guarantee tracker
    # and lead-quality-adjusted CPL (Phase 3/6).
    is_qualified_stage: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    company_id: Mapped[Optional[str]] = mapped_column(ForeignKey("companies.id"))
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id"), nullable=False)
    stage_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_stages.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    value_cents: Mapped[Optional[int]] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(20), default="open", nullable=False
    )  # open | won | lost
    created_at: Mapped[dt.datetime] = created_at_column()
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # call | note | email | sms | meeting
    body: Mapped[Optional[str]] = mapped_column(Text)
    # Organization-internal entries are excluded from client-role queries at
    # the data layer (api/crm.py filters on it), not just hidden in the UI.
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = created_at_column()


class CrmTask(Base):
    __tablename__ = "crm_tasks"

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    due_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    assigned_to_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = created_at_column()


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_tag_client_name"),)

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class LeadFormConfig(Base):
    """Routes a platform's native lead-form delivery to one client.

    Meta leadgen webhooks arrive on ONE app-level endpoint for every tenant,
    so the page_id in the payload is the routing key (external_key). Google
    lead-form webhooks are configured per form with a per-client URL + key,
    so external_key is the shared google_key the advertiser sets in Google
    Ads. Routing by an indexed key — never by trusting anything else in a
    public payload — is what keeps one tenant's leads out of another's CRM.
    """

    __tablename__ = "lead_form_configs"
    __table_args__ = (
        UniqueConstraint("platform", "external_key", name="uq_lead_form_platform_key"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # meta | google
    external_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class ContactTag(Base):
    __tablename__ = "contact_tags"
    __table_args__ = (
        UniqueConstraint("contact_id", "tag_id", name="uq_contact_tag"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id"), nullable=False)


class ContactList(Base):
    """A named, client-scoped audience of contacts — managed like Tags but
    used to target outreach enrollment (email/SMS campaign pickers)."""

    __tablename__ = "contact_lists"
    __table_args__ = (
        UniqueConstraint("client_id", "name", name="uq_contact_list_client_name"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class ContactListMember(Base):
    __tablename__ = "contact_list_members"
    __table_args__ = (
        UniqueConstraint("list_id", "contact_id", name="uq_contact_list_member"),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    list_id: Mapped[str] = mapped_column(
        ForeignKey("contact_lists.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id"), nullable=False, index=True
    )


# Custom-field value types (Phase 14). Kept here so the model, the validation
# layer, and the API schemas all read one definition.
CUSTOM_FIELD_TYPES: frozenset[str] = frozenset(
    {"text", "number", "select", "multi_select", "date", "boolean", "url"}
)
# Types that carry an `options` list (values must be one of the option keys).
CUSTOM_FIELD_OPTION_TYPES: frozenset[str] = frozenset({"select", "multi_select"})


class CustomFieldDefinition(Base):
    """Per-Organization custom field definition (Phase 14).

    This is what lets a plumbing agency track "number of trucks" while a SaaS
    agency tracks "MRR" in the same product. `entity_type` is 'contact' today;
    the column exists so 'deal'/'company' can join later without a migration.

    `key` is the immutable machine identifier: generated once from the label at
    creation, never regenerated on rename (task 5), unique per (org,
    entity_type), and guarded against colliding with system contact fields
    (RESERVED_CONTACT_FIELD_KEYS, task 8). Values live in Contact.custom_fields
    keyed by this `key`.
    """

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "key",
            name="uq_custom_field_org_entity_key",
        ),
    )

    id: Mapped[str] = id_column()
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="contact"
    )
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # options: list of {"key": str, "label": str} for select/multi_select.
    options: Mapped[Optional[list]] = mapped_column(JSON)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether the field renders in Client-portal views/API (task 15). Defaults
    # to hidden: agencies keep internal notes-grade data in these fields.
    visible_to_clients: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Archive, don't delete (task 6): archived fields hide from forms/default
    # views but keep their stored values and stay filterable under a toggle.
    archived_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
