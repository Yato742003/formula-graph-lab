"""Persistence store for research ProblemSpecs, Lineage, and Compatibility."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.admission import (
    AdmissionContext,
    AdmissionDecision,
    AdmissionEvaluationRequest,
    AdmissionEvaluationResponse,
    AdmissionReplayInput,
    decide_admission,
    make_admission_replay_input,
)
from app.analysis_versions import canonical_json, source_hash
from app.candidate_verification import (
    CANDIDATE_CHECKER_VERSION,
    CandidateCheckResponse,
    CandidateCheckResult,
    discharged_obligations,
    verify_compiled_candidate,
)
from app.compatibility import (
    COMPATIBILITY_POLICY_VERSION,
    CompatibilityAssessment,
    CompatibilityEvidence,
    CompatibilityReview,
    PortDescriptor,
    PortMapping,
    PortMappingCreateRequest,
    PortReference,
    ResolvedDependency,
    assess_mapping,
    compute_dependency_fingerprint,
    generate_mapping_id,
)
from app.episodes import workspace_group_id
from app.evolution import (
    EvolutionCampaign,
    EvolutionEvaluation,
    MetricSample,
    freeze_finalists,
    make_confirmation_evaluation,
    make_evaluation,
    record_confirmation,
    reserve_generation,
    select_parents,
    settle_generation,
    start_campaign,
    stop_campaign,
)
from app.lineage import (
    CYCLE_PROHIBITED_RELATIONS,
    MAX_TRAVERSAL_DEPTH,
    MAX_TRAVERSAL_NODES,
    LineageAssertionCreateRequest,
    LineageAssertionRecord,
    LineageCoverageResponse,
    LineageCycleError,
    LineageEndpoint,
    LineageRelationType,
    LineageReview,
    MetadataPaperCreateRequest,
    PaperCoverageRecord,
    check_cycle,
    generate_assertion_id,
)
from app.numerical_verification import (
    NumericalFixtureReceipt,
    NumericalFixtureReceiptResponse,
    build_numerical_suite_input,
    numerical_fixture_input_hash,
)
from app.problem_spec import (
    ProblemDefinition,
    ProblemSpecSnapshot,
    definition_hash,
    generate_campaign_id,
    generate_spec_id,
)
from app.proposals import (
    ProposalCreateResponse,
    ProposalDraft,
    ProposalReviewCreateRequest,
    ProposalReviewRecord,
    ProposalReviewResponse,
    ResearchProposal,
    _parse_model_output,
    make_source_span_id,
    parse_source_span_id,
    validate_model_proposal,
)
from app.replay_bundle import (
    CompilerReplayBundle,
    ReplaySourceReference,
    make_compiler_replay_bundle,
)
from app.research_case import (
    ImplementationBindingReceipt,
    ResearchCaseReceipt,
    ResearchCaseReceiptResponse,
    ResearchCaseReviewRequest,
    ResearchProtocolReview,
    make_implementation_binding,
    phase_budget_ms,
    protocol_admission_context,
    protocol_scope_key,
    quality_summary,
    validate_registered_spec,
)
from app.research_case_worker import SEEDS as RESEARCH_CASE_SEEDS
from app.research_compiler import (
    CompileCandidateRequest,
    CompileCandidateResponse,
    CompileContext,
    CompiledCandidate,
    ParentRef,
    ResolvedFeatureMap,
    TransformationActivity,
    _verify_candidate_identity,
    compile_transform,
)
from app.research_lock import lock_research_workspace
from app.symbol_contracts import ContractReview, ReviewedContractValue
from app.transformation_dsl import (
    LowerMixtureToConcatenation,
    MixPositiveFeatureMaps,
    validate_transform,
)

logger = logging.getLogger(__name__)


class ResearchStoreError(Exception):
    """Base exception for research store operations."""


class IdempotencyConflictError(ResearchStoreError):
    """Idempotency key reused with altered intent."""


class ParentSpecNotFoundError(ResearchStoreError):
    """Parent spec not found in this workspace."""


class ResearchReferenceNotFoundError(ResearchStoreError):
    """A requested source-backed reference is absent from this workspace."""


class ResearchValidationError(ResearchStoreError):
    """A request attempts an unsupported or unsafe research state transition."""


class ResearchAuthorizationError(ResearchStoreError):
    """An authenticated caller is not authorized for a research action."""


class ProposalGenerationBudgetExceededError(ResearchStoreError):
    """Workspace's UTC-day proposal-generation reservation budget is exhausted."""


class ProposalGenerationInProgressError(ResearchStoreError):
    """The key already reserved provider work and must not be invoked again."""


