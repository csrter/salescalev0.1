"""Shared personalization engine — token/conditional/spintax rendering and
save-time validation (services/email_personalize.py), exercised directly
against the pure render/validate functions (no DB, no HTTP) since they take
plain contact-shaped objects + a facts dict. API-level 422s and the SMS AI
snippet/failsafe wiring are covered in test_email_campaigns.py /
test_sms_outreach.py."""

from app.services import email_personalize as ep
from app.services import sms_campaigns


class _Contact:
    def __init__(self, id, **over):
        self.id = id
        self.first_name = None
        self.last_name = None
        self.company_id = None
        self.city = None
        self.state = None
        self.email = None
        self.job_title = None
        self.custom_fields = {}
        for k, v in over.items():
            setattr(self, k, v)


_NO_FACTS = {
    "company": None,
    "company_description": None,
    "company_revenue": None,
    "company_employees": None,
}


def _facts(**over):
    f = dict(_NO_FACTS)
    f.update(over)
    return f


# --- new grounded tokens -----------------------------------------------------


def test_new_tokens_render_and_job_title_is_cased():
    c = _Contact("c1", job_title="owner")
    facts = _facts(
        company="Acme HVAC",
        company_description="Residential HVAC contractor.",
        company_revenue="$2M-$5M",
        company_employees="12",
    )
    tmpl = (
        "{{job_title}} of {{company}}: {{company_description}} "
        "({{company_revenue}}, {{company_employees}} employees)"
    )
    out = ep._render_template(tmpl, c, facts, {})
    assert out == (
        "Owner of Acme HVAC: Residential HVAC contractor. "
        "($2M-$5M, 12 employees)"
    )


def test_new_tokens_in_known_tokens():
    for tok in (
        "job_title",
        "company_description",
        "company_revenue",
        "company_employees",
    ):
        assert tok in ep.KNOWN_TOKENS


# --- conditionals -------------------------------------------------------------


def test_if_true_shows_true_branch():
    c = _Contact("c1", job_title="Owner")
    out = ep._render_template(
        "Hi{{#if job_title}} {{job_title}}{{/if}}!", c, _NO_FACTS, {}
    )
    assert out == "Hi Owner!"


def test_if_false_shows_nothing_without_else():
    c = _Contact("c1", job_title=None)
    out = ep._render_template(
        "Hi{{#if job_title}} {{job_title}}{{/if}}!", c, _NO_FACTS, {}
    )
    assert out == "Hi!"


def test_if_else_branches():
    present = ep._render_template(
        "{{#if job_title}}Owner path{{else}}Fallback path{{/if}}",
        _Contact("c1", job_title="Owner"),
        _NO_FACTS,
        {},
    )
    absent = ep._render_template(
        "{{#if job_title}}Owner path{{else}}Fallback path{{/if}}",
        _Contact("c2", job_title=None),
        _NO_FACTS,
        {},
    )
    assert present == "Owner path"
    assert absent == "Fallback path"


def test_if_with_custom_field():
    c = _Contact("c1", custom_fields={"plan": "gold"})
    out = ep._render_template(
        "{{#if custom.plan}}Plan: {{custom.plan}}{{else}}No plan{{/if}}",
        c,
        _NO_FACTS,
        {},
    )
    assert out == "Plan: gold"
    c2 = _Contact("c2", custom_fields={})
    out2 = ep._render_template(
        "{{#if custom.plan}}Plan: {{custom.plan}}{{else}}No plan{{/if}}",
        c2,
        _NO_FACTS,
        {},
    )
    assert out2 == "No plan"


def test_unclosed_if_renders_as_literal_text():
    c = _Contact("c1", job_title="Owner")
    out = ep._render_template("Hi {{#if job_title}}there", c, _NO_FACTS, {})
    assert "{{#if job_title}}" in out  # survives, caught by the send-time guard


# --- spintax --------------------------------------------------------------


def test_spintax_deterministic_same_contact_same_output():
    c = _Contact("stable-id")
    tmpl = "{{spin:Hello|Hey|Hi}} there"
    out1 = ep._render_template(tmpl, c, _NO_FACTS, {})
    out2 = ep._render_template(tmpl, c, _NO_FACTS, {})
    assert out1 == out2


def test_spintax_varies_across_contacts_reaches_all_variants():
    tmpl = "{{spin:one|two|three}}"
    seen = set()
    for i in range(30):
        c = _Contact(f"contact-{i}")
        seen.add(ep._render_template(tmpl, c, _NO_FACTS, {}))
    assert seen == {"one", "two", "three"}


def test_spintax_variants_may_contain_tokens():
    c = _Contact("c1", first_name="dana")
    out = ep._render_template(
        "{{spin:Hi {{first_name}}|Hey {{first_name}}}}", c, _NO_FACTS, {}
    )
    assert out in ("Hi Dana", "Hey Dana")


# --- save-time validation (unknown_tokens) ------------------------------------


def test_unknown_if_token_reported():
    assert "bogus" in ep.unknown_tokens("{{#if bogus}}x{{/if}}")


def test_unclosed_if_reported():
    assert "#if without {{/if}}" in ep.unknown_tokens("{{#if job_title}}x")


def test_spin_lt_2_variants_reported():
    assert "spin with <2 variants" in ep.unknown_tokens("{{spin:only one}}")


