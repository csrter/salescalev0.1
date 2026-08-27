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


# --- {{company}} business-name fallback (SMS) ---


def test_company_fallback_from_business_name_when_no_company_linked():
    """A lead whose business/place name is in first_name with no linked Company
    and no surname fills {{company}} from that name, proper-cased."""
    c = _Contact("c1", first_name="desert air hvac")  # no last_name, no company
    assert sms_campaigns._company_from_name(c) == "Desert Air HVAC"

    c2 = _Contact("c2", first_name="Environment Concepts Inc")
    assert sms_campaigns._company_from_name(c2) == "Environment Concepts Inc"


def test_company_fallback_ignores_real_people():
    """A person (has a surname, or a single-word given name) is never taken as
    a company — the fallback must not turn 'Mike' or 'John Smith' into {{company}}."""
    # single-word given name, no business hint -> not a company
    assert sms_campaigns._company_from_name(_Contact("c1", first_name="Mike")) is None
    # has a surname -> a person, even if multi-word first_name
    assert (
        sms_campaigns._company_from_name(
            _Contact("c2", first_name="Mary Jane", last_name="Watson")
        )
        is None
    )
    # empty name -> nothing to fall back to
    assert sms_campaigns._company_from_name(_Contact("c3")) is None


# --- SMS name scrubbing (legal suffixes + junk characters) --------------------
#
# Every input below is a REAL name from the production CRM (or a close variant),
# not an invented case: across 207 business-style leads the observed suffixes
# were inc 33, llc 29, corp 2, co 2, corporation 1, and the observed non-
# alphanumerics were & . , - / ' ( ) @ : and a curly apostrophe.


def test_clean_display_name_strips_trailing_legal_suffixes():
    from app.services.sms_campaigns import clean_display_name as clean

    assert clean("CasaCool LLC") == "CasaCool"
    assert clean("Kalos Services Inc.") == "Kalos Services"
    assert clean("Goddards HVAC Service, LLC") == "Goddards HVAC Service"
    assert clean("Carvajal A/C Mechanical Corp") == "Carvajal A/C Mechanical"
    assert (
        clean("Shane's Air Conditioning & Heating, Inc.")
        == "Shane's Air Conditioning & Heating"
    )
    # More than one suffix needs more than one pass.
    assert clean("Cooling Co., Inc.") == "Cooling"


def test_clean_display_name_keeps_punctuation_that_is_part_of_the_name():
    """The filter must not be greedy: & - / and apostrophes carry meaning in
    these names, and stripping them would misspell the business."""
    from app.services.sms_campaigns import clean_display_name as clean

    assert clean("A-1 Heat & Air") == "A-1 Heat & Air"
    assert clean("A/C Tech Professionals Inc.") == "A/C Tech Professionals"
    assert clean("Greens Energy HVAC & Fuel") == "Greens Energy HVAC & Fuel"
    assert (
        clean("Elite Cooling, Heating, Plumbing, & Electrical")
        == "Elite Cooling, Heating, Plumbing, & Electrical"
    )
    # A curly apostrophe must be FOLDED, not dropped — NFKC alone leaves it
    # outside the keep-set, which silently produced "Anthonys".
    assert clean("Anthony’s Cooling-Heating-Electrical") == (
        "Anthony's Cooling-Heating-Electrical"
    )


def test_clean_display_name_only_strips_a_suffix_at_the_end():
    """"Master Cooling Mechanical LLC Air Conditioning and Heating" is a real
    row: cutting at the embedded LLC would discard words the business goes by."""
    from app.services.sms_campaigns import clean_display_name as clean

    name = "Master Cooling Mechanical LLC Air Conditioning and Heating"
    assert clean(name) == name
    # ...and a word that merely LOOKS like a suffix stays when it is the name.
    assert clean("The Cooling Company") == "The Cooling Company"


def test_clean_display_name_drops_junk_and_blanks_non_names():
    from app.services.sms_campaigns import clean_display_name as clean

    assert clean("Cool ❄️ Zone Inc") == "Cool Zone"
    # Google Places returns "Name: category"; the lead goes by the part before
    # the colon, not the directory descriptor after it.
    assert (
        clean("West Palm Beach HVAC Services: Air conditioning contractor")
        == "West Palm Beach HVAC Services"
    )
    assert (
        clean("South Florida Air Conditioning Contractors Association (SFACA)")
        == "South Florida Air Conditioning Contractors Association"
    )
    # Not names at all -> "" so the template's own |fallback takes over.
    # This address is a real value sitting in a production first_name field.
    assert clean("069inigueznichols@gmail.com") == ""
    assert clean("www.coolzone.com") == ""
    assert clean("12345") == ""
    assert clean("") == "" and clean(None) == "" and clean("   ") == ""
    # A name that is ONLY a suffix is left alone rather than blanked — there is
    # nothing better to greet them by.
    assert clean("LLC") == "LLC"


class _Step:
    def __init__(self, body):
        self.id = "s1"
        self.body_template = body
        self.ai_instructions = ""


def test_sms_render_strips_suffix_end_to_end():
    """Through render_body — what the engine AND the preview both call — so the
    scrub is proven where it actually ships, not just in the helper."""
    c = _Contact("c1", first_name="Kalos Services Inc.")  # business name in the name field
    step = _Step("Hi {{first_name|there}}, quick question about {{company}}.")
    body = sms_campaigns.render_body(None, c, step)
    assert body == "Hi Kalos Services, quick question about Kalos Services."
    assert "Inc" not in body


def test_sms_render_falls_back_when_the_name_is_an_email_address():
    """A first_name holding an email address (a real production row) must not
    be texted at the lead — it scrubs to blank, so the template's own
    |fallback takes over."""
    c = _Contact("c2", first_name="069inigueznichols@gmail.com")
    step = _Step("Hi {{first_name|there}}!")
    assert sms_campaigns.render_body(None, c, step) == "Hi there!"


def test_sms_render_keeps_meaningful_punctuation_end_to_end():
    c = _Contact("c3", first_name="Anthony’s Cooling-Heating-Electrical")
    step = _Step("Hi {{first_name|there}}!")
    assert (
        sms_campaigns.render_body(None, c, step)
        == "Hi Anthony's Cooling-Heating-Electrical!"
    )