class Neo4jResearchStore:
    """Atomic research writes scoped strictly to workspace groups."""

    def __init__(self, driver=None, *, database: str = "neo4j"):
        self.driver = driver
        self.database = database
        # In-memory storage for offline testing when driver is None
        self._mem_specs: dict[str, dict[str, Any]] = {}
        self._mem_idemp: dict[str, dict[str, Any]] = {}
        self._mem_campaigns: dict[str, dict[str, Any]] = {}
        self._mem_assertions: dict[str, dict[str, Any]] = {}
        self._mem_lineage_reviews: dict[str, list[dict[str, Any]]] = {}
        self._mem_papers: dict[str, dict[str, Any]] = {}
        self._mem_compat_mappings: dict[str, dict[str, Any]] = {}
        self._mem_compat_assessments: dict[str, dict[str, Any]] = {}
        self._mem_compat_reviews: dict[str, list[dict[str, Any]]] = {}
        self._mem_evolutions: dict[str, dict[str, Any]] = {}

    @classmethod
    def connect(
        cls, uri: str, user: str, password: str, *, database: str = "neo4j"
    ) -> Neo4jResearchStore:
        from neo4j import AsyncGraphDatabase

        return cls(AsyncGraphDatabase.driver(uri, auth=(user, password)), database=database)

    async def close(self) -> None:
        if self.driver is not None:
            await self.driver.close()

    async def initialize(self) -> None:
        if self.driver is None:
            return
        constraints = [
            ("ProblemSpec", "fgl_problem_spec_id", "spec_id"),
            ("ResearchCampaign", "fgl_research_campaign_id", "campaign_id"),
            ("ResearchIdempotency", "fgl_research_idempotency_key", "key"),
            ("LineageAssertion", "fgl_lineage_assertion_id", "assertion_id"),
            ("LineageReview", "fgl_lineage_review_id", "review_id"),
            ("CompatibilityMapping", "fgl_compatibility_mapping_id", "mapping_id"),
            ("CompatibilityReview", "fgl_compatibility_review_id", "review_id"),
            ("ResearchCandidate", "fgl_research_candidate_id", "candidate_id"),
            ("ResearchCandidateCheck", "fgl_research_candidate_check_id", "check_id"),
            ("ResearchProposal", "fgl_research_proposal_id", "proposal_id"),
            ("ResearchProposalReview", "fgl_research_proposal_review_id", "review_id"),
            ("ResearchAdmissionDecision", "fgl_research_admission_decision_id", "decision_id"),
            (
                "ResearchNumericalFixtureResult",
                "fgl_research_numerical_fixture_result_id",
                "result_id",
            ),
            ("TransformationActivity", "fgl_transformation_activity_id", "activity_id"),
            ("ResearchPaperCoverage", "fgl_research_paper_coverage_id", "coverage_id"),
            ("ResearchWorkspaceLock", "fgl_research_workspace_lock", "group_id"),
            ("EvolutionCampaign", "fgl_evolution_campaign_id", "evolution_id"),
        ]
        for label, constraint, prop in constraints:
            await self.driver.execute_query(
                f"CREATE CONSTRAINT {constraint} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE",
                database_=self.database,
            )

    @staticmethod
    def _payload_object(payload: str, *, label: str) -> dict[str, Any]:
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ResearchStoreError(f"Stored {label} payload is invalid.") from exc
        if not isinstance(value, dict):
            raise ResearchStoreError(f"Stored {label} payload is invalid.")
        return value

    @staticmethod
    def _stable_hash(value: object) -> str:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    async def _resolve_lineage_endpoint(
        self,
        session,
        *,
        group_id: str,
        endpoint,
    ):
        """Resolve a browser-supplied paper/equation reference to immutable Evidence."""
        kind = "PaperVersion" if endpoint.kind == "paper" else "Equation"
        result = await session.run(
            """
            MATCH (e:Evidence {group_id: $group, kind: $kind})
            WHERE (e.uuid = $identifier OR e.logical_id = $identifier OR
                   ($kind = 'PaperVersion' AND e.paper_id = $identifier))
              AND ($version IS NULL OR e.paper_version = $version)
            RETURN e.uuid AS uuid, e.paper_version AS version
            LIMIT 2
            """,
            group=group_id,
            kind=kind,
            identifier=endpoint.id,
            version=endpoint.version,
        )
        rows = [row async for row in result]
        if len(rows) != 1:
            raise ResearchReferenceNotFoundError(
                "Source endpoint was not found uniquely in this workspace."
            )
        row = rows[0]
        version = row["version"]
        if version is None:
            raise ResearchReferenceNotFoundError("Source endpoint has no immutable version.")
        return endpoint.model_copy(update={"id": str(row["uuid"]), "version": int(version)})

    async def _resolve_lineage_evidence(
        self,
        session,
        *,
        group_id: str,
        evidence,
        endpoint_ids: set[str],
    ):
        result = await session.run(
            """
            MATCH (e:Evidence {group_id: $group})
            WHERE e.kind IN ['Section', 'Equation']
              AND (e.uuid = $identifier OR e.logical_id = $identifier)
            OPTIONAL MATCH (e)-[r:EVIDENCE_RELATION {group_id: $group}]-()
            OPTIONAL MATCH path = (e)-[:EVIDENCE_RELATION*1..3]-
                                  (endpoint:Evidence {group_id: $group})
            WHERE endpoint.uuid IN $endpoint_ids
              AND all(n IN nodes(path) WHERE n.group_id = $group)
              AND all(edge IN relationships(path) WHERE edge.group_id = $group)
            RETURN e.uuid AS uuid, e.payload AS payload, e.source_sha256 AS source_sha256,
                   collect(DISTINCT r.source_anchor) AS anchors,
                   collect(DISTINCT endpoint.uuid) AS endpoint_ids
            LIMIT 2
            """,
            group=group_id,
            identifier=evidence.source_entity_id,
            endpoint_ids=list(endpoint_ids),
        )
        rows = [row async for row in result]
        if len(rows) != 1 or (
            str(rows[0]["uuid"]) not in endpoint_ids and not rows[0]["endpoint_ids"]
        ):
            raise ResearchReferenceNotFoundError(
                "Lineage evidence was not found in the asserted endpoint scope."
            )
        row = rows[0]
        payload = str(row["payload"])
        payload_object = self._payload_object(payload, label="evidence")
        if payload_object.get("anchor_is_source") is False:
            raise ResearchValidationError(
                "Generated anchors cannot act as original source evidence."
            )
        anchors = {str(anchor) for anchor in row["anchors"] if anchor}
        payload_anchor = payload_object.get("anchor")
        if isinstance(payload_anchor, str) and payload_anchor:
            anchors.add(payload_anchor)
        if evidence.anchor not in anchors:
            raise ResearchValidationError("Lineage evidence anchor is not source-backed.")
        actual_hash = row["source_sha256"]
        if not isinstance(actual_hash, str) or len(actual_hash) != 64:
            actual_hash = source_hash(payload)
        if evidence.source_hash != actual_hash:
            raise ResearchValidationError("Lineage evidence hash does not match its source.")
        return evidence.model_copy(
            update={"source_entity_id": str(row["uuid"]), "source_hash": actual_hash}
        )

    async def _resolve_compatibility_port(
        self,
        session,
        *,
        group_id: str,
        port: PortDescriptor | PortReference,
    ) -> tuple[PortDescriptor, ResolvedDependency]:
        """Resolve one port from Equation/Symbol/ContractReview records, never request claims."""
        equation_result = await session.run(
            """
            MATCH (e:Evidence {group_id: $group, kind: 'Equation'})
            WHERE e.uuid = $identifier OR e.logical_id = $identifier
            RETURN e.uuid AS uuid, e.payload AS payload, e.paper_version AS version
            LIMIT 2
            """,
            group=group_id,
            identifier=port.equation_id,
        )
        equations = [row async for row in equation_result]
        if len(equations) != 1:
            raise ResearchReferenceNotFoundError(
                "Port equation was not found uniquely in this workspace."
            )
        equation = equations[0]
        version = int(equation["version"] or 1)
        if port.version != version:
            raise ResearchValidationError("Port version does not match the stored equation.")

        symbols_result = await session.run(
            """
            MATCH (e:Evidence {uuid: $equation_id, group_id: $group, kind: 'Equation'})
                  -[:EVIDENCE_RELATION {group_id: $group}]->
                  (s:Evidence {group_id: $group, kind: 'Symbol'})
            RETURN s.uuid AS uuid, s.payload AS payload
            """,
            group=group_id,
            equation_id=equation["uuid"],
        )
        symbols = []
        async for row in symbols_result:
            symbol_payload = self._payload_object(str(row["payload"]), label="symbol")
            if symbol_payload.get("notation") == port.symbol_name:
                symbols.append((row, symbol_payload))
        if len(symbols) != 1:
            raise ResearchReferenceNotFoundError(
                "Port symbol was not found uniquely in the stored equation scope."
            )
        symbol, symbol_payload = symbols[0]

        reviews_result = await session.run(
            """
            MATCH (r:ContractReview {group_id: $group})-[:REVIEWS]->
                  (e:Evidence {uuid: $equation_id, group_id: $group, kind: 'Equation'})
            WHERE r.symbol_name = $symbol_name
            RETURN r.uuid AS uuid, r.payload AS payload, r.reviewed_at AS reviewed_at
            ORDER BY r.reviewed_at DESC, r.uuid DESC
            LIMIT 1
            """,
            group=group_id,
            equation_id=equation["uuid"],
            symbol_name=port.symbol_name,
        )
        review = await reviews_result.single()
        contract = symbol_payload
        review_payload: dict[str, Any] | None = None
        accepted_contract = False
        if review is not None:
            review_payload = self._payload_object(str(review["payload"]), label="contract review")
            reviewed_contract = review_payload.get("reviewed_contract")
            if (
                review_payload.get("decision") == "accepted"
                and review_payload.get("scope") == str(equation["uuid"])
                and isinstance(reviewed_contract, dict)
            ):
                contract = reviewed_contract
                accepted_contract = True

        shape_value = contract.get("shape")
        shape = tuple(shape_value) if isinstance(shape_value, list) else None
        domain = {
            "positive": "strictly_positive_real",
            "non_negative": "non_negative_real",
        }.get(str(contract.get("domain", "unknown")), str(contract.get("domain", "unknown")))
        if accepted_contract and contract.get("category") == "function":
            domain = contract.get("feature_output_domain") or "unknown"
        if domain not in {
            "real",
            "strictly_positive_real",
            "non_negative_real",
            "integer",
            "positive_integer",
            "complex",
            "probability_simplex",
            "unit_interval",
            "boolean",
            "unknown",
        }:
            domain = "unknown"
        resolved = PortDescriptor(
            equation_id=str(equation["uuid"]),
            version=version,
            scoped_symbol_id=str(symbol["uuid"]),
            symbol_name=port.symbol_name,
            domain=domain,
            domain_is_reviewed=accepted_contract,
            shape=shape,
            normalization=(
                contract.get("normalization", "unknown") if accepted_contract else "unknown"
            ),
            mask=contract.get("mask", "missing") if accepted_contract else "missing",
            causal=contract.get("causal") if accepted_contract else None,
            resource_class=(
                contract.get("resource_class", "unknown") if accepted_contract else "unknown"
            ),
        )
        analysis_result = await session.run(
            """
            MATCH (a:FormulaAnalysisVersion {group_id: $group})-[:ANALYZES]->
                  (e:Evidence {uuid: $equation_id, group_id: $group, kind: 'Equation'})
            WHERE coalesce(a.retired, false) = false
            RETURN a.uuid AS uuid, a.source_hash AS source_hash, a.payload AS payload
            ORDER BY a.created_at DESC, a.uuid DESC
            LIMIT 1
            """,
            group=group_id,
            equation_id=equation["uuid"],
        )
        analysis = await analysis_result.single()
        dependency_hash = self._stable_hash(
            {
                "equation": self._payload_object(str(equation["payload"]), label="equation"),
                "symbol_uuid": str(symbol["uuid"]),
                "symbol": symbol_payload,
                "analysis": dict(analysis) if analysis is not None else None,
                "review": review_payload,
            }
        )
        return resolved, ResolvedDependency(
            entity_id=f"{equation['uuid']}:{symbol['uuid']}",
            version=version,
            content_hash=dependency_hash,
        )

    async def _resolve_compiler_feature_map(
        self,
        session,
        *,
        group_id: str,
        workspace_id: str,
        port: PortDescriptor,
    ) -> tuple[ResolvedFeatureMap, ResolvedDependency]:
        resolved, dependency = await self._resolve_compatibility_port(
            session,
            group_id=group_id,
            port=port,
        )
        if not resolved.domain_is_reviewed or resolved.domain != "strictly_positive_real":
            raise ResearchValidationError("Feature maps require a reviewed positive-real contract.")

        equation_result = await session.run(
            "MATCH (e:Evidence {uuid:$uuid, group_id:$group, kind:'Equation'}) "
            "RETURN e.payload AS payload, e.source_sha256 AS source_hash LIMIT 1",
            uuid=resolved.equation_id,
            group=group_id,
        )
        equation = await equation_result.single()
        if equation is None:
            raise ResearchReferenceNotFoundError("Feature-map equation is unavailable.")
        source_payload_text = str(equation["payload"])
        source_digest = equation["source_hash"]
        if not isinstance(source_digest, str) or len(source_digest) != 64:
            source_digest = source_hash(source_payload_text)

        review_result = await session.run(
            "MATCH (r:ContractReview {group_id:$group})-[:REVIEWS]->"
            "(e:Evidence {uuid:$uuid, group_id:$group, kind:'Equation'}) "
            "WHERE r.symbol_name=$symbol_name "
            "RETURN r.payload AS payload ORDER BY r.reviewed_at DESC, r.uuid DESC LIMIT 1",
            group=group_id,
            uuid=resolved.equation_id,
            symbol_name=resolved.symbol_name,
        )
        review_row = await review_result.single()
        if review_row is None:
            raise ResearchValidationError("A human feature-map contract review is required.")
        review = self._payload_object(str(review_row["payload"]), label="contract review")
        if review.get("decision") != "accepted" or review.get("scope") != resolved.equation_id:
            raise ResearchValidationError("The current feature-map contract is not accepted.")
        try:
            contract = ReviewedContractValue.model_validate(review.get("reviewed_contract"))
        except (TypeError, ValueError) as exc:
            raise ResearchValidationError("The stored feature-map contract is invalid.") from exc
        if contract.feature_rank is None:
            raise ResearchValidationError(
                "A reviewed feature-map rank is required for compilation."
            )
        return (
            ResolvedFeatureMap(
                workspace_id=workspace_id,
                port=resolved,
                source_hash=source_digest,
                contract_hash=self._stable_hash(contract.model_dump(mode="json")),
                dependency_hash=dependency.content_hash,
                feature_rank=contract.feature_rank,
            ),
            dependency,
        )

    async def _resolve_compiler_target(
        self,
        session,
        *,
        group_id: str,
        target_id: str,
    ) -> tuple[ParentRef, ResolvedDependency]:
        result = await session.run(
            "MATCH (e:Evidence {group_id:$group, kind:'Equation'}) "
            "WHERE e.uuid=$identifier OR e.logical_id=$identifier "
            "RETURN e.uuid AS uuid, e.payload AS payload, e.source_sha256 AS source_hash, "
            "e.paper_version AS version LIMIT 2",
            group=group_id,
            identifier=target_id,
        )
        rows = [row async for row in result]
        if len(rows) != 1:
            raise ResearchReferenceNotFoundError(
                "Target equation was not found uniquely in this workspace."
            )
        row = rows[0]
        if str(row["uuid"]) != target_id:
            raise ResearchValidationError("Target must use its canonical stored equation ID.")
        raw_payload = str(row["payload"])
        source_digest = row["source_hash"]
        if not isinstance(source_digest, str) or len(source_digest) != 64:
            source_digest = source_hash(raw_payload)
        analysis_result = await session.run(
            "MATCH (a:FormulaAnalysisVersion {group_id:$group})-[:ANALYZES]->"
            "(e:Evidence {uuid:$uuid, group_id:$group, kind:'Equation'}) "
            "WHERE coalesce(a.retired,false)=false "
            "RETURN a.uuid AS uuid, a.source_hash AS source_hash, a.payload AS payload "
            "ORDER BY a.created_at DESC, a.uuid DESC LIMIT 1",
            group=group_id,
            uuid=target_id,
        )
        analysis = await analysis_result.single()
        dependency_hash = self._stable_hash(
            {
                "source_hash": source_digest,
                "equation": self._payload_object(raw_payload, label="target equation"),
                "analysis": dict(analysis) if analysis is not None else None,
            }
        )
        version = int(row["version"] or 1)
        parent = ParentRef(entity_id=target_id, version=version, content_hash=dependency_hash)
        return parent, ResolvedDependency(
            entity_id=target_id,
            version=version,
            content_hash=dependency_hash,
        )

    async def freeze_problem_spec(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        actor_role: str,
        idempotency_key: str,
        definition: ProblemDefinition,
        parent_spec_id: str | None = None,
    ) -> tuple[ProblemSpecSnapshot, bool]:
        group_id = workspace_group_id(workspace_id)
        content_hash = definition_hash(definition)
        intent_payload = canonical_json([content_hash, parent_spec_id])
        intent_hash = hashlib.sha256(intent_payload.encode("utf-8")).hexdigest()
        idemp_key = f"{group_id}:problemspec:{idempotency_key}"

        if self.driver is None:
            # In-memory execution
            existing_rec = self._mem_idemp.get(idemp_key)
            if existing_rec is not None:
                if existing_rec["intent_hash"] == intent_hash:
                    return ProblemSpecSnapshot.model_validate_json(existing_rec["payload"]), True
                raise IdempotencyConflictError(
                    "Idempotency key reused with altered problem spec intent."
                )

            version = 1
            if parent_spec_id is not None:
                parent = self._mem_specs.get(parent_spec_id)
                if parent is None or parent["group_id"] != group_id:
                    raise ParentSpecNotFoundError(
                        f"Parent spec '{parent_spec_id}' not found in this workspace."
                    )
                version = parent["version"] + 1

            spec_id = generate_spec_id(workspace_id, content_hash, parent_spec_id, version)
            campaign_id = generate_campaign_id(workspace_id, spec_id)
            now_iso = datetime.now(UTC).isoformat()

            existing_spec = self._mem_specs.get(spec_id)
            if existing_spec is not None:
                existing = ProblemSpecSnapshot.model_validate_json(existing_spec["payload"])
                self._mem_idemp[idemp_key] = {
                    "key": idemp_key,
                    "spec_id": spec_id,
                    "intent_hash": intent_hash,
                    "payload": existing_spec["payload"],
                }
                return existing, False

            snapshot = ProblemSpecSnapshot(
                schema_version="problem-spec.v1",
                content_hash=content_hash,
                spec_id=spec_id,
                workspace_id=workspace_id,
                parent_spec_id=parent_spec_id,
                version=version,
                campaign_id=campaign_id,
                created_by=actor_id,
                created_at=now_iso,
                definition=definition,
            )
            raw_payload = canonical_json(snapshot.model_dump(mode="json"))

            self._mem_specs[spec_id] = {
                "spec_id": spec_id,
                "group_id": group_id,
                "version": version,
                "payload": raw_payload,
                "created_at": now_iso,
            }
            self._mem_campaigns[campaign_id] = {
                "campaign_id": campaign_id,
                "spec_id": spec_id,
                "group_id": group_id,
            }
            self._mem_idemp[idemp_key] = {
                "key": idemp_key,
                "spec_id": spec_id,
                "intent_hash": intent_hash,
                "payload": raw_payload,
            }
            return snapshot, False

        # Real Neo4j write transaction
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_freeze_problem_spec,
                group_id,
                workspace_id,
                actor_id,
                idemp_key,
                intent_hash,
                content_hash,
                parent_spec_id,
                definition,
                idempotency_key,
            )

    @staticmethod
    async def _tx_freeze_problem_spec(
        tx,
        group_id: str,
        workspace_id: str,
        actor_id: str,
        idemp_key: str,
        intent_hash: str,
        content_hash: str,
        parent_spec_id: str | None,
        definition: ProblemDefinition,
        idempotency_key: str,
    ) -> tuple[ProblemSpecSnapshot, bool]:
        # 1. Check idempotency record
        res = await tx.run(
            """
            MATCH (rec:ResearchIdempotency {key: $key})
            RETURN rec.intent_hash AS intent_hash, rec.payload AS payload
            LIMIT 1
            """,
            key=idemp_key,
        )
        record = await res.single()
        if record is not None:
            if record["intent_hash"] == intent_hash:
                return ProblemSpecSnapshot.model_validate_json(record["payload"]), True
            raise IdempotencyConflictError(
                "Idempotency key reused with altered problem spec intent."
            )

        # 2. Verify parent if specified
        version = 1
        if parent_spec_id is not None:
            p_res = await tx.run(
                """
                MATCH (p:ProblemSpec {spec_id: $parent_spec_id, group_id: $group})
                RETURN p.version AS version
                LIMIT 1
                """,
                parent_spec_id=parent_spec_id,
                group=group_id,
            )
            p_rec = await p_res.single()
            if p_rec is None:
                raise ParentSpecNotFoundError(
                    f"Parent spec '{parent_spec_id}' not found in this workspace."
                )
            version = int(p_rec["version"]) + 1

        # 3. Generate IDs and snapshot
        spec_id = generate_spec_id(workspace_id, content_hash, parent_spec_id, version)
        campaign_id = generate_campaign_id(workspace_id, spec_id)
        now_iso = datetime.now(UTC).isoformat()

        snapshot = ProblemSpecSnapshot(
            schema_version="problem-spec.v1",
            content_hash=content_hash,
            spec_id=spec_id,
            workspace_id=workspace_id,
            parent_spec_id=parent_spec_id,
            version=version,
            campaign_id=campaign_id,
            created_by=actor_id,
            created_at=now_iso,
            definition=definition,
        )
        raw_payload = canonical_json(snapshot.model_dump(mode="json"))

        # 4. Atomically persist snapshot, campaign, and idempotency lock
        spec_result = await tx.run(
            """
            MERGE (spec:ProblemSpec {spec_id: $spec_id})
            ON CREATE SET spec.group_id = $group,
                          spec.workspace_id = $workspace_id,
                          spec.parent_spec_id = $parent_spec_id,
                          spec.version = $version,
                          spec.content_hash = $content_hash,
                          spec.campaign_id = $campaign_id,
                          spec.created_by = $created_by,
                          spec.created_at = $created_at,
                          spec.payload = $payload
            RETURN spec.payload AS payload
            """,
            spec_id=spec_id,
            group=group_id,
            workspace_id=workspace_id,
            parent_spec_id=parent_spec_id,
            version=version,
            content_hash=content_hash,
            campaign_id=campaign_id,
            created_by=actor_id,
            created_at=now_iso,
            payload=raw_payload,
        )
        spec_record = await spec_result.single(strict=True)
        persisted_snapshot = ProblemSpecSnapshot.model_validate_json(spec_record["payload"])

        if parent_spec_id is not None:
            await tx.run(
                """
                MATCH (p:ProblemSpec {spec_id: $parent_spec_id, group_id: $group})
                MATCH (s:ProblemSpec {spec_id: $spec_id, group_id: $group})
                MERGE (p)-[:REVISED_TO]->(s)
                """,
                parent_spec_id=parent_spec_id,
                spec_id=spec_id,
                group=group_id,
            )

        await tx.run(
            """
            MERGE (c:ResearchCampaign {campaign_id: $campaign_id})
            ON CREATE SET c.spec_id = $spec_id,
                          c.group_id = $group,
                          c.created_at = $created_at
            WITH c
            MATCH (s:ProblemSpec {spec_id: $spec_id, group_id: $group})
            MERGE (s)-[:ASSOCIATED_CAMPAIGN]->(c)
            """,
            campaign_id=campaign_id,
            spec_id=spec_id,
            group=group_id,
            created_at=now_iso,
        )

        creation_token = str(uuid4())
        idemp_res = await tx.run(
            """
            MERGE (rec:ResearchIdempotency {key: $key})
            ON CREATE SET rec.spec_id = $spec_id,
                          rec.intent_hash = $intent_hash,
                          rec.payload = $payload,
                          rec.created_at = $created_at,
                          rec.creation_token = $creation_token
            RETURN rec.payload AS payload,
                   rec.intent_hash AS intent_hash,
                   rec.creation_token = $creation_token AS created
            """,
            key=idemp_key,
            spec_id=spec_id,
            intent_hash=intent_hash,
            payload=canonical_json(persisted_snapshot.model_dump(mode="json")),
            created_at=now_iso,
            creation_token=creation_token,
        )
        idemp_rec = await idemp_res.single()
        if idemp_rec is not None and not idemp_rec["created"]:
            if idemp_rec["intent_hash"] == intent_hash:
                return ProblemSpecSnapshot.model_validate_json(idemp_rec["payload"]), True
            raise IdempotencyConflictError(
                "Idempotency key reused with altered problem spec intent."
            )

        return persisted_snapshot, False

    async def get_problem_spec(
        self,
        *,
        workspace_id: str,
        spec_id: str,
    ) -> ProblemSpecSnapshot | None:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            rec = self._mem_specs.get(spec_id)
            if rec is None or rec["group_id"] != group_id:
                return None
            return ProblemSpecSnapshot.model_validate_json(rec["payload"])

        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                """
                MATCH (s:ProblemSpec {spec_id: $spec_id, group_id: $group})
                RETURN s.payload AS payload
                LIMIT 1
                """,
                spec_id=spec_id,
                group=group_id,
            )
            record = await result.single()
            if record is None:
                return None
            return ProblemSpecSnapshot.model_validate_json(record["payload"])

    async def list_problem_specs(
        self,
        *,
        workspace_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ProblemSpecSnapshot], int]:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            matching = [rec for rec in self._mem_specs.values() if rec["group_id"] == group_id]
            matching.sort(key=lambda r: r["created_at"], reverse=True)
            total = len(matching)
            slice_items = matching[offset : offset + limit]
            items = [ProblemSpecSnapshot.model_validate_json(r["payload"]) for r in slice_items]
            return items, total

        async with self.driver.session(database=self.database) as session:
            count_res = await session.run(
                """
                MATCH (s:ProblemSpec {group_id: $group})
                RETURN count(s) AS total
                """,
                group=group_id,
            )
            count_rec = await count_res.single()
            total = int(count_rec["total"]) if count_rec else 0

            items_res = await session.run(
                """
                MATCH (s:ProblemSpec {group_id: $group})
                RETURN s.payload AS payload
                ORDER BY s.created_at DESC
                SKIP $offset LIMIT $limit
                """,
                group=group_id,
                offset=offset,
                limit=limit,
            )
            items = [
                ProblemSpecSnapshot.model_validate_json(row["payload"]) async for row in items_res
            ]
            return items, total

    # -------------------------------------------------------------------------
    # Lineage Methods (G2)
    # -------------------------------------------------------------------------

    async def register_paper_coverage(
        self,
        *,
        workspace_id: str,
        paper: PaperCoverageRecord,
    ) -> None:
        group_id = workspace_group_id(workspace_id)
        self._mem_papers[f"{group_id}:{paper.paper_id}:{paper.version}"] = {
            "group_id": group_id,
            "record": paper,
        }
        if self.driver is None:
            return
        coverage_id = self._stable_hash(
            [
                group_id,
                paper.paper_id,
                paper.version,
                paper.ingested_at,
            ]
        )
        async with self.driver.session(database=self.database) as session:
            await session.execute_write(
                self._tx_register_paper_coverage,
                group_id,
                coverage_id,
                canonical_json(paper.model_dump(mode="json")),
            )

    async def register_metadata_paper(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        request: MetadataPaperCreateRequest,
    ) -> PaperCoverageRecord:
        if self.driver is None:
            raise ResearchStoreError("Metadata registration requires durable research storage.")
        group = workspace_group_id(workspace_id)
        key = f"{group}:metadata:{idempotency_key}"
        intent_hash = self._stable_hash(request.model_dump(mode="json"))
        record = PaperCoverageRecord(
            **request.model_dump(mode="json"),
            has_html=False,
            equation_count=0,
            registered_by=actor_id,
            ingested_at=datetime.now(UTC).isoformat(),
            warnings=("html_missing", "user_supplied_metadata"),
        )

        async def persist(tx):
            result = await tx.run(
                """
                MERGE (r:ResearchIdempotency {key: $key})
                ON CREATE SET r.group_id = $group, r.intent_hash = $intent_hash,
                              r.payload = $payload
                RETURN r.group_id AS group_id, r.intent_hash AS intent_hash, r.payload AS payload
                """,
                key=key,
                group=group,
                intent_hash=intent_hash,
                payload=canonical_json(record.model_dump(mode="json")),
            )
            receipt = await result.single(strict=True)
            if receipt["group_id"] != group or receipt["intent_hash"] != intent_hash:
                raise IdempotencyConflictError("Metadata retry key conflicts with prior content.")
            saved = PaperCoverageRecord.model_validate_json(receipt["payload"])
            await self._tx_register_paper_coverage(
                tx,
                group,
                self._stable_hash([group, "metadata", key]),
                receipt["payload"],
            )
            return saved

        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(persist)

    async def list_research_sources(
        self,
        *,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
        kind: str = "equation",
        source_id: str | None = None,
    ) -> dict[str, Any]:
        if self.driver is None:
            raise ResearchStoreError("Research sources require configured evidence storage.")
        limit = max(1, min(limit, 100))
        offset = max(offset, 0)
        if kind not in {"equation", "paper", "section"}:
            raise ResearchValidationError("Unknown research source kind.")
        if source_id is not None and (not source_id or len(source_id) > 200):
            raise ResearchValidationError("Invalid research source ID.")
        group = workspace_group_id(workspace_id)
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                """
                MATCH (e:Evidence {group_id: $group})
                WHERE ($source_id IS NOT NULL AND e.uuid = $source_id
                       AND e.kind IN ['Section', 'Equation'])
                   OR ($source_id IS NULL AND e.kind = $evidence_kind)
                WITH e ORDER BY e.uuid SKIP $offset LIMIT $limit
                OPTIONAL MATCH (e)-[:EVIDENCE_RELATION {group_id: $group}]->
                               (s:Evidence {group_id: $group, kind: 'Symbol'})
                RETURN e.uuid AS uuid, e.kind AS stored_kind,
                       e.paper_id AS paper_id, e.paper_version AS version,
                       e.payload AS payload, e.source_sha256 AS source_sha256,
                collect({id:s.uuid,payload:s.payload}) AS symbols
                ORDER BY uuid
                """,
                group=group,
                evidence_kind={
                    "equation": "Equation",
                    "paper": "PaperVersion",
                    "section": "Section",
                }[kind],
                source_id=source_id,
                offset=offset,
                limit=limit + 1,
            )
            rows = [row async for row in result]
            items = []
            for row in rows[:limit]:
                payload = self._payload_object(row["payload"], label="equation")
                anchor = (
                    payload.get("anchor") if payload.get("anchor_is_source") is not False else None
                )
                symbol_refs = {
                    str(raw["id"]): symbol["notation"]
                    for raw in row["symbols"]
                    if isinstance(raw, dict)
                    and isinstance(raw.get("id"), str)
                    and isinstance(
                        (symbol := self._payload_object(raw.get("payload"), label="symbol")).get(
                            "notation"
                        ),
                        str,
                    )
                }
                symbols = sorted(set(symbol_refs.values()))
                items.append(
                    {
                        "id": row["uuid"],
                        "kind": {
                            "Equation": "equation",
                            "PaperVersion": "paper",
                            "Section": "section",
                        }.get(row["stored_kind"], str(row["stored_kind"]).lower()),
                        "paper_id": row["paper_id"],
                        "version": row["version"],
                        "latex": payload.get("latex", ""),
                        "text": payload.get("text", ""),
                        "anchor": anchor,
                        "source_span_id": (
                            make_source_span_id(str(row["uuid"]), anchor)
                            if isinstance(anchor, str) and anchor
                            else None
                        ),
                        "symbols": symbols,
                        "symbol_refs": [
                            {"id": symbol_id, "notation": notation}
                            for symbol_id, notation in sorted(symbol_refs.items())
                        ],
                        "source_hash": row["source_sha256"] or source_hash(row["payload"]),
                    }
                )
            return {"items": items, "offset": offset, "limit": limit, "has_more": len(rows) > limit}

    @staticmethod
    async def _tx_register_paper_coverage(
        tx, group_id: str, coverage_id: str, payload: str
    ) -> None:
        result = await tx.run(
            """
            MERGE (p:ResearchPaperCoverage {coverage_id: $coverage_id})
            ON CREATE SET p.group_id = $group, p.payload = $payload
            RETURN p.group_id AS group_id, p.payload AS payload
            """,
            coverage_id=coverage_id,
            group=group_id,
            payload=payload,
        )
        row = await result.single(strict=True)
        if row["group_id"] != group_id or row["payload"] != payload:
            raise IdempotencyConflictError(
                "Paper coverage identity conflicts with immutable content."
            )

    @staticmethod
    async def _read_create_receipt(
        tx,
        key: str,
        group_id: str,
        intent_hash: str,
    ) -> str | None:
        result = await tx.run(
            "MATCH (r:ResearchIdempotency {key: $key}) "
            "RETURN r.group_id AS group_id, r.intent_hash AS intent_hash, "
            "r.payload AS payload LIMIT 1",
            key=key,
        )
        row = await result.single()
        if row is None:
            return None
        if row["group_id"] != group_id or row["intent_hash"] != intent_hash:
            raise IdempotencyConflictError("Create retry key conflicts with prior content.")
        return row["payload"]

    @staticmethod
    async def _save_create_receipt(
        tx,
        key: str,
        group_id: str,
        intent_hash: str,
        payload: str,
    ) -> None:
        result = await tx.run(
            "MERGE (r:ResearchIdempotency {key: $key}) "
            "ON CREATE SET r.group_id = $group, r.intent_hash = $intent_hash, "
            "r.payload = $payload "
            "RETURN r.group_id AS group_id, r.intent_hash AS intent_hash, "
            "r.payload AS payload",
            key=key,
            group=group_id,
            intent_hash=intent_hash,
            payload=payload,
        )
        row = await result.single(strict=True)
        if (
            row["group_id"] != group_id
            or row["intent_hash"] != intent_hash
            or row["payload"] != payload
        ):
            raise IdempotencyConflictError("Create retry key conflicts with prior content.")

    async def create_lineage_assertion(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        actor_role: str,
        request: LineageAssertionCreateRequest,
        idempotency_key: str | None = None,
    ) -> LineageAssertionRecord:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            raise ResearchStoreError("Source-backed lineage requires configured research storage.")
        receipt_key = f"{group_id}:lineage-create:{idempotency_key}" if idempotency_key else None
        intent_hash = self._stable_hash(request.model_dump(mode="json"))

        async def create(tx):
            await lock_research_workspace(tx, group_id)
            if receipt_key:
                receipt = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
                if receipt is not None:
                    return LineageAssertionRecord.model_validate_json(receipt)
            source = await self._resolve_lineage_endpoint(
                tx,
                group_id=group_id,
                endpoint=request.source,
            )
            target = await self._resolve_lineage_endpoint(
                tx,
                group_id=group_id,
                endpoint=request.target,
            )
            if request.relation_type in {"citation", "revision"}:
                relation_result = await tx.run(
                    "MATCH (s:Evidence {uuid: $source_id, group_id: $group, kind: 'PaperVersion'}) "
                    "MATCH (t:Evidence {uuid: $target_id, group_id: $group, kind: 'PaperVersion'}) "
                    "RETURN s.paper_id AS source_paper, t.paper_id AS target_paper",
                    source_id=source.id,
                    target_id=target.id,
                    group=group_id,
                )
                pair = await relation_result.single(strict=True)
                same_paper = pair["source_paper"] == pair["target_paper"]
                if request.relation_type == "revision" and not same_paper:
                    raise ResearchValidationError("Revision must stay within one paper lineage.")
                if request.relation_type == "citation" and same_paper:
                    raise ResearchValidationError("Citation must connect different papers.")
            endpoint_ids = {source.id, target.id}
            evidence = tuple(
                [
                    await self._resolve_lineage_evidence(
                        tx,
                        group_id=group_id,
                        evidence=item,
                        endpoint_ids=endpoint_ids,
                    )
                    for item in request.evidence
                ]
            )
            resolved_request = request.model_copy(
                update={
                    "source": source,
                    "target": target,
                    "evidence": evidence,
                }
            )
            source_key = f"{source.kind}:{source.id}:{source.version}"
            target_key = f"{target.kind}:{target.id}:{target.version}"
            record = await self._tx_create_lineage_assertion(
                tx,
                group_id,
                workspace_id,
                actor_id,
                resolved_request,
                source_key,
                target_key,
            )
            if receipt_key:
                await self._save_create_receipt(
                    tx,
                    receipt_key,
                    group_id,
                    intent_hash,
                    canonical_json(record.model_dump(mode="json")),
                )
            return record

        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(create)

    @staticmethod
    async def _tx_create_lineage_assertion(
        tx,
        group_id: str,
        workspace_id: str,
        actor_id: str,
        request: LineageAssertionCreateRequest,
        source_key: str,
        target_key: str,
    ) -> LineageAssertionRecord:
        await lock_research_workspace(tx, group_id)
        if request.relation_type in CYCLE_PROHIBITED_RELATIONS:
            cycle_check = await tx.run(
                """
                MATCH (a:LineageAssertion {group_id: $group})
                WHERE a.relation_type IN ['revision', 'mathematical_derivation']
                RETURN a.source_key AS source, a.target_key AS target,
                       a.relation_type AS relation
                LIMIT 10001
                """,
                group=group_id,
            )
            edges = [(row["source"], row["target"], row["relation"]) async for row in cycle_check]
            if len(edges) > 10000:
                raise ResearchValidationError("Lineage cycle-check edge budget exceeded.")
            # Include rejected historical dependencies conservatively: a later
            # review must not be able to reactivate a cyclic dependency graph.
            if check_cycle(edges, source_key, target_key, request.relation_type):
                raise LineageCycleError(
                    f"Adding this {request.relation_type} assertion creates an illegal cycle."
                )

        identity_content = {
            "evidence": [item.model_dump(mode="json") for item in request.evidence],
            "description": request.description,
        }
        assertion_id = generate_assertion_id(
            workspace_id,
            request.relation_type,
            source_key,
            target_key,
            identity_content,
        )
        now_iso = datetime.now(UTC).isoformat()
        record = LineageAssertionRecord(
            assertion_id=assertion_id,
            workspace_id=workspace_id,
            relation_type=request.relation_type,
            source=request.source,
            target=request.target,
            evidence=request.evidence,
            description=request.description,
            created_by=actor_id,
            created_at=now_iso,
            status="asserted",
        )
        raw_payload = canonical_json(record.model_dump(mode="json"))

        existing_result = await tx.run(
            """
            MATCH (a:LineageAssertion {assertion_id: $assertion_id})
            RETURN a.group_id AS group_id, a.payload AS payload
            LIMIT 1
            """,
            assertion_id=assertion_id,
        )
        existing = await existing_result.single()
        if existing is not None:
            persisted = LineageAssertionRecord.model_validate_json(existing["payload"])
            scientific_fields = {"created_at", "created_by", "status", "review"}
            if existing["group_id"] != group_id or persisted.model_dump(
                exclude=scientific_fields
            ) != record.model_dump(exclude=scientific_fields):
                raise IdempotencyConflictError(
                    "Lineage assertion identity conflicts with immutable content."
                )
            return persisted

        await tx.run(
            """
            MERGE (s_ep:LineageEndpoint {key: $source_key, group_id: $group})
            MERGE (t_ep:LineageEndpoint {key: $target_key, group_id: $group})
            MERGE (a:LineageAssertion {assertion_id: $assertion_id})
            ON CREATE SET a.group_id = $group,
                          a.workspace_id = $workspace_id,
                          a.relation_type = $rel,
                          a.source_key = $source_key,
                          a.target_key = $target_key,
                          a.payload = $payload,
                          a.status = 'asserted',
                          a.created_by = $created_by,
                          a.created_at = $created_at
            MERGE (s_ep)-[:LINEAGE_EDGE {relation_type: $rel, assertion_id: $assertion_id}]->(t_ep)
            MERGE (a)-[:LINEAGE_SOURCE]->(s_ep)
            MERGE (a)-[:LINEAGE_TARGET]->(t_ep)
            """,
            source_key=source_key,
            target_key=target_key,
            group=group_id,
            assertion_id=assertion_id,
            workspace_id=workspace_id,
            rel=request.relation_type,
            payload=raw_payload,
            created_by=actor_id,
            created_at=now_iso,
        )
        return record

    async def get_lineage_assertion(
        self,
        *,
        workspace_id: str,
        assertion_id: str,
    ) -> LineageAssertionRecord | None:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            rec = self._mem_assertions.get(assertion_id)
            if rec is None or rec["group_id"] != group_id:
                return None
            return self._lineage_record_with_reviews(
                rec["payload"],
                self._mem_lineage_reviews.get(assertion_id, []),
            )

        async with self.driver.session(database=self.database) as session:
            res = await session.run(
                """
                MATCH (a:LineageAssertion {assertion_id: $assertion_id, group_id: $group})
                OPTIONAL MATCH (a)-[:HAS_REVIEW]->(r:LineageReview {group_id: $group})
                RETURN a.payload AS payload, collect(r.payload) AS reviews
                LIMIT 1
                """,
                assertion_id=assertion_id,
                group=group_id,
            )
            rec = await res.single()
            if rec is None:
                return None
            return self._lineage_record_with_reviews(rec["payload"], rec["reviews"])

    @staticmethod
    def _lineage_record_with_reviews(
        payload: str,
        review_payloads: list[str] | list[dict[str, Any]],
    ) -> LineageAssertionRecord:
        initial = LineageAssertionRecord.model_validate_json(payload)
        legacy_review = initial.review
        base = initial.model_copy(update={"status": "asserted", "review": None})
        reviews = [
            item
            if isinstance(item, LineageReview)
            else LineageReview.model_validate_json(item)
            if isinstance(item, str)
            else LineageReview.model_validate(item)
            for item in review_payloads
            if item is not None
        ]
        if legacy_review is not None and all(
            item.review_id != legacy_review.review_id for item in reviews
        ):
            reviews.append(legacy_review)
        if not reviews:
            return base
        latest = max(reviews, key=lambda item: (item.reviewed_at, item.review_id))
        return base.model_copy(update={"status": latest.decision, "review": latest})

    async def list_lineage_assertions(
        self,
        *,
        workspace_id: str,
        relation_type: LineageRelationType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LineageAssertionRecord], int]:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            matching = [
                a
                for a in self._mem_assertions.values()
                if a["group_id"] == group_id
                and (relation_type is None or a["relation_type"] == relation_type)
            ]
            matching.sort(key=lambda r: r["created_at"], reverse=True)
            total = len(matching)
            items = [
                self._lineage_record_with_reviews(
                    item["payload"],
                    self._mem_lineage_reviews.get(item["assertion_id"], []),
                )
                for item in matching[offset : offset + limit]
            ]
            return items, total

        async with self.driver.session(database=self.database) as session:
            count_res = await session.run(
                """
                MATCH (a:LineageAssertion {group_id: $group})
                WHERE $rel IS NULL OR a.relation_type = $rel
                RETURN count(a) AS total
                """,
                group=group_id,
                rel=relation_type,
            )
            count_rec = await count_res.single()
            total = int(count_rec["total"]) if count_rec else 0

            items_res = await session.run(
                """
                MATCH (a:LineageAssertion {group_id: $group})
                WHERE $rel IS NULL OR a.relation_type = $rel
                OPTIONAL MATCH (a)-[:HAS_REVIEW]->(r:LineageReview {group_id: $group})
                RETURN a.payload AS payload, collect(r.payload) AS reviews,
                       a.created_at AS created_at, a.assertion_id AS assertion_id
                ORDER BY created_at DESC, assertion_id ASC
                SKIP $offset LIMIT $limit
                """,
                group=group_id,
                rel=relation_type,
                offset=offset,
                limit=limit,
            )
            items = [
                self._lineage_record_with_reviews(row["payload"], row["reviews"])
                async for row in items_res
            ]
            return items, total

    async def review_lineage_assertion(
        self,
        *,
        workspace_id: str,
        assertion_id: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: str,
        notes: str = "",
        idempotency_key: str = "",
    ) -> LineageAssertionRecord:
        group_id = workspace_group_id(workspace_id)
        if not idempotency_key:
            raise ResearchValidationError("An idempotency key is required for lineage review.")
        if self.driver is None:
            raise ResearchStoreError("Source-backed lineage requires configured research storage.")
        review_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{group_id}/lineage-review/{assertion_id}/{reviewer_id}/{idempotency_key}",
            )
        )
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_review_lineage_assertion,
                group_id,
                assertion_id,
                reviewer_id,
                reviewer_role,
                decision,
                notes,
                review_id,
            )

    @staticmethod
    async def _tx_review_lineage_assertion(
        tx,
        group_id: str,
        assertion_id: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: str,
        notes: str,
        review_id: str,
    ) -> LineageAssertionRecord:
        await lock_research_workspace(tx, group_id)
        res = await tx.run(
            """
            MATCH (a:LineageAssertion {assertion_id: $assertion_id, group_id: $group})
            RETURN a.payload AS payload
            LIMIT 1
            """,
            assertion_id=assertion_id,
            group=group_id,
        )
        assertion = await res.single()
        if assertion is None:
            raise ResearchReferenceNotFoundError(
                f"Assertion '{assertion_id}' not found in this workspace."
            )

        existing_res = await tx.run(
            """
            MATCH (r:LineageReview {review_id: $review_id})
            RETURN r.group_id AS group_id, r.payload AS payload
            LIMIT 1
            """,
            review_id=review_id,
        )
        existing = await existing_res.single()
        if existing is not None:
            stored = LineageReview.model_validate_json(existing["payload"])
            if (
                existing["group_id"] != group_id
                or stored.assertion_id != assertion_id
                or stored.reviewer_id != reviewer_id
                or stored.reviewer_role != reviewer_role
                or stored.decision != decision
                or stored.notes != notes
            ):
                raise IdempotencyConflictError(
                    "Lineage review idempotency key conflicts with prior content."
                )
            return Neo4jResearchStore._lineage_record_with_reviews(
                assertion["payload"],
                [stored.model_dump(mode="json")],
            )

        review = LineageReview(
            review_id=review_id,
            assertion_id=assertion_id,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision=decision,
            notes=notes,
            reviewed_at=datetime.now(UTC).isoformat(),
        )
        raw_review = canonical_json(review.model_dump(mode="json"))

        await tx.run(
            """
            MATCH (a:LineageAssertion {assertion_id: $assertion_id, group_id: $group})
            CREATE (r:LineageReview {review_id: $review_id, group_id: $group,
                                    decision: $decision,
                                    payload: $payload, reviewed_at: $reviewed_at})
            CREATE (a)-[:HAS_REVIEW]->(r)
            """,
            assertion_id=assertion_id,
            group=group_id,
            payload=raw_review,
            review_id=review_id,
            reviewed_at=review.reviewed_at,
            decision=review.decision,
        )
        return Neo4jResearchStore._lineage_record_with_reviews(
            assertion["payload"],
            [review.model_dump(mode="json")],
        )

    async def get_lineage_coverage(self, *, workspace_id: str) -> LineageCoverageResponse:
        group_id = workspace_group_id(workspace_id)
        now_iso = datetime.now(UTC).isoformat()

        if self.driver is None:
            records = [p["record"] for p in self._mem_papers.values() if p["group_id"] == group_id]
            gaps = tuple(sorted([p.paper_id for p in records if not p.has_html]))
            return LineageCoverageResponse(
                workspace_id=workspace_id,
                indexed_papers=tuple(records),
                coverage_gaps=gaps,
                timestamp=now_iso,
            )

        async with self.driver.session(database=self.database) as session:
            source_rows = await session.run(
                """
                MATCH (p:Evidence {group_id: $group, kind: 'PaperVersion'})
                OPTIONAL MATCH (e:Evidence {group_id: $group, kind: 'Equation'})
                WHERE e.paper_id = p.paper_id AND e.paper_version = p.paper_version
                RETURN p.paper_id AS paper_id, p.paper_version AS version,
                       p.payload AS payload, p.created_at AS ingested_at,
                       count(DISTINCT e) AS eq_count
                """,
                group=group_id,
            )
            by_identity: dict[tuple[str, int | None], PaperCoverageRecord] = {}
            async for row in source_rows:
                payload = self._payload_object(str(row["payload"]), label="paper")
                paper_id = str(row["paper_id"] or payload.get("paper_id") or "")
                if not paper_id:
                    continue
                version = int(row["version"]) if row["version"] is not None else None
                by_identity[(paper_id, version)] = PaperCoverageRecord(
                    paper_id=paper_id,
                    version=version,
                    title=str(payload.get("title") or paper_id),
                    has_html=True,
                    first_publication_at=payload.get("first_publication_at"),
                    version_published_at=payload.get("version_published_at"),
                    venue_published_at=payload.get("venue_published_at"),
                    ingested_at=str(row["ingested_at"] or now_iso),
                    equation_count=int(row["eq_count"] or 0),
                    warnings=tuple(payload.get("metadata_warnings", [])),
                )
            coverage_rows = await session.run(
                """
                MATCH (p:ResearchPaperCoverage {group_id: $group})
                RETURN p.payload AS payload ORDER BY p.coverage_id ASC
                """,
                group=group_id,
            )
            async for row in coverage_rows:
                record = PaperCoverageRecord.model_validate_json(row["payload"])
                identity = (record.paper_id, record.version)
                existing = by_identity.get(identity)
                # Imported immutable evidence takes precedence over metadata-only notes.
                if existing is None or (
                    not existing.has_html and record.ingested_at > existing.ingested_at
                ):
                    by_identity[identity] = record
            records = sorted(
                by_identity.values(),
                key=lambda item: (item.paper_id, item.version or 0),
            )
            gaps = tuple(sorted([p.paper_id for p in records if not p.has_html]))
            return LineageCoverageResponse(
                workspace_id=workspace_id,
                indexed_papers=tuple(records),
                coverage_gaps=gaps,
                timestamp=now_iso,
            )

    async def traverse_lineage(
        self,
        *,
        workspace_id: str,
        endpoint_kind: str,
        endpoint_id: str,
        endpoint_version: int | None = None,
        direction: str = "descendants",
        relation_type: LineageRelationType | None = None,
        max_depth: int = 8,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        if (
            direction not in {"descendants", "ancestors"}
            or type(max_depth) is not int
            or not 0 <= max_depth <= MAX_TRAVERSAL_DEPTH
            or type(max_nodes) is not int
            or not 1 <= max_nodes <= MAX_TRAVERSAL_NODES
        ):
            raise ResearchValidationError("Invalid traversal direction or resource limits.")
        endpoint = LineageEndpoint(kind=endpoint_kind, id=endpoint_id, version=endpoint_version)
        if self.driver is None:
            raise ResearchStoreError(
                "Source-backed traversal requires configured research storage."
            )
        group_id = workspace_group_id(workspace_id)
        limits_reached: set[str] = set()
        collected_edges: list[LineageAssertionRecord] = []
        # Edge budget bounds dense graphs independently of node/depth ceilings.
        max_edges = 1000
        scanned_edges = 0
        async with self.driver.session(database=self.database) as session:
            endpoint = await self._resolve_lineage_endpoint(
                session,
                group_id=group_id,
                endpoint=endpoint,
            )
            start_key = f"{endpoint.kind}:{endpoint.id}:{endpoint.version}"
            visited_nodes = {start_key}
            frontier = [start_key]
            for depth in range(max_depth + 1):
                if not frontier:
                    break
                result = await session.run(
                    """
                    MATCH (a:LineageAssertion {group_id: $group})
                    WHERE ($rel IS NULL OR a.relation_type = $rel)
                      AND CASE WHEN $direction = 'descendants'
                          THEN a.source_key IN $frontier ELSE a.target_key IN $frontier END
                    OPTIONAL MATCH (a)-[:HAS_REVIEW]->(r:LineageReview {group_id: $group})
                    WITH a, r ORDER BY r.reviewed_at DESC, r.review_id DESC
                    WITH a, head(collect(r)) AS latest
                    WHERE coalesce(latest.decision, a.status) <> 'rejected'
                    RETURN a.payload AS payload, latest.payload AS review
                    ORDER BY a.assertion_id ASC LIMIT $limit
                    """,
                    group=group_id,
                    rel=relation_type,
                    direction=direction,
                    frontier=frontier,
                    limit=max_edges - scanned_edges + 1,
                )
                rows = [row async for row in result]
                if depth == max_depth:
                    if rows:
                        limits_reached.add("max_depth")
                    break
                frontier = []
                for row in rows:
                    if scanned_edges == max_edges:
                        limits_reached.add("max_edges")
                        break
                    scanned_edges += 1
                    edge = self._lineage_record_with_reviews(
                        row["payload"],
                        [row["review"]] if row["review"] else [],
                    )
                    if edge.status == "rejected":
                        continue
                    next_endpoint = edge.target if direction == "descendants" else edge.source
                    next_node = f"{next_endpoint.kind}:{next_endpoint.id}:{next_endpoint.version}"
                    if next_node not in visited_nodes:
                        if len(visited_nodes) == max_nodes:
                            limits_reached.add("max_nodes")
                            continue
                        visited_nodes.add(next_node)
                        frontier.append(next_node)
                    collected_edges.append(edge)
                if "max_edges" in limits_reached:
                    break

        return {
            "root": start_key,
            "direction": direction,
            "nodes": sorted(visited_nodes),
            "edges": [e.model_dump(mode="json") for e in collected_edges],
            "truncated": bool(limits_reached),
            "limits_reached": sorted(limits_reached),
            "limits": {"max_depth": max_depth, "max_nodes": max_nodes, "max_edges": max_edges},
        }

    # -------------------------------------------------------------------------
    # Compatibility Methods (G3 / T5)
    # -------------------------------------------------------------------------

    @staticmethod
    def _compatibility_reviews(
        review_payloads: list[str] | list[dict[str, Any]],
    ) -> list[CompatibilityReview]:
        return [
            CompatibilityReview.model_validate_json(item)
            if isinstance(item, str)
            else CompatibilityReview.model_validate(item)
            for item in review_payloads
            if item is not None
        ]

    @classmethod
    def _effective_compatibility(
        cls,
        mapping: PortMapping,
        initial: CompatibilityAssessment,
        dependencies: tuple[ResolvedDependency, ...],
        review_payloads: list[str] | list[dict[str, Any]],
    ) -> tuple[PortMapping, CompatibilityAssessment]:
        reviews = cls._compatibility_reviews(review_payloads)
        if initial.policy_version != COMPATIBILITY_POLICY_VERSION:
            return mapping, initial.model_copy(
                update={
                    "freshness": "stale",
                    "usable": False,
                    "reasons": tuple(sorted(set((*initial.reasons, "policy_version_changed")))),
                }
            )
        latest = (
            max(reviews, key=lambda item: (item.reviewed_at, item.review_id)) if reviews else None
        )
        effective_mapping = mapping
        binding_approved = (
            latest is not None
            and latest.decision == "reviewed"
            and latest.dependency_fingerprint == initial.dependency_fingerprint
        )
        if binding_approved:
            effective_mapping = mapping.model_copy(update={"explicit_binding_reviewed": True})
        assessment = assess_mapping(
            effective_mapping,
            dependencies,
            expected_fingerprint=initial.dependency_fingerprint,
        )
        if latest is not None and binding_approved:
            assessment = assessment.model_copy(
                update={
                    "reasons": tuple(sorted(set((*assessment.reasons, "human_binding_approved")))),
                    "evidence_refs": (latest.review_id,),
                }
            )
        if latest is not None and latest.decision == "rejected":
            assessment = assessment.model_copy(
                update={
                    "status": "incompatible",
                    "usable": False,
                    "reasons": tuple(sorted(set((*assessment.reasons, "human_review_rejected")))),
                }
            )
        return effective_mapping, assessment

    async def _current_compatibility_dependencies(
        self,
        session,
        *,
        group_id: str,
        mapping: PortMapping,
    ) -> tuple[ResolvedDependency, ...]:
        _, producer = await self._resolve_compatibility_port(
            session,
            group_id=group_id,
            port=mapping.producer_port,
        )
        _, consumer = await self._resolve_compatibility_port(
            session,
            group_id=group_id,
            port=mapping.consumer_port,
        )
        return producer, consumer

    async def create_compatibility_mapping(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        actor_role: str,
        request: PortMappingCreateRequest,
        idempotency_key: str | None = None,
        resolved_dependencies: tuple[ResolvedDependency, ...] | None = None,
        evidence: tuple[CompatibilityEvidence, ...] = (),
    ) -> tuple[PortMapping, CompatibilityAssessment]:
        """Create an immutable mapping from server-resolved source contracts only."""
        del actor_id, actor_role
        if resolved_dependencies or evidence:
            raise ResearchValidationError(
                "Compatibility dependencies and evidence are server-resolved."
            )
        if request.conversion_rule is not None:
            raise ResearchValidationError(
                "Conversion rules require the Sprint 5B DSL and are not executable in 5A."
            )
        if self.driver is None:
            raise ResearchStoreError(
                "Source-backed compatibility requires configured research storage."
            )

        group_id = workspace_group_id(workspace_id)
        receipt_key = (
            f"{group_id}:compatibility-create:{idempotency_key}" if idempotency_key else None
        )
        intent_hash = self._stable_hash(request.model_dump(mode="json"))

        async def create(tx):
            await lock_research_workspace(tx, group_id)
            if receipt_key:
                receipt = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
                if receipt is not None:
                    mapping_payload, assessment_payload = json.loads(receipt)
                    return (
                        PortMapping.model_validate(mapping_payload),
                        CompatibilityAssessment.model_validate(assessment_payload),
                    )
            producer, producer_dependency = await self._resolve_compatibility_port(
                tx,
                group_id=group_id,
                port=request.producer_port,
            )
            consumer, consumer_dependency = await self._resolve_compatibility_port(
                tx,
                group_id=group_id,
                port=request.consumer_port,
            )
            mapping_id = generate_mapping_id(
                workspace_id, producer, consumer, (producer_dependency, consumer_dependency)
            )
            mapping = PortMapping(
                mapping_id=mapping_id,
                workspace_id=workspace_id,
                producer_port=producer,
                consumer_port=consumer,
                explicit_binding_reviewed=False,
                conversion_rule=None,
                embedding_similarity=request.embedding_similarity,
            )
            assessment = assess_mapping(mapping, (producer_dependency, consumer_dependency))
            saved = await self._tx_create_compatibility_mapping(
                tx,
                group_id,
                workspace_id,
                mapping,
                assessment,
            )
            if receipt_key:
                await self._save_create_receipt(
                    tx,
                    receipt_key,
                    group_id,
                    intent_hash,
                    canonical_json([item.model_dump(mode="json") for item in saved]),
                )
            return saved

        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(create)

    @staticmethod
    async def _tx_create_compatibility_mapping(
        tx,
        group_id: str,
        workspace_id: str,
        mapping: PortMapping,
        assessment: CompatibilityAssessment,
    ) -> tuple[PortMapping, CompatibilityAssessment]:
        raw_mapping = canonical_json(mapping.model_dump(mode="json"))
        raw_assessment = canonical_json(assessment.model_dump(mode="json"))
        existing_result = await tx.run(
            """
            MATCH (m:CompatibilityMapping {mapping_id: $mapping_id})
            RETURN m.group_id AS group_id, m.mapping_payload AS mapping_payload,
                   m.assessment_payload AS assessment_payload
            LIMIT 1
            """,
            mapping_id=mapping.mapping_id,
        )
        existing = await existing_result.single()
        if existing is not None:
            if existing["group_id"] != group_id or existing["mapping_payload"] != raw_mapping:
                raise IdempotencyConflictError(
                    "Compatibility mapping identity conflicts with immutable content."
                )
            return (
                PortMapping.model_validate_json(existing["mapping_payload"]),
                CompatibilityAssessment.model_validate_json(existing["assessment_payload"]),
            )
        await tx.run(
            """
            CREATE (m:CompatibilityMapping {mapping_id: $mapping_id, group_id: $group,
                                             workspace_id: $workspace_id,
                                             mapping_payload: $mapping_payload,
                                             assessment_payload: $assessment_payload})
            """,
            mapping_id=mapping.mapping_id,
            group=group_id,
            workspace_id=workspace_id,
            mapping_payload=raw_mapping,
            assessment_payload=raw_assessment,
        )
        return mapping, assessment

    async def get_compatibility_mapping(
        self,
        *,
        workspace_id: str,
        mapping_id: str,
    ) -> tuple[PortMapping, CompatibilityAssessment] | None:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            raise ResearchStoreError(
                "Current compatibility dependencies require configured research storage."
            )

        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                """
                MATCH (m:CompatibilityMapping {mapping_id: $mapping_id, group_id: $group})
                OPTIONAL MATCH (m)-[:HAS_REVIEW]->(r:CompatibilityReview {group_id: $group})
                RETURN m.mapping_payload AS mapping_payload,
                       m.assessment_payload AS assessment_payload,
                       collect(r.payload) AS reviews
                LIMIT 1
                """,
                mapping_id=mapping_id,
                group=group_id,
            )
            row = await result.single()
            if row is None:
                return None
            mapping = PortMapping.model_validate_json(row["mapping_payload"])
            dependencies = await self._current_compatibility_dependencies(
                session,
                group_id=group_id,
                mapping=mapping,
            )
            return self._effective_compatibility(
                mapping,
                CompatibilityAssessment.model_validate_json(row["assessment_payload"]),
                dependencies,
                row["reviews"],
            )

    async def evaluate_candidate_admission(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        request: AdmissionEvaluationRequest,
        idempotency_key: str,
    ) -> AdmissionEvaluationResponse:
        """Persist one immutable V0 decision; current D2 records fail closed."""
        if self.driver is None:
            raise ResearchStoreError("Admission decisions require durable research storage.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        group_id = workspace_group_id(workspace_id)
        receipt_key = f"{group_id}:admission:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
        intent_hash = self._stable_hash(
            [workspace_id, candidate_id, actor_id, request.model_dump(mode="json")]
        )
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_evaluate_candidate_admission,
                group_id,
                workspace_id,
                candidate_id,
                actor_id,
                request,
                receipt_key,
                intent_hash,
            )

    async def _tx_evaluate_candidate_admission(
        self,
        tx,
        group_id: str,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        request: AdmissionEvaluationRequest,
        receipt_key: str,
        intent_hash: str,
    ) -> AdmissionEvaluationResponse:
        await lock_research_workspace(tx, group_id)
        receipt = await Neo4jResearchStore._read_create_receipt(
            tx, receipt_key, group_id, intent_hash
        )
        if receipt is not None:
            saved = json.loads(receipt)
            return AdmissionEvaluationResponse(
                decision=saved["decision"],
                replayed=True,
            )

        candidate_result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
        )
        candidate_row = await candidate_result.single()
        if candidate_row is None:
            raise ResearchReferenceNotFoundError("Candidate was not found in this workspace.")
        candidate = CompiledCandidate.model_validate_json(candidate_row["payload"])
        spec_result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=candidate.problem_spec_id,
            group=group_id,
        )
        spec_row = await spec_result.single()
        if spec_row is None:
            raise ResearchReferenceNotFoundError(
                "Candidate ProblemSpec was not found in this workspace."
            )
        spec = ProblemSpecSnapshot.model_validate_json(spec_row["payload"])

        check_result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group})-"
            "[:HAS_CANDIDATE_CHECK]->(r:ResearchCandidateCheck {group_id:$group}) "
            "RETURN r.check_id AS check_id, r.payload AS payload "
            "ORDER BY r.created_at DESC LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
        )
        check_row = await check_result.single()
        candidate_check = (
            CandidateCheckResult.model_validate_json(check_row["payload"])
            if check_row is not None
            else None
        )
        if candidate_check is not None and (
            candidate_check.check_id != check_row["check_id"]
            or candidate_check.candidate_id != candidate.candidate_id
            or candidate_check.workspace_id != workspace_id
            or candidate_check.candidate_hash != candidate.content_hash
        ):
            raise ResearchStoreError("Stored candidate check has an invalid scope or identity.")

        mapping_freshness, mapping_usable = await self._candidate_mapping_admission_status(
            tx,
            group_id=group_id,
            candidate=candidate,
        )

        # The synthetic fixture is diagnostic, not paper-artifact or experiment
        # evidence; V0 must not promote it. Runtime verification, protocol, and
        # quota are still unavailable and stay fail-closed.
        context = AdmissionContext(
            candidate=candidate,
            spec=spec,
            verification=candidate_check.vector if candidate_check else None,
            discharged_obligation_names=tuple(sorted(discharged_obligations(candidate_check))),
            symbolic_result_ids=(candidate_check.check_id,) if candidate_check else (),
            mapping_freshness=mapping_freshness,
            mapping_usable=mapping_usable,
        )
        protocol_context = await self._tx_protocol_admission_context(
            tx, group_id, candidate, spec, candidate_check,
            mapping_freshness=mapping_freshness, mapping_usable=mapping_usable,
        )
        if protocol_context is not None:
            context = protocol_context
        decision = decide_admission(
            context,
            action=request.action,
            claim_scope=request.claim_scope or "mathematical",
            actor_id=actor_id,
        )
        replay_input = make_admission_replay_input(context, decision)
        payload = canonical_json(decision.model_dump(mode="json"))
        replay_payload = canonical_json(replay_input.model_dump(mode="json"))
        await tx.run(
            "CREATE (d:ResearchAdmissionDecision {decision_id:$decision_id, "
            "group_id:$group, workspace_id:$workspace, candidate_id:$candidate_id, "
            "policy_version:$policy_version, payload:$payload, "
            "replay_input_payload:$replay_payload, created_at:$created_at})",
            decision_id=decision.decision_id,
            group=group_id,
            workspace=workspace_id,
            candidate_id=candidate_id,
            policy_version=decision.policy_version,
            payload=payload,
            replay_payload=replay_payload,
            created_at=decision.decided_at.isoformat(),
        )
        await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "MATCH (d:ResearchAdmissionDecision {decision_id:$decision_id, group_id:$group}) "
            "CREATE (c)-[:HAS_ADMISSION_DECISION]->(d)",
            candidate_id=candidate_id,
            decision_id=decision.decision_id,
            group=group_id,
        )
        response_payload = canonical_json({"decision": decision.model_dump(mode="json")})
        await Neo4jResearchStore._save_create_receipt(
            tx, receipt_key, group_id, intent_hash, response_payload
        )
        return AdmissionEvaluationResponse(decision=decision, replayed=False)

    async def _tx_protocol_admission_context(
        self, tx, group_id, candidate, spec, check, *,
        mapping_freshness, mapping_usable, result_id=None,
    ) -> AdmissionContext | None:
        if check is None or len(candidate.parents) != 1:
            return None
        row = await (await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id,group_id:$group})-"
            "[:HAS_IMPLEMENTATION_BINDING]->(b:ResearchImplementationBinding {group_id:$group})-"
            "[:PRODUCED_EXPERIMENT_RESULT]->(r:ResearchExperimentResult {group_id:$group}) "
            "WHERE ($result_id IS NULL AND r.evaluation_role='search') OR r.result_id=$result_id "
            "RETURN b.payload AS binding,r.payload AS result "
            "ORDER BY r.created_at DESC,r.result_id DESC LIMIT 1",
            candidate_id=candidate.candidate_id, group=group_id, result_id=result_id,
        )).single()
        if row is None:
            return None
        parent_row = await (await tx.run(
            "MATCH (p:ResearchCandidate {candidate_id:$id,group_id:$group}) "
            "RETURN p.payload AS payload", id=candidate.parents[0].entity_id, group=group_id,
        )).single()
        if parent_row is None:
            raise ResearchStoreError("Protocol evidence parent is missing.")
        result = ResearchCaseReceipt.model_validate_json(row["result"])
        binding = ImplementationBindingReceipt.model_validate_json(row["binding"])
        review = await (await tx.run(
            "MATCH (v:ResearchProtocolReview {group_id:$group,scope_key:$scope_key}) "
            "RETURN v.decision AS decision,v.review_id AS review_id "
            "ORDER BY v.reviewed_at DESC,v.review_id DESC LIMIT 1",
            group=group_id, scope_key=protocol_scope_key(binding),
        )).single()
        return protocol_admission_context(
            candidate, spec, CompiledCandidate.model_validate_json(parent_row["payload"]),
            check, binding, result,
            reviewed=review is not None and review["decision"] == "accept_protocol_scope",
            review_id=review["review_id"] if review is not None else None,
            mapping_freshness=mapping_freshness, mapping_usable=mapping_usable,
            # Eligibility does not spend quota; execution reserves it in ResearchQueue.
            quota_available=True,
        )

    async def review_research_case(
        self, *, workspace_id: str, candidate_id: str, actor_id: str, actor_role: str,
        idempotency_key: str, request: ResearchCaseReviewRequest,
    ) -> dict[str, object]:
        self._validate_evolution_write(actor_id, idempotency_key)
        if actor_role not in {"researcher", "reviewer", "admin"}:
            raise ResearchAuthorizationError("Only a human may review protocol scope.")
        if self.driver is None:
            raise ResearchStoreError("Protocol reviews require durable storage.")
        group = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        receipt_key = f"{group}:protocol-review:{key_hash}"
        intent = self._stable_hash([candidate_id, actor_id, request.model_dump(mode="json")])

        async def write(tx):
            await lock_research_workspace(tx, group)
            saved = await self._read_create_receipt(tx, receipt_key, group, intent)
            if saved is not None:
                return json.loads(saved)
            row = await (await tx.run(
                "MATCH (c:ResearchCandidate {candidate_id:$candidate,group_id:$group})-"
                "[:HAS_EXPERIMENT_RESULT]->(r:ResearchExperimentResult {result_id:$result,"
                "group_id:$group,evaluation_role:'search'}) "
                "MATCH (c)-[:HAS_CANDIDATE_CHECK]->(k:ResearchCandidateCheck {group_id:$group}) "
                "MATCH (c)-[:HAS_IMPLEMENTATION_BINDING]->(b:ResearchImplementationBinding "
                "{group_id:$group})-[:PRODUCED_EXPERIMENT_RESULT]->(r) "
                "RETURN r.payload AS result,k.payload AS check,b.payload AS binding "
                "ORDER BY k.created_at DESC LIMIT 1",
                candidate=candidate_id, result=request.result_id, group=group,
            )).single()
            if row is None:
                raise ResearchReferenceNotFoundError(
                    "Scoped search result or checker was not found."
                )
            result = ResearchCaseReceipt.model_validate_json(row["result"])
            check = CandidateCheckResult.model_validate_json(row["check"])
            binding = ImplementationBindingReceipt.model_validate_json(row["binding"])
            if result.evaluation_role != "search" or check.candidate_hash != result.candidate_hash:
                raise ResearchValidationError("Protocol review evidence does not match.")
            payload = {
                "review_id": "prv_" + self._stable_hash([receipt_key, intent])[:32],
                "candidate_id": candidate_id, "result_id": result.result_id,
                "check_id": check.check_id, "candidate_hash": result.candidate_hash,
                "problem_spec_hash": result.problem_spec_hash,
                "reviewer_id": actor_id, "reviewer_role": actor_role,
                "decision": request.decision, "notes": request.notes,
                "scope": "synthetic_operator_only_no_product_claim",
                "scope_key": protocol_scope_key(binding),
                "authorization": "bounded_parameter_search_for_this_frozen_feature_map_family",
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
            payload = ResearchProtocolReview.model_validate(payload).model_dump(mode="json")
            await tx.run(
                "MATCH (r:ResearchExperimentResult {result_id:$result,group_id:$group}) "
                "CREATE (v:ResearchProtocolReview {review_id:$review_id,group_id:$group,"
                "check_id:$check_id,scope_key:$scope_key,decision:$decision,"
                "reviewed_at:$reviewed_at,payload:$payload}) "
                "CREATE (r)-[:HAS_PROTOCOL_REVIEW]->(v)",
                result=result.result_id, group=group, review_id=payload["review_id"],
                check_id=check.check_id, decision=request.decision,
                scope_key=payload["scope_key"],
                reviewed_at=payload["reviewed_at"], payload=canonical_json(payload),
            )
            await self._save_create_receipt(tx, receipt_key, group, intent, canonical_json(payload))
            return payload

        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(write)

    async def verify_candidate(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        idempotency_key: str,
    ) -> CandidateCheckResponse:
        """Resolve immutable candidate parents, run V1, and append its receipt."""
        if self.driver is None:
            raise ResearchStoreError("Candidate verification requires durable research storage.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        receipt_key = f"{group_id}:candidate-check:{CANDIDATE_CHECKER_VERSION}:{key_hash}"
        intent_hash = self._stable_hash([workspace_id, candidate_id, CANDIDATE_CHECKER_VERSION])
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_verify_candidate,
                group_id,
                workspace_id,
                candidate_id,
                receipt_key,
                intent_hash,
            )

    async def _tx_verify_candidate(
        self,
        tx,
        group_id: str,
        workspace_id: str,
        candidate_id: str,
        receipt_key: str,
        intent_hash: str,
    ) -> CandidateCheckResponse:
        await lock_research_workspace(tx, group_id)
        receipt = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
        if receipt is not None:
            saved = json.loads(receipt)
            return CandidateCheckResponse(check=saved["check"], replayed=True)

        result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
        )
        row = await result.single()
        if row is None:
            raise ResearchReferenceNotFoundError("Candidate was not found in this workspace.")
        candidate = CompiledCandidate.model_validate_json(row["payload"])
        parent_candidate = None
        if candidate.operator == "lower_mixture_to_concatenation":
            parent_result = await tx.run(
                "MATCH (p:ResearchCandidate {candidate_id:$parent_id, group_id:$group}) "
                "RETURN p.payload AS payload LIMIT 1",
                parent_id=candidate.parents[0].entity_id if candidate.parents else "",
                group=group_id,
            )
            parent_row = await parent_result.single()
            if parent_row is None:
                raise ResearchReferenceNotFoundError("Candidate's mixture parent was not found.")
            parent_candidate = CompiledCandidate.model_validate_json(parent_row["payload"])

        check = verify_compiled_candidate(candidate, parent_candidate=parent_candidate)
        payload = canonical_json(check.model_dump(mode="json"))
        existing_result = await tx.run(
            "MATCH (r:ResearchCandidateCheck {check_id:$check_id}) "
            "RETURN r.group_id AS group_id, r.payload AS payload LIMIT 1",
            check_id=check.check_id,
        )
        existing = await existing_result.single()
        if existing is not None:
            saved_check = CandidateCheckResult.model_validate_json(existing["payload"])
            if existing["group_id"] != group_id or saved_check.model_dump(
                exclude={"created_at"}
            ) != check.model_dump(exclude={"created_at"}):
                raise IdempotencyConflictError(
                    "Candidate check identity conflicts with stored data."
                )
            check = saved_check
            payload = existing["payload"]
        else:
            await tx.run(
                "CREATE (r:ResearchCandidateCheck {check_id:$check_id, group_id:$group, "
                "workspace_id:$workspace_id, candidate_id:$candidate_id, "
                "checker_version:$checker_version, payload:$payload, created_at:$created_at})",
                check_id=check.check_id,
                group=group_id,
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                checker_version=check.checker_version,
                payload=payload,
                created_at=check.created_at.isoformat(),
            )
            await tx.run(
                "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
                "MATCH (r:ResearchCandidateCheck {check_id:$check_id, group_id:$group}) "
                "MERGE (c)-[:HAS_CANDIDATE_CHECK]->(r)",
                candidate_id=candidate_id,
                check_id=check.check_id,
                group=group_id,
            )
        response_payload = canonical_json({"check": check.model_dump(mode="json")})
        await self._save_create_receipt(tx, receipt_key, group_id, intent_hash, response_payload)
        return CandidateCheckResponse(check=check, replayed=False)

    async def _candidate_mapping_admission_status(
        self,
        tx,
        *,
        group_id: str,
        candidate: CompiledCandidate,
    ) -> tuple[str, bool]:
        """Re-resolve a candidate's frozen mapping and all source parents."""
        if candidate.mapping_id is None:
            return "unknown", False
        result = await tx.run(
            "MATCH (m:CompatibilityMapping {mapping_id:$mapping_id, group_id:$group}) "
            "OPTIONAL MATCH (m)-[:HAS_REVIEW]->(r:CompatibilityReview {group_id:$group}) "
            "RETURN m.mapping_payload AS mapping_payload, "
            "m.assessment_payload AS assessment_payload, collect(r.payload) AS reviews LIMIT 1",
            mapping_id=candidate.mapping_id,
            group=group_id,
        )
        row = await result.single()
        if row is None:
            return "unknown", False
        mapping = PortMapping.model_validate_json(row["mapping_payload"])
        try:
            dependencies = await self._current_compatibility_dependencies(
                tx,
                group_id=group_id,
                mapping=mapping,
            )
        except (ResearchReferenceNotFoundError, ResearchValidationError):
            return "stale", False
        effective_mapping, assessment = self._effective_compatibility(
            mapping,
            CompatibilityAssessment.model_validate_json(row["assessment_payload"]),
            dependencies,
            row["reviews"],
        )

        root_candidate = candidate
        if candidate.operator == "lower_mixture_to_concatenation":
            parent_id = candidate.parents[0].entity_id
            parent_result = await tx.run(
                "MATCH (p:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
                "RETURN p.payload AS payload LIMIT 1",
                candidate_id=parent_id,
                group=group_id,
            )
            parent_row = await parent_result.single()
            if parent_row is None:
                return "stale", False
            root_candidate = CompiledCandidate.model_validate_json(parent_row["payload"])
            _verify_candidate_identity(root_candidate)

        if (
            root_candidate.operator != "mix_positive_feature_maps"
            or len(root_candidate.parents) != 3
        ):
            return assessment.freshness, False
        expected_sources = tuple(
            (item.entity_id, item.version, item.content_hash) for item in root_candidate.parents[1:]
        )
        current_sources = tuple(
            (item.entity_id, item.version, item.content_hash) for item in dependencies
        )
        try:
            current_target, _ = await self._resolve_compiler_target(
                tx,
                group_id=group_id,
                target_id=root_candidate.parents[0].entity_id,
            )
        except (ResearchReferenceNotFoundError, ResearchValidationError):
            return "stale", False
        usable = (
            candidate.mapping_id == mapping.mapping_id
            and root_candidate.mapping_id == mapping.mapping_id
            and effective_mapping.explicit_binding_reviewed
            and assessment.status == "compatible"
            and assessment.usable
            and expected_sources == current_sources
            and current_target == root_candidate.parents[0]
        )
        return assessment.freshness, usable

    async def append_candidate_numerical_fixture(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        idempotency_key: str,
        result: NumericalFixtureReceipt,
    ) -> NumericalFixtureReceiptResponse:
        """Append a bound synthetic V2 receipt; never authorize execution or admission."""
        if self.driver is None:
            raise ResearchStoreError("Numerical fixture receipts require durable research storage.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        result = NumericalFixtureReceipt.model_validate(result.model_dump(mode="python"))
        group_id, receipt_key, intent_hash = self.numerical_fixture_receipt_identity(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            result=result,
        )
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_append_candidate_numerical_fixture,
                group_id,
                workspace_id,
                candidate_id,
                actor_id,
                receipt_key,
                intent_hash,
                result,
            )

    def numerical_fixture_receipt_identity(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        idempotency_key: str,
        result: NumericalFixtureReceipt,
    ) -> tuple[str, str, str]:
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        receipt_key = f"{group_id}:numerical-fixture:{key_hash}"
        intent_hash = self._stable_hash(
            [workspace_id, candidate_id, actor_id, result.run_id, result.result_hash]
        )
        return group_id, receipt_key, intent_hash

    async def prepare_candidate_numerical_fixture(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        seed: int,
    ) -> tuple[CompiledCandidate, ProblemSpecSnapshot, CompiledCandidate | None]:
        """Resolve immutable inputs and recheck current compatibility before reservation."""
        if self.driver is None:
            raise ResearchStoreError(
                "Numerical fixture preparation requires durable research storage."
            )
        if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
            raise ResearchValidationError("A bounded numerical seed is required.")
        group_id = workspace_group_id(workspace_id)
        async with self.driver.session(database=self.database) as session:
            return await session.execute_read(
                self._tx_prepare_candidate_numerical_fixture,
                group_id,
                workspace_id,
                candidate_id,
                seed,
            )

    async def _tx_prepare_candidate_numerical_fixture(
        self,
        tx,
        group_id: str,
        workspace_id: str,
        candidate_id: str,
        seed: int,
    ) -> tuple[CompiledCandidate, ProblemSpecSnapshot, CompiledCandidate | None]:
        candidate_result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
        )
        candidate_row = await candidate_result.single()
        if candidate_row is None:
            raise ResearchReferenceNotFoundError("Candidate was not found in this workspace.")
        candidate = CompiledCandidate.model_validate_json(candidate_row["payload"])
        _verify_candidate_identity(candidate)
        if candidate.workspace_id != workspace_id:
            raise ResearchReferenceNotFoundError("Candidate was not found in this workspace.")

        spec_result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=candidate.problem_spec_id,
            group=group_id,
        )
        spec_row = await spec_result.single()
        if spec_row is None:
            raise ResearchReferenceNotFoundError(
                "Candidate ProblemSpec was not found in this workspace."
            )
        spec = ProblemSpecSnapshot.model_validate_json(spec_row["payload"])
        if (
            definition_hash(spec.definition) != spec.content_hash
            or spec.workspace_id != workspace_id
            or spec.content_hash != candidate.problem_spec_hash
            or seed not in spec.definition.seeds
        ):
            raise ResearchValidationError(
                "Candidate does not match the frozen ProblemSpec or seed."
            )

        parent_candidate = None
        if candidate.operator == "lower_mixture_to_concatenation":
            if len(candidate.parents) != 1:
                raise ResearchValidationError("Lowering candidate has invalid parent lineage.")
            parent_result = await tx.run(
                "MATCH (p:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
                "RETURN p.payload AS payload LIMIT 1",
                candidate_id=candidate.parents[0].entity_id,
                group=group_id,
            )
            parent_row = await parent_result.single()
            if parent_row is None:
                raise ResearchReferenceNotFoundError("Candidate's mixture parent was not found.")
            parent_candidate = CompiledCandidate.model_validate_json(parent_row["payload"])
            _verify_candidate_identity(parent_candidate)
            if candidate.parents[0] != ParentRef(
                entity_id=parent_candidate.candidate_id,
                version=1,
                content_hash=parent_candidate.content_hash,
            ):
                raise ResearchValidationError("Candidate parent reference is stale.")

        try:
            build_numerical_suite_input(
                candidate,
                spec,
                seed=seed,
                parent_candidate=parent_candidate,
            )
        except ValueError as exc:
            raise ResearchValidationError(str(exc)) from exc
        freshness, usable = await self._candidate_mapping_admission_status(
            tx, group_id=group_id, candidate=candidate
        )
        if freshness != "current" or not usable:
            raise ResearchValidationError(
                "Candidate compatibility mapping is not current and usable."
            )
        return candidate, spec, parent_candidate

    async def _tx_append_candidate_numerical_fixture(
        self,
        tx,
        group_id: str,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        receipt_key: str,
        intent_hash: str,
        result: NumericalFixtureReceipt,
    ) -> NumericalFixtureReceiptResponse:
        await lock_research_workspace(tx, group_id)
        saved = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
        if saved is not None:
            payload = json.loads(saved)
            return NumericalFixtureReceiptResponse(result=payload["result"], replayed=True)

        candidate_result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
        )
        candidate_row = await candidate_result.single()
        if candidate_row is None:
            raise ResearchReferenceNotFoundError("Candidate was not found in this workspace.")
        candidate = CompiledCandidate.model_validate_json(candidate_row["payload"])
        _verify_candidate_identity(candidate)
        if (
            result.workspace_id != workspace_id
            or result.actor_id != actor_id
            or result.candidate_id != candidate.candidate_id
            or result.candidate_hash != candidate.content_hash
            or result.parent_refs != candidate.parents
            or result.problem_spec_id != candidate.problem_spec_id
            or result.problem_spec_hash != candidate.problem_spec_hash
        ):
            raise ResearchValidationError(
                "Numerical fixture receipt does not match its candidate scope."
            )

        spec_result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=candidate.problem_spec_id,
            group=group_id,
        )
        spec_row = await spec_result.single()
        if spec_row is None:
            raise ResearchReferenceNotFoundError(
                "Candidate ProblemSpec was not found in this workspace."
            )
        spec = ProblemSpecSnapshot.model_validate_json(spec_row["payload"])
        if (
            definition_hash(spec.definition) != spec.content_hash
            or spec.content_hash != candidate.problem_spec_hash
            or result.dtype != spec.definition.dtype
            or result.seed not in spec.definition.seeds
        ):
            raise ResearchValidationError(
                "Numerical fixture receipt does not match its frozen ProblemSpec."
            )

        parent_candidate = None
        if candidate.operator == "lower_mixture_to_concatenation":
            if len(candidate.parents) != 1:
                raise ResearchValidationError("Lowering candidate has invalid parent lineage.")
            parent_result = await tx.run(
                "MATCH (p:ResearchCandidate {candidate_id:$parent_id, group_id:$group}) "
                "RETURN p.payload AS payload LIMIT 1",
                parent_id=candidate.parents[0].entity_id,
                group=group_id,
            )
            parent_row = await parent_result.single()
            if parent_row is None:
                raise ResearchReferenceNotFoundError("Candidate's mixture parent was not found.")
            parent_candidate = CompiledCandidate.model_validate_json(parent_row["payload"])
            _verify_candidate_identity(parent_candidate)
            if candidate.parents[0] != ParentRef(
                entity_id=parent_candidate.candidate_id,
                version=1,
                content_hash=parent_candidate.content_hash,
            ):
                raise ResearchValidationError("Numerical fixture parent reference is stale.")

        expected_input = build_numerical_suite_input(
            candidate,
            spec,
            seed=result.seed,
            parent_candidate=parent_candidate,
        )
        if result.suite_version != expected_input[
            "suite_version"
        ] or result.input_hash != numerical_fixture_input_hash(expected_input):
            raise ResearchValidationError(
                "Numerical fixture input does not match resolved server inputs."
            )

        freshness, usable = await self._candidate_mapping_admission_status(
            tx, group_id=group_id, candidate=candidate
        )
        if freshness != "current" or not usable:
            raise ResearchValidationError(
                "Candidate compatibility mapping is not current and usable."
            )

        payload = canonical_json(result.model_dump(mode="json"))
        existing_result = await tx.run(
            "MATCH (r:ResearchNumericalFixtureResult {result_id:$result_id}) "
            "RETURN r.group_id AS group_id, r.payload AS payload LIMIT 1",
            result_id=result.result_id,
        )
        existing = await existing_result.single()
        if existing is not None and (
            existing["group_id"] != group_id or existing["payload"] != payload
        ):
            raise IdempotencyConflictError(
                "Numerical fixture result identity conflicts with stored data."
            )
        if existing is None:
            await tx.run(
                "CREATE (r:ResearchNumericalFixtureResult {result_id:$result_id, group_id:$group, "
                "workspace_id:$workspace_id, candidate_id:$candidate_id, "
                "suite_version:$suite_version, "
                "created_at:$created_at, payload:$payload})",
                result_id=result.result_id,
                group=group_id,
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                suite_version=result.suite_version,
                created_at=result.created_at.isoformat(),
                payload=payload,
            )
        await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "MATCH (r:ResearchNumericalFixtureResult {result_id:$result_id, group_id:$group}) "
            "MERGE (c)-[:HAS_NUMERICAL_FIXTURE_RESULT]->(r)",
            candidate_id=candidate_id,
            result_id=result.result_id,
            group=group_id,
        )
        response_payload = canonical_json({"result": result.model_dump(mode="json")})
        await self._save_create_receipt(tx, receipt_key, group_id, intent_hash, response_payload)
        return NumericalFixtureReceiptResponse(result=result, replayed=False)

    async def prepare_registered_research_case(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
    ) -> tuple[CompiledCandidate, ProblemSpecSnapshot, CompiledCandidate]:
        """Resolve the one registered V3 protocol from immutable server records."""
        candidate, spec, parent = await self.prepare_candidate_numerical_fixture(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            seed=RESEARCH_CASE_SEEDS[0],
        )
        try:
            validate_registered_spec(spec)
        except ValueError as exc:
            raise ResearchValidationError(str(exc)) from exc
        if parent is None:
            raise ResearchValidationError(
                "The registered research case requires a lowered mixture candidate."
            )
        return candidate, spec, parent

    async def authorize_protocol_phase(
        self, *, workspace_id: str, candidate_id: str, evaluation_role: str,
        evolution_id: str | None,
    ) -> None:
        if self.driver is None:
            raise ResearchStoreError("Scoped research execution requires durable storage.")
        async with self.driver.session(database=self.database) as session:
            await session.execute_read(
                self._tx_authorize_protocol_phase, workspace_group_id(workspace_id),
                candidate_id, evaluation_role, evolution_id,
                True,
            )

    async def require_protocol_scope_review(self, binding: ImplementationBindingReceipt) -> None:
        rows, _, _ = await self.driver.execute_query(
            "MATCH (r:ResearchProtocolReview {group_id:$group,scope_key:$scope}) "
            "RETURN r.decision AS decision ORDER BY r.reviewed_at DESC,r.review_id DESC LIMIT 1",
            group=workspace_group_id(binding.workspace_id), scope=protocol_scope_key(binding),
            database_=self.database,
        )
        if not rows or rows[0]["decision"] != "accept_protocol_scope":
            raise ResearchValidationError("The frozen feature-map family needs human scope review.")

    @staticmethod
    async def _tx_authorize_protocol_phase(
        tx, group, candidate_id, role, evolution_id, execution_start=False,
    ):
        if role == "search" and evolution_id is None:
            return
        if role not in {"search", "holdout"} or evolution_id is None:
            raise ResearchValidationError(
                "Only scoped search or locked holdout execution is allowed."
            )
        row = await (await tx.run(
            "MATCH (e:EvolutionCampaign {evolution_id:$id,group_id:$group}) "
            "MATCH (c:ResearchCandidate {candidate_id:$candidate,group_id:$group}) "
            "RETURN e.payload AS campaign,c.payload AS candidate",
            id=evolution_id, candidate=candidate_id, group=group,
        )).single()
        if row is None:
            raise ResearchReferenceNotFoundError("Campaign or candidate was not found.")
        campaign = EvolutionCampaign.model_validate_json(row["campaign"])
        candidate = CompiledCandidate.model_validate_json(row["candidate"])
        if candidate.problem_spec_hash != campaign.spec.content_hash:
            raise ResearchValidationError("Candidate is outside the frozen campaign.")
        if role == "holdout" and (
            campaign.status != "finalists_frozen" or candidate_id not in campaign.finalist_ids
        ):
            raise ResearchValidationError("Holdout is locked until this finalist is frozen.")
        if role == "search" and campaign.status != "active":
            raise ResearchValidationError("Search cannot resume after finalist freeze or stop.")
        if execution_start:
            elapsed = (datetime.now(UTC) - campaign.events[0].occurred_at).total_seconds() * 1000
            if elapsed + phase_budget_ms(role) > campaign.spec.definition.budget.wall_time_ms:
                raise ResearchValidationError("The frozen wall-time budget cannot fit this phase.")

    def research_case_receipt_identity(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        idempotency_key: str,
        binding: ImplementationBindingReceipt,
        result: ResearchCaseReceipt,
    ) -> tuple[str, str, str]:
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        receipt_key = f"{group_id}:research-case:{key_hash}"
        intent_hash = self._stable_hash([
            workspace_id,
            candidate_id,
            actor_id,
            binding.binding_hash,
            result.result_hash,
        ])
        return group_id, receipt_key, intent_hash

    async def append_registered_research_case(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        idempotency_key: str,
        binding: ImplementationBindingReceipt,
        result: ResearchCaseReceipt,
    ) -> ResearchCaseReceiptResponse:
        if self.driver is None:
            raise ResearchStoreError("Research-case receipts require durable storage.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        binding = ImplementationBindingReceipt.model_validate(
            binding.model_dump(mode="python")
        )
        result = ResearchCaseReceipt.model_validate(result.model_dump(mode="python"))
        group_id, receipt_key, intent_hash = self.research_case_receipt_identity(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            binding=binding,
            result=result,
        )
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_append_registered_research_case,
                group_id,
                workspace_id,
                candidate_id,
                actor_id,
                receipt_key,
                intent_hash,
                binding,
                result,
            )

    async def _tx_append_registered_research_case(
        self,
        tx,
        group_id: str,
        workspace_id: str,
        candidate_id: str,
        actor_id: str,
        receipt_key: str,
        intent_hash: str,
        binding: ImplementationBindingReceipt,
        result: ResearchCaseReceipt,
    ) -> ResearchCaseReceiptResponse:
        await lock_research_workspace(tx, group_id)
        saved = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
        if saved is not None:
            payload = json.loads(saved)
            return ResearchCaseReceiptResponse(
                binding=payload["binding"], result=payload["result"], replayed=True
            )

        candidate_result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
        )
        candidate_row = await candidate_result.single()
        if candidate_row is None:
            raise ResearchReferenceNotFoundError("Candidate was not found in this workspace.")
        candidate = CompiledCandidate.model_validate_json(candidate_row["payload"])
        _verify_candidate_identity(candidate)
        if candidate.workspace_id != workspace_id or len(candidate.parents) != 1:
            raise ResearchValidationError("Research case does not match its workspace candidate.")

        spec_result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=candidate.problem_spec_id,
            group=group_id,
        )
        spec_row = await spec_result.single()
        parent_result = await tx.run(
            "MATCH (p:ResearchCandidate {candidate_id:$parent_id, group_id:$group}) "
            "RETURN p.payload AS payload LIMIT 1",
            parent_id=candidate.parents[0].entity_id,
            group=group_id,
        )
        parent_row = await parent_result.single()
        if spec_row is None or parent_row is None:
            raise ResearchReferenceNotFoundError(
                "Research-case ProblemSpec or candidate parent was not found."
            )
        spec = ProblemSpecSnapshot.model_validate_json(spec_row["payload"])
        parent = CompiledCandidate.model_validate_json(parent_row["payload"])
        _verify_candidate_identity(parent)
        try:
            expected_binding = make_implementation_binding(
                candidate,
                spec,
                parent_candidate=parent,
                execution_image=binding.execution_image,
                now=binding.created_at,
            )
        except ValueError as exc:
            raise ResearchValidationError(str(exc)) from exc
        if expected_binding != binding:
            raise ResearchValidationError(
                "Implementation binding does not match current server-owned inputs."
            )
        if (
            result.workspace_id != workspace_id
            or result.actor_id != actor_id
            or result.candidate_id != candidate.candidate_id
            or result.candidate_hash != candidate.content_hash
            or result.parent_refs != candidate.parents
            or result.problem_spec_id != spec.spec_id
            or result.problem_spec_hash != spec.content_hash
            or result.binding_id != binding.binding_id
            or result.binding_hash != binding.binding_hash
            or result.protocol_hash != binding.protocol_hash
        ):
            raise ResearchValidationError("Research-case result does not match its binding.")
        freshness, usable = await self._candidate_mapping_admission_status(
            tx, group_id=group_id, candidate=candidate
        )
        if freshness != "current" or not usable:
            raise ResearchValidationError(
                "Candidate compatibility mapping is not current and usable."
            )

        if result.evaluation_role != "legacy_full":
            await self._tx_authorize_protocol_phase(
                tx, group_id, candidate_id, result.evaluation_role, result.evolution_id,
            )
        binding_payload = canonical_json(binding.model_dump(mode="json"))
        result_payload = canonical_json(result.model_dump(mode="json"))
        for label, identity, payload in (
            ("ResearchImplementationBinding", binding.binding_id, binding_payload),
            ("ResearchExperimentResult", result.result_id, result_payload),
        ):
            identity_field = (
                "binding_id" if label == "ResearchImplementationBinding" else "result_id"
            )
            existing_result = await tx.run(
                f"MATCH (r:{label} {{{identity_field}:$identity}}) "
                "RETURN r.group_id AS group_id, r.payload AS payload LIMIT 1",
                identity=identity,
            )
            existing = await existing_result.single()
            if existing is not None and (
                existing["group_id"] != group_id or existing["payload"] != payload
            ):
                raise IdempotencyConflictError(
                    "Research-case evidence identity conflicts with stored data."
                )
            if existing is None:
                await tx.run(
                    f"CREATE (r:{label} {{{identity_field}:$identity, group_id:$group, "
                    "workspace_id:$workspace, candidate_id:$candidate_id, "
                    "created_at:$created_at, payload:$payload, evaluation_role:$evaluation_role})",
                    identity=identity,
                    group=group_id,
                    workspace=workspace_id,
                    candidate_id=candidate_id,
                    created_at=(
                        binding.created_at if label == "ResearchImplementationBinding"
                        else result.created_at
                    ).isoformat(),
                    payload=payload,
                    evaluation_role=result.evaluation_role,
                )
        await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "MATCH (b:ResearchImplementationBinding {binding_id:$binding_id, group_id:$group}) "
            "MATCH (r:ResearchExperimentResult {result_id:$result_id, group_id:$group}) "
            "MERGE (c)-[:HAS_IMPLEMENTATION_BINDING]->(b) "
            "MERGE (b)-[:PRODUCED_EXPERIMENT_RESULT]->(r) "
            "MERGE (c)-[:HAS_EXPERIMENT_RESULT]->(r)",
            candidate_id=candidate_id,
            binding_id=binding.binding_id,
            result_id=result.result_id,
            group=group_id,
        )
        response_payload = canonical_json({
            "binding": binding.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        })
        await self._save_create_receipt(tx, receipt_key, group_id, intent_hash, response_payload)
        return ResearchCaseReceiptResponse(
            binding=binding, result=result, replayed=False
        )

    async def record_proposal(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        output_json: str,
        idempotency_key: str,
    ) -> ProposalCreateResponse:
        """Resolve proposal references and append an untrusted proposal atomically."""
        if self.driver is None:
            raise ResearchStoreError("Proposal persistence requires durable research storage.")
        if not actor_id.strip():
            raise ResearchValidationError("A trusted proposer identity is required.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        group_id = workspace_group_id(workspace_id)
        receipt_key = (
            f"{group_id}:proposal:{actor_id}:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
        )
        intent_hash = self._stable_hash([workspace_id, actor_id, output_json])
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_record_proposal,
                group_id,
                workspace_id,
                actor_id,
                output_json,
                receipt_key,
                idempotency_key,
                intent_hash,
            )

    async def _tx_record_proposal(
        self,
        tx,
        group_id: str,
        workspace_id: str,
        actor_id: str,
        output_json: str,
        receipt_key: str,
        idempotency_key: str,
        intent_hash: str,
    ) -> ProposalCreateResponse:
        await lock_research_workspace(tx, group_id)
        receipt = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
        if receipt is not None:
            saved = json.loads(receipt)
            return ProposalCreateResponse(proposal=saved["proposal"], replayed=True)

        draft = ProposalDraft.model_validate(_parse_model_output(output_json))
        spec_result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=draft.problem_spec_id,
            group=group_id,
        )

        spec_row = await spec_result.single()
        if spec_row is None:
            raise ResearchReferenceNotFoundError("ProblemSpec was not found in this workspace.")
        spec = ProblemSpecSnapshot.model_validate_json(spec_row["payload"])

        parent_rows_result = await tx.run(
            "UNWIND $ids AS ref_id "
            "OPTIONAL MATCH (c:ResearchCandidate {candidate_id:ref_id, group_id:$group}) "
            "OPTIONAL MATCH (e:Evidence {uuid:ref_id, group_id:$group}) "
            "RETURN ref_id,c.payload AS candidate_payload,e.kind AS evidence_kind,"
            "e.payload AS evidence_payload",
            ids=list(draft.parent_ids),
            group=group_id,
        )
        parent_rows = [row async for row in parent_rows_result]
        resolved_parent_kinds: dict[str, str] = {}
        resolved_parent_candidates: dict[str, CompiledCandidate] = {}
        for row in parent_rows:
            parent_id = str(row["ref_id"])
            if row["candidate_payload"] is not None:
                parent = CompiledCandidate.model_validate_json(row["candidate_payload"])
                if (
                    parent.problem_spec_id != spec.spec_id
                    or parent.problem_spec_hash != spec.content_hash
                    or parent.workspace_id != workspace_id
                ):
                    raise ResearchValidationError("Proposal candidate parent uses another spec.")
                resolved_parent_kinds[parent_id] = "candidate"
                resolved_parent_candidates[parent_id] = parent
            elif row["evidence_payload"] is not None and row["evidence_kind"] == "Equation":
                resolved_parent_kinds[parent_id] = "equation"
            else:
                raise ResearchReferenceNotFoundError(
                    "Proposal parent was not found as a candidate or equation in this workspace."
                )
        if len(resolved_parent_kinds) != len(draft.parent_ids):
            raise ResearchReferenceNotFoundError("Proposal parent references are not unique.")

        source_spans = [
            {
                "id": span_id,
                "entity_id": parse_source_span_id(span_id)[0],
                "anchor": parse_source_span_id(span_id)[1],
            }
            for span_id in draft.source_span_ids
        ]
        span_result = await tx.run(
            "UNWIND $spans AS span "
            "MATCH (e:Evidence {uuid:span.entity_id, group_id:$group}) "
            "WHERE e.kind IN ['Section','Equation'] "
            "RETURN span.id AS span_id,e.uuid AS entity_id,e.kind AS evidence_kind,"
            "e.payload AS payload",
            spans=source_spans,
            group=group_id,
        )
        resolved_spans: dict[str, str] = {}
        source_equation_ids: set[str] = set()
        async for row in span_result:
            payload = self._payload_object(str(row["payload"]), label="proposal source")
            span_id = str(row["span_id"])
            _entity_id, expected_anchor = parse_source_span_id(span_id)
            if (
                payload.get("anchor_is_source") is False
                or payload.get("anchor") != expected_anchor
                or str(row["entity_id"]) != _entity_id
            ):
                raise ResearchValidationError("Proposal source span is not a current HTML anchor.")
            resolved_spans[span_id] = _entity_id
            if row["evidence_kind"] == "Equation":
                source_equation_ids.add(_entity_id)
        if len(resolved_spans) != len(draft.source_span_ids):
            raise ResearchReferenceNotFoundError(
                "Proposal source span was not found in this workspace."
            )

        transform, _manifest = validate_transform(
            draft.transform, spec.definition.allowed_transforms
        )
        if transform.target_node_id not in resolved_parent_kinds:
            raise ResearchValidationError("Proposal transform target must be one of its parents.")
        if isinstance(transform, MixPositiveFeatureMaps):
            symbol_ids = (transform.bindings.left, transform.bindings.right)
            symbol_result = await tx.run(
                "UNWIND $symbol_ids AS symbol_id "
                "MATCH (s:Evidence {uuid:symbol_id,group_id:$group,kind:'Symbol'}) "
                "MATCH (e:Evidence {group_id:$group,kind:'Equation'})-"
                "[:EVIDENCE_RELATION {group_id:$group}]->(s) "
                "RETURN symbol_id,collect(DISTINCT e.uuid) AS equations",
                symbol_ids=list(symbol_ids),
                group=group_id,
            )
            symbol_owners = {
                str(row["symbol_id"]): set(map(str, row["equations"]))
                async for row in symbol_result
            }
            resolved_binding_equations: list[str] = []
            for symbol_id in symbol_ids:
                owners = symbol_owners.get(symbol_id, set()) & source_equation_ids
                if not owners:
                    raise ResearchReferenceNotFoundError(
                        "Proposal feature-map binding was not found in its source spans."
                    )
                if len(owners) != 1:
                    raise ResearchValidationError(
                        "Proposal feature-map binding has ambiguous source ownership."
                    )
                if not owners <= set(draft.parent_ids):
                    raise ResearchValidationError(
                        "Proposal feature-map binding is not one of its declared parents."
                    )
                resolved_binding_equations.append(next(iter(owners)))
            if resolved_binding_equations[0] == resolved_binding_equations[1]:
                raise ResearchValidationError("Proposal feature-map bindings must be distinct.")
        elif isinstance(transform, LowerMixtureToConcatenation):
            mixture = resolved_parent_candidates.get(transform.bindings.mixture)
            if (
                mixture is None
                or mixture.operator != "mix_positive_feature_maps"
                or transform.target_node_id != mixture.candidate_id
            ):
                raise ResearchValidationError(
                    "Proposal lowering must target its registered mixture candidate."
                )
            required_source_ids = {
                ref.entity_id.split(":", maxsplit=1)[0]
                for ref in mixture.parents
                if not ref.entity_id.startswith("cand_")
            }
            if not required_source_ids <= source_equation_ids:
                raise ResearchValidationError(
                    "Proposal lowering is missing a source span from its mixture lineage."
                )
        proposal = validate_model_proposal(
            output_json,
            workspace_id=workspace_id,
            spec=spec,
            allowed_parent_ids=frozenset(resolved_parent_kinds),
            allowed_source_span_ids=frozenset(resolved_spans),
            allowed_target_node_ids=frozenset(resolved_parent_kinds),
        )
        payload = canonical_json(proposal.model_dump(mode="json"))
        now = datetime.now(UTC).isoformat()
        saved_result = await tx.run(
            "MERGE (p:ResearchProposal {proposal_id:$proposal_id}) "
            "ON CREATE SET p.group_id=$group,p.workspace_id=$workspace,"
            "p.problem_spec_id=$spec_id,p.content_hash=$content_hash,p.payload=$payload,"
            "p.created_by=$actor_id,p.created_at=$created_at "
            "RETURN p.group_id AS group_id,p.payload AS payload",
            proposal_id=proposal.proposal_id,
            group=group_id,
            workspace=workspace_id,
            spec_id=spec.spec_id,
            content_hash=proposal.content_hash,
            payload=payload,
            actor_id=actor_id,
            created_at=now,
        )
        saved_row = await saved_result.single(strict=True)
        if saved_row["group_id"] != group_id or saved_row["payload"] != payload:
            raise IdempotencyConflictError("Proposal identity conflicts with immutable content.")

        link_result = await tx.run(
            "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
            "MATCH (s:ProblemSpec {spec_id:$spec_id,group_id:$group}) "
            "MERGE (p)-[:PROPOSED_FOR]->(s) RETURN p.proposal_id AS proposal_id",
            proposal_id=proposal.proposal_id,
            spec_id=spec.spec_id,
            group=group_id,
        )
        if await link_result.single() is None:
            raise ResearchReferenceNotFoundError("Proposal ProblemSpec disappeared during write.")

        for parent_id, kind in resolved_parent_kinds.items():
            if kind == "candidate":
                query = (
                    "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
                    "MATCH (r:ResearchCandidate {candidate_id:$parent_id,group_id:$group}) "
                    "MERGE (p)-[:PROPOSED_FROM_CANDIDATE]->(r) RETURN r.candidate_id AS id"
                )
            else:
                query = (
                    "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
                    "MATCH (r:Evidence {uuid:$parent_id,group_id:$group,kind:'Equation'}) "
                    "MERGE (p)-[:PROPOSED_FROM_EQUATION]->(r) RETURN r.uuid AS id"
                )
            parent_link = await tx.run(
                query,
                proposal_id=proposal.proposal_id,
                parent_id=parent_id,
                group=group_id,
            )
            if await parent_link.single() is None:
                raise ResearchReferenceNotFoundError("Proposal parent disappeared during write.")
        for span_id, entity_id in resolved_spans.items():
            _source_id, anchor = parse_source_span_id(span_id)
            source_link = await tx.run(
                "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
                "MATCH (e:Evidence {uuid:$entity_id,group_id:$group}) "
                "MERGE (p)-[:CITES_SOURCE_SPAN {source_span_id:$span_id,anchor:$anchor}]->(e) "
                "RETURN e.uuid AS uuid",
                proposal_id=proposal.proposal_id,
                entity_id=entity_id,
                span_id=span_id,
                anchor=anchor,
                group=group_id,
            )
            if await source_link.single() is None:
                raise ResearchReferenceNotFoundError("Proposal source disappeared during write.")

        response_payload = canonical_json({"proposal": proposal.model_dump(mode="json")})
        await self._save_create_receipt(tx, receipt_key, group_id, intent_hash, response_payload)
        return ProposalCreateResponse(proposal=proposal, replayed=False)

    async def reserve_proposal_generation(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        intent_hash: str,
        reserve_usd: Decimal,
        daily_limit_usd: Decimal,
    ) -> dict[str, Any]:
        """Reserve worst-case provider cost before calling the model; retries never re-spend."""
        if self.driver is None:
            raise ResearchStoreError("Proposal generation requires durable research storage.")
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        operation_key = f"{group_id}:proposal-generation:{actor_id}:{key_hash}"
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_reserve_proposal_generation,
                group_id,
                operation_key,
                intent_hash,
                reserve_usd,
                daily_limit_usd,
            )

    @staticmethod
    async def _tx_reserve_proposal_generation(
        tx,
        group_id: str,
        operation_key: str,
        intent_hash: str,
        reserve_usd: Decimal,
        daily_limit_usd: Decimal,
    ) -> dict[str, Any]:
        await lock_research_workspace(tx, group_id)
        result = await tx.run(
            "MATCH (r:ResearchIdempotency {key:$key}) "
            "RETURN r.group_id AS group_id,r.intent_hash AS intent_hash,"
            "r.operation_status AS status,r.operation_payload AS payload LIMIT 1",
            key=operation_key,
        )
        row = await result.single()
        if row is not None:
            if row["group_id"] != group_id or row["intent_hash"] != intent_hash:
                raise IdempotencyConflictError(
                    "Proposal generation key conflicts with prior intent."
                )
            if row["status"] in {"completed", "generated"}:
                return {"status": row["status"], "payload": row["payload"]}
            raise ProposalGenerationInProgressError(
                "Proposal generation for this key is already reserved or failed."
            )

        today = datetime.now(UTC).date().isoformat()
        quota_result = await tx.run(
            "MATCH (q:ResearchProposalQuota {group_id:$group}) "
            "RETURN q.day AS day,q.reserved_usd AS reserved_usd LIMIT 1",
            group=group_id,
        )
        quota = await quota_result.single()
        already_reserved = (
            Decimal(str(quota["reserved_usd"])) if quota and quota["day"] == today else Decimal(0)
        )
        if already_reserved + reserve_usd > daily_limit_usd:
            raise ProposalGenerationBudgetExceededError(
                "Daily proposal generation budget exceeded."
            )
        await (
            await tx.run(
                "MERGE (q:ResearchProposalQuota {group_id:$group}) "
                "SET q.day=$day,q.reserved_usd=$reserved RETURN q.group_id AS group_id",
                group=group_id,
                day=today,
                reserved=str(already_reserved + reserve_usd),
            )
        ).single(strict=True)
        payload = canonical_json({})
        await Neo4jResearchStore._save_create_receipt(
            tx, operation_key, group_id, intent_hash, payload
        )
        await (
            await tx.run(
                "MATCH (r:ResearchIdempotency {key:$key}) "
                "SET r.operation_status='reserved',r.operation_payload=$payload "
                "RETURN r.key AS key",
                key=operation_key,
                payload=payload,
            )
        ).single(strict=True)
        return {"status": "reserved", "payload": None}

    async def save_generated_proposal_draft(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        intent_hash: str,
        output_json: str,
    ) -> None:
        await self._update_proposal_generation_operation(
            workspace_id=workspace_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            intent_hash=intent_hash,
            status="generated",
            payload=output_json,
        )

    async def complete_proposal_generation(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        intent_hash: str,
        response_json: str,
    ) -> None:
        await self._update_proposal_generation_operation(
            workspace_id=workspace_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            intent_hash=intent_hash,
            status="completed",
            payload=response_json,
        )

    async def _update_proposal_generation_operation(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        intent_hash: str,
        status: str,
        payload: str,
    ) -> None:
        if self.driver is None:
            raise ResearchStoreError("Proposal generation requires durable research storage.")
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        operation_key = f"{group_id}:proposal-generation:{actor_id}:{key_hash}"
        async with self.driver.session(database=self.database) as session:
            await session.execute_write(
                self._tx_update_proposal_generation_operation,
                group_id,
                operation_key,
                intent_hash,
                status,
                payload,
            )

    @staticmethod
    async def _tx_update_proposal_generation_operation(
        tx,
        group_id: str,
        operation_key: str,
        intent_hash: str,
        status: str,
        payload: str,
    ) -> None:
        await lock_research_workspace(tx, group_id)
        result = await tx.run(
            "MATCH (r:ResearchIdempotency {key:$key,group_id:$group,intent_hash:$intent_hash}) "
            "SET r.operation_status=$status,r.operation_payload=$payload RETURN r.key AS key",
            key=operation_key,
            group=group_id,
            intent_hash=intent_hash,
            status=status,
            payload=payload,
        )
        if await result.single() is None:
            raise ResearchStoreError("Proposal generation reservation was lost.")

    async def list_research_proposals(
        self,
        *,
        workspace_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return validated, bounded proposal history for one workspace."""
        if self.driver is None:
            raise ResearchStoreError("Proposal history requires durable research storage.")
        group_id = workspace_group_id(workspace_id)
        limit, offset = max(1, min(limit, 50)), min(max(offset, 0), 100_000)
        async with self.driver.session(database=self.database) as session:
            count_row = await (
                await session.run(
                    "MATCH (p:ResearchProposal {group_id:$group}) RETURN count(p) AS total",
                    group=group_id,
                )
            ).single(strict=True)
            page = await session.run(
                "MATCH (p:ResearchProposal {group_id:$group}) "
                "OPTIONAL MATCH (p)-[:HAS_REVIEW]->"
                "(r:ResearchProposalReview {group_id:$group}) "
                "WITH p,r ORDER BY r.reviewed_at DESC,r.review_id DESC "
                "WITH p,collect(r.payload) AS review_payloads "
                "RETURN p.payload AS payload,p.created_at AS created_at,"
                "review_payloads "
                "ORDER BY p.created_at DESC,p.proposal_id ASC SKIP $offset LIMIT $limit",
                group=group_id,
                offset=offset,
                limit=limit,
            )
            rows = [row async for row in page]

        items: list[dict[str, Any]] = []
        for row in rows:
            proposal = ResearchProposal.model_validate_json(row["payload"])
            if proposal.workspace_id != workspace_id:
                raise ResearchStoreError("Stored proposal has an invalid workspace scope.")
            created_at = row["created_at"]
            if not isinstance(created_at, str) or len(created_at) > 64:
                raise ResearchStoreError("Stored proposal timestamp is invalid.")
            review_payloads = row["review_payloads"] or []
            if len(review_payloads) > 1:
                raise ResearchStoreError("Proposal has conflicting immutable review records.")
            review = (
                ProposalReviewRecord.model_validate_json(review_payloads[0])
                if review_payloads and review_payloads[0] is not None
                else None
            )
            if review is not None and (
                review.workspace_id != workspace_id
                or review.proposal_id != proposal.proposal_id
                or review.proposal_hash != proposal.content_hash
            ):
                raise ResearchStoreError("Stored proposal review has an invalid scope.")
            items.append(
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "created_at": created_at,
                    "review": review.model_dump(mode="json") if review else None,
                }
            )
        return items, int(count_row["total"])

    async def review_research_proposal(
        self,
        *,
        workspace_id: str,
        proposal_id: str,
        reviewer_id: str,
        reviewer_role: str,
        request: ProposalReviewCreateRequest,
        idempotency_key: str,
    ) -> ProposalReviewResponse:
        """Append one human decision; acceptance permits compilation only."""
        if self.driver is None:
            raise ResearchStoreError("Proposal review requires durable research storage.")
        if not reviewer_id.strip() or not reviewer_id.isascii():
            raise ResearchAuthorizationError("A trusted human reviewer is required.")
        if reviewer_role not in {"researcher", "reviewer", "admin"}:
            raise ResearchAuthorizationError("A human reviewer role is required.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
        actor_hash = hashlib.sha256(reviewer_id.encode("ascii")).hexdigest()
        receipt_key = f"{group_id}:proposal-review:{actor_hash}:{key_hash}"
        intent_hash = self._stable_hash(
            [
                workspace_id,
                proposal_id,
                reviewer_id,
                reviewer_role,
                request.model_dump(mode="json"),
            ]
        )
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_review_research_proposal,
                group_id,
                workspace_id,
                proposal_id,
                reviewer_id,
                reviewer_role,
                request,
                receipt_key,
                intent_hash,
            )

    @staticmethod
    async def _tx_review_research_proposal(
        tx,
        group_id: str,
        workspace_id: str,
        proposal_id: str,
        reviewer_id: str,
        reviewer_role: str,
        request: ProposalReviewCreateRequest,
        receipt_key: str,
        intent_hash: str,
    ) -> ProposalReviewResponse:
        await lock_research_workspace(tx, group_id)
        receipt = await Neo4jResearchStore._read_create_receipt(
            tx, receipt_key, group_id, intent_hash
        )
        if receipt is not None:
            saved = json.loads(receipt)
            review = ProposalReviewRecord.model_validate(saved["review"])
            return ProposalReviewResponse(review=review, replayed=True)

        result = await tx.run(
            "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
            "OPTIONAL MATCH (p)-[:HAS_REVIEW]->(existing:ResearchProposalReview "
            "{group_id:$group}) "
            "RETURN p.payload AS payload,p.workspace_id AS workspace_id,"
            "p.created_by AS created_by,collect(existing.payload) AS reviews LIMIT 1",
            proposal_id=proposal_id,
            group=group_id,
        )
        row = await result.single()
        if row is None:
            raise ResearchReferenceNotFoundError("Proposal was not found in this workspace.")
        proposal = ResearchProposal.model_validate_json(row["payload"])
        if row["workspace_id"] != workspace_id or proposal.workspace_id != workspace_id:
            raise ResearchReferenceNotFoundError("Proposal was not found in this workspace.")
        if row["created_by"] == reviewer_id:
            raise ResearchAuthorizationError("The proposal's service author cannot review it.")
        if row["reviews"]:
            raise ResearchValidationError("This immutable proposal already has a final review.")

        # Pydantic's JSON datetime form normalizes UTC to ``Z``; hash that exact form.
        reviewed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        review_identity = {
            "workspace_id": workspace_id,
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.content_hash,
            "reviewer_id": reviewer_id,
            "reviewer_role": reviewer_role,
            "decision": request.decision,
            "notes": request.notes,
            "reviewed_at": reviewed_at,
            "schema_version": "proposal-review.v1",
        }
        review_hash = hashlib.sha256(canonical_json(review_identity).encode("utf-8")).hexdigest()
        review = ProposalReviewRecord(
            review_id=f"prev_{review_hash[:32]}",
            review_hash=review_hash,
            **review_identity,
        )
        payload = canonical_json(review.model_dump(mode="json"))
        await tx.run(
            "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
            "CREATE (r:ResearchProposalReview {review_id:$review_id,group_id:$group,"
            "workspace_id:$workspace_id,decision:$decision,proposal_hash:$proposal_hash,"
            "reviewed_at:$reviewed_at,payload:$payload}) "
            "CREATE (p)-[:HAS_REVIEW]->(r)",
            proposal_id=proposal.proposal_id,
            review_id=review.review_id,
            group=group_id,
            workspace_id=workspace_id,
            decision=review.decision,
            proposal_hash=proposal.content_hash,
            reviewed_at=review.reviewed_at.isoformat(),
            payload=payload,
        )
        response_payload = canonical_json({"review": review.model_dump(mode="json")})
        await Neo4jResearchStore._save_create_receipt(
            tx, receipt_key, group_id, intent_hash, response_payload
        )
        return ProposalReviewResponse(review=review, replayed=False)

    async def compile_candidate(
        self,
        *,
        request: CompileCandidateRequest,
        actor_id: str,
        idempotency_key: str,
    ) -> CompileCandidateResponse:
        """Resolve, compile, and atomically append candidate, activity, and receipt."""
        if self.driver is None:
            raise ResearchStoreError("Candidate compilation requires durable research storage.")
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")
        group_id = workspace_group_id(request.workspace_id)
        receipt_key = (
            f"{group_id}:candidate-compile:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
        )
        intent_hash = self._stable_hash(request.model_dump(mode="json"))
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_compile_candidate,
                group_id,
                request,
                actor_id,
                receipt_key,
                idempotency_key,
                intent_hash,
            )

    async def _tx_compile_candidate(
        self,
        tx,
        group_id: str,
        request: CompileCandidateRequest,
        actor_id: str,
        receipt_key: str,
        idempotency_key: str,
        intent_hash: str,
    ) -> CompileCandidateResponse:
        await lock_research_workspace(tx, group_id)
        receipt = await self._read_create_receipt(tx, receipt_key, group_id, intent_hash)
        if receipt is not None:
            saved = json.loads(receipt)
            return CompileCandidateResponse(
                candidate=saved["candidate"],
                activity=saved["activity"],
                replayed=True,
            )

        proposal = None
        if request.proposal_id is not None:
            proposal_result = await tx.run(
                "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
                "OPTIONAL MATCH (p)-[:HAS_REVIEW]->"
                "(r:ResearchProposalReview {group_id:$group}) "
                "WITH p,r ORDER BY r.reviewed_at DESC,r.review_id DESC "
                "WITH p,collect(r.payload) AS reviews "
                "RETURN p.payload AS payload,p.workspace_id AS workspace_id,reviews LIMIT 1",
                proposal_id=request.proposal_id,
                group=group_id,
            )
            proposal_row = await proposal_result.single()
            if proposal_row is None:
                raise ResearchReferenceNotFoundError("Proposal was not found in this workspace.")
            proposal = ResearchProposal.model_validate_json(proposal_row["payload"])
            if proposal_row["workspace_id"] != request.workspace_id or (
                proposal.workspace_id != request.workspace_id
            ):
                raise ResearchReferenceNotFoundError("Proposal was not found in this workspace.")
            review_payloads = proposal_row["reviews"] or []
            if len(review_payloads) != 1 or review_payloads[0] is None:
                raise ResearchValidationError(
                    "An immutable human review is required before proposal compilation."
                )
            review = ProposalReviewRecord.model_validate_json(review_payloads[0])
            if (
                review.workspace_id != request.workspace_id
                or review.proposal_id != proposal.proposal_id
                or review.proposal_hash != proposal.content_hash
            ):
                raise ResearchStoreError("Proposal review has an invalid scope.")
            if review.decision != "accept_for_compilation":
                raise ResearchValidationError("Rejected proposals cannot be compiled.")

        spec_result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=request.spec_id,
            group=group_id,
        )
        spec_row = await spec_result.single()
        if spec_row is None:
            raise ResearchReferenceNotFoundError("ProblemSpec was not found in this workspace.")
        spec = ProblemSpecSnapshot.model_validate_json(spec_row["payload"])
        if proposal is not None and (
            proposal.problem_spec_id != spec.spec_id
            or proposal.problem_spec_hash != spec.content_hash
            or proposal.problem_spec_id != request.spec_id
            or proposal.transform_json != canonical_json(request.transform)
        ):
            raise ResearchValidationError(
                "Proposal compilation must use its exact frozen spec and stored transform."
            )
        transform, _ = validate_transform(request.transform, spec.definition.allowed_transforms)
        if proposal is not None:
            if (
                proposal.operator != transform.operator
                or proposal.operator_version != transform.operator_version
            ):
                raise ResearchValidationError(
                    "Proposal operator does not match its stored transform."
                )
            if transform.target_node_id not in proposal.parent_ids:
                raise ResearchValidationError("Proposal target is not one of its stored parents.")
            if isinstance(transform, LowerMixtureToConcatenation):
                if (
                    request.parent_candidate_id != transform.bindings.mixture
                    or request.parent_candidate_id not in proposal.parent_ids
                ):
                    raise ResearchValidationError(
                        "Proposal compilation must use its stored mixture parent."
                    )
            elif request.parent_candidate_id is not None:
                raise ResearchValidationError(
                    "This proposal does not authorize a replacement candidate parent."
                )

            for span_id in proposal.source_span_ids:
                source_id, anchor = parse_source_span_id(span_id)
                source_result = await tx.run(
                    "MATCH (e:Evidence {uuid:$source_id,group_id:$group}) "
                    "WHERE e.kind IN ['Section','Equation'] "
                    "RETURN e.kind AS kind,e.payload AS payload LIMIT 1",
                    source_id=source_id,
                    group=group_id,
                )
                source_row = await source_result.single()
                if source_row is None:
                    raise ResearchReferenceNotFoundError(
                        "Proposal source span is no longer available in this workspace."
                    )
                source_payload = self._payload_object(
                    str(source_row["payload"]), label="proposal source"
                )
                if (
                    source_payload.get("anchor_is_source") is False
                    or source_payload.get("anchor") != anchor
                ):
                    raise ResearchValidationError("Proposal source anchor is no longer current.")

            parent_result = await tx.run(
                "UNWIND $parent_ids AS parent_id "
                "OPTIONAL MATCH (c:ResearchCandidate {candidate_id:parent_id,group_id:$group}) "
                "OPTIONAL MATCH (e:Evidence {uuid:parent_id,group_id:$group,kind:'Equation'}) "
                "RETURN parent_id,c.payload AS candidate_payload,e.uuid AS equation_id",
                parent_ids=list(proposal.parent_ids),
                group=group_id,
            )
            resolved_parent_ids: set[str] = set()
            async for parent_row in parent_result:
                parent_id = str(parent_row["parent_id"])
                if parent_row["candidate_payload"] is not None:
                    parent = CompiledCandidate.model_validate_json(parent_row["candidate_payload"])
                    if (
                        parent.workspace_id != request.workspace_id
                        or parent.problem_spec_id != proposal.problem_spec_id
                        or parent.problem_spec_hash != proposal.problem_spec_hash
                    ):
                        raise ResearchValidationError(
                            "Proposal parent no longer matches its frozen workspace and spec."
                        )
                    resolved_parent_ids.add(parent_id)
                elif parent_row["equation_id"] == parent_id:
                    resolved_parent_ids.add(parent_id)
            if resolved_parent_ids != set(proposal.parent_ids):
                raise ResearchReferenceNotFoundError(
                    "A proposal parent is no longer available in this workspace."
                )

        mapping_result = await tx.run(
            "MATCH (m:CompatibilityMapping {mapping_id:$mapping_id, group_id:$group}) "
            "OPTIONAL MATCH (m)-[:HAS_REVIEW]->(r:CompatibilityReview {group_id:$group}) "
            "RETURN m.mapping_payload AS mapping_payload, "
            "m.assessment_payload AS assessment_payload, collect(r.payload) AS reviews LIMIT 1",
            mapping_id=request.mapping_id,
            group=group_id,
        )
        mapping_row = await mapping_result.single()
        if mapping_row is None:
            raise ResearchReferenceNotFoundError(
                "Compatibility mapping was not found in this workspace."
            )
        mapping = PortMapping.model_validate_json(mapping_row["mapping_payload"])
        left, left_dependency = await self._resolve_compiler_feature_map(
            tx,
            group_id=group_id,
            workspace_id=request.workspace_id,
            port=mapping.producer_port,
        )
        right, right_dependency = await self._resolve_compiler_feature_map(
            tx,
            group_id=group_id,
            workspace_id=request.workspace_id,
            port=mapping.consumer_port,
        )
        dependencies = (left_dependency, right_dependency)
        effective_mapping, assessment = self._effective_compatibility(
            mapping,
            CompatibilityAssessment.model_validate_json(mapping_row["assessment_payload"]),
            dependencies,
            mapping_row["reviews"],
        )

        parent_candidate = None
        if request.parent_candidate_id is not None:
            parent_result = await tx.run(
                "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
                "RETURN c.payload AS payload LIMIT 1",
                candidate_id=request.parent_candidate_id,
                group=group_id,
            )
            parent_row = await parent_result.single()
            if parent_row is None:
                raise ResearchReferenceNotFoundError(
                    "Parent candidate was not found in this workspace."
                )
            parent_candidate = CompiledCandidate.model_validate_json(parent_row["payload"])
            if not parent_candidate.parents:
                raise ResearchValidationError("Parent candidate has no source lineage.")
            target_parent = parent_candidate.parents[0]
            target_dependency = ResolvedDependency(
                entity_id=target_parent.entity_id,
                version=target_parent.version,
                content_hash=target_parent.content_hash,
            )
        else:
            if isinstance(transform, LowerMixtureToConcatenation):
                raise ResearchValidationError(
                    "Concatenation lowering requires a parent_candidate_id."
                )
            target_parent, target_dependency = await self._resolve_compiler_target(
                tx,
                group_id=group_id,
                target_id=transform.target_node_id,
            )

        context = CompileContext(
            workspace_id=request.workspace_id,
            target_node_id=target_parent.entity_id,
            target_parent=target_parent,
            target_dependency=target_dependency,
            spec=spec,
            left=left,
            right=right,
            mapping=effective_mapping,
            assessment=assessment,
            dependencies=dependencies,
            current_dependency_fingerprint=compute_dependency_fingerprint(dependencies),
        )
        try:
            candidate = compile_transform(
                request.transform,
                context=context,
                parent_candidate=parent_candidate,
            )
        except ValueError as exc:
            raise ResearchValidationError(str(exc)) from exc

        activity_id = f"act_{uuid5(NAMESPACE_URL, f'{group_id}/{idempotency_key}').hex}"
        compiler_context_json = canonical_json(context.model_dump(mode="json"))
        activity = TransformationActivity(
            activity_id=activity_id,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.content_hash,
            workspace_id=request.workspace_id,
            actor_id=actor_id,
            operator=candidate.operator,
            operator_version=candidate.operator_version,
            mapping_id=candidate.mapping_id,
            source_proposal_id=proposal.proposal_id if proposal is not None else None,
            semantics_class=candidate.semantics_class,
            request_json=canonical_json(request.transform),
            parents=candidate.parents,
            created_at=datetime.now(UTC).isoformat(),
            schema_version="transformation-activity.v2",
            compiler_context_json=compiler_context_json,
            compiler_context_hash=hashlib.sha256(
                compiler_context_json.encode("utf-8")
            ).hexdigest(),
        )
        candidate_payload = canonical_json(candidate.model_dump(mode="json"))
        activity_payload = canonical_json(activity.model_dump(mode="json"))
        existing_result = await tx.run(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id}) "
            "RETURN c.group_id AS group_id, c.payload AS payload LIMIT 1",
            candidate_id=candidate.candidate_id,
        )
        existing = await existing_result.single()
        if existing is not None:
            if existing["group_id"] != group_id or existing["payload"] != candidate_payload:
                raise IdempotencyConflictError(
                    "Candidate identity conflicts with existing content."
                )
        else:
            await tx.run(
                "CREATE (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group, "
                "workspace_id:$workspace, content_hash:$content_hash, payload:$payload})",
                candidate_id=candidate.candidate_id,
                group=group_id,
                workspace=request.workspace_id,
                content_hash=candidate.content_hash,
                payload=candidate_payload,
            )
        await tx.run(
            "CREATE (a:TransformationActivity {activity_id:$activity_id, group_id:$group, "
            "workspace_id:$workspace, candidate_id:$candidate_id, payload:$payload})",
            activity_id=activity.activity_id,
            group=group_id,
            workspace=request.workspace_id,
            candidate_id=candidate.candidate_id,
            payload=activity_payload,
        )
        await tx.run(
            "MATCH (a:TransformationActivity {activity_id:$activity_id, group_id:$group}) "
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "MERGE (a)-[:PRODUCED]->(c)",
            activity_id=activity.activity_id,
            candidate_id=candidate.candidate_id,
            group=group_id,
        )
        if proposal is not None:
            proposal_link = await tx.run(
                "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
                "MATCH (a:TransformationActivity {activity_id:$activity_id,group_id:$group}) "
                "MERGE (p)-[:HAS_COMPILATION]->(a) RETURN a.activity_id AS activity_id",
                proposal_id=proposal.proposal_id,
                activity_id=activity.activity_id,
                group=group_id,
            )
            if await proposal_link.single() is None:
                raise ResearchReferenceNotFoundError("Proposal disappeared during compilation.")
        candidate_parent_ids = [
            item.entity_id for item in activity.parents if item.entity_id.startswith("cand_")
        ]
        for candidate_parent_id in candidate_parent_ids:
            parent_link = await tx.run(
                "MATCH (a:TransformationActivity {activity_id:$activity_id, group_id:$group}) "
                "MATCH (p:ResearchCandidate {candidate_id:$parent_id, group_id:$group}) "
                "MERGE (a)-[:TRANSFORMS_FROM]->(p) RETURN p.candidate_id AS candidate_id",
                activity_id=activity.activity_id,
                parent_id=candidate_parent_id,
                group=group_id,
            )
            if await parent_link.single() is None:
                raise ResearchReferenceNotFoundError(
                    "Candidate parent disappeared during compilation."
                )
        response_payload = canonical_json(
            {
                "candidate": candidate.model_dump(mode="json"),
                "activity": activity.model_dump(mode="json"),
            }
        )
        await self._save_create_receipt(tx, receipt_key, group_id, intent_hash, response_payload)
        return CompileCandidateResponse(candidate=candidate, activity=activity, replayed=False)

    async def list_research_candidates(
        self,
        *,
        workspace_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a bounded, workspace-scoped history read model."""
        if self.driver is None:
            raise ResearchStoreError("Candidate history requires durable research storage.")
        group_id = workspace_group_id(workspace_id)
        limit, offset = max(1, min(limit, 50)), max(0, offset)
        async with self.driver.session(database=self.database) as session:
            count_row = await (
                await session.run(
                    "MATCH (c:ResearchCandidate {group_id:$group}) RETURN count(c) AS total",
                    group=group_id,
                )
            ).single()
            page = await session.run(
                "MATCH (c:ResearchCandidate {group_id:$group}) "
                "OPTIONAL MATCH (a:TransformationActivity {group_id:$group})-[:PRODUCED]->(c) "
                "WITH c,a ORDER BY a.created_at DESC "
                "WITH c,collect(a.payload)[0] AS activity,max(a.created_at) AS latest "
                "ORDER BY latest DESC,c.candidate_id ASC SKIP $offset LIMIT $limit "
                "RETURN c.candidate_id AS candidate_id,c.payload AS candidate,activity",
                group=group_id,
                offset=offset,
                limit=limit,
            )
            rows = [row async for row in page]
            candidate_ids = [row["candidate_id"] for row in rows]
            checks: dict[str, str] = {}
            admissions: dict[str, str] = {}
            numerical_fixtures: dict[str, str] = {}
            research_cases: dict[str, dict[str, str]] = {}
            if candidate_ids:
                check_rows = await session.run(
                    "MATCH (c:ResearchCandidate {group_id:$group})-[:HAS_CANDIDATE_CHECK]->"
                    "(r:ResearchCandidateCheck {group_id:$group}) "
                    "WHERE c.candidate_id IN $ids WITH c,r ORDER BY r.created_at DESC "
                    "WITH c.candidate_id AS candidate_id,collect(r.payload)[0] AS payload "
                    "RETURN candidate_id,payload",
                    group=group_id,
                    ids=candidate_ids,
                )
                checks = {row["candidate_id"]: row["payload"] async for row in check_rows}
                admission_rows = await session.run(
                    "MATCH (c:ResearchCandidate {group_id:$group})-[:HAS_ADMISSION_DECISION]->"
                    "(d:ResearchAdmissionDecision {group_id:$group}) "
                    "WHERE c.candidate_id IN $ids WITH c,d ORDER BY d.created_at DESC "
                    "WITH c.candidate_id AS candidate_id,collect(d.payload)[0] AS payload "
                    "RETURN candidate_id,payload",
                    group=group_id,
                    ids=candidate_ids,
                )
                admissions = {row["candidate_id"]: row["payload"] async for row in admission_rows}
                fixture_rows = await session.run(
                    "MATCH (c:ResearchCandidate {group_id:$group})-"
                    "[:HAS_NUMERICAL_FIXTURE_RESULT]->(r:ResearchNumericalFixtureResult "
                    "{group_id:$group}) WHERE c.candidate_id IN $ids "
                    "WITH c,r ORDER BY r.created_at DESC "
                    "WITH c.candidate_id AS candidate_id,collect(r.payload)[0] AS payload "
                    "RETURN candidate_id,payload",
                    group=group_id,
                    ids=candidate_ids,
                )
                numerical_fixtures = {
                    row["candidate_id"]: row["payload"] async for row in fixture_rows
                }
                research_case_rows = await session.run(
                    "MATCH (c:ResearchCandidate {group_id:$group})-"
                    "[:HAS_IMPLEMENTATION_BINDING]->"
                    "(b:ResearchImplementationBinding {group_id:$group})-"
                    "[:PRODUCED_EXPERIMENT_RESULT]->"
                    "(r:ResearchExperimentResult {group_id:$group}) "
                    "WHERE c.candidate_id IN $ids WITH c,b,r ORDER BY r.created_at DESC "
                    "WITH c.candidate_id AS candidate_id, "
                    "collect({binding:b.payload,result:r.payload})[0] AS payload "
                    "RETURN candidate_id,payload",
                    group=group_id,
                    ids=candidate_ids,
                )
                research_cases = {
                    row["candidate_id"]: row["payload"]
                    async for row in research_case_rows
                }

        items: list[dict[str, Any]] = []
        for row in rows:
            candidate = CompiledCandidate.model_validate_json(row["candidate"])
            _verify_candidate_identity(candidate)
            activity = (
                TransformationActivity.model_validate_json(row["activity"])
                if row["activity"] is not None
                else None
            )
            if activity is not None and (
                activity.candidate_id != candidate.candidate_id
                or activity.workspace_id != workspace_id
            ):
                raise ResearchStoreError("Stored candidate activity has an invalid scope.")
            check = (
                CandidateCheckResult.model_validate_json(checks[candidate.candidate_id])
                if candidate.candidate_id in checks
                else None
            )
            if check is not None and (
                check.candidate_id != candidate.candidate_id
                or check.workspace_id != workspace_id
                or check.candidate_hash != candidate.content_hash
            ):
                raise ResearchStoreError("Stored candidate check has an invalid scope.")
            admission = (
                AdmissionDecision.model_validate_json(admissions[candidate.candidate_id])
                if candidate.candidate_id in admissions
                else None
            )
            if admission is not None and (
                admission.candidate_id != candidate.candidate_id
                or admission.workspace_id != workspace_id
            ):
                raise ResearchStoreError("Stored candidate decision has an invalid scope.")
            numerical_fixture = (
                NumericalFixtureReceipt.model_validate_json(
                    numerical_fixtures[candidate.candidate_id]
                )
                if candidate.candidate_id in numerical_fixtures
                else None
            )
            if numerical_fixture is not None and (
                numerical_fixture.candidate_id != candidate.candidate_id
                or numerical_fixture.workspace_id != workspace_id
                or numerical_fixture.candidate_hash != candidate.content_hash
                or numerical_fixture.problem_spec_id != candidate.problem_spec_id
                or numerical_fixture.problem_spec_hash != candidate.problem_spec_hash
                or numerical_fixture.parent_refs != candidate.parents
                or numerical_fixture.performance_claim
            ):
                raise ResearchStoreError("Stored numerical fixture has an invalid scope.")
            research_case = research_cases.get(candidate.candidate_id)
            if research_case is not None:
                if not isinstance(research_case, dict):
                    raise ResearchStoreError("Stored research case has an invalid payload.")
                binding = ImplementationBindingReceipt.model_validate_json(
                    research_case["binding"]
                )
                experiment = ResearchCaseReceipt.model_validate_json(
                    research_case["result"]
                )
                if (
                    binding.workspace_id != workspace_id
                    or binding.candidate_id != candidate.candidate_id
                    or binding.candidate_hash != candidate.content_hash
                    or experiment.workspace_id != workspace_id
                    or experiment.candidate_id != candidate.candidate_id
                    or experiment.candidate_hash != candidate.content_hash
                    or experiment.binding_id != binding.binding_id
                    or experiment.binding_hash != binding.binding_hash
                ):
                    raise ResearchStoreError("Stored research case has an invalid scope.")

            candidate_data = candidate.model_dump(mode="json")
            candidate_data.pop("ir_json")
            activity_data = activity.model_dump(mode="json") if activity is not None else None
            if activity_data is not None:
                activity_data.pop("request_json")
                activity_data.pop("candidate_hash", None)
                activity_data.pop("compiler_context_hash", None)
                activity_data.pop("compiler_context_json", None)
            check_data = check.model_dump(mode="json") if check is not None else None
            if check_data is not None:
                check_data.pop("witness")
            fixture_data = None
            if numerical_fixture is not None:
                fixture_data = {
                    "result_id": numerical_fixture.result_id,
                    "result_hash": numerical_fixture.result_hash,
                    "candidate_id": numerical_fixture.candidate_id,
                    "problem_spec_id": numerical_fixture.problem_spec_id,
                    "outcome": numerical_fixture.outcome,
                    "created_at": numerical_fixture.created_at.isoformat(),
                    "suite_version": numerical_fixture.suite_version,
                    "execution_image": numerical_fixture.execution_image,
                    "seed": numerical_fixture.seed,
                    "dtype": numerical_fixture.dtype,
                    "tolerance": numerical_fixture.tolerance,
                    "checks": numerical_fixture.checks,
                    "error_code": numerical_fixture.error_code,
                    "fixture_scope": numerical_fixture.fixture_scope,
                    "performance_claim": False,
                }
            research_case_data = None
            if research_case is not None:
                research_case_data = {
                    "binding": binding.model_dump(mode="json"),
                    "result": experiment.model_dump(mode="json"),
                    "replayed": False,
                }
            items.append(
                {
                    "candidate": candidate_data,
                    "activity": activity_data,
                    "check": check_data,
                    "admission": admission.model_dump(mode="json") if admission else None,
                    "numerical_fixture": fixture_data,
                    "research_case": research_case_data,
                }
            )
        return items, int(count_row["total"]) if count_row else 0

    async def export_compiler_replay_bundle(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        activity_id: str,
    ) -> CompilerReplayBundle:
        """Export exact replay inputs and source locators, never raw paper HTML."""
        if self.driver is None:
            raise ResearchStoreError("Replay bundle export requires durable research storage.")
        group_id = workspace_group_id(workspace_id)
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                "MATCH (c:ResearchCandidate {candidate_id:$candidate_id,group_id:$group,"
                "workspace_id:$workspace_id}) "
                "MATCH (a:TransformationActivity {activity_id:$activity_id,group_id:$group,"
                "workspace_id:$workspace_id,candidate_id:$candidate_id})-[:PRODUCED]->(c) "
                "RETURN c.payload AS candidate,a.payload AS activity LIMIT 1",
                candidate_id=candidate_id,
                activity_id=activity_id,
                group=group_id,
                workspace_id=workspace_id,
            )
            row = await result.single()
            if row is None:
                raise ResearchReferenceNotFoundError("Candidate activity was not found.")
            candidate = CompiledCandidate.model_validate_json(row["candidate"])
            activity = TransformationActivity.model_validate_json(row["activity"])
            if activity.schema_version != "transformation-activity.v2":
                raise ResearchValidationError(
                    "This legacy activity lacks the immutable compiler snapshot "
                    "required for replay."
                )
            if activity.compiler_context_json is None:
                raise ResearchStoreError("Stored compiler activity has no replay context.")
            context = CompileContext.model_validate_json(activity.compiler_context_json)

            parent_ids = sorted(
                parent.entity_id for parent in candidate.parents
                if parent.entity_id.startswith("cand_")
            )
            parents_by_id: dict[str, CompiledCandidate] = {}
            if parent_ids:
                parent_result = await session.run(
                    "MATCH (p:ResearchCandidate {group_id:$group,workspace_id:$workspace_id}) "
                    "WHERE p.candidate_id IN $ids RETURN p.candidate_id AS id,p.payload AS payload",
                    group=group_id,
                    workspace_id=workspace_id,
                    ids=parent_ids,
                )
                async for parent_row in parent_result:
                    parent = CompiledCandidate.model_validate_json(parent_row["payload"])
                    _verify_candidate_identity(parent)
                    parents_by_id[str(parent_row["id"])] = parent
                if set(parents_by_id) != set(parent_ids):
                    raise ResearchStoreError("A replay bundle candidate parent is unavailable.")

            equation_ids = sorted({
                context.target_parent.entity_id,
                context.left.port.equation_id,
                context.right.port.equation_id,
            })
            if any(item.startswith("cand_") for item in equation_ids):
                raise ResearchStoreError(
                    "Compiler context has an invalid source equation reference."
                )
            source_result = await session.run(
                "MATCH (e:Evidence {group_id:$group,kind:'Equation'}) "
                "WHERE e.uuid IN $ids "
                "OPTIONAL MATCH (p:Evidence {group_id:$group,kind:'PaperVersion',"
                "paper_id:e.paper_id,paper_version:e.paper_version}) "
                "RETURN e.uuid AS equation_id,e.payload AS equation_payload,"
                "e.source_sha256 AS equation_hash,e.paper_id AS paper_id,"
                "e.paper_version AS paper_version,p.uuid AS paper_version_id,"
                "p.payload AS paper_payload,p.source_sha256 AS paper_hash",
                group=group_id,
                ids=equation_ids,
            )
            sources: list[ReplaySourceReference] = []
            source_keys: set[str] = set()
            contract_review_requirements = {
                (context.left.port.equation_id, context.left.port.symbol_name):
                    context.left.contract_hash,
                (context.right.port.equation_id, context.right.port.symbol_name):
                    context.right.contract_hash,
            }
            contract_reviews: list[Any] = []
            async for source_row in source_result:
                equation_id = str(source_row["equation_id"])
                equation_payload = self._payload_object(
                    str(source_row["equation_payload"]), label="replay source equation"
                )
                paper_payload = self._payload_object(
                    str(source_row["paper_payload"]), label="replay source paper"
                ) if source_row["paper_payload"] is not None else {}
                version = source_row["paper_version"]
                if (
                    type(version) is not int
                    or not isinstance(source_row["paper_version_id"], str)
                    or not isinstance(source_row["paper_hash"], str)
                ):
                    raise ResearchValidationError(
                        "Replay bundle requires a versioned, hash-addressable HTML source."
                    )
                sources.append(ReplaySourceReference(
                    equation_id=equation_id,
                    equation_source_hash=(
                        source_row["equation_hash"]
                        if isinstance(source_row["equation_hash"], str)
                        else source_hash(str(source_row["equation_payload"]))
                    ),
                    paper_id=str(source_row["paper_id"]),
                    paper_version=version,
                    paper_version_id=source_row["paper_version_id"],
                    paper_html_hash=source_row["paper_hash"],
                    source_url=str(paper_payload.get("source_url") or ""),
                    anchor=str(equation_payload.get("anchor") or ""),
                    anchor_is_source=equation_payload.get("anchor_is_source") is True,
                ))
                source_keys.add(f"equation:{equation_id}:{version}")
                source_keys.add(f"paper:{source_row['paper_version_id']}:{version}")

                for (review_equation_id, symbol_name), expected_hash in (
                    contract_review_requirements.items()
                ):
                    if review_equation_id != equation_id:
                        continue
                    review_result = await session.run(
                        "MATCH (r:ContractReview {group_id:$group})-[:REVIEWS]->"
                        "(e:Evidence {uuid:$equation_id,group_id:$group,kind:'Equation'}) "
                        "WHERE r.symbol_name=$symbol_name RETURN r.payload AS payload "
                        "ORDER BY r.reviewed_at DESC,r.uuid DESC LIMIT 16",
                        group=group_id,
                        equation_id=equation_id,
                        symbol_name=symbol_name,
                    )
                    matched = None
                    async for review_row in review_result:
                        review = ContractReview.model_validate_json(review_row["payload"])
                        if (
                            review.decision == "accepted"
                            and review.reviewed_contract is not None
                            and self._stable_hash(
                                review.reviewed_contract.model_dump(mode="json")
                            ) == expected_hash
                        ):
                            matched = review
                            break
                    if matched is None:
                        raise ResearchStoreError(
                            "The exact contract review used by the compiler is unavailable."
                        )
                    contract_reviews.append(matched)
            if {item.equation_id for item in sources} != set(equation_ids):
                raise ResearchValidationError(
                    "A compiler source no longer resolves to an authorized HTML equation."
                )

            lineage_result = await session.run(
                "MATCH (a:LineageAssertion {group_id:$group}) "
                "WHERE a.source_key IN $keys OR a.target_key IN $keys "
                "OPTIONAL MATCH (a)-[:HAS_REVIEW]->(r:LineageReview {group_id:$group}) "
                "WITH a,r ORDER BY r.reviewed_at DESC,r.review_id DESC "
                "WITH a,collect(r.payload) AS reviews ORDER BY a.created_at,a.assertion_id "
                "RETURN a.payload AS payload,reviews LIMIT 201",
                group=group_id,
                keys=sorted(source_keys),
            )
            lineage_rows = [item async for item in lineage_result]
            if len(lineage_rows) > 200:
                raise ResearchValidationError("Replay bundle source lineage exceeds 200 records.")
            lineage = [
                self._lineage_record_with_reviews(item["payload"], item["reviews"])
                for item in lineage_rows
            ]

            compatibility_result = await session.run(
                "MATCH (m:CompatibilityMapping {mapping_id:$mapping_id,group_id:$group})-"
                "[:HAS_REVIEW]->(r:CompatibilityReview {group_id:$group}) "
                "RETURN r.payload AS payload ORDER BY r.reviewed_at,r.review_id LIMIT 17",
                mapping_id=context.mapping.mapping_id,
                group=group_id,
            )
            compatibility_reviews = [
                CompatibilityReview.model_validate_json(item["payload"])
                async for item in compatibility_result
            ]
            if len(compatibility_reviews) > 16:
                raise ResearchValidationError("Replay bundle has too many mapping reviews.")

            proposal = None
            proposal_review = None
            if activity.source_proposal_id is not None:
                proposal_result = await session.run(
                    "MATCH (p:ResearchProposal {proposal_id:$proposal_id,group_id:$group}) "
                    "OPTIONAL MATCH (p)-[:HAS_REVIEW]->"
                    "(r:ResearchProposalReview {group_id:$group}) "
                    "WITH p,r ORDER BY r.reviewed_at DESC,r.review_id DESC "
                    "RETURN p.payload AS proposal,collect(r.payload) AS reviews LIMIT 1",
                    proposal_id=activity.source_proposal_id,
                    group=group_id,
                )
                proposal_row = await proposal_result.single()
                if proposal_row is None:
                    raise ResearchStoreError("Compiled proposal provenance is unavailable.")
                proposal = ResearchProposal.model_validate_json(proposal_row["proposal"])
                review_payloads = [item for item in proposal_row["reviews"] if item is not None]
                if len(review_payloads) != 1:
                    raise ResearchStoreError("Proposal review provenance is incomplete.")
                proposal_review = ProposalReviewRecord.model_validate_json(review_payloads[0])

            check_result = await session.run(
                "MATCH (:ResearchCandidate {candidate_id:$candidate_id,group_id:$group})-"
                "[:HAS_CANDIDATE_CHECK]->(r:ResearchCandidateCheck {group_id:$group}) "
                "RETURN r.payload AS payload ORDER BY r.created_at,r.check_id LIMIT 33",
                candidate_id=candidate_id,
                group=group_id,
            )
            checks = [CandidateCheckResult.model_validate_json(item["payload"])
                      async for item in check_result]
            admission_result = await session.run(
                "MATCH (:ResearchCandidate {candidate_id:$candidate_id,group_id:$group})-"
                "[:HAS_ADMISSION_DECISION]->(r:ResearchAdmissionDecision {group_id:$group}) "
                "RETURN r.payload AS payload, r.replay_input_payload AS replay_input_payload "
                "ORDER BY r.created_at,r.decision_id LIMIT 33",
                candidate_id=candidate_id,
                group=group_id,
            )
            admissions = []
            admission_replay_inputs = []
            async for item in admission_result:
                admissions.append(AdmissionDecision.model_validate_json(item["payload"]))
                if item["replay_input_payload"] is not None:
                    admission_replay_inputs.append(
                        AdmissionReplayInput.model_validate_json(item["replay_input_payload"])
                    )
            fixture_result = await session.run(
                "MATCH (:ResearchCandidate {candidate_id:$candidate_id,group_id:$group})-"
                "[:HAS_NUMERICAL_FIXTURE_RESULT]->(r:ResearchNumericalFixtureResult "
                "{group_id:$group}) RETURN r.payload AS payload "
                "ORDER BY r.created_at,r.result_id LIMIT 33",
                candidate_id=candidate_id,
                group=group_id,
            )
            fixtures = [NumericalFixtureReceipt.model_validate_json(item["payload"])
                        async for item in fixture_result]
            research_case_result = await session.run(
                "MATCH (:ResearchCandidate {candidate_id:$candidate_id,group_id:$group})-"
                "[:HAS_IMPLEMENTATION_BINDING]->"
                "(b:ResearchImplementationBinding {group_id:$group})-"
                "[:PRODUCED_EXPERIMENT_RESULT]->"
                "(r:ResearchExperimentResult {group_id:$group}) "
                "RETURN b.payload AS binding,r.payload AS result "
                "ORDER BY r.created_at,r.result_id LIMIT 33",
                candidate_id=candidate_id,
                group=group_id,
            )
            implementation_bindings = []
            research_cases = []
            async for item in research_case_result:
                implementation_bindings.append(
                    ImplementationBindingReceipt.model_validate_json(item["binding"])
                )
                research_cases.append(
                    ResearchCaseReceipt.model_validate_json(item["result"])
                )
            review_ids = {ref for item in admission_replay_inputs for ref in item.review_result_ids}
            review_rows = await session.run(
                "MATCH (r:ResearchProtocolReview {group_id:$group}) "
                "WHERE r.review_id IN $ids RETURN r.payload AS payload ORDER BY r.review_id",
                group=group_id, ids=sorted(review_ids),
            )
            protocol_reviews = [ResearchProtocolReview.model_validate_json(item["payload"])
                                async for item in review_rows]

        if any(len(items) > 32 for items in (
            checks, admissions, fixtures, implementation_bindings, research_cases
        )):
            raise ResearchValidationError("Replay bundle result history exceeds its bounded limit.")
        try:
            return make_compiler_replay_bundle(
                workspace_id=workspace_id,
                candidate=candidate,
                activity=activity,
                parent_candidates=tuple(parents_by_id[key] for key in parent_ids),
                sources=tuple(sources),
                lineage_assertions=tuple(lineage),
                compatibility_reviews=tuple(compatibility_reviews),
                contract_reviews=tuple(contract_reviews),
                proposal=proposal,
                proposal_review=proposal_review,
                candidate_checks=tuple(checks),
                admission_decisions=tuple(admissions),
                admission_replay_inputs=tuple(admission_replay_inputs),
                numerical_fixtures=tuple(fixtures),
                implementation_bindings=tuple(implementation_bindings),
                research_cases=tuple(research_cases),
                protocol_reviews=tuple(protocol_reviews),
            )
        except ValueError as exc:
            raise ResearchStoreError(
                "Stored candidate records cannot form a replay bundle."
            ) from exc

    async def list_compatibility_mappings(
        self,
        *,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        if self.driver is None:
            raise ResearchStoreError(
                "Current compatibility dependencies require configured research storage."
            )
        group_id = workspace_group_id(workspace_id)
        async with self.driver.session(database=self.database) as session:
            count_result = await session.run(
                "MATCH (m:CompatibilityMapping {group_id: $group}) RETURN count(m) AS total",
                group=group_id,
            )
            count_row = await count_result.single()
            rows = await session.run(
                """
                MATCH (m:CompatibilityMapping {group_id: $group})
                OPTIONAL MATCH (m)-[:HAS_REVIEW]->(r:CompatibilityReview {group_id: $group})
                RETURN m.mapping_payload AS mapping_payload,
                       m.assessment_payload AS assessment_payload,
                       collect(r.payload) AS reviews, m.mapping_id AS mapping_id
                ORDER BY mapping_id ASC SKIP $offset LIMIT $limit
                """,
                group=group_id,
                offset=offset,
                limit=limit,
            )
            items: list[dict[str, Any]] = []
            async for row in rows:
                mapping = PortMapping.model_validate_json(row["mapping_payload"])
                effective, assessment = self._effective_compatibility(
                    mapping,
                    CompatibilityAssessment.model_validate_json(row["assessment_payload"]),
                    await self._current_compatibility_dependencies(
                        session,
                        group_id=group_id,
                        mapping=mapping,
                    ),
                    row["reviews"],
                )
                items.append(
                    {
                        "mapping": effective.model_dump(mode="json"),
                        "assessment": assessment.model_dump(mode="json"),
                        "reviews": [
                            item.model_dump(mode="json")
                            for item in self._compatibility_reviews(row["reviews"])
                        ],
                    }
                )
            return items, int(count_row["total"]) if count_row else 0

    async def review_compatibility_mapping(
        self,
        *,
        workspace_id: str,
        mapping_id: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: str,
        notes: str = "",
        idempotency_key: str = "",
    ) -> CompatibilityReview:
        if not idempotency_key:
            raise ResearchValidationError(
                "An idempotency key is required for compatibility review."
            )
        if self.driver is None:
            raise ResearchStoreError(
                "Source-backed compatibility requires configured research storage."
            )
        group_id = workspace_group_id(workspace_id)
        review_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{group_id}/compatibility-review/{mapping_id}/{reviewer_id}/{idempotency_key}",
            )
        )
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_review_compatibility_mapping,
                group_id,
                mapping_id,
                reviewer_id,
                reviewer_role,
                decision,
                notes,
                review_id,
            )

    async def _tx_review_compatibility_mapping(
        self,
        tx,
        group_id: str,
        mapping_id: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: str,
        notes: str,
        review_id: str,
    ) -> CompatibilityReview:
        await lock_research_workspace(tx, group_id)
        mapping_result = await tx.run(
            """
            MATCH (m:CompatibilityMapping {mapping_id: $mapping_id, group_id: $group})
            SET m.review_lock = coalesce(m.review_lock, 0) + 1
            RETURN m.mapping_payload AS mapping_payload, m.assessment_payload AS assessment_payload
            LIMIT 1
            """,
            mapping_id=mapping_id,
            group=group_id,
        )
        mapping_row = await mapping_result.single()
        if mapping_row is None:
            raise ResearchReferenceNotFoundError(
                f"Mapping '{mapping_id}' not found in this workspace."
            )
        existing_result = await tx.run(
            """
            MATCH (r:CompatibilityReview {review_id: $review_id})
            RETURN r.group_id AS group_id, r.payload AS payload
            LIMIT 1
            """,
            review_id=review_id,
        )
        existing = await existing_result.single()
        if existing is not None:
            stored = CompatibilityReview.model_validate_json(existing["payload"])
            if (
                existing["group_id"] != group_id
                or stored.mapping_id != mapping_id
                or stored.reviewer_id != reviewer_id
                or stored.reviewer_role != reviewer_role
                or stored.decision != decision
                or stored.notes != notes
            ):
                raise IdempotencyConflictError(
                    "Compatibility review idempotency key conflicts with prior content."
                )
            return stored
        mapping = PortMapping.model_validate_json(mapping_row["mapping_payload"])
        initial = CompatibilityAssessment.model_validate_json(mapping_row["assessment_payload"])
        if initial.policy_version != COMPATIBILITY_POLICY_VERSION:
            raise ResearchValidationError(
                "Mapping policy is outdated; create a new assessment before review."
            )
        dependencies = await self._current_compatibility_dependencies(
            tx,
            group_id=group_id,
            mapping=mapping,
        )
        if compute_dependency_fingerprint(dependencies) != initial.dependency_fingerprint:
            raise ResearchValidationError(
                "Mapping is stale; create a new assessment before review."
            )
        review = CompatibilityReview(
            review_id=review_id,
            mapping_id=mapping_id,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision=decision,
            notes=notes,
            reviewed_at=datetime.now(UTC).isoformat(),
            dependency_fingerprint=initial.dependency_fingerprint,
        )
        await tx.run(
            """
            MATCH (m:CompatibilityMapping {mapping_id: $mapping_id, group_id: $group})
            CREATE (r:CompatibilityReview {review_id: $review_id, group_id: $group,
                                            payload: $payload, reviewed_at: $reviewed_at})
            CREATE (m)-[:HAS_REVIEW]->(r)
            """,
            mapping_id=mapping_id,
            group=group_id,
            review_id=review_id,
            payload=canonical_json(review.model_dump(mode="json")),
            reviewed_at=review.reviewed_at,
        )
        return review

    @staticmethod
    def _validate_evolution_write(actor_id: str, idempotency_key: str) -> None:
        if not actor_id.strip() or len(actor_id) > 200 or not actor_id.isascii():
            raise ResearchAuthorizationError("A bounded trusted actor identity is required.")
        if (
            not idempotency_key
            or len(idempotency_key) > 200
            or not idempotency_key.isascii()
        ):
            raise ResearchValidationError("A bounded ASCII idempotency key is required.")

    async def start_evolution_campaign(
        self,
        *,
        workspace_id: str,
        spec_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> tuple[EvolutionCampaign, bool]:
        """Create the one immutable-spec evolution stream; never restart existing state."""
        self._validate_evolution_write(actor_id, idempotency_key)
        group_id = workspace_group_id(workspace_id)
        intent_hash = self._stable_hash([workspace_id, spec_id, actor_id])
        receipt_key = (
            f"{group_id}:evolution-start:"
            f"{hashlib.sha256(idempotency_key.encode('ascii')).hexdigest()}"
        )

        if self.driver is None:
            receipt = self._mem_idemp.get(receipt_key)
            if receipt is not None:
                if receipt["intent_hash"] != intent_hash:
                    raise IdempotencyConflictError(
                        "Evolution start retry key conflicts with prior content."
                    )
                return EvolutionCampaign.model_validate_json(receipt["payload"]), True
            spec_record = self._mem_specs.get(spec_id)
            if spec_record is None or spec_record["group_id"] != group_id:
                raise ResearchReferenceNotFoundError(
                    "ProblemSpec was not found in this workspace."
                )
            spec = ProblemSpecSnapshot.model_validate_json(spec_record["payload"])
            expected = start_campaign(spec, actor_id=actor_id)
            existing = self._mem_evolutions.get(expected.evolution_id)
            if existing is not None and existing["group_id"] != group_id:
                raise ResearchAuthorizationError(
                    "Evolution campaign belongs to another workspace."
                )
            campaign = (
                EvolutionCampaign.model_validate_json(existing["payload"])
                if existing is not None
                else expected
            )
            payload = canonical_json(campaign.model_dump(mode="json"))
            if existing is None:
                self._mem_evolutions[campaign.evolution_id] = {
                    "group_id": group_id,
                    "workspace_id": workspace_id,
                    "spec_id": spec_id,
                    "payload": payload,
                    "revision": 0,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            self._mem_idemp[receipt_key] = {
                "intent_hash": intent_hash,
                "payload": payload,
            }
            return campaign, existing is not None

        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._tx_start_evolution_campaign,
                group_id,
                workspace_id,
                spec_id,
                actor_id,
                receipt_key,
                intent_hash,
            )

    @staticmethod
    async def _tx_start_evolution_campaign(
        tx,
        group_id: str,
        workspace_id: str,
        spec_id: str,
        actor_id: str,
        receipt_key: str,
        intent_hash: str,
    ) -> tuple[EvolutionCampaign, bool]:
        await lock_research_workspace(tx, group_id)
        receipt = await Neo4jResearchStore._read_create_receipt(
            tx, receipt_key, group_id, intent_hash
        )
        if receipt is not None:
            return EvolutionCampaign.model_validate_json(receipt), True
        result = await tx.run(
            "MATCH (s:ProblemSpec {spec_id:$spec_id,group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=spec_id,
            group=group_id,
        )
        row = await result.single()
        if row is None:
            raise ResearchReferenceNotFoundError(
                "ProblemSpec was not found in this workspace."
            )
        spec = ProblemSpecSnapshot.model_validate_json(row["payload"])
        expected = start_campaign(spec, actor_id=actor_id)
        existing_result = await tx.run(
            "MATCH (e:EvolutionCampaign {evolution_id:$evolution_id}) "
            "RETURN e.group_id AS group_id,e.payload AS payload LIMIT 1",
            evolution_id=expected.evolution_id,
        )
        existing = await existing_result.single()
        reused = existing is not None
        if existing is not None:
            if existing["group_id"] != group_id:
                raise ResearchAuthorizationError(
                    "Evolution campaign belongs to another workspace."
                )
            campaign = EvolutionCampaign.model_validate_json(existing["payload"])
        else:
            campaign = expected
            payload = canonical_json(campaign.model_dump(mode="json"))
            await tx.run(
                "MATCH (s:ProblemSpec {spec_id:$spec_id,group_id:$group}) "
                "CREATE (e:EvolutionCampaign {evolution_id:$evolution_id,group_id:$group,"
                "workspace_id:$workspace_id,spec_id:$spec_id,payload:$payload,revision:0,"
                "updated_at:$updated_at}) CREATE (s)-[:HAS_EVOLUTION]->(e)",
                spec_id=spec_id,
                group=group_id,
                workspace_id=workspace_id,
                evolution_id=campaign.evolution_id,
                payload=payload,
                updated_at=datetime.now(UTC).isoformat(),
            )
        payload = canonical_json(campaign.model_dump(mode="json"))
        await Neo4jResearchStore._save_create_receipt(
            tx, receipt_key, group_id, intent_hash, payload
        )
        return campaign, reused

    async def get_evolution_campaign(
        self, *, workspace_id: str, evolution_id: str
    ) -> EvolutionCampaign | None:
        group_id = workspace_group_id(workspace_id)
        if self.driver is None:
            record = self._mem_evolutions.get(evolution_id)
            if record is None or record["group_id"] != group_id:
                return None
            return EvolutionCampaign.model_validate_json(record["payload"])
        result = await self.driver.execute_query(
            "MATCH (e:EvolutionCampaign {evolution_id:$evolution_id,group_id:$group}) "
            "RETURN e.payload AS payload LIMIT 1",
            evolution_id=evolution_id,
            group=group_id,
            database_=self.database,
        )
        if not result.records:
            return None
        return EvolutionCampaign.model_validate_json(result.records[0]["payload"])

    async def list_evolution_campaigns(
        self, *, workspace_id: str, limit: int = 20, offset: int = 0
    ) -> tuple[list[EvolutionCampaign], int]:
        group_id = workspace_group_id(workspace_id)
        limit, offset = min(max(limit, 1), 50), max(offset, 0)
        if self.driver is None:
            records = sorted(
                (
                    item
                    for item in self._mem_evolutions.values()
                    if item["group_id"] == group_id
                ),
                key=lambda item: item["updated_at"],
                reverse=True,
            )
            return (
                [
                    EvolutionCampaign.model_validate_json(item["payload"])
                    for item in records[offset : offset + limit]
                ],
                len(records),
            )
        result = await self.driver.execute_query(
            "MATCH (e:EvolutionCampaign {group_id:$group}) "
            "WITH e ORDER BY e.updated_at DESC,e.evolution_id DESC "
            "WITH collect(e.payload) AS payloads "
            "RETURN payloads[$offset..$stop] AS page,size(payloads) AS total",
            group=group_id,
            offset=offset,
            stop=offset + limit,
            database_=self.database,
        )
        row = result.records[0]
        return (
            [EvolutionCampaign.model_validate_json(item) for item in row["page"]],
            int(row["total"]),
        )

    async def _mutate_evolution_campaign(
        self,
        *,
        workspace_id: str,
        evolution_id: str,
        actor_id: str,
        idempotency_key: str,
        operation: str,
        intent: object,
        mutate: Callable[[EvolutionCampaign], EvolutionCampaign],
        validate: Callable[[Any], Awaitable[None]] | None = None,
    ) -> tuple[EvolutionCampaign, bool]:
        self._validate_evolution_write(actor_id, idempotency_key)
        group_id = workspace_group_id(workspace_id)
        key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
        receipt_key = f"{group_id}:evolution:{evolution_id}:{operation}:{key_hash}"
        intent_hash = self._stable_hash([workspace_id, evolution_id, actor_id, operation, intent])

        if self.driver is None:
            if validate is not None:
                raise ResearchStoreError("Evidence-backed evolution requires durable storage.")
            receipt = self._mem_idemp.get(receipt_key)
            if receipt is not None:
                if receipt["intent_hash"] != intent_hash:
                    raise IdempotencyConflictError(
                        "Evolution retry key conflicts with prior content."
                    )
                return EvolutionCampaign.model_validate_json(receipt["payload"]), True
            record = self._mem_evolutions.get(evolution_id)
            if record is None or record["group_id"] != group_id:
                raise ResearchReferenceNotFoundError(
                    "Evolution campaign was not found in this workspace."
                )
            campaign = mutate(EvolutionCampaign.model_validate_json(record["payload"]))
            payload = canonical_json(campaign.model_dump(mode="json"))
            record.update(
                payload=payload,
                revision=int(record["revision"]) + 1,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._mem_idemp[receipt_key] = {
                "intent_hash": intent_hash,
                "payload": payload,
            }
            return campaign, False

        async def write(tx):
            await lock_research_workspace(tx, group_id)
            receipt = await self._read_create_receipt(
                tx, receipt_key, group_id, intent_hash
            )
            if receipt is not None:
                return EvolutionCampaign.model_validate_json(receipt), True
            if validate is not None:
                await validate(tx)
            result = await tx.run(
                "MATCH (e:EvolutionCampaign {evolution_id:$evolution_id,group_id:$group}) "
                "RETURN e.payload AS payload,e.revision AS revision LIMIT 1",
                evolution_id=evolution_id,
                group=group_id,
            )
            row = await result.single()
            if row is None:
                raise ResearchReferenceNotFoundError(
                    "Evolution campaign was not found in this workspace."
                )
            campaign = mutate(EvolutionCampaign.model_validate_json(row["payload"]))
            payload = canonical_json(campaign.model_dump(mode="json"))
            update = await tx.run(
                "MATCH (e:EvolutionCampaign {evolution_id:$evolution_id,group_id:$group,"
                "revision:$revision}) SET e.payload=$payload,e.revision=$revision+1,"
                "e.updated_at=$updated_at RETURN e.revision AS revision",
                evolution_id=evolution_id,
                group=group_id,
                revision=int(row["revision"]),
                payload=payload,
                updated_at=datetime.now(UTC).isoformat(),
            )
            if await update.single() is None:
                raise ResearchStoreError("Evolution campaign changed concurrently.")
            await self._save_create_receipt(
                tx, receipt_key, group_id, intent_hash, payload
            )
            return campaign, False

        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(write)

    async def reserve_evolution_generation(
        self,
        *,
        workspace_id: str,
        evolution_id: str,
        candidate_slots: int,
        compute_reserved: float,
        actor_id: str,
        idempotency_key: str,
        request_context: object = None,
    ) -> tuple[EvolutionCampaign, bool]:
        return await self._mutate_evolution_campaign(
            workspace_id=workspace_id,
            evolution_id=evolution_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation="reserve",
            intent=[candidate_slots, compute_reserved] if request_context is None else (
                [candidate_slots, compute_reserved, request_context]
            ),
            mutate=lambda campaign: reserve_generation(
                campaign,
                candidate_slots=candidate_slots,
                compute_reserved=compute_reserved,
                actor_id=actor_id,
            ),
        )

    async def select_evolution_parents(
        self,
        *,
        workspace_id: str,
        evolution_id: str,
        count: int,
        seed: int,
        actor_id: str,
        idempotency_key: str,
    ) -> tuple[EvolutionCampaign, bool]:
        return await self._mutate_evolution_campaign(
            workspace_id=workspace_id,
            evolution_id=evolution_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation="select",
            intent=[count, seed],
            mutate=lambda campaign: select_parents(
                campaign,
                count=count,
                seed=seed,
                actor_id=actor_id,
            )[0],
        )

    async def freeze_evolution_finalists(
        self,
        *,
        workspace_id: str,
        evolution_id: str,
        finalist_ids: tuple[str, ...],
        actor_id: str,
        idempotency_key: str,
    ) -> tuple[EvolutionCampaign, bool]:
        return await self._mutate_evolution_campaign(
            workspace_id=workspace_id,
            evolution_id=evolution_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation="freeze-finalists",
            intent=finalist_ids,
            mutate=lambda campaign: freeze_finalists(
                campaign, finalist_ids, actor_id=actor_id
            ),
        )

    async def stop_evolution_campaign(
        self,
        *,
        workspace_id: str,
        evolution_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> tuple[EvolutionCampaign, bool]:
        return await self._mutate_evolution_campaign(
            workspace_id=workspace_id,
            evolution_id=evolution_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            operation="stop",
            intent=[],
            mutate=lambda campaign: stop_campaign(campaign, actor_id=actor_id),
        )

    async def settle_evolution_from_results(
        self, *, workspace_id: str, evolution_id: str, reservation_id: str | None,
        candidate_results: tuple[tuple[str, str], ...], actor_id: str, idempotency_key: str,
        search_parent_ids: tuple[str, ...] = (), confirmation: bool = False,
    ) -> tuple[EvolutionCampaign, bool]:
        """Resolve every metric and gate inside the same transaction as campaign settlement."""
        evaluations: list[EvolutionEvaluation] = []
        candidates: list[CompiledCandidate] = []
        group = workspace_group_id(workspace_id)

        async def validate(tx):
            evaluations.clear()
            candidates.clear()
            campaign_row = await (await tx.run(
                "MATCH (e:EvolutionCampaign {evolution_id:$id,group_id:$group}) "
                "RETURN e.payload AS payload", id=evolution_id, group=group,
            )).single()
            if campaign_row is None:
                raise ResearchReferenceNotFoundError("Evolution campaign was not found.")
            campaign = EvolutionCampaign.model_validate_json(campaign_row["payload"])
            if search_parent_ids:
                selection = campaign.selections[-1] if campaign.selections else None
                if selection is None or selection.candidate_ids != search_parent_ids:
                    raise ResearchValidationError("Search parents must be the persisted selection.")
            for candidate_id, result_id in candidate_results:
                candidate, spec, _ = await self._tx_prepare_candidate_numerical_fixture(
                    tx, group, workspace_id, candidate_id, RESEARCH_CASE_SEEDS[0],
                )
                if spec.content_hash != campaign.spec.content_hash:
                    raise ResearchValidationError("Candidate is outside the frozen campaign.")
                row = await (await tx.run(
                    "MATCH (c:ResearchCandidate {candidate_id:$id,group_id:$group})-"
                    "[:HAS_CANDIDATE_CHECK]->(k:ResearchCandidateCheck {group_id:$group}) "
                    "RETURN k.payload AS payload ORDER BY k.created_at DESC LIMIT 1",
                    id=candidate_id, group=group,
                )).single()
                check = CandidateCheckResult.model_validate_json(row["payload"]) if row else None
                context = await self._tx_protocol_admission_context(
                    tx, group, candidate, spec, check,
                    result_id=None if confirmation else result_id,
                    mapping_freshness="current", mapping_usable=True,
                )
                if context is None:
                    raise ResearchValidationError("Scoped search evidence is missing.")
                result_row = await (await tx.run(
                    "MATCH (c:ResearchCandidate {candidate_id:$candidate,group_id:$group})-"
                    "[:HAS_EXPERIMENT_RESULT]->(r:ResearchExperimentResult "
                    "{result_id:$result,group_id:$group}) RETURN r.payload AS payload",
                    candidate=candidate_id, result=result_id, group=group,
                )).single()
                if result_row is None:
                    raise ResearchReferenceNotFoundError("Evaluation result was not found.")
                result = ResearchCaseReceipt.model_validate_json(result_row["payload"])
                role = "holdout" if confirmation else "search"
                if result.evaluation_role != role or (
                    result.evolution_id not in {None, evolution_id}
                ) or (confirmation and result.evolution_id != evolution_id):
                    raise ResearchValidationError("Result cannot be used in this campaign phase.")
                # Resolve and validate the exact result binding, even when selection
                # uses the latest search receipt for the current human-scope gate.
                await self._tx_protocol_admission_context(
                    tx, group, candidate, spec, check, result_id=result_id,
                    mapping_freshness="current", mapping_usable=True,
                )
                decision = decide_admission(
                    context, action="can_enter_parent_pool", actor_id=actor_id,
                )
                replay_input = make_admission_replay_input(context, decision)
                await tx.run(
                    "MATCH (c:ResearchCandidate {candidate_id:$candidate,group_id:$group}) "
                    "MERGE (d:ResearchAdmissionDecision {decision_id:$id,group_id:$group}) "
                    "ON CREATE SET d.workspace_id=$workspace,d.candidate_id=$candidate,"
                    "d.policy_version=$policy,d.payload=$payload,d.replay_input_payload=$input,"
                    "d.created_at=$created MERGE (c)-[:HAS_ADMISSION_DECISION]->(d)",
                    candidate=candidate_id, group=group, id=decision.decision_id,
                    workspace=workspace_id, policy=decision.policy_version,
                    payload=canonical_json(decision.model_dump(mode="json")),
                    input=canonical_json(replay_input.model_dump(mode="json")),
                    created=decision.decided_at.isoformat(),
                )
                eligible = decision.allowed and result.quality_constraints_met
                cost = float(result.search_cost["wall_time_ms"]) / 1000
                metric = spec.definition.metrics
                if len(metric) != 1 or metric[0].name != result.primary_metric:
                    raise ResearchValidationError(
                        "The registered pilot supports its frozen metric only."
                    )
                metrics = (MetricSample(
                    name=metric[0].name, unit=metric[0].unit, direction=metric[0].direction,
                    value=quality_summary(result.trials, role)[0],
                ),) if eligible else ()
                refs = (result.result_id, *decision.input_result_ids)
                if confirmation and eligible:
                    evaluation = make_confirmation_evaluation(
                        candidate, spec=spec, metrics=metrics, result_ids=refs,
                        compute_cost=cost, admission=decision,
                    )
                else:
                    outcome = "eligible" if eligible else "invalid"
                    if any(item.outcome == "timeout" for item in result.trials):
                        outcome = "timeout"
                    elif any(item.outcome == "error" for item in result.trials):
                        outcome = "infrastructure_error"
                    evaluation = make_evaluation(
                        candidate, spec=spec, outcome=outcome, metrics=metrics,
                        result_ids=refs, compute_cost=cost, admission=decision,
                        failure_reason=None if eligible else (
                            "protocol_quality_or_current_admission_not_supported"
                        ), search_parent_ids=search_parent_ids,
                    )
                    if confirmation:
                        payload = evaluation.model_dump(mode="json", exclude={"evaluation_id"})
                        if not evaluation.search_parent_ids:
                            payload.pop("search_parent_ids", None)
                        payload["data_role"] = "holdout"
                        evaluation = EvolutionEvaluation(
                            evaluation_id="eev_" + self._stable_hash(payload)[:32], **payload,
                        )
                evaluations.append(evaluation)
                candidates.append(candidate)
                for parent_id in search_parent_ids:
                    await tx.run(
                        "MATCH (c:ResearchCandidate {candidate_id:$child,group_id:$group}) "
                        "MATCH (p:ResearchCandidate {candidate_id:$parent,group_id:$group}) "
                        "MERGE (c)-[:EVOLVED_FROM {evolution_id:$evolution}]->(p)",
                        child=candidate_id, parent=parent_id, group=group, evolution=evolution_id,
                    )

        return await self._mutate_evolution_campaign(
            workspace_id=workspace_id, evolution_id=evolution_id, actor_id=actor_id,
            idempotency_key=idempotency_key, operation="confirm" if confirmation else "settle",
            intent=[reservation_id, candidate_results, search_parent_ids], validate=validate,
            mutate=lambda campaign: record_confirmation(
                campaign, tuple(evaluations), actor_id=actor_id,
            ) if confirmation else settle_generation(
                campaign, reservation_id=reservation_id, evaluations=tuple(evaluations),
                resolved_candidates=tuple(candidates), actor_id=actor_id,
            ),
        )

    async def evolution_candidate_context(self, *, workspace_id: str, candidate_id: str):
        candidate, spec, parent = await self.prepare_registered_research_case(
            workspace_id=workspace_id, candidate_id=candidate_id,
        )
        rows, _, _ = await self.driver.execute_query(
            "MATCH (a:TransformationActivity {group_id:$group})-[:PRODUCED]->"
            "(c:ResearchCandidate {candidate_id:$id,group_id:$group}) "
            "RETURN a.payload AS payload ORDER BY a.created_at DESC LIMIT 1",
            id=candidate_id, group=workspace_group_id(workspace_id), database_=self.database,
        )
        if not rows:
            raise ResearchReferenceNotFoundError("Candidate compiler context is missing.")
        activity = TransformationActivity.model_validate_json(rows[0]["payload"])
        if activity.compiler_context_json is None:
            raise ResearchValidationError("Evolution requires a versioned compiler context.")
        return candidate, spec, parent, CompileContext.model_validate_json(
            activity.compiler_context_json
        )

    async def export_evolution_bundle(self, *, workspace_id: str, evolution_id: str):
        from app.evolution_replay import make_evolution_bundle

        campaign = await self.get_evolution_campaign(
            workspace_id=workspace_id, evolution_id=evolution_id,
        )
        if campaign is None:
            raise ResearchReferenceNotFoundError("Evolution campaign was not found.")
        ids = sorted({e.candidate_id for e in campaign.evaluations})
        if not ids or len(ids) > 32:
            raise ResearchValidationError("Pilot replay needs 1–32 evaluated candidates.")
        bundles = []
        for candidate_id in ids:
            rows, _, _ = await self.driver.execute_query(
                "MATCH (a:TransformationActivity {group_id:$group})-[:PRODUCED]->"
                "(c:ResearchCandidate {candidate_id:$id,group_id:$group}) "
                "RETURN a.activity_id AS id ORDER BY a.created_at DESC LIMIT 1",
                group=workspace_group_id(workspace_id), id=candidate_id, database_=self.database,
            )
            if not rows:
                raise ResearchStoreError("Evolution compiler activity is missing.")
            bundles.append(await self.export_compiler_replay_bundle(
                workspace_id=workspace_id, candidate_id=candidate_id, activity_id=rows[0]["id"],
            ))
        return make_evolution_bundle(campaign, bundles)
