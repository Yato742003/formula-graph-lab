"""Real-Neo4j acceptance tests for Sprint 5A's source-backed state."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import numerical_verification
from app.admission import AdmissionEvaluationRequest
from app.analysis_versions import source_hash
from app.compatibility import PortDescriptor, PortMappingCreateRequest, ResolvedDependency
from app.episodes import workspace_group_id
from app.evidence import build_evidence_graph
from app.evidence_store import Neo4jEvidenceStore
from app.extractor import extract_paper
from app.lineage import (
    EvidenceReference,
    LineageAssertionCreateRequest,
    LineageEndpoint,
    MetadataPaperCreateRequest,
    PaperCoverageRecord,
)
from app.numerical_verification import (
    build_numerical_suite_input,
    make_numerical_fixture_receipt,
    run_candidate_numerical_fixture,
)
from app.numerical_worker import _evaluate
from app.problem_spec import ProblemDefinition, TransformDeclaration
from app.proposals import (
    ProposalReviewCreateRequest,
    make_source_span_id,
    parse_source_span_id,
)
from app.replay_bundle import replay_candidate_from_bundle
from app.research_compiler import (
    CompileCandidateRequest,
    TransformationActivity,
    replay_compiled_candidate,
)
from app.research_jobs import ResearchQueue
from app.research_report import build_compiler_replay_report
from app.research_store import (
    IdempotencyConflictError,
    LineageCycleError,
    Neo4jResearchStore,
    ProposalGenerationBudgetExceededError,
    ProposalGenerationInProgressError,
    ResearchAuthorizationError,
    ResearchReferenceNotFoundError,
    ResearchValidationError,
)
from app.symbol_contracts import ContractReview, ReviewedContractValue
from app.worker_auth import WorkerPrincipal
from tests.test_problem_spec import make_valid_definition_payload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ATTENTION_CORPUS = Path(__file__).parent / "fixtures" / "corpus"


def connection():
    if not os.getenv("TEST_NEO4J_PASSWORD"):
        pytest.fail("Integration tests require the dedicated test Neo4j configuration.")
    return (
        os.getenv("TEST_NEO4J_URI", "bolt://127.0.0.1:17687"),
        "neo4j",
        os.environ["TEST_NEO4J_PASSWORD"],
    )


def problem_definition(task: str = "Attention optimization") -> ProblemDefinition:
    payload = make_valid_definition_payload()
    payload["task"] = task
    return ProblemDefinition.model_validate(payload)


def paper_graph(
    workspace_id: str,
    paper_id: str,
    expression: str,
    *,
    reference_to: str | None = None,
):
    references = (
        f"<section id='Refs'><h2>References</h2><p>See arXiv:{reference_to} "
        f"<a href='https://arxiv.org/abs/{reference_to}'>source paper</a>.</p></section>"
        if reference_to
        else ""
    )
    paper = extract_paper(
        "<html><div id='watermark-tr'>"
        f"arXiv:{paper_id}v1 [cs.AI] 01 Feb 2024"
        "</div><section id='S1'><h2>Result</h2>"
        f"<math id='S1.E1' display='block' alttext='{expression}'></math>"
        f"</section>{references}</html>",
        f"https://arxiv.org/html/{paper_id}v1",
    )
    return build_evidence_graph(paper, workspace_id=workspace_id)


def equation(graph):
    return next(node for node in graph.nodes if node["kind"] == "Equation")


def evidence_ref(node) -> EvidenceReference:
    payload = json.loads(node["payload"])
    return EvidenceReference(
        source_entity_id=node["uuid"],
        anchor=payload["anchor"],
        source_hash=source_hash(node["payload"]),
    )


def attention_corpus_graph(workspace_id: str, paper_id: str):
    cases = json.loads((ATTENTION_CORPUS / "expected.json").read_text(encoding="utf-8"))
    case = next(item for item in cases if item["paper_id"] == paper_id)
    source = (ATTENTION_CORPUS / f"{paper_id}.html").read_text(encoding="utf-8")
    paper = extract_paper(source, case["source_url"])
    return build_evidence_graph(paper, workspace_id=workspace_id)


def source_node(graph, kind: str, anchor: str) -> dict:
    for node in graph.nodes:
        if node["kind"] == kind and json.loads(node["payload"])["anchor"] == anchor:
            return node
    raise AssertionError(f"Missing source-backed {kind} anchor {anchor}")


def browser_port(
    node, *, shape=(999,), domain="complex", symbol_name="x",
) -> PortDescriptor:
    return PortDescriptor(
        equation_id=node["uuid"],
        version=1,
        scoped_symbol_id="browser-invented-scope",
        symbol_name=symbol_name,
        domain=domain,
        domain_is_reviewed=True,
        shape=shape,
        normalization="softmax",
        mask="causal",
        causal=True,
    )


async def accept_contract(
    evidence_store: Neo4jEvidenceStore,
    workspace_id: str,
    node,
    *,
    shape: tuple[int, ...] = (),
    domain: str = "real",
    feature_rank: int | None = None,
    symbol_name: str = "x",
    key: str,
    reviewed_at: datetime | None = None,
):
    payload = json.loads(node["payload"])
    review = ContractReview(
        review_id="server-owned",
        symbol_name=symbol_name,
        reviewer_id="reviewer_1",
        reviewer_role="reviewer",
        decision="accepted",
        scope=node["uuid"],
        reviewed_contract=ReviewedContractValue(
            name=symbol_name,
            category="scalar" if shape == () else "vector",
            shape=shape,
            feature_rank=feature_rank,
            domain=domain,
            constraints=[],
            scope=payload["section_id"],
            normalization="none",
            mask="none",
            causal=False,
            resource_class="not_applicable",
        ),
        evidence=[f"source-anchor:{payload['anchor']}"],
        reviewed_at=reviewed_at or datetime.now(UTC),
    )
    return await evidence_store.append_contract_review(
        workspace_id=workspace_id,
        equation_uuid=node["uuid"],
        idempotency_key=key,
        review=review,
    )


async def evidence_backed_stores(workspace_id: str):
    evidence_store = Neo4jEvidenceStore.connect(*connection())
    research_store = Neo4jResearchStore(
        evidence_store.driver,
        database=evidence_store.database,
    )
    graph_a = paper_graph(workspace_id, "2402.08954", "x=1")
    graph_b = paper_graph(workspace_id, "2402.08955", "x=2")
    await evidence_store.initialize()
    await research_store.initialize()
    await evidence_store.ingest(graph_a)
    await evidence_store.ingest(graph_b)
    return evidence_store, research_store, graph_a, graph_b


async def test_evolution_campaign_append_replay_is_durable_and_workspace_scoped():
    store = Neo4jResearchStore.connect(*connection())
    await store.initialize()
    workspace_id = f"evolution_{uuid4().hex}"
    try:
        spec, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            idempotency_key="evolution-spec",
            definition=problem_definition(),
        )
        campaign, replayed = await store.start_evolution_campaign(
            workspace_id=workspace_id,
            spec_id=spec.spec_id,
            actor_id="researcher_1",
            idempotency_key="evolution-start",
        )
        assert replayed is False
        reserved, replayed = await store.reserve_evolution_generation(
            workspace_id=workspace_id,
            evolution_id=campaign.evolution_id,
            candidate_slots=1,
            compute_reserved=1,
            actor_id="controller_1",
            idempotency_key="evolution-reserve",
        )
        assert replayed is False
        duplicate, replayed = await store.reserve_evolution_generation(
            workspace_id=workspace_id,
            evolution_id=campaign.evolution_id,
            candidate_slots=1,
            compute_reserved=1,
            actor_id="controller_1",
            idempotency_key="evolution-reserve",
        )
        assert replayed is True
        assert duplicate == reserved
        assert await store.get_evolution_campaign(
            workspace_id=workspace_id, evolution_id=campaign.evolution_id
        ) == reserved
        assert await store.get_evolution_campaign(
            workspace_id="foreign", evolution_id=campaign.evolution_id
        ) is None
        page, total = await store.list_evolution_campaigns(workspace_id=workspace_id)
        assert total == 1 and page == [reserved]
    finally:
        await store.close()


async def test_problem_spec_idempotency_is_atomic_and_content_does_not_move_campaigns():
    store = Neo4jResearchStore.connect(*connection())
    await store.initialize()
    workspace_id = f"spec_{uuid4().hex}"
    definition = problem_definition()
    try:
        first, second = await asyncio.gather(
            store.freeze_problem_spec(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                idempotency_key="same-request-key",
                definition=definition,
            ),
            store.freeze_problem_spec(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                idempotency_key="same-request-key",
                definition=definition,
            ),
        )
        assert first[0] == second[0]
        assert {first[1], second[1]} <= {False, True}

        same_content, replayed = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher_2",
            actor_role="researcher",
            idempotency_key="new-request-key",
            definition=definition,
        )
        assert replayed is False
        assert same_content.spec_id == first[0].spec_id
        assert same_content.campaign_id == first[0].campaign_id
        assert same_content.created_at == first[0].created_at

        with pytest.raises(IdempotencyConflictError):
            await store.freeze_problem_spec(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                idempotency_key="same-request-key",
                definition=problem_definition("Changed task"),
            )

        child, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            idempotency_key="revision-request-key",
            definition=problem_definition("Changed task"),
            parent_spec_id=first[0].spec_id,
        )
        assert child.parent_spec_id == first[0].spec_id
        assert child.campaign_id != first[0].campaign_id
    finally:
        await store.close()


async def test_lineage_resolves_real_evidence_preserves_assertion_and_rejects_cycles():
    workspace_id = f"lineage_{uuid4().hex}"
    evidence_store, store, graph_a, graph_b = await evidence_backed_stores(workspace_id)
    eq_a, eq_b = equation(graph_a), equation(graph_b)
    try:
        with pytest.raises(ResearchReferenceNotFoundError):
            await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=LineageAssertionCreateRequest(
                    relation_type="citation",
                    source=LineageEndpoint(kind="paper", id="invented", version=1),
                    target=LineageEndpoint(kind="paper", id=graph_b.paper_version_uuid, version=1),
                    evidence=(evidence_ref(eq_b),),
                ),
            )

        citation = await store.create_lineage_assertion(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            request=LineageAssertionCreateRequest(
                relation_type="citation",
                source=LineageEndpoint(kind="paper", id=graph_a.paper_version_uuid, version=1),
                target=LineageEndpoint(kind="paper", id=graph_b.paper_version_uuid, version=1),
                evidence=(evidence_ref(eq_b),),
                description="B cites the source-backed A occurrence.",
            ),
            idempotency_key="citation-create-key",
        )
        assert citation.source.id == graph_a.paper_version_uuid
        assert citation.evidence[0].source_entity_id == eq_b["uuid"]
        symbol = next(node for node in graph_b.nodes if node["kind"] == "Symbol")
        with pytest.raises(ResearchReferenceNotFoundError):
            await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=LineageAssertionCreateRequest(
                    relation_type="citation",
                    source=citation.source,
                    target=citation.target,
                    evidence=(
                        EvidenceReference(
                            source_entity_id=symbol["uuid"],
                            anchor=evidence_ref(eq_b).anchor,
                            source_hash=source_hash(symbol["payload"]),
                        ),
                    ),
                ),
            )
        with pytest.raises(ResearchValidationError, match="one paper lineage"):
            await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=LineageAssertionCreateRequest(
                    relation_type="revision",
                    source=LineageEndpoint(kind="paper", id=graph_a.paper_version_uuid, version=1),
                    target=LineageEndpoint(kind="paper", id=graph_b.paper_version_uuid, version=1),
                    evidence=(evidence_ref(eq_b),),
                ),
            )
        citation_retry = await store.create_lineage_assertion(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            request=LineageAssertionCreateRequest(
                relation_type=citation.relation_type,
                source=citation.source,
                target=citation.target,
                evidence=citation.evidence,
                description=citation.description,
            ),
            idempotency_key="citation-create-key",
        )
        assert citation_retry == citation
        with pytest.raises(IdempotencyConflictError):
            await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=LineageAssertionCreateRequest(
                    relation_type=citation.relation_type,
                    source=citation.source,
                    target=citation.target,
                    evidence=citation.evidence,
                    description="A changed claim with the same retry key.",
                ),
                idempotency_key="citation-create-key",
            )

        derivation = await store.create_lineage_assertion(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            request=LineageAssertionCreateRequest(
                relation_type="mathematical_derivation",
                source=LineageEndpoint(kind="equation", id=eq_a["uuid"], version=1),
                target=LineageEndpoint(kind="equation", id=eq_b["uuid"], version=1),
                evidence=(evidence_ref(eq_a),),
            ),
        )
        reviewed = await store.review_lineage_assertion(
            workspace_id=workspace_id,
            assertion_id=derivation.assertion_id,
            reviewer_id="reviewer_1",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Checked the exact equation occurrence and anchor.",
            idempotency_key="lineage-review-key",
        )
        replay = await store.review_lineage_assertion(
            workspace_id=workspace_id,
            assertion_id=derivation.assertion_id,
            reviewer_id="reviewer_1",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Checked the exact equation occurrence and anchor.",
            idempotency_key="lineage-review-key",
        )
        assert reviewed.review and replay.review
        assert reviewed.review.review_id == replay.review.review_id

        rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (a:LineageAssertion {assertion_id:$id}) "
            "RETURN a.payload AS payload, count { (a)-[:HAS_REVIEW]->() } AS reviews",
            id=derivation.assertion_id,
            database_=evidence_store.database,
        )
        persisted = json.loads(rows[0]["payload"])
        assert persisted["status"] == "asserted"
        assert persisted["review"] is None
        assert rows[0]["reviews"] == 1

        with pytest.raises(LineageCycleError):
            await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=LineageAssertionCreateRequest(
                    relation_type="mathematical_derivation",
                    source=LineageEndpoint(kind="equation", id=eq_b["uuid"], version=1),
                    target=LineageEndpoint(kind="equation", id=eq_a["uuid"], version=1),
                    evidence=(evidence_ref(eq_b),),
                ),
            )

        traversal = await store.traverse_lineage(
            workspace_id=workspace_id,
            endpoint_kind="equation",
            endpoint_id=eq_a["uuid"],
            relation_type="mathematical_derivation",
        )
        assert f"equation:{eq_b['uuid']}:1" in traversal["nodes"]
        assert traversal["truncated"] is False
        assert all(
            edge["relation_type"] == "mathematical_derivation" for edge in traversal["edges"]
        )
        for limits, reason in [({"max_depth": 0}, "max_depth"), ({"max_nodes": 1}, "max_nodes")]:
            limited = await store.traverse_lineage(
                workspace_id=workspace_id,
                endpoint_kind="equation",
                endpoint_id=eq_a["uuid"],
                relation_type="mathematical_derivation",
                **limits,
            )
            assert limited["truncated"] is True
            assert reason in limited["limits_reached"]
            assert len(limited["nodes"]) == 1
            assert limited["edges"] == []
        with pytest.raises(ResearchReferenceNotFoundError):
            await store.traverse_lineage(
                workspace_id="foreign-workspace",
                endpoint_kind="equation",
                endpoint_id=eq_a["uuid"],
            )
        await store.review_lineage_assertion(
            workspace_id=workspace_id,
            assertion_id=derivation.assertion_id,
            reviewer_id="reviewer_1",
            reviewer_role="reviewer",
            decision="rejected",
            notes="The source does not justify this derivation.",
            idempotency_key="reject-derivation-key",
        )
        rejected_view = await store.traverse_lineage(
            workspace_id=workspace_id,
            endpoint_kind="equation",
            endpoint_id=eq_a["uuid"],
            relation_type="mathematical_derivation",
        )
        assert rejected_view["edges"] == []
        assert rejected_view["truncated"] is False
    finally:
        await evidence_store.close()


async def test_compiler_persists_replayable_candidate_and_separate_lowering_activity(monkeypatch):
    workspace_id = f"compile_{uuid4().hex}"
    evidence_store, store, graph_a, graph_b = await evidence_backed_stores(workspace_id)
    eq_a, eq_b = equation(graph_a), equation(graph_b)
    try:
        for node, key in ((eq_a, "feature-a"), (eq_b, "feature-b")):
            await accept_contract(
                evidence_store,
                workspace_id,
                node,
                shape=(64,),
                domain="positive",
                feature_rank=16,
                key=key,
            )

        definition = problem_definition().model_copy(
            update={
                "allowed_transforms": (
                    TransformDeclaration(name="mix_positive_feature_maps", version="1"),
                    TransformDeclaration(name="lower_mixture_to_concatenation", version="1"),
                ),
            }
        )
        spec, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher-1",
            actor_role="researcher",
            idempotency_key="compile-spec",
            definition=definition,
        )
        mapping, _ = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher-1",
            actor_role="researcher",
            idempotency_key="compile-mapping",
            request=PortMappingCreateRequest(
                producer_port=browser_port(eq_a),
                consumer_port=browser_port(eq_b),
            ),
        )
        await store.review_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
            reviewer_id="reviewer-1",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Reviewed both positive-real vector feature-map contracts.",
            idempotency_key="compile-mapping-review",
        )
        mix_request = CompileCandidateRequest(
            workspace_id=workspace_id,
            spec_id=spec.spec_id,
            mapping_id=mapping.mapping_id,
            transform={
                "operator": "mix_positive_feature_maps",
                "operator_version": "1",
                "target_node_id": eq_a["uuid"],
                "parameters": {"lambda": 0.25},
                "bindings": {
                    "left": mapping.producer_port.scoped_symbol_id,
                    "right": mapping.consumer_port.scoped_symbol_id,
                },
            },
        )

        mix = await store.compile_candidate(
            request=mix_request,
            actor_id="compiler-1",
            idempotency_key="compile-mixture-key",
        )
        assert replay_compiled_candidate(mix.candidate, mix.activity) == mix.candidate
        replay = await store.compile_candidate(
            request=mix_request,
            actor_id="compiler-1",
            idempotency_key="compile-mixture-key",
        )
        assert mix.replayed is False and replay.replayed is True
        assert replay.candidate == mix.candidate
        assert replay.activity == mix.activity
        assert mix.candidate.semantics_class == "hypothesis_changing"
        assert mix.candidate.mapping_id == mapping.mapping_id
        assert mix.activity.mapping_id == mapping.mapping_id
        assert mix.candidate.parents[0].entity_id == eq_a["uuid"]
        assert mix.candidate.obligations

        proposal_source_spans = [
            make_source_span_id(node["uuid"], json.loads(node["payload"])["anchor"])
            for node in (eq_a, eq_b)
        ]
        proposal_output = json.dumps(
            {
                "problem_spec_id": spec.spec_id,
                "parent_ids": [eq_a["uuid"], eq_b["uuid"]],
                "source_span_ids": proposal_source_spans,
                "transform": mix_request.transform,
                "assumptions": ["Feature maps remain positive under the reviewed contracts."],
                "rationale": "Reuse the exact compiler transform as a human-reviewed proposal.",
                "expected_effect": "Explore a hypothesis; no performance claim.",
            }
        )
        proposal = await store.record_proposal(
            workspace_id=workspace_id,
            actor_id="proposal-worker",
            output_json=proposal_output,
            idempotency_key="compile-proposal-create",
        )
        proposal_compile_request = mix_request.model_copy(
            update={"proposal_id": proposal.proposal.proposal_id}
        )
        with pytest.raises(ResearchValidationError, match="human review"):
            await store.compile_candidate(
                request=proposal_compile_request,
                actor_id="researcher-1",
                idempotency_key="compile-unreviewed-proposal",
            )
        await store.review_research_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal.proposal.proposal_id,
            reviewer_id="reviewer-1",
            reviewer_role="reviewer",
            request=ProposalReviewCreateRequest(
                decision="accept_for_compilation", notes="Exact transform and source reviewed."
            ),
            idempotency_key="compile-proposal-review",
        )
        proposal_compiled = await store.compile_candidate(
            request=proposal_compile_request,
            actor_id="researcher-1",
            idempotency_key="compile-accepted-proposal",
        )
        assert proposal_compiled.candidate == mix.candidate
        assert proposal_compiled.activity.source_proposal_id == proposal.proposal.proposal_id
        proposal_bundle = await store.export_compiler_replay_bundle(
            workspace_id=workspace_id,
            candidate_id=proposal_compiled.candidate.candidate_id,
            activity_id=proposal_compiled.activity.activity_id,
        )
        assert replay_candidate_from_bundle(proposal_bundle) == proposal_compiled.candidate
        assert proposal_bundle.proposal == proposal.proposal
        assert proposal_bundle.proposal_review is not None
        assert proposal_bundle.proposal_review.proposal_hash == proposal.proposal.content_hash
        changed_proposal_request = proposal_compile_request.model_copy(
            update={"transform": mix_request.transform | {"parameters": {"lambda": 0.75}}}
        )
        with pytest.raises(
            ResearchValidationError,
            match="exact frozen spec and stored transform",
        ):
            await store.compile_candidate(
                request=changed_proposal_request,
                actor_id="researcher-1",
                idempotency_key="compile-altered-proposal",
            )

        admission_request = AdmissionEvaluationRequest(action="can_run_numerical")
        admission = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=mix.candidate.candidate_id,
            actor_id="researcher-1",
            request=admission_request,
            idempotency_key="admission-numerical-key",
        )
        replayed_admission = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=mix.candidate.candidate_id,
            actor_id="researcher-1",
            request=admission_request,
            idempotency_key="admission-numerical-key",
        )
        assert admission.decision.allowed is False
        assert "budget_or_quota_unavailable" in admission.decision.reasons
        assert "verification_vector_missing" in admission.decision.reasons
        assert admission.decision.input_result_ids == ()
        assert replayed_admission.replayed is True
        assert replayed_admission.decision == admission.decision
        with pytest.raises(IdempotencyConflictError):
            await store.evaluate_candidate_admission(
                workspace_id=workspace_id,
                candidate_id=mix.candidate.candidate_id,
                actor_id="researcher-1",
                request=AdmissionEvaluationRequest(action="can_run_experiment"),
                idempotency_key="admission-numerical-key",
            )

        mix_check = await store.verify_candidate(
            workspace_id=workspace_id,
            candidate_id=mix.candidate.candidate_id,
            idempotency_key="candidate-check-mixture",
        )
        mix_check_replay = await store.verify_candidate(
            workspace_id=workspace_id,
            candidate_id=mix.candidate.candidate_id,
            idempotency_key="candidate-check-mixture",
        )
        assert mix_check.check.outcome == "unknown"
        assert mix_check.check.vector.symbolic == "unknown"
        assert mix_check_replay.replayed is True
        assert mix_check_replay.check == mix_check.check

        with pytest.raises(IdempotencyConflictError):
            await store.compile_candidate(
                request=mix_request.model_copy(
                    update={
                        "transform": mix_request.transform | {"parameters": {"lambda": 0.75}},
                    }
                ),
                actor_id="compiler-1",
                idempotency_key="compile-mixture-key",
            )

        parent_before = mix.candidate.model_dump(mode="json")
        lower_request = CompileCandidateRequest(
            workspace_id=workspace_id,
            spec_id=spec.spec_id,
            mapping_id=mapping.mapping_id,
            parent_candidate_id=mix.candidate.candidate_id,
            transform={
                "operator": "lower_mixture_to_concatenation",
                "operator_version": "1",
                "target_node_id": mix.candidate.candidate_id,
                "parameters": {},
                "bindings": {"mixture": mix.candidate.candidate_id},
            },
        )
        lowered = await store.compile_candidate(
            request=lower_request,
            actor_id="compiler-1",
            idempotency_key="compile-lowering-key",
        )
        assert replay_compiled_candidate(
            lowered.candidate,
            lowered.activity,
            parent_candidate=mix.candidate,
        ) == lowered.candidate
        assert lowered.candidate.semantics_class == "preserving"
        assert lowered.activity.activity_id != mix.activity.activity_id
        assert lowered.candidate.parents[0].entity_id == mix.candidate.candidate_id
        assert lowered.candidate.mapping_id == mapping.mapping_id
        assert lowered.activity.mapping_id == mapping.mapping_id
        assert mix.candidate.model_dump(mode="json") == parent_before

        (
            prepared_candidate,
            prepared_spec,
            prepared_parent,
        ) = await store.prepare_candidate_numerical_fixture(
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            seed=spec.definition.seeds[0],
        )
        assert prepared_candidate == lowered.candidate
        assert prepared_spec == spec
        assert prepared_parent == mix.candidate
        with pytest.raises(ResearchValidationError, match="seed"):
            await store.prepare_candidate_numerical_fixture(
                workspace_id=workspace_id,
                candidate_id=lowered.candidate.candidate_id,
                seed=999,
            )

        lowering_check = await store.verify_candidate(
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            idempotency_key="candidate-check-lowering",
        )
        assert lowering_check.check.outcome == "supported"
        assert lowering_check.check.scope == "feature_kernel_identity"
        assert lowering_check.check.vector.domain == "conditional"
        lower_admission = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            actor_id="researcher-1",
            request=admission_request,
            idempotency_key="admission-lowering-key",
        )
        assert lower_admission.decision.allowed is False
        assert lower_admission.decision.input_result_ids == (lowering_check.check.check_id,)
        assert "budget_or_quota_unavailable" in lower_admission.decision.reasons
        with pytest.raises(IdempotencyConflictError):
            await store.verify_candidate(
                workspace_id=workspace_id,
                candidate_id=lowered.candidate.candidate_id,
                idempotency_key="candidate-check-mixture",
            )

        monkeypatch.setattr(
            numerical_verification,
            "run_numerical_sandbox",
            lambda payload, **_kwargs: _evaluate(payload),
        )
        monkeypatch.setattr(
            numerical_verification, "configured_image", lambda: "sha256:" + "a" * 64
        )
        run = run_candidate_numerical_fixture(
            lowered.candidate,
            spec,
            seed=spec.definition.seeds[0],
            parent_candidate=mix.candidate,
        )
        queue_actor = WorkerPrincipal("verifier-1", "verifier", frozenset({workspace_id}))
        queue = ResearchQueue(evidence_store, b"q" * 40)
        fixture_payload = build_numerical_suite_input(
            lowered.candidate,
            spec,
            seed=spec.definition.seeds[0],
            parent_candidate=mix.candidate,
        ) | {"candidate_id": lowered.candidate.candidate_id}
        reserved_ms = min(30_000, max(10, spec.definition.budget.wall_time_ms))
        queue_image = "sha256:" + "a" * 64
        ticket = await queue.admit_numerical_fixture(
            queue_actor,
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            idempotency_key="numerical-fixture-job",
            payload=fixture_payload,
            image=queue_image,
            reserved_ms=reserved_ms,
        )
        assert ticket.envelope.operation == "numerical_fixture:run"
        assert ticket.envelope.reserved_ms == reserved_ms
        await queue.claim(ticket, queue_actor, fixture_payload, queue_image)
        numerical_receipt = make_numerical_fixture_receipt(
            run,
            workspace_id=workspace_id,
            actor_id=queue_actor.identity,
            run_id=ticket.envelope.job_id,
        )
        persisted_numerical = await queue.finish_numerical_fixture(
            ticket,
            research_store=store,
            idempotency_key="numerical-fixture-job",
            result=numerical_receipt,
        )
        assert persisted_numerical.replayed is False
        assert persisted_numerical.result == numerical_receipt
        assert persisted_numerical.result.parent_refs == lowered.candidate.parents
        assert persisted_numerical.result.performance_claim is False
        replayed_numerical = await store.append_candidate_numerical_fixture(
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            actor_id=queue_actor.identity,
            idempotency_key="numerical-fixture-job",
            result=numerical_receipt,
        )
        assert replayed_numerical.replayed is True
        assert replayed_numerical.result == numerical_receipt
        queue_replay = await queue.admit_numerical_fixture(
            queue_actor,
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            idempotency_key="numerical-fixture-job",
            payload=fixture_payload,
            image=queue_image,
            reserved_ms=reserved_ms,
        )
        assert queue_replay.result == numerical_receipt.model_dump_json()
        after_fixture_admission = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            actor_id="researcher-1",
            request=AdmissionEvaluationRequest(action="can_run_experiment"),
            idempotency_key="admission-after-synthetic-fixture",
        )
        assert after_fixture_admission.decision.allowed is False
        assert after_fixture_admission.decision.input_result_ids == (lowering_check.check.check_id,)
        assert numerical_receipt.result_id not in after_fixture_admission.decision.input_result_ids
        with pytest.raises(HTTPException):
            await queue.admit_numerical_fixture(
                WorkerPrincipal("proposer", "proposer", frozenset({workspace_id})),
                workspace_id=workspace_id,
                candidate_id=lowered.candidate.candidate_id,
                idempotency_key="numerical-fixture-wrong-role",
                payload=fixture_payload,
                image=queue_image,
                reserved_ms=reserved_ms,
            )

        with pytest.raises(ResearchValidationError, match="candidate scope"):
            await store.append_candidate_numerical_fixture(
                workspace_id=workspace_id,
                candidate_id=lowered.candidate.candidate_id,
                actor_id="another-verifier",
                idempotency_key="numerical-fixture-wrong-actor",
                result=numerical_receipt,
            )
        with pytest.raises(ResearchReferenceNotFoundError):
            await store.append_candidate_numerical_fixture(
                workspace_id=f"foreign_{uuid4().hex}",
                candidate_id=lowered.candidate.candidate_id,
                actor_id="verifier-1",
                idempotency_key="numerical-fixture-foreign-workspace",
                result=numerical_receipt,
            )
        wrong_candidate_receipt = make_numerical_fixture_receipt(
            run_candidate_numerical_fixture(
                mix.candidate,
                spec,
                seed=spec.definition.seeds[0],
            ),
            workspace_id=workspace_id,
            actor_id="verifier-1",
            run_id=str(uuid4()),
        )
        with pytest.raises(ResearchValidationError, match="candidate scope"):
            await store.append_candidate_numerical_fixture(
                workspace_id=workspace_id,
                candidate_id=lowered.candidate.candidate_id,
                actor_id="verifier-1",
                idempotency_key="numerical-fixture-wrong-candidate",
                result=wrong_candidate_receipt,
            )
        changed_attempt = make_numerical_fixture_receipt(
            run,
            workspace_id=workspace_id,
            actor_id=queue_actor.identity,
            run_id=str(uuid4()),
        )
        with pytest.raises(IdempotencyConflictError):
            await store.append_candidate_numerical_fixture(
                workspace_id=workspace_id,
                candidate_id=lowered.candidate.candidate_id,
                actor_id="verifier-1",
                idempotency_key="numerical-fixture-job",
                result=changed_attempt,
            )

        rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id}) "
            "MATCH (a:TransformationActivity {activity_id:$activity_id}) "
            "RETURN c.payload AS candidate, a.payload AS activity, "
            "count { (a)-[:PRODUCED]->(c) } AS produced",
            candidate_id=lowered.candidate.candidate_id,
            activity_id=lowered.activity.activity_id,
            database_=evidence_store.database,
        )
        assert json.loads(rows[0]["candidate"]) == lowered.candidate.model_dump(mode="json")
        assert json.loads(rows[0]["activity"]) == lowered.activity.model_dump(mode="json")
        assert rows[0]["produced"] == 1
        persisted_activity = TransformationActivity.model_validate_json(rows[0]["activity"])
        assert replay_compiled_candidate(
            lowered.candidate,
            persisted_activity,
            parent_candidate=mix.candidate,
        ) == lowered.candidate
        bundle = await store.export_compiler_replay_bundle(
            workspace_id=workspace_id,
            candidate_id=lowered.candidate.candidate_id,
            activity_id=lowered.activity.activity_id,
        )
        assert replay_candidate_from_bundle(bundle) == lowered.candidate
        assert bundle.parent_candidates == (mix.candidate,)
        assert {item.equation_id for item in bundle.sources} == {eq_a["uuid"], eq_b["uuid"]}
        assert all(item.paper_html_hash for item in bundle.sources)
        report = build_compiler_replay_report(bundle)
        assert report.compiler_replay == "reproduced"
        assert report.semantics_class == "preserving"
        assert report.checks[0].outcome == "supported"
        assert report.checks[0].domain_status == "conditional"
        assert report.policy_decisions[0].outcome == "denied"
        assert report.numerical_fixtures[0].scope == "synthetic_feature_kernel_fixture"
        assert report.numerical_fixtures[0].performance_claim is False
        assert report.empirical_experiment == "not_run"
        assert report.status == "partial"
        numerical_rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id})-"
            "[:HAS_NUMERICAL_FIXTURE_RESULT]->(r:ResearchNumericalFixtureResult) "
            "RETURN r.payload AS payload, count(r) AS count",
            candidate_id=lowered.candidate.candidate_id,
            database_=evidence_store.database,
        )
        assert json.loads(numerical_rows[0]["payload"]) == numerical_receipt.model_dump(mode="json")
        assert bundle.numerical_fixtures == (numerical_receipt,)
        assert bundle.numerical_fixtures[0].performance_claim is False
        assert numerical_rows[0]["count"] == 1
        checks, _, _ = await evidence_store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id})-[:HAS_CANDIDATE_CHECK]"
            "->(r:ResearchCandidateCheck) RETURN r.payload AS payload, count(r) AS total",
            candidate_id=lowered.candidate.candidate_id,
            database_=evidence_store.database,
        )
        assert checks[0]["total"] == 1
        assert json.loads(checks[0]["payload"])["check_id"] == lowering_check.check.check_id
        decisions, _, _ = await evidence_store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id})-[:HAS_ADMISSION_DECISION]"
            "->(d:ResearchAdmissionDecision) RETURN d.payload AS payload, count(d) AS total",
            candidate_id=mix.candidate.candidate_id,
            database_=evidence_store.database,
        )
        assert decisions[0]["total"] == 1
        assert json.loads(decisions[0]["payload"])["allowed"] is False

        history, history_total = await store.list_research_candidates(
            workspace_id=workspace_id,
            limit=1,
            offset=0,
        )
        assert history_total == 2 and len(history) == 1
        latest = history[0]
        latest_id = latest["candidate"]["candidate_id"]
        assert latest_id in {mix.candidate.candidate_id, lowered.candidate.candidate_id}
        is_mixture = latest_id == mix.candidate.candidate_id
        expected_activity = (
            proposal_compiled.activity
            if is_mixture and latest["activity"].get("source_proposal_id")
            else mix.activity
            if is_mixture
            else lowered.activity
        )
        expected_check = mix_check.check if is_mixture else lowering_check.check
        expected_admission = admission.decision if is_mixture else after_fixture_admission.decision
        assert latest["activity"]["activity_id"] == expected_activity.activity_id
        if expected_activity.source_proposal_id is not None:
            assert expected_activity.source_proposal_id == proposal.proposal.proposal_id
        assert latest["check"]["check_id"] == expected_check.check_id
        assert latest["admission"]["decision_id"] == expected_admission.decision_id, {
            "candidate_id": latest_id,
            "is_mixture": is_mixture,
            "actual_decision_id": latest["admission"]["decision_id"],
            "mixture_decision_id": admission.decision.decision_id,
            "lower_initial_decision_id": lower_admission.decision.decision_id,
            "lower_latest_decision_id": after_fixture_admission.decision.decision_id,
        }
        assert "ir_json" not in latest["candidate"]
        assert "request_json" not in latest["activity"]
        assert "witness" not in latest["check"]

        older_page, older_total = await store.list_research_candidates(
            workspace_id=workspace_id,
            limit=1,
            offset=1,
        )
        assert older_total == 2 and len(older_page) == 1
        older = older_page[0]
        expected_older_id = (
            lowered.candidate.candidate_id if is_mixture else mix.candidate.candidate_id
        )
        assert older["candidate"]["candidate_id"] == expected_older_id
        expected_older_check = (
            lowering_check.check
            if expected_older_id == lowered.candidate.candidate_id
            else mix_check.check
        )
        expected_older_admission = (
            after_fixture_admission.decision
            if expected_older_id == lowered.candidate.candidate_id
            else admission.decision
        )
        assert older["check"]["check_id"] == expected_older_check.check_id
        assert older["admission"]["decision_id"] == expected_older_admission.decision_id
        foreign_page, foreign_total = await store.list_research_candidates(
            workspace_id=workspace_id + "_foreign",
        )
        assert foreign_page == [] and foreign_total == 0
    finally:
        await evidence_store.close()


async def test_compatibility_is_server_resolved_reviewed_and_stale_without_overwrite():
    workspace_id = f"compatibility_{uuid4().hex}"
    evidence_store, store, graph_a, graph_b = await evidence_backed_stores(workspace_id)
    eq_a, eq_b = equation(graph_a), equation(graph_b)
    try:
        await accept_contract(evidence_store, workspace_id, eq_a, key="contract-a-v1")
        await accept_contract(evidence_store, workspace_id, eq_b, key="contract-b-v1")

        request = PortMappingCreateRequest(
            producer_port=browser_port(eq_a),
            consumer_port=browser_port(eq_b),
            embedding_similarity=0.99,
        )
        mapping, initial = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            request=request,
            idempotency_key="mapping-create-key",
        )
        assert mapping.producer_port.domain == "real"
        assert mapping.producer_port.shape == ()
        assert mapping.producer_port.scoped_symbol_id != "browser-invented-scope"
        assert initial.status == "unknown"
        assert initial.usable is False

        with pytest.raises(ResearchValidationError):
            await store.create_compatibility_mapping(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=request,
                resolved_dependencies=(
                    ResolvedDependency(
                        entity_id="browser-invented", version=1, content_hash="f" * 64
                    ),
                ),
            )

        with pytest.raises(IdempotencyConflictError):
            await store.create_compatibility_mapping(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=request.model_copy(update={"embedding_similarity": 0.1}),
            )

        review = await store.review_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
            reviewer_id="reviewer_1",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Reviewed all unresolved port requirements against source evidence.",
            idempotency_key="compatibility-review-key",
        )
        replay = await store.review_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
            reviewer_id="reviewer_1",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Reviewed all unresolved port requirements against source evidence.",
            idempotency_key="compatibility-review-key",
        )
        assert review.review_id == replay.review_id

        effective_mapping, approved = await store.get_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
        ) or (None, None)
        assert effective_mapping and approved
        assert effective_mapping.explicit_binding_reviewed is True
        assert approved.status == "compatible"
        assert approved.usable is True
        assert approved.evidence_refs == (review.review_id,)

        rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (m:CompatibilityMapping {mapping_id:$id}) "
            "RETURN m.mapping_payload AS payload, count { (m)-[:HAS_REVIEW]->() } AS reviews",
            id=mapping.mapping_id,
            database_=evidence_store.database,
        )
        assert json.loads(rows[0]["payload"])["explicit_binding_reviewed"] is False
        assert rows[0]["reviews"] == 1

        await accept_contract(
            evidence_store,
            workspace_id,
            eq_b,
            shape=(2,),
            key="contract-b-v2",
            reviewed_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        _, stale = await store.get_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
        ) or (None, None)
        assert stale
        assert stale.freshness == "stale"
        assert stale.status == "compatible"  # The historical contract pair is unchanged.
        assert stale.usable is False
        assert "dimension_mismatch" not in stale.reasons
        assert "stale_dependency" in stale.reasons
        replay_mapping, replay_assessment = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            request=request,
            idempotency_key="mapping-create-key",
        )
        assert replay_mapping == mapping
        assert replay_assessment == initial
        with pytest.raises(IdempotencyConflictError):
            await store.create_compatibility_mapping(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=request.model_copy(update={"embedding_similarity": 0.1}),
                idempotency_key="mapping-create-key",
            )
        with pytest.raises(ResearchValidationError, match="stale"):
            await store.review_compatibility_mapping(
                workspace_id=workspace_id,
                mapping_id=mapping.mapping_id,
                reviewer_id="reviewer_1",
                reviewer_role="reviewer",
                decision="reviewed",
                notes="Cannot approve outdated contracts",
                idempotency_key="stale-review-key",
            )
        replacement, reassessed = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher_1",
            actor_role="researcher",
            request=request,
        )
        assert replacement.mapping_id != mapping.mapping_id
        assert reassessed.status == "incompatible"
        assert "dimension_mismatch" in reassessed.reasons
        items, total = await store.list_compatibility_mappings(workspace_id=workspace_id)
        assert total == len(items) == 2
    finally:
        await evidence_store.close()


async def test_lineage_long_cycle_and_concurrent_reverse_edges_are_rejected():
    workspace_id = f"cycle_{uuid4().hex}"
    evidence_store, store, graph_a, graph_b = await evidence_backed_stores(workspace_id)
    try:
        nodes = [equation(graph_a), equation(graph_b)]
        for index in range(8):
            graph = paper_graph(workspace_id, f"2402.{90000 + index}", f"x={index + 3}")
            await evidence_store.ingest(graph)
            nodes.append(equation(graph))

        async def link(source, target):
            return await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher_1",
                actor_role="researcher",
                request=LineageAssertionCreateRequest(
                    relation_type="mathematical_derivation",
                    source=LineageEndpoint(kind="equation", id=source["uuid"], version=1),
                    target=LineageEndpoint(kind="equation", id=target["uuid"], version=1),
                    evidence=(evidence_ref(source),),
                ),
            )

        outcomes = await asyncio.gather(
            link(nodes[0], nodes[1]),
            link(nodes[1], nodes[0]),
            return_exceptions=True,
        )
        assert sum(isinstance(result, LineageCycleError) for result in outcomes) == 1
        assert sum(not isinstance(result, Exception) for result in outcomes) == 1
        if isinstance(outcomes[0], Exception):
            nodes[0], nodes[1] = nodes[1], nodes[0]
        # Both idempotent callers retain the original timestamp and identity.
        duplicates = await asyncio.gather(link(nodes[0], nodes[1]), link(nodes[0], nodes[1]))
        assert duplicates[0] == duplicates[1]
        for source, target in zip(nodes[1:-1], nodes[2:], strict=True):
            await link(source, target)
        with pytest.raises(LineageCycleError):
            await link(nodes[-1], nodes[0])
        view = await store.traverse_lineage(
            workspace_id=workspace_id,
            endpoint_kind="equation",
            endpoint_id=nodes[0]["uuid"],
        )
        assert view["truncated"] is True
        assert "max_depth" in view["limits_reached"]
        assert len(view["nodes"]) == 9
    finally:
        await evidence_store.close()


async def test_coverage_counts_section_equations_and_preserves_imported_html():
    workspace_id = f"coverage_{uuid4().hex}"
    evidence_store, store, _, _ = await evidence_backed_stores(workspace_id)
    try:
        for paper_id in ("2402.08954", "2402.99999"):
            await store.register_paper_coverage(
                workspace_id=workspace_id,
                paper=PaperCoverageRecord(
                    paper_id=paper_id,
                    version=1,
                    title="Metadata-only observation",
                    has_html=False,
                    ingested_at=datetime.now(UTC).isoformat(),
                    warnings=("html_missing",),
                ),
            )
        coverage = await store.get_lineage_coverage(workspace_id=workspace_id)
        papers = {paper.paper_id: paper for paper in coverage.indexed_papers}
        assert papers["2402.08954"].has_html is True
        assert papers["2402.08954"].equation_count == 1
        assert papers["2402.08955"].equation_count == 1
        assert coverage.coverage_gaps == ("2402.99999",)
        assert papers["2402.08954"].first_publication_at is None
        assert papers["2402.08954"].version_published_at == "2024-02-01"
        foreign = await store.get_lineage_coverage(workspace_id="foreign-workspace")
        assert foreign.indexed_papers == ()
    finally:
        await evidence_store.close()


async def test_metadata_registration_is_durable_scoped_and_idempotent():
    workspace_id = f"metadata_{uuid4().hex}"
    evidence_store, store, _, _ = await evidence_backed_stores(workspace_id)
    request = MetadataPaperCreateRequest(
        paper_id="paper-c",
        version=1,
        title="Missing HTML",
        source_reference="Paper A bibliography",
    )
    try:

        async def register(value=request):
            return await store.register_metadata_paper(
                workspace_id=workspace_id,
                actor_id="human-1",
                idempotency_key="metadata-1",
                request=value,
            )

        first, replay = await asyncio.gather(register(), register())
        assert first == replay
        assert first.has_html is False
        assert first.equation_count == 0
        assert first.registered_by == "human-1"
        with pytest.raises(IdempotencyConflictError):
            await register(request.model_copy(update={"title": "Changed title"}))
        coverage = await store.get_lineage_coverage(workspace_id=workspace_id)
        assert coverage.coverage_gaps == ("paper-c",)
        assert (await store.get_lineage_coverage(workspace_id="foreign")).indexed_papers == ()
        first_page = await store.list_research_sources(workspace_id=workspace_id, limit=1)
        second_page = await store.list_research_sources(
            workspace_id=workspace_id,
            limit=1,
            offset=1,
        )
        paper_page = await store.list_research_sources(
            workspace_id=workspace_id,
            kind="paper",
            limit=100,
        )
        assert len(paper_page["items"]) == 2
        assert {item["paper_id"] for item in paper_page["items"]} == {
            "2402.08954",
            "2402.08955",
        }
        assert all(item["kind"] == "paper" for item in paper_page["items"])
        assert first_page["has_more"] is True
        assert second_page["has_more"] is False
        source, target = first_page["items"][0], second_page["items"][0]
        assert source["id"] != target["id"]
        assert source["source_span_id"] == make_source_span_id(source["id"], source["anchor"])
        assert parse_source_span_id(source["source_span_id"]) == (source["id"], source["anchor"])
        assert source["symbols"] == target["symbols"] == ["x"]
        assert len(source["symbol_refs"]) == 1
        assert source["symbol_refs"][0]["id"]
        assert source["symbol_refs"][0]["notation"] == "x"
        resolved = await store.list_research_sources(
            workspace_id=workspace_id,
            source_id=source["id"],
        )
        assert resolved["items"] == [source]
        assert (
            await store.list_research_sources(
                workspace_id="foreign",
                source_id=source["id"],
            )
        )["items"] == []
        mapping, assessment = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="human-1",
            actor_role="researcher",
            request=PortMappingCreateRequest.model_validate(
                {
                    "producer_port": {
                        "equation_id": source["id"],
                        "version": 1,
                        "symbol_name": "x",
                    },
                    "consumer_port": {
                        "equation_id": target["id"],
                        "version": 1,
                        "symbol_name": "x",
                    },
                }
            ),
        )
        assert mapping.producer_port.equation_id == source["id"]
        assert assessment.status == "unknown"
        assert (await store.list_research_sources(workspace_id="foreign"))["items"] == []
    finally:
        await evidence_store.close()


async def test_phase_5a_html_to_revision_reloads_without_mutating_sources():
    workspace_id = os.getenv("TEST_PHASE5A_WORKSPACE_ID") or f"phase5a_{uuid4().hex}"
    evidence_store = Neo4jEvidenceStore.connect(*connection())
    store = Neo4jResearchStore(evidence_store.driver, database=evidence_store.database)
    graph_a = paper_graph(workspace_id, "2402.08954", "x=1")
    graph_b = paper_graph(
        workspace_id,
        "2402.08955",
        "x=2",
        reference_to="2402.08954",
    )
    eq_a, eq_b = equation(graph_a), equation(graph_b)
    raw_a, raw_b = eq_a["payload"], eq_b["payload"]
    try:
        await evidence_store.initialize()
        await store.initialize()
        await evidence_store.ingest(graph_a)
        await evidence_store.ingest(graph_b)
        reference_section = next(
            node
            for node in graph_b.nodes
            if node["kind"] == "Section" and json.loads(node["payload"])["anchor"] == "Refs"
        )
        assert "2402.08954" in json.loads(reference_section["payload"])["text"]
        snapshot_v1, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="spec-v1",
            definition=problem_definition(),
        )
        await store.register_metadata_paper(
            workspace_id=workspace_id,
            actor_id="researcher",
            idempotency_key="paper-c",
            request=MetadataPaperCreateRequest(
                paper_id="2402.99999",
                title="Metadata only",
                version=1,
                source_reference="Paper B references",
            ),
        )
        citation = await store.create_lineage_assertion(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="citation-a-b",
            request=LineageAssertionCreateRequest(
                relation_type="citation",
                source=LineageEndpoint(kind="paper", id=graph_a.paper_version_uuid, version=1),
                target=LineageEndpoint(kind="paper", id=graph_b.paper_version_uuid, version=1),
                evidence=(evidence_ref(reference_section),),
                description="Paper B cites A in its References section.",
            ),
        )
        derivation = await store.create_lineage_assertion(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="derivation-a-b",
            request=LineageAssertionCreateRequest(
                relation_type="mathematical_derivation",
                source=LineageEndpoint(kind="equation", id=eq_a["uuid"], version=1),
                target=LineageEndpoint(kind="equation", id=eq_b["uuid"], version=1),
                evidence=(evidence_ref(eq_b),),
            ),
        )
        mapping_request = PortMappingCreateRequest(
            producer_port=browser_port(eq_a),
            consumer_port=browser_port(eq_b),
        )
        unknown_mapping, unknown = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="unknown-mapping",
            request=mapping_request,
        )
        assert unknown.status == "unknown" and not unknown.usable
        await accept_contract(evidence_store, workspace_id, eq_a, key="accept-a")
        await accept_contract(evidence_store, workspace_id, eq_b, key="accept-b")
        reviewed_mapping, pending = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="reviewed-mapping",
            request=mapping_request,
        )
        assert pending.status == "unknown"
        await store.review_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=reviewed_mapping.mapping_id,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Checked only the x symbol binding on both versioned equations.",
            idempotency_key="binding-review",
        )
        _, compatible = await store.get_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=reviewed_mapping.mapping_id,
        ) or (None, None)
        assert compatible and compatible.status == "compatible" and compatible.usable
        await accept_contract(
            evidence_store,
            workspace_id,
            eq_b,
            shape=(2,),
            key="change-b",
            reviewed_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        _, stale = await store.get_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=reviewed_mapping.mapping_id,
        ) or (None, None)
        assert stale and stale.freshness == "stale" and not stale.usable
        incompatible_mapping, incompatible = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="changed-mapping",
            request=mapping_request,
        )
        assert incompatible.status == "incompatible" and not incompatible.usable
        snapshot_v2, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="spec-v2",
            definition=problem_definition("Revised attention objective"),
            parent_spec_id=snapshot_v1.spec_id,
        )
        reloaded = Neo4jResearchStore(evidence_store.driver, database=evidence_store.database)
        assert (
            await reloaded.get_problem_spec(
                workspace_id=workspace_id,
                spec_id=snapshot_v1.spec_id,
            )
            == snapshot_v1
        )
        assert (
            await reloaded.get_problem_spec(
                workspace_id=workspace_id,
                spec_id=snapshot_v2.spec_id,
            )
        ).parent_spec_id == snapshot_v1.spec_id
        assert (
            await reloaded.get_lineage_assertion(
                workspace_id=workspace_id,
                assertion_id=citation.assertion_id,
            )
        ).evidence == (evidence_ref(reference_section),)
        assert (
            await reloaded.get_lineage_assertion(
                workspace_id=workspace_id,
                assertion_id=derivation.assertion_id,
            )
        ).relation_type == "mathematical_derivation"
        coverage = await reloaded.get_lineage_coverage(workspace_id=workspace_id)
        assert len(coverage.indexed_papers) == 3
        assert coverage.coverage_gaps == ("2402.99999",)
        items, total = await reloaded.list_compatibility_mappings(workspace_id=workspace_id)
        assert total == len(items) == 3
        expected_mapping_ids = {
            unknown_mapping.mapping_id,
            reviewed_mapping.mapping_id,
            incompatible_mapping.mapping_id,
        }
        assert expected_mapping_ids == {item["mapping"]["mapping_id"] for item in items}
        rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (e:Evidence {group_id: $group, kind: 'Equation'}) "
            "RETURN e.uuid AS id, e.payload AS payload",
            group=graph_a.group_id,
            database_=evidence_store.database,
        )
        assert {row["id"]: row["payload"] for row in rows}[eq_a["uuid"]] == raw_a
        assert {row["id"]: row["payload"] for row in rows}[eq_b["uuid"]] == raw_b
    finally:
        await evidence_store.close()


async def test_r1_attention_lineage_and_synthetic_pipeline_replay(monkeypatch):
    workspace_id = f"r1_attention_{uuid4().hex}"
    evidence_store = Neo4jEvidenceStore.connect(*connection())
    store = Neo4jResearchStore(evidence_store.driver, database=evidence_store.database)
    graphs = {
        paper_id: attention_corpus_graph(workspace_id, paper_id)
        for paper_id in ("1706.03762", "2006.16236", "2009.14794", "2205.14135")
    }
    await evidence_store.initialize()
    await store.initialize()
    try:
        for graph in graphs.values():
            await evidence_store.ingest(graph)

        transformer = graphs["1706.03762"]
        linear = graphs["2006.16236"]
        performer = graphs["2009.14794"]
        flash = graphs["2205.14135"]
        assert (
            transformer.version,
            linear.version,
            performer.version,
            flash.version,
        ) == (7, 3, 4, 2)
        transformer_eq = source_node(transformer, "Equation", "S3.E1")
        linear_eq4 = source_node(linear, "Equation", "S3.E4")
        linear_eq5 = source_node(linear, "Equation", "S3.E5")
        performer_eq1 = source_node(performer, "Equation", "S2.E1")
        performer_eq4 = source_node(performer, "Equation", "S2.E4")
        linear_raw = json.loads(linear_eq4["payload"])
        linear_analysis = next(
            item for item in linear.analyses if item["equation_uuid"] == linear_eq4["uuid"]
        )
        assert linear_raw["latex"].endswith(".")
        assert json.loads(linear_analysis["payload"])["status"] != "unsupported"
        assert {"Q", "K"}.issubset(
            {item["name"] for item in json.loads(linear_analysis["payload"])["symbols"]}
        )
        linear_symbol_names = {
            item["name"] for item in json.loads(linear_analysis["payload"])["symbols"]
        }
        assert "V'" in linear_symbol_names
        assert linear_symbol_names.isdisjoint({"prime", "displaystyle"})
        performer_eq1_analysis = next(
            item for item in performer.analyses
            if item["equation_uuid"] == performer_eq1["uuid"]
        )
        performer_eq1_payload = json.loads(performer_eq1_analysis["payload"])
        assert performer_eq1_payload["status"] != "unsupported"
        assert performer_eq1_payload["ast"]["kind"] == "sequence"
        performer_eq4_analysis = next(
            item for item in performer.analyses
            if item["equation_uuid"] == performer_eq4["uuid"]
        )
        performer_eq4_payload = json.loads(performer_eq4_analysis["payload"])
        assert performer_eq4_payload["status"] == "unsupported"
        assert performer_eq4_payload["error"]["code"] == "UNSUPPORTED_ACCENT"

        claims = (
            (
                "citation",
                LineageEndpoint(kind="paper", id=transformer.paper_version_uuid,
                                version=transformer.version),
                LineageEndpoint(kind="paper", id=linear.paper_version_uuid,
                                version=linear.version),
                source_node(linear, "Section", "S1"),
                "Linear Transformers cites the Transformer source in its introduction.",
            ),
            (
                "citation",
                LineageEndpoint(kind="paper", id=transformer.paper_version_uuid,
                                version=transformer.version),
                LineageEndpoint(kind="paper", id=performer.paper_version_uuid,
                                version=performer.version),
                source_node(performer, "Section", "S2"),
                "Performer identifies the cited Transformer attention baseline.",
            ),
            (
                "mathematical_derivation",
                LineageEndpoint(kind="equation", id=linear_eq4["uuid"],
                                version=linear.version),
                LineageEndpoint(kind="equation", id=linear_eq5["uuid"],
                                version=linear.version),
                source_node(linear, "Section", "S3"),
                "Equation (5) reorders the kernel-feature sums using associativity.",
            ),
            (
                "approximation",
                LineageEndpoint(kind="equation", id=performer_eq1["uuid"],
                                version=performer.version),
                LineageEndpoint(kind="equation", id=performer_eq4["uuid"],
                                version=performer.version),
                source_node(performer, "Section", "S2"),
                "FAVOR+ expresses an approximate attention calculation with positive features.",
            ),
            (
                "implementation",
                LineageEndpoint(kind="equation", id=transformer_eq["uuid"],
                                version=transformer.version),
                LineageEndpoint(kind="paper", id=flash.paper_version_uuid,
                                version=flash.version),
                source_node(flash, "Section", "S1"),
                "FlashAttention describes an IO-aware implementation of exact attention.",
            ),
        )
        created = []
        for index, (relation, source, target, evidence_node, description) in enumerate(claims):
            record = await store.create_lineage_assertion(
                workspace_id=workspace_id,
                actor_id="researcher",
                actor_role="researcher",
                idempotency_key=f"attention-lineage-{index}",
                request=LineageAssertionCreateRequest(
                    relation_type=relation,
                    source=source,
                    target=target,
                    evidence=(evidence_ref(evidence_node),),
                    description=description,
                ),
            )
            created.append(record)

        reloaded, total = await store.list_lineage_assertions(
            workspace_id=workspace_id, limit=10,
        )
        assert total == len(reloaded) == 5
        assert {record.relation_type for record in reloaded} == {
            "citation", "mathematical_derivation", "approximation", "implementation",
        }
        assert all(record.status == "asserted" for record in reloaded)
        assert {record.assertion_id for record in created} == {
            record.assertion_id for record in reloaded
        }
        assert all(len(record.evidence) == 1 for record in reloaded)

        synthetic_left = paper_graph(workspace_id, "2402.09991", "x")
        synthetic_right = paper_graph(workspace_id, "2402.09992", "y")
        await evidence_store.ingest(synthetic_left)
        await evidence_store.ingest(synthetic_right)
        feature_sources = (equation(synthetic_left), equation(synthetic_right))
        # These isolated, symbol-only HTML fixtures deliberately use reviewer-supplied
        # positive-vector assumptions; they are compiler plumbing, not paper evidence.
        contract_keys = ("synthetic-left-contract", "synthetic-right-contract")
        for node, symbol_name, key in zip(
            feature_sources, ("x", "y"), contract_keys, strict=True,
        ):
            await accept_contract(
                evidence_store,
                workspace_id,
                node,
                shape=(64,),
                domain="positive",
                feature_rank=16,
                symbol_name=symbol_name,
                key=key,
            )
        spec_definition = problem_definition(
            "Synthetic feature-map compiler pilot; no empirical attention claim"
        ).model_copy(
            update={
                "allowed_transforms": (
                    TransformDeclaration(name="mix_positive_feature_maps", version="1"),
                    TransformDeclaration(name="lower_mixture_to_concatenation", version="1"),
                ),
            }
        )
        spec, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="r1-attention-spec",
            definition=spec_definition,
        )
        mapping, _ = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="r1-attention-mapping",
            request=PortMappingCreateRequest(
                producer_port=browser_port(feature_sources[0]),
                consumer_port=browser_port(feature_sources[1], symbol_name="y"),
            ),
        )
        await store.review_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            decision="reviewed",
            notes=(
                "Reviewed both synthetic positive-vector fixture contracts for this test scope."
            ),
            idempotency_key="r1-attention-mapping-review",
        )
        _, mapping_assessment = await store.get_compatibility_mapping(
            workspace_id=workspace_id,
            mapping_id=mapping.mapping_id,
        ) or (None, None)
        assert mapping_assessment and mapping_assessment.status == "compatible"
        assert mapping_assessment.usable

        mix_transform = {
            "operator": "mix_positive_feature_maps",
            "operator_version": "1",
            "target_node_id": feature_sources[0]["uuid"],
            "parameters": {"lambda": 0.25},
            "bindings": {
                "left": mapping.producer_port.scoped_symbol_id,
                "right": mapping.consumer_port.scoped_symbol_id,
            },
        }
        source_span_ids = [
            make_source_span_id(node["uuid"], json.loads(node["payload"])["anchor"])
            for node in feature_sources
        ]
        proposal_output = json.dumps(
            {
                "problem_spec_id": spec.spec_id,
                "parent_ids": [node["uuid"] for node in feature_sources],
                "source_span_ids": source_span_ids,
                "transform": mix_transform,
                "assumptions": [
                    "The accepted source contracts are within this test's reviewed scope."
                ],
                "rationale": "Exercise a bounded, hypothesis-changing feature-map mixture.",
                "expected_effect": (
                    "Produce a testable hypothesis; no quality or performance claim."
                ),
            }
        )
        rejected_draft = json.loads(proposal_output)
        rejected_draft["rationale"] = "Reject this separate synthetic branch before compilation."
        rejected = await store.record_proposal(
            workspace_id=workspace_id,
            actor_id="proposal-worker",
            output_json=json.dumps(rejected_draft),
            idempotency_key="r1-attention-rejected-proposal",
        )
        rejected_review = await store.review_research_proposal(
            workspace_id=workspace_id,
            proposal_id=rejected.proposal.proposal_id,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            request=ProposalReviewCreateRequest(
                decision="reject",
                notes="Do not compile this separately rejected test branch.",
            ),
            idempotency_key="r1-attention-rejected-review",
        )
        assert rejected_review.review.decision == "reject"
        with pytest.raises(ResearchValidationError, match="Rejected proposals"):
            await store.compile_candidate(
                request=CompileCandidateRequest(
                    workspace_id=workspace_id,
                    spec_id=spec.spec_id,
                    mapping_id=mapping.mapping_id,
                    proposal_id=rejected.proposal.proposal_id,
                    transform=mix_transform,
                ),
                actor_id="researcher",
                idempotency_key="r1-attention-rejected-compile",
            )

        proposal = await store.record_proposal(
            workspace_id=workspace_id,
            actor_id="proposal-worker",
            output_json=proposal_output,
            idempotency_key="r1-attention-proposal",
        )
        await store.review_research_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal.proposal.proposal_id,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            request=ProposalReviewCreateRequest(
                decision="accept_for_compilation",
                notes="Reviewed the exact source spans and stored transform for this test.",
            ),
            idempotency_key="r1-attention-proposal-review",
        )
        mixture = await store.compile_candidate(
            request=CompileCandidateRequest(
                workspace_id=workspace_id,
                spec_id=spec.spec_id,
                mapping_id=mapping.mapping_id,
                proposal_id=proposal.proposal.proposal_id,
                transform=mix_transform,
            ),
            actor_id="researcher",
            idempotency_key="r1-attention-compile-mixture",
        )
        assert mixture.candidate.semantics_class == "hypothesis_changing"
        assert mixture.activity.source_proposal_id == proposal.proposal.proposal_id
        mix_check = await store.verify_candidate(
            workspace_id=workspace_id,
            candidate_id=mixture.candidate.candidate_id,
            idempotency_key="r1-attention-check-mixture",
        )
        assert mix_check.check.outcome == "unknown"
        mix_admission = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=mixture.candidate.candidate_id,
            actor_id="researcher",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
            idempotency_key="r1-attention-admit-mixture",
        )
        assert not mix_admission.decision.allowed

        lowering = await store.compile_candidate(
            request=CompileCandidateRequest(
                workspace_id=workspace_id,
                spec_id=spec.spec_id,
                mapping_id=mapping.mapping_id,
                parent_candidate_id=mixture.candidate.candidate_id,
                transform={
                    "operator": "lower_mixture_to_concatenation",
                    "operator_version": "1",
                    "target_node_id": mixture.candidate.candidate_id,
                    "parameters": {},
                    "bindings": {"mixture": mixture.candidate.candidate_id},
                },
            ),
            actor_id="compiler",
            idempotency_key="r1-attention-compile-lowering",
        )
        lower_check = await store.verify_candidate(
            workspace_id=workspace_id,
            candidate_id=lowering.candidate.candidate_id,
            idempotency_key="r1-attention-check-lowering",
        )
        assert lower_check.check.outcome == "supported"
        assert lower_check.check.vector.domain == "conditional"

        monkeypatch.setattr(
            numerical_verification,
            "run_numerical_sandbox",
            lambda payload, **_kwargs: _evaluate(payload),
        )
        monkeypatch.setattr(
            numerical_verification,
            "configured_image",
            lambda: "sha256:" + "b" * 64,
        )
        fixture_run = run_candidate_numerical_fixture(
            lowering.candidate,
            spec,
            seed=spec.definition.seeds[0],
            parent_candidate=mixture.candidate,
        )
        fixture = make_numerical_fixture_receipt(
            fixture_run,
            workspace_id=workspace_id,
            actor_id="verifier",
            run_id=str(uuid4()),
        )
        assert fixture.performance_claim is False
        persisted_fixture = await store.append_candidate_numerical_fixture(
            workspace_id=workspace_id,
            candidate_id=lowering.candidate.candidate_id,
            actor_id="verifier",
            idempotency_key="r1-attention-synthetic-fixture",
            result=fixture,
        )
        assert persisted_fixture.result == fixture
        lower_admission = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=lowering.candidate.candidate_id,
            actor_id="researcher",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
            idempotency_key="r1-attention-admit-lowering",
        )
        assert not lower_admission.decision.allowed

        mixture_bundle = await store.export_compiler_replay_bundle(
            workspace_id=workspace_id,
            candidate_id=mixture.candidate.candidate_id,
            activity_id=mixture.activity.activity_id,
        )
        lowering_bundle = await store.export_compiler_replay_bundle(
            workspace_id=workspace_id,
            candidate_id=lowering.candidate.candidate_id,
            activity_id=lowering.activity.activity_id,
        )
        assert replay_candidate_from_bundle(mixture_bundle) == mixture.candidate
        assert replay_candidate_from_bundle(lowering_bundle) == lowering.candidate
        assert lowering_bundle.parent_candidates == (mixture.candidate,)
        assert mixture_bundle.lineage_assertions == ()
        assert proposal.proposal.proposal_id == mixture_bundle.proposal.proposal_id
        assert mixture_bundle.proposal_review is not None
        assert mixture_bundle.proposal_review.decision == "accept_for_compilation"

        mixture_report = build_compiler_replay_report(mixture_bundle)
        lowering_report = build_compiler_replay_report(lowering_bundle)
        assert mixture_report.checks[0].outcome == "unknown"
        assert any(item.outcome == "denied" for item in mixture_report.policy_decisions)
        assert all(
            item.replay_status == "replayed"
            for item in mixture_report.policy_decisions
        )
        assert lowering_report.checks[0].outcome == "supported"
        assert lowering_report.checks[0].domain_status == "conditional"
        assert any(item.outcome == "denied" for item in lowering_report.policy_decisions)
        assert all(
            item.replay_status == "replayed"
            for item in lowering_report.policy_decisions
        )
        assert lowering_report.numerical_fixtures[0].performance_claim is False
        assert lowering_report.empirical_experiment == "not_run"
        assert lowering_report.status == "partial"
        assert {item.paper_id for item in lowering_report.source_refs} == {
            "2402.09991", "2402.09992",
        }
        assert lowering_report.lineage_assertion_ids == ()
        persisted_linear_rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (e:Evidence {uuid:$id, group_id:$group, kind:'Equation'}) "
            "RETURN e.payload AS payload",
            id=linear_eq4["uuid"],
            group=linear.group_id,
            database_=evidence_store.database,
        )
        assert json.loads(persisted_linear_rows[0]["payload"]) == linear_raw
    finally:
        await evidence_store.close()


async def test_proposal_append_resolves_sources_bindings_and_replays_atomically():
    workspace_id = f"proposal_{uuid4().hex}"
    evidence_store = Neo4jEvidenceStore.connect(*connection())
    store = Neo4jResearchStore(evidence_store.driver, database=evidence_store.database)
    graphs = [
        paper_graph(workspace_id, "2402.08961", "a=1"),
        paper_graph(workspace_id, "2402.08962", "b=2"),
        paper_graph(workspace_id, "2402.08963", "c=3"),
    ]
    await evidence_store.initialize()
    await store.initialize()
    try:
        reservation_args = {
            "workspace_id": workspace_id,
            "actor_id": "researcher-1",
            "idempotency_key": "proposal-reservation-replay",
            "intent_hash": "intent-v1",
            "reserve_usd": Decimal("0.01"),
            "daily_limit_usd": Decimal("0.025"),
        }
        concurrent_reservations = await asyncio.gather(
            store.reserve_proposal_generation(**reservation_args),
            store.reserve_proposal_generation(**reservation_args),
            return_exceptions=True,
        )
        assert (
            sum(item == {"status": "reserved", "payload": None} for item in concurrent_reservations)
            == 1
        )
        assert (
            sum(
                isinstance(item, ProposalGenerationInProgressError)
                for item in concurrent_reservations
            )
            == 1
        )
        with pytest.raises(IdempotencyConflictError):
            await store.reserve_proposal_generation(
                **{**reservation_args, "intent_hash": "different-intent"}
            )

        generated_json = '{"draft":"validated"}'
        await store.save_generated_proposal_draft(
            workspace_id=workspace_id,
            actor_id="researcher-1",
            idempotency_key="proposal-reservation-replay",
            intent_hash="intent-v1",
            output_json=generated_json,
        )
        generated = await store.reserve_proposal_generation(**reservation_args)
        assert generated == {"status": "generated", "payload": generated_json}
        completed_json = '{"proposal":"persisted"}'
        await store.complete_proposal_generation(
            workspace_id=workspace_id,
            actor_id="researcher-1",
            idempotency_key="proposal-reservation-replay",
            intent_hash="intent-v1",
            response_json=completed_json,
        )
        replayed = await store.reserve_proposal_generation(**reservation_args)
        assert replayed == {"status": "completed", "payload": completed_json}

        await store.reserve_proposal_generation(
            **{
                **reservation_args,
                "idempotency_key": "proposal-reservation-second",
                "reserve_usd": Decimal("0.015"),
            }
        )
        with pytest.raises(ProposalGenerationBudgetExceededError):
            await store.reserve_proposal_generation(
                **{
                    **reservation_args,
                    "idempotency_key": "proposal-reservation-over-budget",
                    "reserve_usd": Decimal("0.0001"),
                }
            )

        async with evidence_store.driver.session(database=evidence_store.database) as session:
            await session.run(
                "MATCH (q:ResearchProposalQuota {group_id:$group}) SET q.day=$previous_day",
                group=workspace_group_id(workspace_id),
                previous_day=(datetime.now(UTC).date() - timedelta(days=1)).isoformat(),
            )
        rollover = await store.reserve_proposal_generation(
            **{
                **reservation_args,
                "idempotency_key": "proposal-reservation-after-rollover",
            }
        )
        assert rollover == {"status": "reserved", "payload": None}

        for graph in graphs:
            await evidence_store.ingest(graph)
        equations = [equation(graph) for graph in graphs]
        symbols = [
            next(node for node in graph.nodes if node["kind"] == "Symbol") for graph in graphs
        ]
        source_spans = [
            make_source_span_id(node["uuid"], json.loads(node["payload"])["anchor"])
            for node in equations
        ]
        async with evidence_store.driver.session(database=evidence_store.database) as session:
            for symbol, source in zip(symbols, equations, strict=True):
                owner_row = await (
                    await session.run(
                        "MATCH (e:Evidence {group_id:$group,kind:'Equation'})-"
                        "[:EVIDENCE_RELATION {group_id:$group}]->"
                        "(s:Evidence {uuid:$symbol_id,group_id:$group,kind:'Symbol'}) "
                        "RETURN collect(DISTINCT e.uuid) AS ids",
                        group=workspace_group_id(workspace_id),
                        symbol_id=symbol["uuid"],
                    )
                ).single(strict=True)
                assert source["uuid"] in owner_row["ids"]
            span_rows = await session.run(
                "UNWIND $ids AS span_id "
                "MATCH (e:Evidence {uuid:split(span_id,'#')[0],group_id:$group}) "
                "RETURN e.uuid AS id,e.kind AS kind,e.payload AS payload",
                ids=[parse_source_span_id(item)[0] for item in source_spans],
                group=workspace_group_id(workspace_id),
            )
            resolved_source_rows = [row async for row in span_rows]
            assert len(resolved_source_rows) == 3
            assert all(row["kind"] == "Equation" for row in resolved_source_rows)
        definition = problem_definition().model_copy(
            update={
                "allowed_transforms": (
                    TransformDeclaration(name="mix_positive_feature_maps", version="1"),
                )
            }
        )
        spec, _ = await store.freeze_problem_spec(
            workspace_id=workspace_id,
            actor_id="researcher",
            actor_role="researcher",
            idempotency_key="proposal-spec-freeze",
            definition=definition,
        )
        draft = {
            "problem_spec_id": spec.spec_id,
            "parent_ids": [node["uuid"] for node in equations],
            "source_span_ids": source_spans,
            "transform": {
                "operator": "mix_positive_feature_maps",
                "operator_version": "1",
                "target_node_id": equations[0]["uuid"],
                "parameters": {"lambda": 0.25},
                "bindings": {
                    "left": symbols[0]["uuid"],
                    "right": symbols[1]["uuid"],
                },
            },
            "assumptions": ["Feature maps are positive on the reviewed domain."],
            "rationale": "Explore a bounded convex mixture.",
            "expected_effect": "Hypothesis only; no empirical result is asserted.",
        }
        output_json = json.dumps(draft, separators=(",", ":"))
        first, replay = await asyncio.gather(
            store.record_proposal(
                workspace_id=workspace_id,
                actor_id="proposer-worker",
                output_json=output_json,
                idempotency_key="proposal-same-retry-key",
            ),
            store.record_proposal(
                workspace_id=workspace_id,
                actor_id="proposer-worker",
                output_json=output_json,
                idempotency_key="proposal-same-retry-key",
            ),
        )
        assert first.proposal == replay.proposal
        assert {first.replayed, replay.replayed} == {False, True}
        assert first.proposal.review_state == "pending"
        assert all(item.discharged is False for item in first.proposal.assumptions)
        history, total = await store.list_research_proposals(
            workspace_id=workspace_id,
            limit=1,
            offset=0,
        )
        assert total == 1
        assert history[0]["proposal"] == first.proposal.model_dump(mode="json")
        assert datetime.fromisoformat(history[0]["created_at"]).utcoffset() == UTC.utcoffset(
            datetime.now(UTC)
        )

        with pytest.raises(ResearchAuthorizationError):
            await store.review_research_proposal(
                workspace_id=workspace_id,
                proposal_id=first.proposal.proposal_id,
                reviewer_id="proposer-worker",
                reviewer_role="researcher",
                request=ProposalReviewCreateRequest(
                    decision="accept_for_compilation", notes="Self-review is forbidden."
                ),
                idempotency_key="proposal-self-review",
            )
        with pytest.raises(ResearchValidationError, match="human review"):
            await store.compile_candidate(
                request=CompileCandidateRequest(
                    workspace_id=workspace_id,
                    spec_id=spec.spec_id,
                    mapping_id="not-yet-resolved",
                    transform=json.loads(first.proposal.transform_json),
                    proposal_id=first.proposal.proposal_id,
                ),
                actor_id="researcher",
                idempotency_key="proposal-before-review",
            )

        accepted_request = ProposalReviewCreateRequest(
            decision="accept_for_compilation",
            notes="Reviewed the source, bindings, and proposed assumption; compile only.",
        )
        accepted, accepted_retry = await asyncio.gather(
            store.review_research_proposal(
                workspace_id=workspace_id,
                proposal_id=first.proposal.proposal_id,
                reviewer_id="reviewer-1",
                reviewer_role="reviewer",
                request=accepted_request,
                idempotency_key="proposal-human-review",
            ),
            store.review_research_proposal(
                workspace_id=workspace_id,
                proposal_id=first.proposal.proposal_id,
                reviewer_id="reviewer-1",
                reviewer_role="reviewer",
                request=accepted_request,
                idempotency_key="proposal-human-review",
            ),
        )
        assert accepted.review == accepted_retry.review
        assert {accepted.replayed, accepted_retry.replayed} == {False, True}
        assert accepted.review.proposal_hash == first.proposal.content_hash
        assert accepted.review.decision == "accept_for_compilation"
        assert all(item.discharged is False for item in first.proposal.assumptions)
        with pytest.raises(IdempotencyConflictError):
            await store.review_research_proposal(
                workspace_id=workspace_id,
                proposal_id=first.proposal.proposal_id,
                reviewer_id="reviewer-1",
                reviewer_role="reviewer",
                request=ProposalReviewCreateRequest(
                    decision="reject", notes="Changed decision under the same key."
                ),
                idempotency_key="proposal-human-review",
            )

        rejected_draft = json.loads(output_json)
        rejected_draft["rationale"] = "This is a separate proposal to reject."
        rejected = await store.record_proposal(
            workspace_id=workspace_id,
            actor_id="proposer-worker",
            output_json=json.dumps(rejected_draft),
            idempotency_key="proposal-rejected-draft",
        )
        await store.review_research_proposal(
            workspace_id=workspace_id,
            proposal_id=rejected.proposal.proposal_id,
            reviewer_id="reviewer-2",
            reviewer_role="reviewer",
            request=ProposalReviewCreateRequest(decision="reject", notes="Reject this branch."),
            idempotency_key="proposal-reject-review",
        )
        with pytest.raises(ResearchValidationError, match="Rejected proposals"):
            await store.compile_candidate(
                request=CompileCandidateRequest(
                    workspace_id=workspace_id,
                    spec_id=spec.spec_id,
                    mapping_id="not-yet-resolved",
                    transform=json.loads(rejected.proposal.transform_json),
                    proposal_id=rejected.proposal.proposal_id,
                ),
                actor_id="researcher",
                idempotency_key="rejected-proposal-compile",
            )
        with pytest.raises(ResearchReferenceNotFoundError):
            await store.review_research_proposal(
                workspace_id="another-workspace",
                proposal_id=first.proposal.proposal_id,
                reviewer_id="reviewer-1",
                reviewer_role="reviewer",
                request=accepted_request,
                idempotency_key="foreign-proposal-review",
            )
        reviewed_history, reviewed_total = await store.list_research_proposals(
            workspace_id=workspace_id,
            limit=10,
        )
        assert reviewed_total == 2
        reviewed_by_id = {
            item["proposal"]["proposal_id"]: item["review"] for item in reviewed_history
        }
        assert reviewed_by_id[first.proposal.proposal_id]["decision"] == "accept_for_compilation"
        assert reviewed_by_id[rejected.proposal.proposal_id]["decision"] == "reject"

        with pytest.raises(IdempotencyConflictError):
            await store.record_proposal(
                workspace_id=workspace_id,
                actor_id="proposer-worker",
                output_json=output_json.replace("0.25", "0.75"),
                idempotency_key="proposal-same-retry-key",
            )

        fabricated_binding = json.loads(output_json)
        fabricated_binding["transform"]["bindings"]["left"] = "forged-symbol-id"
        with pytest.raises(ResearchReferenceNotFoundError):
            await store.record_proposal(
                workspace_id=workspace_id,
                actor_id="proposer-worker",
                output_json=json.dumps(fabricated_binding),
                idempotency_key="proposal-forged-binding",
            )

        async with evidence_store.driver.session(database=evidence_store.database) as session:
            record = await (
                await session.run(
                    "MATCH (p:ResearchProposal {proposal_id:$id,group_id:$group}) "
                    "OPTIONAL MATCH (p)-[source:CITES_SOURCE_SPAN]->(e:Evidence) "
                    "OPTIONAL MATCH (p)-[parent:PROPOSED_FROM_EQUATION]->(pe:Evidence) "
                    "RETURN p.payload AS payload,count(DISTINCT source) AS source_count,"
                    "count(DISTINCT parent) AS parent_count",
                    id=first.proposal.proposal_id,
                    group=workspace_group_id(workspace_id),
                )
            ).single(strict=True)
        assert json.loads(record["payload"]) == first.proposal.model_dump(mode="json")
        assert record["source_count"] == 3
        assert record["parent_count"] == 3
    finally:
        await evidence_store.close()


async def test_contract_update_and_mapping_review_share_atomic_boundary(monkeypatch):
    workspace_id = f"review_race_{uuid4().hex}"
    evidence_store, store, graph_a, graph_b = await evidence_backed_stores(workspace_id)
    eq_a, eq_b = equation(graph_a), equation(graph_b)
    release = asyncio.Event()
    changed = asyncio.Event()
    tasks = []
    try:
        await accept_contract(evidence_store, workspace_id, eq_a, key="initial-a")
        await accept_contract(evidence_store, workspace_id, eq_b, key="initial-b")
        mapping, _ = await store.create_compatibility_mapping(
            workspace_id=workspace_id,
            actor_id="human",
            actor_role="researcher",
            request=PortMappingCreateRequest(
                producer_port=browser_port(eq_a),
                consumer_port=browser_port(eq_b),
            ),
        )
        original = evidence_store._append_contract_review

        async def hold_update(tx, *args, **kwargs):
            result = await original(tx, *args, **kwargs)
            changed.set()
            await asyncio.wait_for(release.wait(), timeout=10)
            return result

        monkeypatch.setattr(evidence_store, "_append_contract_review", hold_update)
        writer = asyncio.create_task(
            accept_contract(
                evidence_store,
                workspace_id,
                eq_b,
                key="changed-b",
                shape=(2,),
                reviewed_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
        tasks.append(writer)
        await asyncio.wait_for(changed.wait(), timeout=10)
        review = asyncio.create_task(
            store.review_compatibility_mapping(
                workspace_id=workspace_id,
                mapping_id=mapping.mapping_id,
                reviewer_id="human",
                reviewer_role="reviewer",
                decision="reviewed",
                notes="Review while a new contract is committing",
                idempotency_key="race-review",
            )
        )
        tasks.append(review)
        completed, _ = await asyncio.wait({review}, timeout=0.1)
        assert not completed
        release.set()
        await writer
        with pytest.raises(ResearchValidationError, match="stale"):
            await review
        rows, _, _ = await evidence_store.driver.execute_query(
            "MATCH (m:CompatibilityMapping {mapping_id:$id})-[:HAS_REVIEW]->(r) "
            "RETURN count(r) AS reviews",
            id=mapping.mapping_id,
            database_=evidence_store.database,
        )
        assert rows[0]["reviews"] == 0
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await evidence_store.close()