def test_else_never_reported_as_unknown_token():
    bad = ep.unknown_tokens("{{#if job_title}}A{{else}}B{{/if}}")
    assert "else" not in bad
    assert bad == []


def test_custom_key_validated_against_provided_set():
    assert ep.unknown_tokens("{{custom.plan}}", custom_keys={"plan"}) == []
    assert "custom.plan" in ep.unknown_tokens("{{custom.plan}}", custom_keys={"other"})


def test_nested_if_reported():
    # Balanced nesting passes the opener/closer COUNT check but the renderer's
    # single-level regex can't match it — it would render-error and exit live
    # enrollments at send time. Must be a save-time error instead.
    assert "nested {{#if}}" in ep.unknown_tokens(
        "{{#if job_title}}{{#if city}}x{{/if}}{{/if}}"
    )
    # SMS step-save shares the same validation.
    assert "nested {{#if}}" in sms_campaigns.unknown_tokens(
        "{{#if job_title}}{{#if city}}x{{/if}}{{/if}}"
    )


def test_sequential_ifs_not_reported_as_nested():
    assert (
        ep.unknown_tokens("{{#if job_title}}x{{/if}} {{#if city}}y{{/if}}") == []
    )


def test_sms_unknown_tokens_narrower_than_email():
    # company_description is a valid EMAIL token but not an SMS one.
    assert sms_campaigns.unknown_tokens("{{company_description}}") == [
        "company_description"
    ]
    assert sms_campaigns.unknown_tokens("{{job_title}} {{ai_snippet}}") == []
    assert sms_campaigns.unknown_tokens("{{#if bogus}}x{{/if}}") == ["bogus"]


# --- AI output guard -----------------------------------------------------


def test_ai_guard_strips_wrapping_quotes_and_backticks():
    assert ep.clean_ai_snippet('"Hello there."', 60) == "Hello there."
    assert ep.clean_ai_snippet("`Hello there.`", 60) == "Hello there."


def test_ai_guard_discards_urls():
    assert ep.clean_ai_snippet("Check https://example.com out", 60) == ""
    assert ep.clean_ai_snippet("Check http://example.com out", 60) == ""


def test_ai_guard_discards_leftover_braces():
    assert ep.clean_ai_snippet("Hi {{first_name}}", 60) == ""


def test_ai_guard_discards_over_word_limit():
    long_text = " ".join(["word"] * 61)
    assert ep.clean_ai_snippet(long_text, 60) == ""
    ok_text = " ".join(["word"] * 60)
    assert ep.clean_ai_snippet(ok_text, 60) == ok_text


def test_ai_guard_sms_word_limit_narrower():
    text = " ".join(["word"] * 26)
    assert ep.clean_ai_snippet(text, 25) == ""
    ok = " ".join(["word"] * 25)
    assert ep.clean_ai_snippet(ok, 25) == ok


# --- outreach model resolution + provider dispatch ---------------------------


def test_default_provider_is_gemini():
    from app.services import ai_provider

    # Operator default (no org override) is gemini.
    assert ai_provider.active_provider() == "gemini"
    assert ai_provider.resolve().model == "gemini-2.5-flash"
    assert ai_provider.resolve_outreach().model == "gemini-2.5-flash"


class _FakeOrg:
    def __init__(self, provider=None, model=None):
        self.id = "org-fake"
        self.ai_provider = provider
        self.ai_model = model


def test_org_override_selects_provider_and_model():
    from app.services import ai_provider

    # An org that picked anthropic gets the cheap-vs-full split (outreach =
    # Haiku, insights = Opus) — exercises the owner-selectable override path.
    org = _FakeOrg(provider="anthropic")
    assert ai_provider.resolve_outreach(org=org).model == "claude-haiku-4-5"
    assert ai_provider.resolve(org=org).model == "claude-opus-4-8"
    assert ai_provider.active_provider(org) == "anthropic"

    # An explicit model applies to BOTH insights and outreach for that org.
    pinned = _FakeOrg(provider="openai", model="gpt-4o-mini")
    assert ai_provider.resolve(org=pinned).model == "gpt-4o-mini"
    assert ai_provider.resolve_outreach(org=pinned).model == "gpt-4o-mini"

    # Metering prices the outreach model, not DEFAULT_PRICE (Opus-priced).
    assert ai_provider.price("claude-haiku-4-5") != ai_provider.DEFAULT_PRICE
    # Every selectable model is priced (no silent DEFAULT_PRICE over-bill).
    for models in ai_provider.SELECTABLE_MODELS.values():
        for m in models:
            assert m in ai_provider.PRICING_MICRO_USD_PER_TOKEN


def test_gemini_call_disables_thinking_budget(monkeypatch):
    """gemini-2.5-flash is a thinking model: with a small max_output_tokens and
    no thinking config, thinking tokens can eat the whole budget and return
    EMPTY text — the call must pin thinking_budget=0."""
    from app.services import ai_provider

    captured = {}

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["config"] = config

            class _R:
                text = "ok"
                usage_metadata = None

            return _R()

    class _FakeClient:
        def __init__(self, api_key):
            self.models = _FakeModels()

    monkeypatch.setattr("google.genai.Client", _FakeClient)
    res = ai_provider.AiResolution("gemini", "gemini-2.5-flash", "test-key")
    text, _in, _out = ai_provider._gemini(res, "sys", "user", 300)
    assert text == "ok"
    assert captured["config"].thinking_config.thinking_budget == 0
