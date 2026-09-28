"""P1 proposal route remains worker-only and disabled unless explicitly enabled."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.problem_spec import ProblemDefinition, ProblemSpecSnapshot, definition_hash
from app.proposals import (
    ProposalCreateResponse,
    make_source_span_id,
    parse_source_span_id,
    validate_model_proposal,
)
from app.research_store import Neo4jResearchStore

PROPOSER_TOKEN = "proposal-worker-token-distinct-from-gateway-123456"
VERIFIER_TOKEN = "proposal-verifier-token-distinct-from-gateway-123456"


def _body(workspace_id: str = "workspace-1") -> dict[str, str]:
    return {"workspace_id": workspace_id, "output_json": "{}"}


def _headers(token: str = PROPOSER_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Idempotency-Key": "proposal-route-test-key",
    }


def _configure(monkeypatch, *, enabled: bool) -> None:
    monkeypatch.setenv("FGL_ENABLE_PROPOSALS", str(enabled).lower())
    monkeypatch.setenv("FGL_ENABLE_PROPOSAL_GENERATION", "false")
    monkeypatch.setenv("SERVICE_TOKEN", "proposal-gateway-token-for-unrelated-routes")
    monkeypatch.setenv(
        "FGL_WORKER_IDENTITIES",
        '[{"identity":"proposal-worker","token":"'
        + PROPOSER_TOKEN
        + '","role":"proposer","workspaces":["workspace-1"]},'
        '{"identity":"verifier-worker","token":"'
        + VERIFIER_TOKEN
        + '","role":"verifier","workspaces":["workspace-1"]}]',
    )
    app.state.research_store = Neo4jResearchStore(driver=None)


def test_proposal_route_is_disabled_by_default_and_requires_worker_auth(monkeypatch):
    _configure(monkeypatch, enabled=False)
    client = TestClient(app)
    path = "/v1/research/proposals"
    assert client.post(path, json=_body(), headers=_headers()).status_code == 503

    monkeypatch.setenv("FGL_ENABLE_PROPOSALS", "true")
    assert client.post(path, json=_body()).status_code == 401
    assert client.post(path, json=_body(), headers=_headers(VERIFIER_TOKEN)).status_code == 403
    assert client.post(path, json=_body("workspace-2"), headers=_headers()).status_code == 403


def test_proposal_route_passes_only_proposer_scope_and_idempotency_to_store(monkeypatch):
    _configure(monkeypatch, enabled=True)

    class FakeStore:
        async def record_proposal(self, **kwargs):
            assert kwargs == {
                "workspace_id": "workspace-1",
                "actor_id": "proposal-worker",
                "output_json": '{"proposal":true}',
                "idempotency_key": "proposal-route-test-key",
            }
            return SimpleNamespace(
                replayed=False,
                model_dump=lambda mode: {
                    "proposal": {"proposal_id": "prop_" + "a" * 32},
                    "replayed": False,
                },
                model_dump_json=lambda: json.dumps(
                    {
                        "proposal": {"proposal_id": "prop_" + "a" * 32},
                        "replayed": False,
                    }
                ),
            )

    app.state.research_store = FakeStore()
    response = TestClient(app).post(
        "/v1/research/proposals",
        json={"workspace_id": "workspace-1", "output_json": '{"proposal":true}'},
        headers=_headers(),
    )
    assert response.status_code == 201
    assert response.json()["replayed"] is False


def _research_headers(key: str = "proposal-generation-key") -> dict[str, str]:
    return {
        "Authorization": "Bearer proposal-gateway-token-for-unrelated-routes",
        "X-Fgl-Actor-Id": "researcher-1",
        "X-Fgl-Actor-Role": "researcher",
        "X-Fgl-Workspace-Id": "workspace-1",
        "X-Idempotency-Key": key,
    }


def test_proposal_generation_capability_is_authenticated_and_exposes_only_consent_details(
    monkeypatch,
):
    _configure(monkeypatch, enabled=False)
    client = TestClient(app)
    path = "/v1/research/proposals/capabilities"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=_research_headers())
    assert response.status_code == 200
    assert response.json() == {"state": "disabled"}
    assert response.headers["cache-control"] == "no-store"

    monkeypatch.setenv("FGL_ENABLE_PROPOSALS", "true")
    monkeypatch.setenv("FGL_ENABLE_PROPOSAL_GENERATION", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert client.get(path, headers=_research_headers()).json() == {
        "state": "provider_not_configured",
    }

    monkeypatch.setenv("OPENAI_API_KEY", "test-api-secret-must-not-leak")
    monkeypatch.setenv("FGL_PROPOSAL_MODEL", "test-model")
    for name in (
        "FGL_PROPOSAL_MAX_COST_USD",
        "FGL_PROPOSAL_DAILY_MAX_COST_USD",
        "FGL_PROPOSAL_INPUT_USD_PER_MILLION",
        "FGL_PROPOSAL_OUTPUT_USD_PER_MILLION",
    ):
        monkeypatch.delenv(name, raising=False)
    assert client.get(path, headers=_research_headers()).json() == {
        "state": "budget_not_configured",
    }
    monkeypatch.setenv("FGL_PROPOSAL_MAX_COST_USD", "0.02")
    monkeypatch.setenv("FGL_PROPOSAL_DAILY_MAX_COST_USD", "0.10")
    monkeypatch.setenv("FGL_PROPOSAL_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("FGL_PROPOSAL_OUTPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("FGL_PROPOSAL_MAX_OUTPUT_TOKENS", "2000")
    configured = client.get(path, headers=_research_headers()).json()
    assert configured == {
        "state": "configured",
        "model": "test-model",
        "max_generation_cost_usd": "0.02",
        "daily_max_cost_usd": "0.10",
    }
    assert "test-api-secret-must-not-leak" not in json.dumps(configured)
    assert "input_usd_per_million" not in configured
    assert "output_usd_per_million" not in configured
    assert "remaining" not in configured


def _generation_body() -> dict:
    return {
        "spec_id": "spec-test",
        "parent_ids": ["eq-left", "eq-right"],
        "source_span_ids": [
            make_source_span_id("eq-left", "S1.E1"),
            make_source_span_id("eq-right", "S2.E1"),
        ],
        "research_question": "Can these feature maps define a useful mixture?",
    }


def _generation_spec() -> ProblemSpecSnapshot:
    hash_a, hash_b, hash_c = "a" * 64, "b" * 64, "c" * 64
    definition = ProblemDefinition.model_validate(
        {
            "task": "Explore a feature-map mixture",
            "method_family": "linear-attention",
            "metrics": [{"name": "latency", "unit": "ms", "direction": "minimize"}],
            "baselines": [{"name": "baseline", "version": "1", "sha256": hash_a}],
            "dataset": {
                "artifact_hash": hash_b,
                "version": "1",
                "splits": [{"role": "search", "split_hash": hash_c}],
            },
            "model": {"name": "model", "version": "1", "sha256": hash_a},
            "tokenizer": {"status": "not_applicable", "reason": "Route test."},
            "hardware": {"target": "cpu"},
            "backend": {"name": "numpy", "version": "2"},
            "dtype": "float64",
            "evaluator": {
                "name": "test-evaluator",
                "protocol_version": "1",
                "implementation_hash": hash_b,
                "config_hash": hash_c,
            },
            "seeds": [1],
            "budget": {
                "max_candidates": 2,
                "max_generations": 1,
                "wall_time_ms": 1000,
                "compute_budget": 1,
                "compute_unit": "CPU-seconds",
            },
            "allowed_transforms": [{"name": "mix_positive_feature_maps", "version": "1"}],
        }
    )
    return ProblemSpecSnapshot(
        content_hash=definition_hash(definition),
        spec_id="spec-test",
        workspace_id="workspace-1",
        campaign_id="campaign-test",
        created_by="researcher-1",
        created_at="2026-09-25T00:00:00Z",
        definition=definition,
    )


def test_generation_is_disabled_by_default_even_for_authenticated_human(monkeypatch):
    _configure(monkeypatch, enabled=True)
    response = TestClient(app).post(
        "/v1/research/proposals/generate",
        json=_generation_body(),
        headers=_research_headers(),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "AI proposal generation is disabled."


def test_generation_resolves_workspace_sources_preflights_budget_and_records_untrusted_draft(
    monkeypatch,
):
    _configure(monkeypatch, enabled=True)
    monkeypatch.setenv("FGL_ENABLE_PROPOSAL_GENERATION", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FGL_PROPOSAL_MODEL", "test-model")
    monkeypatch.setenv("FGL_PROPOSAL_MAX_COST_USD", "0.02")
    monkeypatch.setenv("FGL_PROPOSAL_DAILY_MAX_COST_USD", "0.10")
    monkeypatch.setenv("FGL_PROPOSAL_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("FGL_PROPOSAL_OUTPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("FGL_PROPOSAL_MAX_OUTPUT_TOKENS", "2000")
    spans = _generation_body()["source_span_ids"]
    draft = {
        "problem_spec_id": "spec-test",
        "parent_ids": ["eq-left", "eq-right"],
        "source_span_ids": spans,
        "transform": {
            "operator": "mix_positive_feature_maps",
            "operator_version": "1",
            "target_node_id": "eq-left",
            "parameters": {"lambda": 0.25},
            "bindings": {"left": "sym-eq-left", "right": "sym-eq-right"},
        },
        "assumptions": ["Both features are positive on the reviewed domain."],
        "rationale": "Explore a bounded mixture of the selected feature maps.",
        "expected_effect": "A new kernel hypothesis; quality remains unmeasured.",
    }
    output_json = json.dumps(draft, separators=(",", ":"))

    class FakeStore:
        async def reserve_proposal_generation(self, **kwargs):
            assert kwargs["workspace_id"] == "workspace-1"
            assert kwargs["actor_id"] == "researcher-1"
            assert kwargs["idempotency_key"] == "proposal-generation-key"
            assert kwargs["intent_hash"]
            assert kwargs["reserve_usd"] > 0
            assert (
                kwargs["daily_limit_usd"]
                == main_module.OpenAIProposalConfig.from_environment().daily_max_cost_usd
            )
            return {"status": "reserved", "payload": None}

        async def save_generated_proposal_draft(self, **kwargs):
            assert kwargs["output_json"] == output_json

        async def get_problem_spec(self, **kwargs):
            assert kwargs == {"workspace_id": "workspace-1", "spec_id": "spec-test"}
            return _generation_spec()

        async def list_research_sources(self, **kwargs):
            span_id = next(
                span for span in spans if parse_source_span_id(span)[0] == kwargs["source_id"]
            )
            return {
                "items": [
                    {
                        "id": kwargs["source_id"],
                        "kind": "equation",
                        "anchor": "S1.E1" if kwargs["source_id"] == "eq-left" else "S2.E1",
                        "source_span_id": span_id,
                        "latex": "\\phi(x)",
                        "symbol_refs": [{"id": "sym-" + kwargs["source_id"], "notation": "\\phi"}],
                    }
                ]
            }

        async def record_proposal(self, **kwargs):
            assert kwargs["workspace_id"] == "workspace-1"
            assert kwargs["actor_id"] == "researcher-1"
            assert kwargs["idempotency_key"] == "proposal-generation-key"
            assert kwargs["output_json"] == output_json
            return SimpleNamespace(
                replayed=False,
                model_dump=lambda mode: {
                    "proposal": {"proposal_id": "prop_" + "a" * 32},
                    "replayed": False,
                },
            )

        async def complete_proposal_generation(self, **kwargs):
            assert kwargs["intent_hash"]
            assert (
                json.loads(kwargs["response_json"])["proposal"]["proposal_id"] == "prop_" + "a" * 32
            )

    app.state.research_store = FakeStore()
    budget_calls = []
    original_budget = main_module.OpenAIProposalConfig.require_budget

    def record_budget(self, prompt_bytes, *, requests=1):
        budget_calls.append((prompt_bytes, requests))
        return original_budget(self, prompt_bytes, requests=requests)

    monkeypatch.setattr(main_module.OpenAIProposalConfig, "require_budget", record_budget)
    calls = []

    async def fake_provider(prompt, *, config):
        calls.append((prompt, config.model))
        if len(calls) == 1:
            return '{"extra":"first attempt is intentionally invalid"}'
        return output_json

    monkeypatch.setattr(main_module, "request_proposal_draft", fake_provider)
    response = TestClient(app).post(
        "/v1/research/proposals/generate",
        json=_generation_body(),
        headers=_research_headers(),
    )
    assert response.status_code == 201
    assert response.json()["proposal"]["proposal_id"] == "prop_" + "a" * 32
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1] == "test-model"
    assert "Local schema validation rejected" not in calls[0][0]
    assert "Local schema validation rejected" in calls[1][0]
    assert budget_calls == [(budget_calls[0][0], 3)]


def test_completed_generation_replay_skips_provider_and_returns_saved_proposal(monkeypatch):
    _configure(monkeypatch, enabled=True)
    monkeypatch.setenv("FGL_ENABLE_PROPOSAL_GENERATION", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FGL_PROPOSAL_MODEL", "test-model")
    monkeypatch.setenv("FGL_PROPOSAL_MAX_COST_USD", "0.02")
    monkeypatch.setenv("FGL_PROPOSAL_DAILY_MAX_COST_USD", "0.10")
    monkeypatch.setenv("FGL_PROPOSAL_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("FGL_PROPOSAL_OUTPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("FGL_PROPOSAL_MAX_OUTPUT_TOKENS", "2000")
    spec = _generation_spec()
    spans = _generation_body()["source_span_ids"]
    draft = {
        "problem_spec_id": spec.spec_id,
        "parent_ids": ["eq-left", "eq-right"],
        "source_span_ids": spans,
        "transform": {
            "operator": "mix_positive_feature_maps",
            "operator_version": "1",
            "target_node_id": "eq-left",
            "parameters": {"lambda": 0.25},
            "bindings": {"left": "sym-eq-left", "right": "sym-eq-right"},
        },
        "assumptions": [],
        "rationale": "A replay fixture proposal.",
        "expected_effect": "No empirical effect has been measured.",
    }
    proposal = validate_model_proposal(
        json.dumps(draft),
        workspace_id="workspace-1",
        spec=spec,
        allowed_parent_ids=frozenset(draft["parent_ids"]),
        allowed_source_span_ids=frozenset(spans),
        allowed_target_node_ids=frozenset(draft["parent_ids"]),
    )
    saved_response = ProposalCreateResponse(proposal=proposal, replayed=False).model_dump_json()

    class CompletedStore:
        async def get_problem_spec(self, **kwargs):
            return spec

        async def list_research_sources(self, **kwargs):
            source_id = kwargs["source_id"]
            span_id = next(span for span in spans if parse_source_span_id(span)[0] == source_id)
            return {
                "items": [
                    {
                        "id": source_id,
                        "kind": "equation",
                        "anchor": "S1.E1" if source_id == "eq-left" else "S2.E1",
                        "source_span_id": span_id,
                        "latex": "\\phi(x)",
                        "symbol_refs": [{"id": "sym-" + source_id, "notation": "\\phi"}],
                    }
                ]
            }

        async def reserve_proposal_generation(self, **kwargs):
            assert kwargs["idempotency_key"] == "proposal-generation-key"
            return {"status": "completed", "payload": saved_response}

    async def provider_must_not_run(*args, **kwargs):
        raise AssertionError("A completed idempotency receipt must not call the model again.")

    app.state.research_store = CompletedStore()
    monkeypatch.setattr(main_module, "request_proposal_draft", provider_must_not_run)
    response = TestClient(app).post(
        "/v1/research/proposals/generate",
        json=_generation_body(),
        headers=_research_headers(),
    )
    assert response.status_code == 200
    assert response.json()["proposal"]["proposal_id"] == proposal.proposal_id
    assert response.json()["replayed"] is True


def test_proposal_history_is_human_workspace_scoped_read_only(monkeypatch):
    _configure(monkeypatch, enabled=True)

    class FakeStore:
        async def list_research_proposals(self, **kwargs):
            assert kwargs == {
                "workspace_id": "workspace-1",
                "limit": 10,
                "offset": 100_000,
            }
            return [
                {
                    "proposal": {"proposal_id": "prop_" + "b" * 32},
                    "created_at": "2026-09-25T00:00:00+00:00",
                }
            ], 21

    app.state.research_store = FakeStore()
    client = TestClient(app)
    path = "/v1/research/proposals?limit=10&offset=200000"
    assert client.get(path).status_code == 401
    response = client.get(
        path,
        headers={
            "Authorization": "Bearer proposal-gateway-token-for-unrelated-routes",
            "X-Fgl-Actor-Id": "researcher-1",
            "X-Fgl-Actor-Role": "researcher",
            "X-Fgl-Workspace-Id": "workspace-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["total"] == 21
    assert response.headers["cache-control"] == "no-store"


def test_proposal_review_requires_human_workspace_identity_and_records_decision(monkeypatch):
    _configure(monkeypatch, enabled=True)

    class FakeStore:
        async def review_research_proposal(self, **kwargs):
            assert kwargs["workspace_id"] == "workspace-1"
            assert kwargs["proposal_id"] == "prop_" + "c" * 32
            assert kwargs["reviewer_id"] == "researcher-1"
            assert kwargs["reviewer_role"] == "reviewer"
            assert kwargs["request"].decision == "accept_for_compilation"
            assert kwargs["idempotency_key"] == "review-key"
            return SimpleNamespace(
                replayed=False,
                model_dump=lambda mode: {
                    "review": {
                        "review_id": "prev_" + "d" * 32,
                        "decision": "accept_for_compilation",
                        "proposal_id": "prop_" + "c" * 32,
                    },
                    "replayed": False,
                },
            )

    app.state.research_store = FakeStore()
    client = TestClient(app)
    path = f"/v1/research/proposals/prop_{'c' * 32}/reviews"
    body = {"decision": "accept_for_compilation", "notes": "Checked source and transform."}
    assert (
        client.post(path, json=body, headers={"X-Idempotency-Key": "review-key"}).status_code == 401
    )
    assert (
        client.post(
            path,
            json=body,
            headers={
                "Authorization": f"Bearer {PROPOSER_TOKEN}",
                "X-Idempotency-Key": "review-key",
            },
        ).status_code
        == 401
    )
    response = client.post(
        path,
        json=body,
        headers={
            "Authorization": "Bearer proposal-gateway-token-for-unrelated-routes",
            "X-Fgl-Actor-Id": "researcher-1",
            "X-Fgl-Actor-Role": "reviewer",
            "X-Fgl-Workspace-Id": "workspace-1",
            "X-Idempotency-Key": "review-key",
        },
    )
    assert response.status_code == 201
    assert response.json()["review"]["decision"] == "accept_for_compilation"
    assert response.headers["cache-control"] == "no-store"


def test_proposal_review_rejects_non_human_role_and_client_actor_fields(monkeypatch):
    _configure(monkeypatch, enabled=True)
    client = TestClient(app)
    path = f"/v1/research/proposals/prop_{'c' * 32}/reviews"
    headers = {
        "Authorization": "Bearer proposal-gateway-token-for-unrelated-routes",
        "X-Fgl-Actor-Id": "worker-1",
        "X-Fgl-Actor-Role": "proposer",
        "X-Fgl-Workspace-Id": "workspace-1",
        "X-Idempotency-Key": "review-key",
    }
    body = {"decision": "reject", "notes": "Not supported."}
    assert client.post(path, json=body, headers=headers).status_code == 403
    human_headers = headers | {"X-Fgl-Actor-Role": "researcher"}
    forged_body = body | {"reviewer_id": "admin", "reviewer_role": "admin"}
    assert client.post(path, json=forged_body, headers=human_headers).status_code == 422
