import asyncio
import json
import logging
from decimal import Decimal

import httpx
import pytest

from app.openai_proposal_provider import (
    MAX_RESPONSE_BYTES,
    OPENAI_RESPONSES_URL,
    OpenAIProposalConfig,
    ProposalProviderError,
    request_proposal_draft,
)


def _config(**overrides) -> OpenAIProposalConfig:
    values = dict(
        api_key="test-secret-that-is-never-logged",
        model="test-model",
        max_cost_usd=Decimal("0.02"),
        daily_max_cost_usd=Decimal("0.50"),
        input_usd_per_million=Decimal("1"),
        output_usd_per_million=Decimal("2"),
        max_output_tokens=2000,
    )
    values.update(overrides)
    return OpenAIProposalConfig(**values)


def test_generation_is_disabled_and_needs_explicit_cost_configuration(monkeypatch):
    monkeypatch.delenv("FGL_ENABLE_PROPOSAL_GENERATION", raising=False)
    with pytest.raises(ProposalProviderError, match="DISABLED"):
        OpenAIProposalConfig.from_environment()

    monkeypatch.setenv("FGL_ENABLE_PROPOSAL_GENERATION", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FGL_PROPOSAL_MODEL", "test-model")
    with pytest.raises(ProposalProviderError, match="COST_LIMIT_NOT_CONFIGURED"):
        OpenAIProposalConfig.from_environment()


@pytest.mark.anyio
async def test_responses_call_is_bounded_private_and_returns_untrusted_json_text():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"draft":true}'}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await request_proposal_draft("bounded prompt", config=_config(), client=client)

    assert result == '{"draft":true}'
    request = calls[0]
    body = __import__("json").loads(request.content)
    assert str(request.url) == OPENAI_RESPONSES_URL
    assert request.headers["authorization"] == "Bearer test-secret-that-is-never-logged"
    assert body["model"] == "test-model"
    assert body["store"] is False
    assert body["max_output_tokens"] == 2000
    assert body["text"] == {"format": {"type": "json_object"}}
    assert "tools" not in body


@pytest.mark.anyio
async def test_preflight_rejects_over_budget_before_network_request():
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    config = _config(max_cost_usd=Decimal("0.000001"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProposalProviderError, match="COST_CAP_WOULD_BE_EXCEEDED"):
            await request_proposal_draft("bounded prompt", config=config, client=client)
    assert calls == 0


def test_budget_reserves_all_bounded_generation_attempts():
    config = _config(max_cost_usd=Decimal("0.01"))
    with pytest.raises(ProposalProviderError, match="COST_CAP_WOULD_BE_EXCEEDED"):
        config.require_budget(len("bounded prompt"), requests=3)


def test_budget_enforces_workspace_daily_cap_before_provider_call():
    config = _config(daily_max_cost_usd=Decimal("0.000001"))
    with pytest.raises(ProposalProviderError, match="DAILY_CAP_WOULD_BE_EXCEEDED"):
        config.require_budget(len("bounded prompt"))


@pytest.mark.anyio
async def test_provider_refusal_or_incomplete_result_is_not_a_proposal():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "cannot comply"}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProposalProviderError, match="OUTPUT_INVALID"):
            await request_proposal_draft("bounded prompt", config=_config(), client=client)


@pytest.mark.anyio
async def test_provider_errors_are_sanitized_and_timeout_is_bounded():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="secret response detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProposalProviderError, match="REJECTED_REQUEST") as caught:
            await request_proposal_draft("bounded prompt", config=_config(), client=client)
    assert "secret response detail" not in str(caught.value)


@pytest.mark.anyio
async def test_prompt_size_and_output_token_ceiling_are_enforced():
    config = _config(max_output_tokens=8001)
    with pytest.raises(ProposalProviderError, match="TOKEN_LIMIT"):
        config.require_budget(2)
    with pytest.raises(ProposalProviderError, match="PROMPT_SIZE"):
        _config().require_budget(32 * 1024 + 1)


@pytest.mark.anyio
@pytest.mark.parametrize("usage", [None, {"input_tokens": 10, "output_tokens": 20}])
async def test_usage_telemetry_distinguishes_unknown_from_provider_report_and_never_logs_content(
    usage,
    caplog,
):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": usage,
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"PRIVATE_MODEL_OUTPUT":true}'},
                        ],
                    }
                ],
            },
        )

    with caplog.at_level(logging.INFO, logger="fgl.telemetry"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await request_proposal_draft("PRIVATE_PROMPT", config=_config(), client=client)
    event = json.loads(next(r.message for r in caplog.records if r.name == "fgl.telemetry"))
    assert event["outcome"] == "completed" and event["duration_ms"] >= 0
    if usage is None:
        assert event["usage_state"] == "unknown" and "estimated_cost_usd" not in event
    else:
        assert event["usage_state"] == "provider_reported"
        assert event["input_tokens"] == 10 and event["output_tokens"] == 20
        assert Decimal(event["estimated_cost_usd"]) == Decimal("0.00005")
    assert all(
        s not in caplog.text
        for s in [
            "PRIVATE_PROMPT",
            "PRIVATE_MODEL_OUTPUT",
            "test-secret-that-is-never-logged",
        ]
    )


@pytest.mark.anyio
async def test_streamed_provider_response_cannot_exceed_wire_cap():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProposalProviderError, match="OUTPUT_SIZE_INVALID"):
            await request_proposal_draft("bounded prompt", config=_config(), client=client)


@pytest.mark.anyio
async def test_drip_response_cannot_reset_total_provider_deadline():
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.003)
                yield b" "

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Drip())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProposalProviderError, match="PROVIDER_UNAVAILABLE"):
            await request_proposal_draft(
                "bounded prompt", config=_config(timeout_seconds=0.01), client=client
            )
