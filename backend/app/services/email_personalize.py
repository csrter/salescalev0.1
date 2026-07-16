"""Cold-email personalization: token substitution + grounded AI snippets.

Two layers, both driven by real CRM data for the SENDING Organization only
(CLAUDE.md #7 — AI grounds in the org's own facts, never invents, never crosses
a tenant boundary):

1. Token substitution — `{{first_name}}`, `{{last_name}}`, `{{company}}`,
   `{{city}}`, `{{state}}`, `{{email}}`, `{{job_title}}`,
   `{{company_description}}`, `{{company_revenue}}`, `{{company_employees}}`,
   `{{custom.<key>}}`, with a `{{token|fallback}}` form ("there" when the
   field is empty/missing). `{{unsubscribe_url}}` is deliberately left as a
   LITERAL token — the send gateway resolves it per-message (each send has
   its own unsubscribe link).

2. `{{ai_snippet}}` — one or two natural sentences the Claude API writes for
   this specific contact from grounded facts only, when a step supplies
   `ai_instructions`. Metered against the org's monthly AI cap and cached on
   the enrollment (ai_snippets JSON, step_id -> text) so re-processing an
   enrollment never re-bills. AI failure NEVER blocks a send — it yields "".

3. `{{#if token}}...{{/if}}` / `{{#if token}}...{{else}}...{{/if}}` —
   conditional blocks, evaluated BEFORE token substitution (and after
   spintax, #4). `token` is any KNOWN token or `custom.<key>`; the block
   shows its "true" branch when the token resolves non-empty. Single level
   only — nesting an `{{#if}}` inside another is not supported and is a
   save-time error path if unclosed (unknown_tokens), while a merely-nested
   opener just won't be matched by the (deliberately non-nested) regex and
   survives as literal text into the send-time leftover-artifact guard.

4. `{{spin:variant one|variant two|variant three}}` — deterministic spintax,
   applied BEFORE conditionals. The choice is `sha256(contact.id + the spin
   block's own full text) % variant_count` — never `random()` — so the same
   contact always gets the same variant (idempotent with the AI-snippet
   cache: re-rendering an enrollment never changes past text) while
   different contacts spread across the variant list.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from ..models.ai import AiUsage
from ..models.core import Organization
from ..models.crm import Company, Contact
from ..models.email_outreach import EmailCampaign, EmailEnrollment, EmailStep
from . import ai_insights, ai_provider, entitlements

log = logging.getLogger("salescale.email_outreach")

# Tokens left untouched by the renderer (resolved elsewhere / later).
_LITERAL_TOKENS = {"unsubscribe_url"}
# Matches {{ name }} and {{ name | fallback }}.
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|\s*([^}]*?)\s*)?\}\}")

# Every non-custom token the renderer understands. A template token outside
# this set (and not custom.<key>) would silently render as "" — so the steps
# API validates against this at save time instead of letting typos vanish
# from sent emails.
KNOWN_TOKENS = frozenset(
    {
        "first_name",
        "last_name",
        "company",
        "city",
        "state",
        "email",
        "job_title",
        "company_description",
        "company_revenue",
        "company_employees",
        "ai_snippet",
        "unsubscribe_url",
    }
)

# Person/place tokens get casing normalized when the stored value was clearly
# never cased by a human (all-lower CSV imports, ALL-CAPS provider data).
# Deliberately excludes email (case-insensitive anyway) and custom.* (opaque
# org data — a SKU like "xL-2" must survive verbatim).
_CASED_TOKENS = frozenset({"first_name", "last_name", "company", "city", "job_title"})

# {{#if token}}...{{/if}} / {{#if token}}...{{else}}...{{/if}} — single level
# (deliberately non-nested: a nested opener just won't match and survives as
# literal text, caught by the send-time leftover-artifact guard).
_IF_RE = re.compile(
    r"\{\{#if\s+([a-zA-Z0-9_.]+)\s*\}\}(.*?)(?:\{\{else\}\}(.*?))?\{\{/if\}\}",
    re.DOTALL,
)
_IF_OPEN_RE = re.compile(r"\{\{#if\s+([a-zA-Z0-9_.]+)\s*\}\}")
_IF_CLOSE_RE = re.compile(r"\{\{/if\}\}")

# {{spin:variant one|variant two|variant three}} — deterministic per contact.
# Parsed with an explicit brace-depth scan (not a plain regex): variants may
# themselves contain {{tokens}}, and a naive `\{\{spin:(.*?)\}\}` non-greedy
# match would stop at the FIRST "}}" — i.e. right after the first nested
# token — instead of the spin block's real closer.
_SPIN_OPEN = "{{spin:"


def _iter_spin_blocks(template: str):
    """Yield (full_text, inner_text, start, end) for each {{spin:...}} block,
    `end` being the index just past its closing "}}". An unterminated
    "{{spin:" (unbalanced braces) is left alone — it stays literal text, which
    the send-time leftover-brace guard then catches."""
    i = 0
    n = len(template)
    while True:
        start = template.find(_SPIN_OPEN, i)
        if start == -1:
            return
        k = start + len(_SPIN_OPEN)
        depth = 1
        while k < n and depth > 0:
            if template[k : k + 2] == "{{":
                depth += 1
                k += 2
            elif template[k : k + 2] == "}}":
                depth -= 1
                k += 2
            else:
                k += 1
        if depth != 0:
            return  # unterminated — stop scanning, rest is literal
        yield template[start:k], template[start + len(_SPIN_OPEN) : k - 2], start, k
        i = k


def _split_variants(inner: str) -> list:
    """Split spin variants on "|", but only at brace-depth 0 — a "|" inside a
    nested {{token|fallback}} must not be mistaken for a variant separator."""
    parts, buf, depth, i, n = [], [], 0, 0, len(inner)
    while i < n:
        two = inner[i : i + 2]
        if two == "{{":
            depth += 1
            buf.append(two)
            i += 2
        elif two == "}}":
            depth -= 1
            buf.append(two)
            i += 2
        elif inner[i] == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(inner[i])
            i += 1
    parts.append("".join(buf))
    return parts


def _is_known(
    name: str, known_tokens: frozenset, custom_keys, research_keys=None
) -> bool:
    if name in known_tokens:
        return True
    if name.startswith("custom.") and name[len("custom.") :]:
        return custom_keys is None or name[len("custom.") :] in custom_keys
    if name.startswith("research.") and name[len("research.") :]:
        return research_keys is None or name[len("research.") :] in research_keys
    return False


def _unknown_tokens_against(
    template: Optional[str],
    known_tokens: frozenset,
    custom_keys=None,
    research_keys=None,
) -> list:
    """Shared unknown_tokens implementation, parameterized on the caller's
    known-token set (email's KNOWN_TOKENS vs SMS's narrower SMS_KNOWN_TOKENS)
    so both modules validate the same {{#if}}/{{spin:}} grammar without
    drifting. Order of first appearance, deduped; also reports structural
    errors as pseudo-tokens the API 422s on."""
    template = template or ""
    out: list = []

    # #if opener tokens (checked against known_tokens same as plain tokens).
    for m in _IF_OPEN_RE.finditer(template):
        name = m.group(1)
        if not _is_known(name, known_tokens, custom_keys, research_keys) and name not in out:
            out.append(name)

    # Plain {{token}}/{{token|fallback}} tokens — scanned with the #if/else/
    # endif markers stripped out first so "else" (which otherwise matches the
    # plain-token grammar) is never misreported as an unknown token.
    stripped = _IF_OPEN_RE.sub("", template).replace("{{else}}", "").replace(
        "{{/if}}", ""
    )
    for m in _TOKEN_RE.finditer(stripped):
        name = m.group(1)
        if not _is_known(name, known_tokens, custom_keys, research_keys) and name not in out:
            out.append(name)

    # Structural: every #if opener needs a matching {{/if}}.
    if len(_IF_OPEN_RE.findall(template)) != len(_IF_CLOSE_RE.findall(template)):
        out.append("#if without {{/if}}")

    # Structural: nested {{#if}} is unsupported. The renderer's regex is
    # single-level, so balanced nesting like
    # "{{#if a}}{{#if b}}x{{/if}}{{/if}}" passes the open/close COUNT check
    # above yet render-errors at send time (leftover braces) — exiting live
    # enrollments. Catch it at save time by scanning opener/closer order and
    # flagging any opener seen while already inside an open #if.
    depth = 0
    for m in re.finditer(r"\{\{#if\s+[a-zA-Z0-9_.]+\s*\}\}|\{\{/if\}\}", template):
        if m.group(0).startswith("{{#if"):
            depth += 1
            if depth > 1:
                out.append("nested {{#if}}")
                break
        elif depth > 0:
            depth -= 1

    # Structural: every spin block needs at least 2 variants.
    for _full, inner, _start, _end in _iter_spin_blocks(template):
        if len(_split_variants(inner)) < 2 and "spin with <2 variants" not in out:
            out.append("spin with <2 variants")

    return out


def unknown_tokens(
    template: Optional[str], custom_keys=None, research_keys=None
) -> list:
    """Tokens in `template` the renderer would drop: not in KNOWN_TOKENS and,
    when `custom_keys`/`research_keys` are given, custom.<key>/research.<key>
    whose key isn't a real field definition. Also reports #if/spin structural
    errors. Order of first appearance, deduped."""
    return _unknown_tokens_against(template, KNOWN_TOKENS, custom_keys, research_keys)


def _cap_token_word(tok: str) -> str:
    """One name word: first letter up, rest down, plus the O'Brien/D'Angelo
    shape (letter-apostrophe-letter) recapitalized after the apostrophe —
    without touching possessives like Dana's."""
    if not tok:
        return tok
    t = tok[0].upper() + tok[1:].lower()
    if len(t) > 2 and t[1] == "'":
        t = t[:2] + t[2].upper() + t[3:]
    return t


def _smart_case(name: str, value: str) -> str:
    """Normalize casing only when the value carries no human casing signal:
    all-lowercase or ALL-UPPERCASE strings are re-cased word-wise (spaces and
    hyphens are word boundaries); anything mixed-case ("McDonald", "iRepair")
    passes through untouched. States: 2-letter codes are uppercased."""
    value = value.strip()
    if name == "state":
        if len(value) == 2 and value.isalpha():
            return value.upper()
        if value.islower() or value.isupper():
            return re.sub(r"[^\s\-]+", lambda m: _cap_token_word(m.group(0)), value)
        return value
    if name in _CASED_TOKENS and (value.islower() or value.isupper()):
        return re.sub(r"[^\s\-]+", lambda m: _cap_token_word(m.group(0)), value)
    return value


# Substitution artifacts, applied in order after token replacement: an emptied
# token leaves "Hi ,", "Denver, .", doubled spaces, or a blank line behind —
# a human proofreader would never send those, so neither do we.
_TIDY_PASSES = (
    (re.compile(r"[ \t]+(\n|$)"), r"\1"),  # trailing spaces per line
    (re.compile(r"[ \t]{2,}"), " "),  # doubled spaces from emptied tokens
    (re.compile(r" +([,.;:!?])"), r"\1"),  # "Hi ," -> "Hi,"
    (re.compile(r",\s*([,.!?;:])"), r"\1"),  # "Denver, ." -> "Denver."
    (re.compile(r"(^|\n)[ \t]*,[ \t]*"), r"\1"),  # line starting with ","
    (re.compile(r"\n{3,}"), "\n\n"),  # collapsed token left a blank gap
)


def _tidy(text: str) -> str:
    for pattern, repl in _TIDY_PASSES:
        text = pattern.sub(repl, text)
    return text.strip()

_AI_SYSTEM_PROMPT = (
    "You write one or two short, natural sentences for a cold outreach email, "
    "personalizing to one specific contact using ONLY the grounded facts you "
    "are given. Hard rules: do not invent facts (companies, locations, numbers, "
    "achievements) that are not in GROUNDED_DATA; if a fact is missing, simply "
    "don't reference it. Do not add a greeting, a sign-off, a subject line, or "
    "quotation marks — this text is inserted into the middle of a template. "
    "Keep it under 40 words, plain and human. If TONE or EXAMPLE_EMAIL is "
    "provided, match its voice."
)


def _company_facts(db: Session, contact: Contact) -> Dict[str, Optional[str]]:
    """The small set of Company-derived grounded facts a template/AI prompt
    may use. One db.get, same as the old _company_name — refactored into a
    dict so the new company_* tokens share the single lookup."""
    facts: Dict[str, Optional[str]] = {
        "company": None,
        "company_description": None,
        "company_revenue": None,
        "company_employees": None,
    }
    if not contact.company_id:
        return facts
    company = db.get(Company, contact.company_id)
    if company is None:
        return facts
    facts["company"] = company.name
    facts["company_description"] = company.description
    facts["company_revenue"] = company.estimated_revenue
    facts["company_employees"] = (
        str(company.employee_count) if company.employee_count is not None else None
    )
    return facts


def _company_name(db: Session, contact: Contact) -> Optional[str]:
    return _company_facts(db, contact)["company"]


def _resolve_token(
    name: str, contact: Contact, facts: Dict[str, Optional[str]], extra: Dict[str, Any]
) -> Optional[Any]:
    if name in extra:
        return extra[name]
    if name == "first_name":
        return contact.first_name
    if name == "last_name":
        return contact.last_name
    if name == "job_title":
        return contact.job_title
    if name in ("company", "company_description", "company_revenue", "company_employees"):
        return facts.get(name)
    if name == "city":
        return contact.city
    if name == "state":
        return contact.state
    if name == "email":
        return contact.email
    if name.startswith("custom."):
        return (contact.custom_fields or {}).get(name[len("custom.") :])
    if name.startswith("research."):
        entry = (getattr(contact, "research", None) or {}).get(
            name[len("research.") :]
        )
        return entry.get("value") if isinstance(entry, dict) else None
    return None


def _render_spintax(template: str, contact: Contact) -> str:
    """Deterministic spintax: sha256(contact.id + the block's own full text)
    picks the variant, so the same contact always gets the same text (stable
    across re-renders — required for the cached ai_snippet/spin combo to stay
    consistent) while different contacts spread across the variant list.
    Never random(). A block with <2 variants is a save-time error
    (unknown_tokens); at render time it's defensively treated as its sole/
    first variant rather than crashing a send."""

    out = []
    last = 0
    for full_text, inner, start, end in _iter_spin_blocks(template):
        out.append(template[last:start])
        variants = _split_variants(inner)
        if len(variants) < 2:
            out.append(variants[0] if variants else "")
        else:
            idx = int(
                hashlib.sha256((str(contact.id) + full_text).encode()).hexdigest(), 16
            ) % len(variants)
            out.append(variants[idx])
        last = end
    out.append(template[last:])
    return "".join(out)


def _render_conditionals(
    template: str, contact: Contact, facts: Dict[str, Optional[str]], extra: Dict[str, Any]
) -> str:
    """{{#if token}}...{{/if}} / {{#if token}}...{{else}}...{{/if}}, single
    level, evaluated before token substitution. `token` may be any KNOWN
    token or custom.<key> — resolved the same way substitution resolves it."""

    def _repl(m: re.Match) -> str:
        name = m.group(1)
        true_branch = m.group(2) or ""
        false_branch = m.group(3) or ""
        value = _resolve_token(name, contact, facts, extra)
        truthy = value is not None and str(value).strip() != ""
        return true_branch if truthy else false_branch

    return _IF_RE.sub(_repl, template)


def _render_template(
    template: Optional[str],
    contact: Contact,
    facts: Dict[str, Optional[str]],
    extra: Dict[str, Any],
) -> str:
    if not template:
        return ""

    def _repl(m: re.Match) -> str:
        name = m.group(1)
        fallback = m.group(2)  # None when no "|fallback" was given
        if name in _LITERAL_TOKENS:
            return m.group(0)  # leave the literal token for the gateway
        value = _resolve_token(name, contact, facts, extra)
        if value is None or str(value).strip() == "":
            return fallback if fallback is not None else ""
        return _smart_case(name, str(value))

    # Spintax first (its own salt is the block's raw text, so it must run
    # before anything rewrites the template), then conditionals, then plain
    # token substitution, then the tidy pass.
    template = _render_spintax(template, contact)
    template = _render_conditionals(template, contact, facts, extra)
    return _tidy(_TOKEN_RE.sub(_repl, template))


def render_step(
    db: Session,
    org: Organization,
    contact: Contact,
    step: EmailStep,
    campaign: EmailCampaign,
    *,
    ai_snippet: str = "",
) -> Tuple[Optional[str], str]:
    """(subject, body) with tokens substituted. A null/blank subject_template
    returns subject=None — the gateway threads it as a "Re:" reply. The
    {{ai_snippet}} token resolves to `ai_snippet` (empty by default)."""
    facts = _company_facts(db, contact)
    extra = {"ai_snippet": ai_snippet}
    raw_subject = (step.subject_template or "").strip()
    subject = (
        _render_template(step.subject_template, contact, facts, extra)
        if raw_subject
        else None
    )
    body = _render_template(step.body_template or "", contact, facts, extra)
    return subject, body


# --- grounded AI snippet ----------------------------------------------------


def _call_model(system: str, user_content: str, max_tokens: int = 300):
    """(text, input_tokens, output_tokens). Dispatches to the operator-selected
    AI provider (anthropic | openai | gemini) via services/ai_provider, using
    the resolution bound by generate_ai_snippet. Isolated as the monkeypatch
    seam for tests."""
    return ai_provider.complete(system, user_content, max_tokens)


def _record_usage(
    db: Session, org: Organization, model: str, input_tokens: int, output_tokens: int
) -> None:
    in_price, out_price = ai_provider.price(model)
    db.add(
        AiUsage(
            organization_id=org.id,
            client_id=None,  # outreach personalization is agency-level, no client
            user_id=None,  # background/scheduler origin
            feature="outreach_personalize",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=int(input_tokens * in_price + output_tokens * out_price),
        )
    )
    db.flush()


def clean_ai_snippet(text: Optional[str], max_words: int) -> str:
    """Post-process a model's raw output into a snippet safe to splice into a
    template, or "" to discard it (fail-open — the caller never crashes on
    this, it just gets an empty snippet). Strips wrapping quotes/backticks;
    discards output that leaks a URL, still contains a template token
    ("{{" — the model echoing the instructions/token soup back), or runs
    over `max_words` (email: 60, SMS: 25 — SMS is character-constrained)."""
    t = (text or "").strip()
    while len(t) >= 2 and t[0] == "`" and t[-1] == "`":
        t = t[1:-1].strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("\"", "'"):
        t = t[1:-1].strip()
    if not t:
        return ""
    if "http://" in t or "https://" in t or "{{" in t:
        return ""
    if len(t.split()) > max_words:
        return ""
    return t


def generate_ai_snippet(
    db: Session,
    org: Organization,
    contact: Contact,
    step: EmailStep,
    campaign: Optional[EmailCampaign] = None,
) -> Optional[str]:
    """One/two grounded sentences for this contact. Returns:
    - the snippet text on success,
    - "" when the model RAN but produced nothing usable (empty output, or the
      output guard discarded an unsafe response) — a real, cacheable result,
    - None when the model never ran (unconfigured key, over the monthly AI cap,
      model/timeout error) — a TRANSIENT failure the caller must NOT cache, so
      the next render/tick retries once the key/cap is fixed.

    A send is never blocked on AI either way — the caller coerces None to ""
    and the template still goes out without the snippet.

    Grounding gains the org's standing outreach_context (when set) and the
    contact's filled AI research fields (Feature A). When `campaign` carries
    ai_tone/ai_example (Feature C), they're appended to the user content as
    explicit labeled sections — never folded into GROUNDED_DATA, and never
    touching the (cache-friendly, byte-stable) system prompt beyond its one
    fixed "match TONE/EXAMPLE_EMAIL" sentence."""
    if not (step.ai_instructions or "").strip():
        return ""  # nothing to generate — a real empty result, not a failure
    grounding = {
        "contact": {
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "company_name": _company_name(db, contact),
            "city": contact.city,
            "state": contact.state,
            "custom_fields": contact.custom_fields or {},
        },
        "org": {"name": org.name},
        "instructions": step.ai_instructions,
    }
    if org.outreach_context:
        grounding["org_context"] = org.outreach_context
    research = {
        k: v.get("value")
        for k, v in (getattr(contact, "research", None) or {}).items()
        if isinstance(v, dict) and v.get("value")
    }
    if research:
        grounding["research"] = research
    try:
        ai_insights.check_allowance(db, org)  # entitlement + monthly cap
        # Cheaper outreach model (Haiku on Anthropic) — a one-sentence task
        # doesn't warrant the insights-tier ai_model.
        res = ai_provider.resolve_outreach(db, org)  # provider + model + BYO/operator key
        user_content = (
            f"GROUNDED_DATA:\n{json.dumps(grounding, sort_keys=True, default=str)}\n\n"
            f"INSTRUCTIONS:\n{step.ai_instructions}"
        )
        if campaign is not None and (campaign.ai_tone or "").strip():
            user_content += f"\n\nTONE:\n{campaign.ai_tone}"
        if campaign is not None and (campaign.ai_example or "").strip():
            user_content += f"\n\nEXAMPLE_EMAIL:\n{campaign.ai_example}"
        with ai_provider.using(res):
            text, input_tokens, output_tokens = _call_model(_AI_SYSTEM_PROMPT, user_content)
        _record_usage(db, org, res.model, input_tokens, output_tokens)
        cleaned = clean_ai_snippet(text, 60)
        if not cleaned:
            log.info("outreach AI snippet discarded by output guard for contact %s", contact.id)
        # The model ran (and billed) — "" here is a real result, cacheable so a
        # re-render doesn't re-bill for the same discarded/empty output.
        return cleaned
    except Exception as e:  # never let AI failure stop a send
        # Transient — the model never ran. Return None so the caller doesn't
        # cache an empty snippet permanently; the next tick retries.
        log.info("outreach AI snippet skipped for contact %s: %s", contact.id, e)
        return None


def _cached_or_generate(
    db: Session,
    org: Organization,
    contact: Contact,
    enrollment: Optional[EmailEnrollment],
    step: EmailStep,
    campaign: Optional[EmailCampaign] = None,
) -> str:
    """Enrollment-cached snippet (ai_snippets: step_id -> text). Idempotent: a
    second render of the same enrollment/step reuses the cache and never
    re-bills. Preview (enrollment=None) generates fresh without caching.

    Only a non-None result is cached: a transient AI failure (None) is NOT
    written, so a missing key / cap hit doesn't permanently kill this
    enrollment's personalization — it retries on the next render/tick."""
    if not (step.ai_instructions or "").strip():
        return ""
    if enrollment is not None:
        cache = dict(enrollment.ai_snippets or {})
        if step.id in cache:
            return cache[step.id] or ""
        snippet = generate_ai_snippet(db, org, contact, step, campaign)
        if snippet is not None:
            cache[step.id] = snippet
            enrollment.ai_snippets = cache  # reassign so SQLAlchemy tracks the JSON
        return snippet or ""
    return generate_ai_snippet(db, org, contact, step, campaign) or ""


def render_full(
    db: Session,
    org: Organization,
    enrollment: Optional[EmailEnrollment],
    step: EmailStep,
    campaign: EmailCampaign,
    contact: Optional[Contact] = None,
) -> Tuple[Optional[str], str]:
    """(subject, body) fully rendered incl. the {{ai_snippet}} token (from the
    enrollment cache, generating once if absent). `contact` may be passed
    directly (preview); otherwise it is loaded from the enrollment."""
    if contact is None:
        contact = db.get(Contact, enrollment.contact_id)
    snippet = _cached_or_generate(db, org, contact, enrollment, step, campaign)
    return render_step(db, org, contact, step, campaign, ai_snippet=snippet)
