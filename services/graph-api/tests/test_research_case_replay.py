"""Replay-bundle coverage for the registered CPU research case."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.analysis_versions import canonical_json
from app.candidate_verification import verify_compiled_candidate
from app.compatibility import CompatibilityReview
from app.replay_bundle import (
    ReplaySourceReference,
    make_compiler_replay_bundle,
    replay_candidate_from_bundle,
)
from app.research_case import make_implementation_binding, run_registered_research_case
from app.research_case_worker import evaluate
from app.research_compiler import TransformationActivity, compile_transform
from app.research_report import build_compiler_replay_report
from app.symbol_contracts import ContractReview, ReviewedContractValue
from tests.test_research_case import IMAGE, NOW, RUN_ID, _spec
from tests.test_research_compiler import MIX, _context


def _activity(candidate, context, request) -> TransformationActivity:
    context_json = canonical_json(context.model_dump(mode="json"))
    return TransformationActivity(
        activity_id="act_" + "2" * 32,
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.content_hash,
        workspace_id=candidate.workspace_id,
        actor_id="researcher",
        operator=candidate.operator,
        operator_version=candidate.operator_version,
        mapping_id=candidate.mapping_id,
        semantics_class=candidate.semantics_class,
        request_json=canonical_json(request),
        parents=candidate.parents,
        created_at=NOW.isoformat().replace("+00:00", "Z"),
        schema_version="transformation-activity.v2",
        compiler_context_json=context_json,
        compiler_context_hash=hashlib.sha256(context_json.encode()).hexdigest(),
    )


def test_registered_research_case_replays_from_persisted_bundle():
    spec = _spec()
    context = _context(target_node_id="eq-left").model_copy(update={"spec": spec})
    context = context.model_copy(update={
        "right": context.right.model_copy(update={"feature_rank": context.left.feature_rank})
    })
    contract = ReviewedContractValue(
        name="phi_a", category="vector", shape=(64,), feature_rank=16,
        domain="positive", normalization="none", mask="causal",
        causal=True, resource_class="not_applicable",
    )
    other_contract = contract.model_copy(update={"name": "phi_b"})
    context = context.model_copy(update={
        "left": context.left.model_copy(update={
            "contract_hash": hashlib.sha256(
                canonical_json(contract.model_dump(mode="json")).encode()
            ).hexdigest()
        }),
        "right": context.right.model_copy(update={
            "contract_hash": hashlib.sha256(
                canonical_json(other_contract.model_dump(mode="json")).encode()
            ).hexdigest()
        }),
    })
    mixture = compile_transform(MIX | {"target_node_id": "eq-left"}, context=context)
    request = {
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": mixture.candidate_id,
        "parameters": {},
        "bindings": {"mixture": mixture.candidate_id},
    }
    candidate = compile_transform(request, context=context, parent_candidate=mixture)
    activity = _activity(candidate, context, request)
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=mixture, execution_image=IMAGE, now=NOW
    )
    receipt = run_registered_research_case(
        binding,
        candidate,
        spec,
        parent_candidate=mixture,
        actor_id="experiment-worker",
        run_id=RUN_ID,
        worker_runner=lambda payload, **_kwargs: evaluate(payload),
        now=NOW,
    )
    contract_reviews = tuple(
        ContractReview(
            review_id=f"research-case-contract-{index}",
            symbol_name=value.name,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            decision="accepted",
            scope=equation_id,
            reviewed_contract=value,
            reviewed_at=datetime(2026, 9, 28, tzinfo=UTC),
        )
        for index, (equation_id, value) in enumerate(
            (("eq-left", contract), ("eq-right", other_contract))
        )
    )
    sources = tuple(
        ReplaySourceReference(
            equation_id=equation_id,
            equation_source_hash=source_hash,
            paper_id="2006.16236",
            paper_version=3,
            paper_version_id=f"paper-version-{equation_id}",
            paper_html_hash="a" * 64,
            source_url="https://arxiv.org/html/2006.16236v3",
            anchor=f"S1.E{index}",
            anchor_is_source=True,
        )
        for index, (equation_id, source_hash) in enumerate(
            (("eq-left", "a" * 64), ("eq-right", "b" * 64)), start=1
        )
    )
    bundle = make_compiler_replay_bundle(
        workspace_id=spec.workspace_id,
        candidate=candidate,
        activity=activity,
        parent_candidates=(mixture,),
        sources=sources,
        lineage_assertions=(),
        compatibility_reviews=(CompatibilityReview(
            review_id="research-case-mapping-review",
            mapping_id=context.mapping.mapping_id,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Reviewed the registered CPU mapping.",
            reviewed_at=NOW.isoformat().replace("+00:00", "Z"),
            dependency_fingerprint=context.current_dependency_fingerprint,
        ),),
        contract_reviews=contract_reviews,
        proposal=None,
        proposal_review=None,
        candidate_checks=(verify_compiled_candidate(candidate, parent_candidate=mixture),),
        admission_decisions=(),
        admission_replay_inputs=(),
        numerical_fixtures=(),
        implementation_bindings=(binding,),
        research_cases=(receipt,),
    )

    assert replay_candidate_from_bundle(bundle) == candidate
    report = build_compiler_replay_report(bundle)
    assert report.research_cases[0].result_id == receipt.result_id
    assert report.research_cases[0].replay_status == "replayed"
    assert report.empirical_experiment == receipt.outcome
