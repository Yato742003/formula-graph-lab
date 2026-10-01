"""Source HTML -> reviewed function ports -> search -> evolution -> locked holdout."""

import json
import os
import subprocess
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app import main
from app.admission import AdmissionEvaluationRequest
from app.analysis_versions import canonical_json
from app.compatibility import PortDescriptor, PortMappingCreateRequest
from app.evidence_store import Neo4jEvidenceStore
from app.evolution import EvolutionGenerationRequest
from app.evolution_replay import make_evolution_bundle, replay_evolution_bundle
from app.evolution_runner import confirm_finalists, run_generation
from app.lineage import LineageAssertionCreateRequest, LineageEndpoint
from app.models import EvidenceSearchRequest
from app.problem_spec import ProblemDefinition
from app.replay_bundle import replay_candidate_from_bundle
from app.research_case import ResearchCaseReviewRequest, run_registered_research_case
from app.research_case_worker import evaluate
from app.research_compiler import CompileCandidateRequest
from app.research_store import Neo4jResearchStore, ResearchValidationError
from app.retrieval_experiment import benchmark_retrieval
from app.search import EvidenceSearchService, SearchCursorCodec
from app.symbol_contracts import ContractReview, ReviewedContractValue
from tests.test_research_case import IMAGE, _spec
from tests.test_research_store_integration import (
    attention_corpus_graph,
    connection,
    evidence_ref,
    source_node,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_paired_retrieval_on_frozen_html_corpus():
    workspace = "retrieval_ab_" + uuid4().hex
    store = Neo4jEvidenceStore.connect(*connection())
    await store.initialize()
    try:
        for paper in ("1706.03762", "2006.16236", "2009.14794", "2205.14135"):
            await store.ingest(attention_corpus_graph(workspace, paper))
        service = EvidenceSearchService(store, SearchCursorCodec("cursor-test-secret-" + "x" * 32))
        cases = [
            (
                EvidenceSearchRequest(
                    workspace_id=workspace, query=query, paper_id=paper, version=version, limit=5
                ),
                {f"{paper}v{version}#{anchor}"},
            )
            for paper, version, anchor, query in (
                ("1706.03762", 7, "S3.E1", "scaled dot product attention S3.E1"),
                ("2006.16236", 3, "S3.E4", "kernel feature representation S3.E4"),
                ("2006.16236", 3, "S3.E5", "associative matrix multiplication S3.E5"),
                ("2009.14794", 4, "S2.E7", "positive random features S2.E7"),
            )
        ]
        report = await benchmark_retrieval(service, cases, repeats=3)
        assert report["summary"]["baseline"]["runs"] == 12
        assert all(row["api_cost_usd"] == 0 for row in report["rows"])
        print("RETRIEVAL_AB_REPORT=" + canonical_json(report))
    finally:
        await store.close()


async def test_phase5_source_to_confirmation_and_replay(monkeypatch, tmp_path):
    workspace = "phase5_" + uuid4().hex
    evidence = Neo4jEvidenceStore.connect(*connection())
    store = Neo4jResearchStore(evidence.driver, database=evidence.database)
    image = os.getenv("TEST_SANDBOX_IMAGE", IMAGE)
    monkeypatch.setenv("FGL_SANDBOX_IMAGE", image)
    monkeypatch.setenv("FGL_RESEARCH_QUEUE_SECRET", "phase5-test-queue-key-" + "q" * 32)
    monkeypatch.setenv(
        "FGL_WORKER_IDENTITIES",
        json.dumps(
            [
                {
                    "identity": "pilot_worker",
                    "role": "experiment",
                    "token": "t" * 40,
                    "workspaces": [workspace],
                }
            ]
        ),
    )
    if image == IMAGE:
        # Ordinary integration checks use the same deterministic evaluator in-process;
        # test-phase5.ps1 also runs this exact flow in the isolated Linux sandbox.
        monkeypatch.setattr(
            main,
            "run_registered_research_case",
            lambda *args, **kwargs: run_registered_research_case(
                *args, **kwargs, worker_runner=lambda payload, **_: evaluate(payload)
            ),
        )
    await evidence.initialize()
    await store.initialize()
    try:
        linear = attention_corpus_graph(workspace, "2006.16236")
        transformer = attention_corpus_graph(workspace, "1706.03762")
        await evidence.ingest(linear)
        await evidence.ingest(transformer)
        left = source_node(linear, "Equation", "S3.E4")
        right = source_node(linear, "Equation", "S3.E5")
        await store.create_lineage_assertion(
            workspace_id=workspace,
            actor_id="human",
            actor_role="researcher",
            idempotency_key="paper-derivation",
            request=LineageAssertionCreateRequest(
                relation_type="mathematical_derivation",
                source=LineageEndpoint(kind="equation", id=left["uuid"], version=linear.version),
                target=LineageEndpoint(kind="equation", id=right["uuid"], version=linear.version),
                evidence=(evidence_ref(source_node(linear, "Section", "S3")),),
                description="Associativity rewrite; not a claim about our reference worker.",
            ),
        )
        for index, node in enumerate((left, right)):
            await evidence.append_contract_review(
                workspace_id=workspace,
                equation_uuid=node["uuid"],
                idempotency_key=f"function-contract-{index}",
                review=ContractReview(
                    review_id="server-owned",
                    symbol_name="phi",
                    reviewer_id="human",
                    reviewer_role="reviewer",
                    decision="accepted",
                    scope=node["uuid"],
                    reviewed_contract=ReviewedContractValue(
                        name="phi",
                        category="function",
                        shape=(64,),
                        domain="real",
                        feature_rank=16,
                        feature_output_domain="strictly_positive_real",
                        normalization="none",
                        mask="none",
                        causal=False,
                        resource_class="not_applicable",
                    ),
                    evidence=["Pilot instantiates generic phi; no author-code equivalence."],
                    reviewed_at=datetime.now(UTC),
                ),
            )
        mapping, _ = await store.create_compatibility_mapping(
            workspace_id=workspace,
            actor_id="human",
            actor_role="researcher",
            idempotency_key="mapping",
            request=PortMappingCreateRequest(
                producer_port=PortDescriptor(
                    equation_id=left["uuid"],
                    version=linear.version,
                    scoped_symbol_id="untrusted",
                    symbol_name="phi",
                    domain="unknown",
                ),
                consumer_port=PortDescriptor(
                    equation_id=right["uuid"],
                    version=linear.version,
                    scoped_symbol_id="untrusted",
                    symbol_name="phi",
                    domain="unknown",
                ),
            ),
        )
        await store.review_compatibility_mapping(
            workspace_id=workspace,
            mapping_id=mapping.mapping_id,
            reviewer_id="human",
            reviewer_role="reviewer",
            decision="reviewed",
            notes="Reviewed function codomains.",
            idempotency_key="review-mapping",
        )
        raw = _spec().definition.model_dump(mode="json")
        raw["budget"].update(
            max_candidates=2, max_generations=2, wall_time_ms=120_000, compute_budget=60
        )
        spec, _ = await store.freeze_problem_spec(
            workspace_id=workspace,
            actor_id="human",
            actor_role="researcher",
            idempotency_key="spec",
            definition=ProblemDefinition.model_validate(raw),
        )
        ports, _ = await store.get_compatibility_mapping(
            workspace_id=workspace,
            mapping_id=mapping.mapping_id,
        )
        mixture = await store.compile_candidate(
            actor_id="human",
            idempotency_key="mix",
            request=CompileCandidateRequest(
                workspace_id=workspace,
                spec_id=spec.spec_id,
                mapping_id=mapping.mapping_id,
                transform={
                    "operator": "mix_positive_feature_maps",
                    "operator_version": "1",
                    "target_node_id": left["uuid"],
                    "parameters": {"lambda": 0.75},
                    "bindings": {
                        "left": ports.producer_port.scoped_symbol_id,
                        "right": ports.consumer_port.scoped_symbol_id,
                    },
                },
            ),
        )
        child = await store.compile_candidate(
            actor_id="human",
            idempotency_key="lower",
            request=CompileCandidateRequest(
                workspace_id=workspace,
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
        )
        candidate_id = child.candidate.candidate_id
        check = await store.verify_candidate(
            workspace_id=workspace, candidate_id=candidate_id, idempotency_key="check"
        )
        assert check.check.vector.domain == "conditional"
        search = await main.execute_research_case(store, workspace, candidate_id, "search")
        assert {t.split for t in search.result.trials} == {"search", "validation"}
        assert search.result.holdout_mean is None
        assert search.result.quality_constraints_met
        retry = await main.execute_research_case(store, workspace, candidate_id, "search")
        assert retry.replayed and retry.result == search.result
        before = await store.evaluate_candidate_admission(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            idempotency_key="before-review",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
        )
        assert not before.decision.allowed
        await store.review_research_case(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            actor_role="reviewer",
            idempotency_key="review-scope",
            request=ResearchCaseReviewRequest(
                result_id=search.result.result_id,
                decision="accept_protocol_scope",
                notes="Authorize frozen-family CPU search only.",
            ),
        )
        gate = await store.evaluate_candidate_admission(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            idempotency_key="after-review",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
        )
        assert gate.decision.allowed and "search_protocol_scope_only" in gate.decision.reasons
        publish = await store.evaluate_candidate_admission(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            idempotency_key="no-global-proof",
            request=AdmissionEvaluationRequest(
                action="can_publish_claim", claim_scope="mathematical"
            ),
        )
        assert not publish.decision.allowed
        await store.review_research_case(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            actor_role="reviewer",
            idempotency_key="revoke-scope",
            request=ResearchCaseReviewRequest(
                result_id=search.result.result_id,
                decision="reject",
                notes="Revoke before starting search.",
            ),
        )
        revoked = await store.evaluate_candidate_admission(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            idempotency_key="after-revocation",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
        )
        assert not revoked.decision.allowed
        await store.review_research_case(
            workspace_id=workspace,
            candidate_id=candidate_id,
            actor_id="human",
            actor_role="reviewer",
            idempotency_key="restore-scope",
            request=ResearchCaseReviewRequest(
                result_id=search.result.result_id,
                decision="accept_protocol_scope",
                notes="Restore frozen synthetic family only.",
            ),
        )
        campaign, _ = await store.start_evolution_campaign(
            workspace_id=workspace, spec_id=spec.spec_id, actor_id="human", idempotency_key="start"
        )
        eid = campaign.evolution_id
        with pytest.raises(ResearchValidationError, match="locked"):
            await main.execute_research_case(
                store,
                workspace,
                candidate_id,
                "premature-holdout",
                evaluation_role="holdout",
                evolution_id=eid,
            )
        first, _ = await run_generation(
            store,
            workspace,
            eid,
            "human",
            "generation-1",
            EvolutionGenerationRequest(seed_candidate_id=candidate_id),
            main.execute_research_case,
        )
        again, replayed = await run_generation(
            store,
            workspace,
            eid,
            "human",
            "generation-1",
            EvolutionGenerationRequest(seed_candidate_id=candidate_id),
            main.execute_research_case,
        )
        assert again == first and replayed
        second, _ = await run_generation(
            store,
            workspace,
            eid,
            "human",
            "generation-2",
            EvolutionGenerationRequest(),
            main.execute_research_case,
        )
        assert second.generation == 2 and len(second.evaluations) == 2
        assert second.evaluations[-1].search_parent_ids == (candidate_id,)
        assert all(e.data_role == "search" for e in second.evaluations)
        assert second.status == "search_stopped"
        frozen, _ = await store.freeze_evolution_finalists(
            workspace_id=workspace,
            evolution_id=eid,
            finalist_ids=second.pareto_archive,
            actor_id="human",
            idempotency_key="freeze-finalists",
        )
        assert frozen.status == "finalists_frozen"
        final, _ = await confirm_finalists(
            store, workspace, eid, "human", "confirm", main.execute_research_case
        )
        assert final.stop_reason == "confirmation_completed"
        assert all(e.data_role == "holdout" for e in final.confirmation_evaluations)
        assert set(final.winner_ids) <= {
            e.candidate_id for e in final.confirmation_evaluations if e.outcome == "eligible"
        }
        bundle = await store.export_compiler_replay_bundle(
            workspace_id=workspace,
            candidate_id=candidate_id,
            activity_id=child.activity.activity_id,
        )
        assert {s.paper_id for s in bundle.sources} == {"2006.16236"}
        assert replay_candidate_from_bundle(bundle) == child.candidate
        evolution_bundle = await store.export_evolution_bundle(
            workspace_id=workspace,
            evolution_id=eid,
        )
        assert replay_evolution_bundle(evolution_bundle) == final
        path = tmp_path / "campaign.json"
        path.write_text(evolution_bundle.model_dump_json(), encoding="utf-8")
        replay = subprocess.run(
            [os.sys.executable, "-m", "app.evolution_replay_cli", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        assert json.loads(replay.stdout)["replay"] == "reproduced"
        if os.getenv("TEST_PHASE5_CLEAN_REPLAY") == "1":
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
            clean = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-File",
                    os.path.join(root, "replay-bundle.ps1"),
                    "-Bundle",
                    str(path),
                    "-Evolution",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                check=True,
            )
            assert '"replay": "reproduced"' in clean.stdout
        changed = final.model_copy(update={"winner_ids": ()})
        if final.winner_ids:
            with pytest.raises(ValueError, match="reproduce"):
                replay_evolution_bundle(make_evolution_bundle(changed, evolution_bundle.candidates))
    finally:
        await evidence.close()
