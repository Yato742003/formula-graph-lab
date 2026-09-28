"""Opt-in OpenAI Responses adapter; model output is always untrusted text."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.proposals import MAX_PROPOSAL_BYTES

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_PROMPT_BYTES = 32 * 1024
MAX_OUTPUT_TOKENS = 8_000


class ProposalProviderError(RuntimeError):
    """Sanitized provider/configuration failure; never contains response content."""


@dataclass(frozen=True)
class OpenAIProposalConfig:
    api_key: str
    model: str
    max_cost_usd: Decimal
    daily_max_cost_usd: Decimal
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    max_output_tokens: int
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> OpenAIProposalConfig:
        if os.getenv("FGL_ENABLE_PROPOSAL_GENERATION", "false") != "true":
            raise ProposalProviderError("PROPOSAL_GENERATION_DISABLED")
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        model = os.getenv("FGL_PROPOSAL_MODEL", "").strip()
        if not api_key or not model or len(model) > 100:
            raise ProposalProviderError("PROPOSAL_PROVIDER_NOT_CONFIGURED")
        try:
            maximum = _positive_decimal("FGL_PROPOSAL_MAX_COST_USD")
            daily_maximum = _positive_decimal("FGL_PROPOSAL_DAILY_MAX_COST_USD")
            input_price = _positive_decimal("FGL_PROPOSAL_INPUT_USD_PER_MILLION")
            output_price = _positive_decimal("FGL_PROPOSAL_OUTPUT_USD_PER_MILLION")
            output_tokens = int(os.getenv("FGL_PROPOSAL_MAX_OUTPUT_TOKENS", "2000"))
        except (ValueError, InvalidOperation) as exc:
            raise ProposalProviderError("PROPOSAL_COST_LIMIT_NOT_CONFIGURED") from exc
        if not 1 <= output_tokens <= MAX_OUTPUT_TOKENS:
            raise ProposalProviderError("PROPOSAL_TOKEN_LIMIT_INVALID")
        return cls(api_key, model, maximum, daily_maximum, input_price, output_price, output_tokens)

    def require_budget(self, prompt_bytes: int, *, requests: int = 1) -> Decimal:
        if not 1 <= prompt_bytes <= MAX_PROMPT_BYTES:
            raise ProposalProviderError("PROPOSAL_PROMPT_SIZE_INVALID")
        if not 1 <= self.max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ProposalProviderError("PROPOSAL_TOKEN_LIMIT_INVALID")
        if not 1 <= requests <= 3:
            raise ProposalProviderError("PROPOSAL_REQUEST_COUNT_INVALID")
        # Conservative preflight: UTF-8 bytes are an upper bound on token count.
        # Configured unit prices are explicit and must be kept current by the operator.
        input_upper = Decimal(prompt_bytes) * self.input_usd_per_million / Decimal(1_000_000)
        output_upper = (
            Decimal(self.max_output_tokens) * self.output_usd_per_million / Decimal(1_000_000)
        )
        maximum = (input_upper + output_upper) * requests
        if maximum > self.max_cost_usd:
            raise ProposalProviderError("PROPOSAL_COST_CAP_WOULD_BE_EXCEEDED")
        if maximum > self.daily_max_cost_usd:
            raise ProposalProviderError("PROPOSAL_DAILY_CAP_WOULD_BE_EXCEEDED")
        return maximum


def proposal_generation_capability() -> dict[str, str]:
    """Expose only safe readiness and the spend ceiling needed for informed consent."""
    if (
        os.getenv("FGL_ENABLE_PROPOSALS", "false") != "true"
        or os.getenv("FGL_ENABLE_PROPOSAL_GENERATION", "false") != "true"
    ):
        return {"state": "disabled"}
    try:
        config = OpenAIProposalConfig.from_environment()
    except ProposalProviderError as exc:
        state = {
            "PROPOSAL_PROVIDER_NOT_CONFIGURED": "provider_not_configured",
            "PROPOSAL_COST_LIMIT_NOT_CONFIGURED": "budget_not_configured",
        }.get(str(exc), "configuration_incomplete")
        return {"state": state}
    return {
        "state": "configured",
        "model": config.model,
        "max_generation_cost_usd": format(config.max_cost_usd, "f"),
        "daily_max_cost_usd": format(config.daily_max_cost_usd, "f"),
    }


def _positive_decimal(name: str) -> Decimal:
    value = Decimal(os.getenv(name, ""))
    if not value.is_finite() or value <= 0:
        raise ValueError(name)
    return value


async def request_proposal_draft(
    prompt: str,
    *,
    config: OpenAIProposalConfig | None = None,
    client: httpx.AsyncClient | None = None,
) -> object:
    """Call Responses with no tools/storage and bounded JSON output; validate locally."""
    provider = config or OpenAIProposalConfig.from_environment()
    prompt_bytes = len(prompt.encode("utf-8"))
    provider.require_budget(prompt_bytes)
    try:
        owned_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=provider.timeout_seconds)
        try:
            response = await client.post(
                OPENAI_RESPONSES_URL,
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json={
                    "model": provider.model,
                    "store": False,
                    "max_output_tokens": provider.max_output_tokens,
                    "text": {"format": {"type": "json_object"}},
                    "input": prompt,
                },
            )
        finally:
            if owned_client:
                await client.aclose()
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProposalProviderError("PROPOSAL_PROVIDER_UNAVAILABLE") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise ProposalProviderError("PROPOSAL_PROVIDER_REJECTED_REQUEST")
    try:
        payload = response.json()
        text = _extract_output_text(payload)
        if len(text.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ProposalProviderError("PROPOSAL_OUTPUT_SIZE_INVALID")
        # Keep JSON as text so the canonical proposal parser can reject duplicate keys.
        return text
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ProposalProviderError("PROPOSAL_PROVIDER_OUTPUT_INVALID") from exc


def _extract_output_text(payload: Any) -> str:
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise ValueError("incomplete response")
    texts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise ValueError("refusal")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if len(texts) != 1:
        raise ValueError("missing or ambiguous text")
    return texts[0]
