"""Multi-provider AI completion behind one grounded-call interface.

services/ai_insights and services/email_personalize each make exactly ONE model
call, and both route it through ai_provider.complete(). Grounding is always done
by the caller BEFORE this: complete() only ever receives a system prompt, a
user_content string, and an already-resolved (provider, model, api_key). It
never sees a DB handle or an Organization, so tenant isolation (CLAUDE.md #7)
holds identically regardless of which provider runs — the model still gets only
the facts the caller chose to put in the prompt.

Provider selection is operator-global (settings.ai_provider: anthropic | openai
| gemini). For the active provider an Organization may bring its own key, stored
encrypted in IntegrationCredential and resolved BYO-first with the operator's
env key as fallback — the exact resolution used for Google Places / ZeroBounce
(integration_creds.resolve_key). The resolved credentials for a given call are
stashed in a contextvar by `using()`, so the actual call seam stays a stable
(system, user_content, max_tokens) function that the test suite monkeypatches.

Each provider SDK is imported lazily inside its own branch: a provider whose
package isn't installed only errors when that provider is actually selected —
and email personalization fails open on ANY error, so a mis/unconfigured
provider never blocks a send.
"""

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Optional, Tuple

from ..config import get_settings

PROVIDERS = ("anthropic", "openai", "gemini")

# Models an org owner may pick per provider (the in-app dropdown + the
# save-time validation whitelist). Every entry MUST also exist in
# PRICING_MICRO_USD_PER_TOKEN so a selected model still meters/prices. The
# first entry of each list is that provider's recommended default.
SELECTABLE_MODELS = {
    "gemini": ("gemini-2.5-flash", "gemini-1.5-pro", "gemini-1.5-flash"),
    "anthropic": ("claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"),
    "openai": ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"),
}


def is_selectable_model(provider: str, model: str) -> bool:
    return model in SELECTABLE_MODELS.get(provider, ())

# Approx list price, USD per 1M tokens (input, output) — which is conveniently
# micro-USD per token. A model not listed (e.g. an operator override) falls
# back to DEFAULT_PRICE; metering never blocks on an unknown price.
PRICING_MICRO_USD_PER_TOKEN = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gemini-2.5-flash": (0.30, 2.50),
    # Retired by Google 2026-06-01 (404 NOT_FOUND) — kept only so price()
    # still prices any pre-existing AiUsage rows recorded under this model.
    "gemini-2.0-flash": (0.1, 0.4),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini-1.5-flash": (0.075, 0.3),
}
DEFAULT_PRICE = (5.0, 25.0)


def price(model: str) -> Tuple[float, float]:
    return PRICING_MICRO_USD_PER_TOKEN.get(model, DEFAULT_PRICE)


@dataclass(frozen=True)
class AiResolution:
    provider: str
    model: str
    api_key: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _model_for(provider: str) -> str:
    s = get_settings()
    return {
        "anthropic": s.ai_model,
        "openai": s.openai_model,
        "gemini": s.gemini_model,
    }.get(provider, s.ai_model)


def _outreach_model_for(provider: str) -> str:
    """The cheaper per-provider model for high-volume outreach personalization
    + research (one-sentence tasks), distinct from _model_for (insights)."""
    s = get_settings()
    return {
        "anthropic": s.ai_outreach_model,
        "openai": s.openai_outreach_model,
        "gemini": s.gemini_outreach_model,
    }.get(provider, s.ai_outreach_model)


def _env_key(provider: str) -> str:
    s = get_settings()
    return {
        "anthropic": s.anthropic_api_key,
        "openai": s.openai_api_key,
        "gemini": s.gemini_api_key,
    }.get(provider, "")


def active_provider(org=None) -> str:
    """The active provider for `org`: the org's own selection when set, else
    the operator-global default (settings.ai_provider, now gemini)."""
    if org is not None:
        p = getattr(org, "ai_provider", None)
        if p in PROVIDERS:
            return p
    p = get_settings().ai_provider
    return p if p in PROVIDERS else "gemini"


