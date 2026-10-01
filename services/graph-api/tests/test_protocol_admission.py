"""Finite protocol evidence grants search eligibility, never a global proof."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.admission import decide_admission
from app.candidate_verification import verify_compiled_candidate
from app.evolution import start_campaign
from app.problem_spec import ProblemDefinition, definition_hash
from app.replay_bundle import _same_scientific_result
from app.research_case import (
    make_implementation_binding,
    protocol_admission_context,
    run_registered_research_case,
    validate_registered_spec,
)
from app.research_case_worker import evaluate
from app.research_compiler import compile_transform
from app.research_store import Neo4jResearchStore, ResearchValidationError
from app.symbol_contracts import ReviewedContractValue
from tests.test_research_case import IMAGE, NOW, RUN_ID, _candidates, _spec
from tests.test_research_compiler import MIX, _context


def scoped_evidence(role="search", failure=False):
    spec = _spec()
    context = _context().model_copy(update={"spec": spec})
    context = context.model_copy(
        update={
            "right": context.right.model_copy(update={"feature_rank": context.left.feature_rank})
        }
    )
    mixture = compile_transform(MIX | {"parameters": {"lambda": 0.75}}, context=context)
    candidate = compile_transform(
        {
            "operator": "lower_mixture_to_concatenation",
            "operator_version": "1",
            "target_node_id": mixture.candidate_id,
            "parameters": {},
            "bindings": {"mixture": mixture.candidate_id},
        },
        context=context,
        parent_candidate=mixture,
    )
    check = verify_compiled_candidate(candidate, parent_candidate=mixture)
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=mixture, execution_image=IMAGE, now=NOW
    )
    result = run_registered_research_case(
        binding,
        candidate,
        spec,
        parent_candidate=mixture,
        actor_id="worker",
        run_id=RUN_ID,
        now=NOW,
        evaluation_role=role,
        evolution_id="evo_" + "a" * 32 if role == "holdout" else None,
        worker_runner=lambda payload, **_: (
            {"outcome": "timeout", "error_code": "TIMEOUT"} if failure else evaluate(payload)
        ),
    )
    return candidate, spec, mixture, check, binding, result


@pytest.mark.parametrize(
    "role,reviewed,failure,expected",
    [
        ("search", False, False, False),
        ("search", True, False, True),
        ("holdout", True, False, False),
        ("search", True, True, False),
    ],
)
def test_scoped_receipts_do_not_bypass_human_or_holdout_boundaries(
    role, reviewed, failure, expected
):
    evidence = scoped_evidence(role, failure)
    context = protocol_admission_context(
        *evidence,
        reviewed=reviewed,
        review_id="prv_" + "c" * 32 if reviewed else None,
        mapping_freshness="current",
        mapping_usable=True,
        quota_available=True,
    )
    decision = decide_admission(context, action="can_enter_parent_pool", actor_id="human")
    assert decision.allowed is expected
    assert not decide_admission(
        context, action="can_publish_claim", claim_scope="mathematical", actor_id="human"
    ).allowed
    assert evidence[3].vector.domain == "conditional"
    if role == "search":
        assert all(t.split != "holdout" for t in evidence[-1].trials)
        assert evidence[-1].holdout_mean is None
    stale = context.model_copy(update={"mapping_freshness": "stale", "mapping_usable": False})
    assert not decide_admission(stale, action="can_enter_parent_pool", actor_id="human").allowed


def test_replay_tolerance_does_not_tolerate_changed_decisions_or_meaningful_metrics():
    assert _same_scientific_result({"metric": 0.1}, {"metric": 0.1 + 1e-15})
    assert not _same_scientific_result({"metric": 0.1}, {"metric": 0.1 + 1e-6})
    assert not _same_scientific_result({"allowed": False}, {"allowed": True})
    assert not _same_scientific_result({"metric": 1}, {"metric": True})


def test_function_codomain_is_explicit_and_legacy_contract_hash_input_is_unchanged():
    vector = ReviewedContractValue(name="phi", category="vector", shape=(64,), feature_rank=16)
    assert "feature_output_domain" not in vector.model_dump(mode="json")
    with pytest.raises(ValueError):
        ReviewedContractValue(name="phi", category="function", shape=(64,), feature_rank=16)
    function = ReviewedContractValue(
        name="phi",
        category="function",
        shape=(64,),
        feature_rank=16,
        domain="real",
        feature_output_domain="strictly_positive_real",
    )
    assert function.category == "function" and function.domain == "real"


def test_checked_in_pilot_manifest_matches_registered_worker_and_frozen_splits():
    definition = ProblemDefinition.model_validate_json(
        (Path(__file__).resolve().parents[3] / "reports/phase5-pilot-spec.json").read_text(
            encoding="utf-8"
        )
    )
    spec = _spec().model_copy(
        update={"definition": definition, "content_hash": definition_hash(definition)}
    )
    validate_registered_spec(spec)


@pytest.mark.asyncio
async def test_expired_campaign_cannot_launch_a_worker_but_can_retain_its_receipt():
    spec, _, candidate = _candidates()
    campaign = start_campaign(spec, actor_id="human", now=datetime.now(UTC) - timedelta(days=1))
    tx = AsyncMock()
    tx.run.return_value.single.return_value = {
        "campaign": campaign.model_dump_json(),
        "candidate": candidate.model_dump_json(),
    }
    with pytest.raises(ResearchValidationError, match="wall-time budget"):
        await Neo4jResearchStore._tx_authorize_protocol_phase(
            tx,
            "group",
            candidate.candidate_id,
            "search",
            campaign.evolution_id,
            True,
        )
    await Neo4jResearchStore._tx_authorize_protocol_phase(
        tx,
        "group",
        candidate.candidate_id,
        "search",
        campaign.evolution_id,
    )
