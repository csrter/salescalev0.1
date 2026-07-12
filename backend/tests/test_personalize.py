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