def active_model(org=None) -> str:
    """The model recorded on AiUsage rows and used for pricing — the org's
    explicit pick when set, else the active provider's default model."""
    provider = active_provider(org)
    if org is not None:
        m = getattr(org, "ai_model", None)
        if m:
            return m
    return _model_for(provider)


def _resolve(db, org, default_model_for) -> AiResolution:
    """Shared resolution: provider + model honor the org's owner-selected
    override (Organization.ai_provider / .ai_model) and otherwise fall back to
    the operator default. An explicit org model applies to BOTH insights and
    outreach; only the fallback differs (default_model_for). The key is the
    org's BYO key for the resolved provider when (db, org) are supplied, else
    the operator's env fallback."""
    provider = active_provider(org)
    override = getattr(org, "ai_model", None) if org is not None else None
    model = override or default_model_for(provider)
    if db is not None and org is not None:
        from . import integration_creds

        api_key = integration_creds.resolve_key(db, org.id, provider)
    else:
        api_key = _env_key(provider)
    return AiResolution(provider, model, api_key)


def resolve(db=None, org=None) -> AiResolution:
    """(provider, model, api_key) for an insights-tier call."""
    return _resolve(db, org, _model_for)


def resolve_outreach(db=None, org=None) -> AiResolution:
    """Like resolve(), but the fallback model is the cheaper per-provider
    OUTREACH model for high-volume one-sentence personalization/research. An
    org that has explicitly picked a model gets that model here too."""
    return _resolve(db, org, _outreach_model_for)


_var: contextvars.ContextVar = contextvars.ContextVar("ai_resolution", default=None)


@contextlib.contextmanager
def using(resolution: AiResolution):
    """Bind a resolution so complete() (the monkeypatchable call seam) can read
    it without a widened signature."""
    tok = _var.set(resolution)
    try:
        yield resolution
    finally:
        _var.reset(tok)


def current() -> AiResolution:
    r = _var.get()
    return r if r is not None else resolve()


def complete(system: str, user_content: str, max_tokens: int) -> Tuple[str, int, int]:
    """(text, input_tokens, output_tokens) from the currently-bound provider.
    Raises ai_insights.AiNotConfigured when the active provider has no key —
    the established seam both callers already catch/map."""
    from . import ai_insights  # AiNotConfigured is the pre-existing error type

    res = current()
    if not res.api_key:
        raise ai_insights.AiNotConfigured(
            f"No API key configured for AI provider '{res.provider}'"
        )
    if res.provider == "openai":
        return _openai(res, system, user_content, max_tokens)
    if res.provider == "gemini":
        return _gemini(res, system, user_content, max_tokens)
    return _anthropic(res, system, user_content, max_tokens)


def _anthropic(res: AiResolution, system: str, user_content: str, max_tokens: int):
    import anthropic

    client = anthropic.Anthropic(api_key=res.api_key)
    response = client.messages.create(
        model=res.model,
        max_tokens=max_tokens,
        # System prompt is byte-stable across tenants/requests — a cache
        # breakpoint means each call pays full price only for its own grounding.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return text, response.usage.input_tokens, response.usage.output_tokens


def _openai(res: AiResolution, system: str, user_content: str, max_tokens: int):
    from openai import OpenAI

    client = OpenAI(api_key=res.api_key)
    response = client.chat.completions.create(
        model=res.model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    text = response.choices[0].message.content or ""
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    return text, int(input_tokens or 0), int(output_tokens or 0)


def _gemini(res: AiResolution, system: str, user_content: str, max_tokens: int):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=res.api_key)
    config_kwargs = dict(
        system_instruction=system,
        max_output_tokens=max_tokens,
    )
    # gemini-2.5-flash is a "thinking" model: with a small max_output_tokens
    # and no thinking config, the internal thinking tokens can consume the
    # whole budget and return EMPTY text. Disable thinking for these short
    # completions. Guard so an older google-genai without ThinkingConfig
    # degrades gracefully (thinking simply left at its default) rather than
    # crashing the call — email personalization would then just fail open.
    try:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except AttributeError:
        pass
    response = client.models.generate_content(
        model=res.model,
        contents=user_content,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = response.text or ""
    meta = getattr(response, "usage_metadata", None)
    input_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    return text, int(input_tokens), int(output_tokens)
