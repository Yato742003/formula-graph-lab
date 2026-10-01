from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from neo4j import AsyncGraphDatabase

from app.analysis_versions import make_analysis_version, source_hash, source_payload
from app.enrichment import EnrichmentAttempt
from app.episodes import workspace_group_id
from app.evidence import EvidenceGraph, ReportedClaim, analyze_equation
from app.extractor import ExtractedEquation
from app.models import (
    EvidenceGraphEdge,
    EvidenceGraphNode,
    EvidenceGraphSnapshotRequest,
    EvidenceGraphSnapshotResponse,
)
from app.research_lock import lock_research_workspace
from app.search import (
    EvidenceCandidate,
    EvidenceSearchFilters,
    build_lucene_query,
)
from app.symbol_contracts import (
    ContractReview,
    contract_review_state_hash,
    latest_contract_reviews,
)
from app.verification import CheckResult

MAX_SNAPSHOT_NODES = 500
MAX_SNAPSHOT_EDGES = 1_500
logger = logging.getLogger(__name__)


class EvidenceSnapshotDataError(RuntimeError):
    """Persisted graph evidence is not structurally valid."""


@dataclass(frozen=True)
class ImportReceipt:
    import_uuid: str
    node_count: int
    edge_count: int
    episode_count: int
    replayed: bool


@dataclass(frozen=True)
class ContractReviewReceipt:
    review: ContractReview
    replayed: bool


@dataclass(frozen=True)
class CheckResultReceipt:
    result: CheckResult
    replayed: bool


@dataclass(frozen=True)
class PaperVersionSnapshot:
    uuid: str
    paper_id: str
    version: int | None
    valid_at: datetime
    payload: str


class Neo4jEvidenceStore:
    """Atomic exact-evidence writes; no model participates in identity or relation creation.

    Evidence is stored outside Graphiti's mutable Entity/RELATES_TO semantic layer.
    Episodes and sagas use Graphiti's schema, so later prose enrichment can attach
    to the same source episodes without rewriting exact equations.
    """

    def __init__(self, driver, *, database: str = "neo4j"):
        self.driver = driver
        self.database = database

    @classmethod
    def connect(cls, uri: str, user: str, password: str, *, database: str = "neo4j"):
        return cls(AsyncGraphDatabase.driver(uri, auth=(user, password)), database=database)

    async def initialize(self) -> None:
        await self.driver.execute_query(
            "CREATE CONSTRAINT fgl_research_workspace_lock IF NOT EXISTS "
            "FOR (n:ResearchWorkspaceLock) REQUIRE n.group_id IS UNIQUE",
            database_=self.database,
        )
        for label in ("ResearchJob", "ResearchQueueLock", "ServiceRequestNonce"):
            await self.driver.execute_query(
                f"CREATE CONSTRAINT fgl_{label.lower()}_id IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE", database_=self.database,
            )
        await self.driver.execute_query(
            "CREATE INDEX fgl_service_nonce_expiry IF NOT EXISTS "
            "FOR (n:ServiceRequestNonce) ON (n.expires_at)", database_=self.database,
        )
        for label, constraint in [
            ("Evidence", "fgl_evidence_uuid"),
            ("EvidenceImport", "fgl_import_uuid"),
            ("Episodic", "fgl_episode_uuid"),
            ("Saga", "fgl_saga_uuid"),
            ("SemanticEnrichment", "fgl_semantic_enrichment_uuid"),
            ("ContractReview", "fgl_contract_review_uuid"),
            ("CheckResult", "fgl_check_result_uuid"),
            ("FormulaAnalysisVersion", "fgl_formula_analysis_uuid"),
            ("AnalysisMigration", "fgl_analysis_migration_uuid"),
        ]:
            # Labels/names are code-owned constants, never request inputs.
            await self.driver.execute_query(
                f"CREATE CONSTRAINT {constraint} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.uuid IS UNIQUE",
                database_=self.database,
            )
        await self.driver.execute_query(
            "CREATE FULLTEXT INDEX fgl_evidence_search IF NOT EXISTS "
            "FOR (n:Evidence) ON EACH [n.group_id, n.logical_id, n.payload]",
            database_=self.database,
        )
        await self.driver.execute_query(
            "CALL db.awaitIndex('fgl_evidence_search', 30)",
            database_=self.database,
        )

    async def close(self) -> None:
        await self.driver.close()

    async def consume_service_nonce(self, nonce: str, expires_at: int) -> bool:
        # One server-owned claim per HTTP request also survives managed TX retries.
        claim = uuid4().hex
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(
                self._consume_service_nonce, nonce, expires_at, claim,
            )

    @staticmethod
    async def _consume_service_nonce(tx, nonce: str, expires_at: int, claim: str) -> bool:
        result = await tx.run(
            "MERGE (n:ServiceRequestNonce {id:$nonce}) "
            "ON CREATE SET n.claim=$claim, n.expires_at=$expires_at "
            "RETURN n.claim=$claim AS accepted",
            nonce=nonce, claim=claim, expires_at=expires_at,
        )
        row = await result.single()
        # Retain expired nonces for another five minutes across replica clock skew.
        await tx.run(
            "MATCH (n:ServiceRequestNonce) WHERE n.expires_at < $cutoff "
            "WITH n LIMIT 100 DELETE n", cutoff=int(time.time()) - 300,
        )
        return bool(row and row["accepted"])

    async def ingest(self, graph: EvidenceGraph) -> ImportReceipt:
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(self._write, graph)

    async def equation_source(self, *, workspace_id: str, equation_uuid: str) -> dict:
        rows, _, _ = await self.driver.execute_query(
            "MATCH (e:Evidence {uuid:$uuid, group_id:$group, kind:'Equation'}) "
            "RETURN e.payload AS payload",
            uuid=equation_uuid, group=workspace_group_id(workspace_id), database_=self.database,
        )
        if not rows:
            raise ValueError("Check target must exist in this workspace.")
        return source_payload(rows[0]["payload"])

    async def equation_contract_reviews(
        self, *, workspace_id: str, equation_uuid: str,
    ) -> list[ContractReview]:
        rows, _, _ = await self.driver.execute_query(
            "MATCH (equation:Evidence {uuid:$uuid, group_id:$group, kind:'Equation'}) "
            "OPTIONAL MATCH (review:ContractReview {group_id:$group})-[:REVIEWS]->(equation) "
            "RETURN collect(review.payload) AS payloads",
            uuid=equation_uuid,
            group=workspace_group_id(workspace_id),
            database_=self.database,
        )
        if not rows:
            raise ValueError("Check target must exist in this workspace.")
        return latest_contract_reviews([
            ContractReview.model_validate_json(payload)
            for payload in rows[0]["payloads"]
            if payload is not None
        ])

    async def append_contract_review(
        self,
        *,
        workspace_id: str,
        equation_uuid: str,
        idempotency_key: str,
        review: ContractReview,
    ) -> ContractReviewReceipt:
        """Append an immutable, idempotent review to a source equation."""
        if review.scope != equation_uuid:
            raise ValueError("Contract review scope does not match the source equation.")
        group_id = workspace_group_id(workspace_id)
        review_uuid = str(uuid5(
            NAMESPACE_URL,
            f"{group_id}/contract-review/{review.reviewer_id}/{idempotency_key}",
        ))
        persisted = review.model_copy(update={"review_id": review_uuid})
        payload = json.dumps(
            persisted.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        async with self.driver.session(database=self.database) as session:
            replayed, effective_payload = await session.execute_write(
                self._append_contract_review,
                group_id,
                equation_uuid,
                persisted.symbol_name,
                persisted.reviewer_id,
                persisted.reviewer_role,
                persisted.decision,
                review_uuid,
                payload,
                persisted.reviewed_at,
            )
        effective_review = ContractReview.model_validate_json(
            effective_payload,
        )
        logger.info("contract_review_receipt", extra={
            "event": "contract_review_receipt", "review_id": effective_review.review_id,
            "replayed": replayed,
        })
        return ContractReviewReceipt(
            review=effective_review, replayed=replayed,
        )

    async def append_check_result(
        self,
        *,
        workspace_id: str,
        target_uuid: str,
        idempotency_key: str,
        result: CheckResult,
    ) -> CheckResultReceipt:
        """Append a worker-derived immutable result scoped to existing evidence."""
        group_id = workspace_group_id(workspace_id)
        check_uuid = str(uuid5(
            NAMESPACE_URL,
            f"{group_id}/check-result/{target_uuid}/{idempotency_key}",
        ))
        persisted = result.model_copy(update={
            "check_id": check_uuid,
            "workspace_id": workspace_id,
            "target_uuid": target_uuid,
        })
        payload = json.dumps(
            persisted.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        async with self.driver.session(database=self.database) as session:
            replayed, effective_payload = await session.execute_write(
                self._append_check_result,
                group_id,
                target_uuid,
                check_uuid,
                persisted.checker,
                persisted.checker_version,
                persisted.outcome,
                payload,
                persisted.created_at,
            )
        effective = CheckResult.model_validate_json(effective_payload)
        logger.info("check_result_receipt", extra={
            "event": "check_result_receipt", "check_id": effective.check_id,
            "outcome": effective.outcome, "replayed": replayed,
        })
        return CheckResultReceipt(result=effective, replayed=replayed)

    async def lookup_check_result(
        self, *, workspace_id: str, target_uuid: str, idempotency_key: str,
        request_hash: str,
    ) -> CheckResultReceipt | None:
        group = workspace_group_id(workspace_id)
        check_uuid = str(uuid5(
            NAMESPACE_URL, f"{group}/check-result/{target_uuid}/{idempotency_key}",
        ))
        records, _, _ = await self.driver.execute_query(
            "MATCH (c:CheckResult {uuid:$uuid, group_id:$group, target_uuid:$target}) "
            "RETURN c.payload AS payload",
            uuid=check_uuid, group=group, target=target_uuid, database_=self.database,
        )
        if not records:
            return None
        result = CheckResult.model_validate_json(records[0]["payload"])
        if result.request_hash is not None and result.request_hash != request_hash:
            raise ValueError("Check result idempotency key conflicts with prior content.")
        return CheckResultReceipt(result=result, replayed=True)

    @staticmethod
    async def _append_check_result(
        tx,
        group_id: str,
        target_uuid: str,
        check_uuid: str,
        checker: str,
        checker_version: str,
        outcome: str,
        payload: str,
        created_at: datetime,
    ) -> tuple[bool, str]:
        await lock_research_workspace(tx, group_id)
        incoming = CheckResult.model_validate_json(payload)
        source = await tx.run(
            """
            MATCH (target:Evidence {uuid:$target_uuid, group_id:$group})
            RETURN target.uuid AS target_uuid
            LIMIT 1
            """,
            target_uuid=target_uuid,
            group=group_id,
        )
        if await source.single() is None:
            raise ValueError("Check target must exist in this workspace.")
        if incoming.contract_review_hash is not None:
            reviews_result = await tx.run(
                "MATCH (equation:Evidence {uuid:$target_uuid, group_id:$group, kind:'Equation'}) "
                "OPTIONAL MATCH (review:ContractReview {group_id:$group})-[:REVIEWS]->(equation) "
                "RETURN collect(review.payload) AS payloads",
                target_uuid=target_uuid,
                group=group_id,
            )
            review_state = await reviews_result.single()
            if review_state is None:
                raise ValueError("Contract-review-bound checks require an equation target.")
            current_reviews = latest_contract_reviews([
                ContractReview.model_validate_json(review_payload)
                for review_payload in review_state["payloads"]
                if review_payload is not None
            ])
            current_review_ids = tuple(review.review_id for review in current_reviews)
            if (
                contract_review_state_hash(current_reviews) != incoming.contract_review_hash
                or current_review_ids != incoming.contract_review_ids
            ):
                raise ValueError("Contract review state changed while the check was running.")
        now = datetime.now(UTC)
        creation_token = str(uuid4())
        query = await tx.run(
            """
            MERGE (check:CheckResult {uuid:$check_uuid})
            ON CREATE SET check.group_id=$group,
                check.target_uuid=$target_uuid,
                check.checker=$checker,
                check.checker_version=$checker_version,
                check.outcome=$outcome,
                check.payload=$payload,
                check.checked_at=$checked_at,
                check.creation_token=$creation_token,
                check.created_at=$now
            RETURN check.group_id AS group_id,
                   check.target_uuid AS target_uuid,
                   check.payload AS payload,
                   check.creation_token = $creation_token AS created
            """,
            check_uuid=check_uuid,
            group=group_id,
            target_uuid=target_uuid,
            checker=checker,
            checker_version=checker_version,
            outcome=outcome,
            payload=payload,
            checked_at=created_at,
            now=now,
            creation_token=creation_token,
        )
        record = await query.single(strict=True)
        stored = CheckResult.model_validate_json(record["payload"])
        incoming = CheckResult.model_validate_json(payload)
        same_request = (
            stored.request_hash == incoming.request_hash
            if stored.request_hash is not None and incoming.request_hash is not None
            else stored.model_dump(exclude={"created_at", "duration_ms", "request_hash"})
            == incoming.model_dump(exclude={"created_at", "duration_ms", "request_hash"})
        )
        if (
            record["group_id"] != group_id
            or record["target_uuid"] != target_uuid
            or not same_request
        ):
            raise ValueError("Check result idempotency key conflicts with prior content.")
        await tx.run(
            """
            MATCH (check:CheckResult {uuid:$check_uuid, group_id:$group})
            MATCH (target:Evidence {uuid:$target_uuid, group_id:$group})
            MERGE (check)-[relation:CHECKS {uuid:$check_uuid}]->(target)
            ON CREATE SET relation.group_id=$group, relation.created_at=$now
            """,
            check_uuid=check_uuid,
            target_uuid=target_uuid,
            group=group_id,
            now=now,
        )
        return not bool(record["created"]), record["payload"]

    @staticmethod
    async def _append_contract_review(
        tx,
        group_id: str,
        equation_uuid: str,
        symbol_name: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: str,
        review_uuid: str,
        payload: str,
        reviewed_at: datetime,
    ) -> tuple[bool, str]:
        await lock_research_workspace(tx, group_id)
        source = await tx.run(
            """
            MATCH (equation:Evidence {uuid:$equation_uuid, group_id:$group,
                                      kind:'Equation'})
            MATCH (equation)-[relation:EVIDENCE_RELATION]->
                  (symbol:Evidence {group_id:$group, kind:'Symbol'})
            WHERE relation.relation IN ['defines', 'uses']
              AND symbol.logical_id ENDS WITH ':' + $symbol_name
            RETURN equation.uuid AS equation_uuid
            LIMIT 1
            """,
            equation_uuid=equation_uuid,
            group=group_id,
            symbol_name=symbol_name,
        )
        if await source.single() is None:
            raise ValueError("Reviewed equation and symbol must exist in this workspace.")

        now = datetime.now(UTC)
        creation_token = str(uuid4())
        result = await tx.run(
            """
            MERGE (review:ContractReview {uuid:$review_uuid})
            ON CREATE SET review.group_id=$group,
                review.equation_uuid=$equation_uuid,
                review.symbol_name=$symbol_name,
                review.reviewer_id=$reviewer_id,
                review.reviewer_role=$reviewer_role,
                review.decision=$decision,
                review.payload=$payload,
                review.reviewed_at=$reviewed_at,
                review.creation_token=$creation_token,
                review.created_at=$now
            RETURN review.group_id AS group_id,
                   review.equation_uuid AS equation_uuid,
                   review.payload AS payload,
                   review.creation_token = $creation_token AS created
            """,
            review_uuid=review_uuid,
            group=group_id,
            equation_uuid=equation_uuid,
            symbol_name=symbol_name,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision=decision,
            payload=payload,
            reviewed_at=reviewed_at,
            now=now,
            creation_token=creation_token,
        )
        record = await result.single(strict=True)
        created = bool(record["created"])
        if not created:
            stored = ContractReview.model_validate_json(record["payload"])
            incoming = ContractReview.model_validate_json(payload)
            if (
                stored.model_dump(exclude={"reviewed_at"})
                != incoming.model_dump(exclude={"reviewed_at"})
            ):
                raise ValueError(
                    "Contract review idempotency key conflicts"
                    " with prior content.",
                )
        if (
            record["group_id"] != group_id
            or record["equation_uuid"] != equation_uuid
        ):
            raise ValueError(
                "Contract review idempotency key conflicts"
                " with prior content.",
            )
        await tx.run(
            """
            MATCH (review:ContractReview {uuid:$review_uuid, group_id:$group})
            MATCH (equation:Evidence {uuid:$equation_uuid, group_id:$group,
                                      kind:'Equation'})
            MERGE (review)-[relation:REVIEWS {uuid:$review_uuid}]->(equation)
            ON CREATE SET relation.group_id=$group, relation.created_at=$now
            """,
            review_uuid=review_uuid,
            equation_uuid=equation_uuid,
            group=group_id,
            now=now,
        )
        eq_data = await tx.run(
            """
            MATCH (equation:Evidence {uuid:$equation_uuid, group_id:$group, kind:'Equation'})
            OPTIONAL MATCH (r:ContractReview {group_id:$group})-[:REVIEWS]->(equation)
            WITH equation, r
            ORDER BY r.reviewed_at, r.uuid
            RETURN equation.payload AS eq_payload, collect(r.payload) AS review_payloads
            """,
            equation_uuid=equation_uuid,
            group=group_id,
        )
        eq_record = await eq_data.single()
        if eq_record and eq_record["eq_payload"]:
            eq_raw = eq_record["eq_payload"]
            extracted_eq = ExtractedEquation.model_validate_json(eq_raw)
            reviews = latest_contract_reviews([
                ContractReview.model_validate_json(payload)
                for payload in eq_record["review_payloads"]
                if payload is not None
            ])
            active_contracts = [
                review.reviewed_contract
                for review in reviews
                if review.decision == "accepted" and review.reviewed_contract is not None
            ]
            analysis, _, _ = analyze_equation(
                extracted_eq,
                reviewed_contracts=active_contracts,
            )
            # Bind each immutable analysis snapshot to the exact review state that
            # informed it; this keeps a later rejection from exposing an older
            # accepted analysis as current while the review records stay separate.
            analysis["contract_review_state"] = {
                "schema_version": "contract-review-state.v1",
                "reviews": [
                    {
                        "review_id": review.review_id,
                        "symbol_name": review.symbol_name,
                        "decision": review.decision,
                    }
                    for review in reviews
                ],
            }
            version_record = make_analysis_version(equation_uuid, eq_raw, analysis)
            await tx.run(
                """
                MATCH (equation:Evidence {
                    uuid:$equation_uuid, group_id:$group, kind:'Equation'
                })
                MERGE (analysis:FormulaAnalysisVersion {uuid:$analysis_uuid})
                ON CREATE SET analysis.group_id=$group,
                    analysis.equation_uuid=$equation_uuid,
                    analysis.payload=$payload,
                    analysis.schema_version=$schema_version,
                    analysis.analyzer_version=$analyzer_version,
                    analysis.canonicalizer_version=$canonicalizer_version,
                    analysis.syntax_hash=$syntax_hash,
                    analysis.source_hash=$source_hash,
                    analysis.retired=false,
                    analysis.created_at=$now
                MERGE (analysis)-[relation:ANALYZES {uuid:$analysis_uuid}]->(equation)
                ON CREATE SET relation.group_id=$group, relation.created_at=$now
                """,
                equation_uuid=equation_uuid,
                group=group_id,
                analysis_uuid=version_record["uuid"],
                payload=version_record["payload"],
                schema_version=version_record["schema_version"],
                analyzer_version=version_record["analyzer_version"],
                canonicalizer_version=version_record["canonicalizer_version"],
                syntax_hash=version_record["syntax_hash"],
                source_hash=version_record["source_hash"],
                now=now,
            )
        replayed = not created
        effective_payload = record["payload"] if replayed else payload
        return replayed, effective_payload

    async def version_at(
        self, *, workspace_id: str, paper_id: str, as_of: datetime,
    ) -> PaperVersionSnapshot | None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Historical query time must be timezone-aware.")
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (v:Evidence {group_id:$group, kind:'PaperVersion', paper_id:$paper_id})
            WHERE v.valid_at <= $as_of
            RETURN v.uuid AS uuid, v.paper_id AS paper_id,
                   v.paper_version AS version, v.valid_at AS valid_at,
                   v.payload AS payload
            ORDER BY v.valid_at DESC, v.paper_version DESC
            LIMIT 1
            """,
            group=workspace_group_id(workspace_id), paper_id=paper_id,
            as_of=as_of.astimezone(UTC), database_=self.database,
        )
        if not records:
            return None
        return PaperVersionSnapshot(**dict(records[0]))

    async def lexical_search(
        self,
        query: str,
        filters: EvidenceSearchFilters,
        limit: int,
    ) -> list[EvidenceCandidate]:
        records, _, _ = await self.driver.execute_query(
            """
            CALL db.index.fulltext.queryNodes(
                'fgl_evidence_search', $query, {limit:$limit}
            ) YIELD node, score
            WHERE node.group_id=$group
              AND ($paper_id IS NULL OR node.paper_id=$paper_id)
              AND ($version IS NULL OR node.paper_version=$version)
              AND (size($entity_types)=0 OR node.kind IN $entity_types)
              AND (
                size($verification_statuses)=0
                OR node.verification_status IN $verification_statuses
              )
              AND ($as_of IS NULL OR node.valid_at <= $as_of)
            OPTIONAL MATCH (node)-[:EXTRACTED_IN]->
                           (episode:Episodic {group_id:$group})
            WITH node, score,
                 [uuid IN collect(DISTINCT episode.uuid) WHERE uuid IS NOT NULL]
                 AS episode_uuids
            RETURN node.uuid AS uuid, node.kind AS kind,
                   node.logical_id AS logical_id, node.payload AS payload,
                   node.paper_id AS paper_id, node.paper_version AS paper_version,
                   node.valid_at AS valid_at,
                   node.verification_status AS verification_status,
                   episode_uuids, score AS lexical_score,
                   NULL AS graph_distance
            ORDER BY lexical_score DESC, uuid ASC
            LIMIT $limit
            """,
            query=build_lucene_query(filters.group_id, query),
            limit=limit,
            **self._search_parameters(filters),
            database_=self.database,
        )
        return [self._search_candidate(record) for record in records]

    async def evidence_for_episodes(
        self,
        episode_uuids: list[str],
        filters: EvidenceSearchFilters,
        limit: int,
    ) -> list[EvidenceCandidate]:
        if not episode_uuids:
            return []
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (node:Evidence {group_id:$group})-[:EXTRACTED_IN]->
                  (episode:Episodic {group_id:$group})
            WHERE episode.uuid IN $episode_uuids
              AND ($paper_id IS NULL OR node.paper_id=$paper_id)
              AND ($version IS NULL OR node.paper_version=$version)
              AND (size($entity_types)=0 OR node.kind IN $entity_types)
              AND (
                size($verification_statuses)=0
                OR node.verification_status IN $verification_statuses
              )
              AND ($as_of IS NULL OR node.valid_at <= $as_of)
            WITH node,
                 [uuid IN collect(DISTINCT episode.uuid) WHERE uuid IS NOT NULL]
                 AS episode_uuids
            RETURN node.uuid AS uuid, node.kind AS kind,
                   node.logical_id AS logical_id, node.payload AS payload,
                   node.paper_id AS paper_id, node.paper_version AS paper_version,
                   node.valid_at AS valid_at,
                   node.verification_status AS verification_status,
                   episode_uuids, NULL AS lexical_score,
                   NULL AS graph_distance
            ORDER BY uuid ASC
            LIMIT $limit
            """,
            episode_uuids=episode_uuids,
            limit=limit,
            **self._search_parameters(filters),
            database_=self.database,
        )
        return [self._search_candidate(record) for record in records]

    async def graph_neighbors(
        self,
        center_node_uuid: str,
        filters: EvidenceSearchFilters,
        limit: int,
    ) -> list[EvidenceCandidate]:
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (origin:Evidence {uuid:$center_uuid, group_id:$group})
            MATCH path=(origin)-[:EVIDENCE_RELATION*1..2]-(node:Evidence)
            WHERE node.group_id=$group
              AND all(relation IN relationships(path) WHERE relation.group_id=$group)
              AND ($paper_id IS NULL OR node.paper_id=$paper_id)
              AND ($version IS NULL OR node.paper_version=$version)
              AND (size($entity_types)=0 OR node.kind IN $entity_types)
              AND (
                size($verification_statuses)=0
                OR node.verification_status IN $verification_statuses
              )
              AND ($as_of IS NULL OR node.valid_at <= $as_of)
            WITH node, min(length(path)) AS graph_distance
            OPTIONAL MATCH (node)-[:EXTRACTED_IN]->
                           (episode:Episodic {group_id:$group})
            WITH node, graph_distance,
                 [uuid IN collect(DISTINCT episode.uuid) WHERE uuid IS NOT NULL]
                 AS episode_uuids
            RETURN node.uuid AS uuid, node.kind AS kind,
                   node.logical_id AS logical_id, node.payload AS payload,
                   node.paper_id AS paper_id, node.paper_version AS paper_version,
                   node.valid_at AS valid_at,
                   node.verification_status AS verification_status,
                   episode_uuids, NULL AS lexical_score, graph_distance
            ORDER BY graph_distance ASC, uuid ASC
            LIMIT $limit
            """,
            center_uuid=center_node_uuid,
            limit=limit,
            **self._search_parameters(filters),
            database_=self.database,
        )
        return [self._search_candidate(record) for record in records]

    async def graph_snapshot(
        self,
        request: EvidenceGraphSnapshotRequest,
    ) -> EvidenceGraphSnapshotResponse:
        group_id = workspace_group_id(request.workspace_id)
        node_records, _, _ = await self.driver.execute_query(
            """
            MATCH (node:Evidence {group_id:$group, paper_id:$paper_id})
            WHERE node.kind='Paper'
               OR node.paper_version=$version
               OR (node.kind='PaperVersion' AND node.paper_version <= $version)
            OPTIONAL MATCH (node)-[:EXTRACTED_IN]->
                           (episode:Episodic {group_id:$group})
            WITH node,
                 [uuid IN collect(DISTINCT episode.uuid) WHERE uuid IS NOT NULL]
                 AS episode_uuids,
                 CASE node.kind
                   WHEN 'Paper' THEN 0
                   WHEN 'PaperVersion' THEN 1
                   WHEN 'Section' THEN 2
                   WHEN 'Equation' THEN 3
                   ELSE 4
                 END AS kind_order
            RETURN node.uuid AS uuid, node.kind AS kind,
                   node.logical_id AS logical_id, node.payload AS payload,
                   node.paper_id AS paper_id, node.paper_version AS version,
                   node.valid_at AS valid_at,
                   node.verification_status AS verification_status,
                   episode_uuids
            ORDER BY kind_order ASC, logical_id ASC, uuid ASC
            LIMIT $limit
            """,
            group=group_id,
            paper_id=request.paper_id,
            version=request.version,
            limit=MAX_SNAPSHOT_NODES + 1,
            database_=self.database,
        )
        nodes_truncated = len(node_records) > MAX_SNAPSHOT_NODES
        node_records = [dict(record) for record in node_records[:MAX_SNAPSHOT_NODES]]
        equation_uuids = [
            record["uuid"] for record in node_records if record["kind"] == "Equation"
        ]
        latest_analyses: dict[str, str] = {}
        if equation_uuids:
            analysis_records, _, _ = await self.driver.execute_query(
                """
                MATCH (analysis:FormulaAnalysisVersion {group_id:$group})
                      -[:ANALYZES]->(equation:Evidence {group_id:$group, kind:'Equation'})
                WHERE equation.uuid IN $equation_uuids
                  AND coalesce(analysis.retired, false) = false
                WITH equation, analysis
                ORDER BY CASE analysis.analyzer_version
                    WHEN 'legacy-embedded.v0' THEN 0 ELSE 1 END DESC,
                    analysis.created_at DESC, analysis.uuid DESC
                WITH equation, collect(analysis.payload)[0] AS payload
                RETURN equation.uuid AS equation_uuid, payload
                """,
                group=group_id,
                equation_uuids=equation_uuids,
                database_=self.database,
            )
            latest_analyses = {
                record["equation_uuid"]: record["payload"] for record in analysis_records
            }
        for record in node_records:
            analysis_payload = latest_analyses.get(record["uuid"])
            if analysis_payload is not None:
                record["analysis_payload"] = analysis_payload
        nodes = [self._graph_node(record) for record in node_records]
        node_uuids = [node.uuid for node in nodes]
        if not node_uuids:
            return EvidenceGraphSnapshotResponse(nodes=[], edges=[], truncated=False)

        edge_records, _, _ = await self.driver.execute_query(
            """
            MATCH (source:Evidence {group_id:$group})
                  -[edge:EVIDENCE_RELATION {group_id:$group}]->
                  (target:Evidence {group_id:$group})
            WHERE source.uuid IN $node_uuids AND target.uuid IN $node_uuids
            RETURN edge.uuid AS uuid, source.uuid AS source_uuid,
                   target.uuid AS target_uuid, edge.relation AS relation,
                   coalesce(edge.source_anchor, '') AS source_anchor,
                   coalesce(edge.episode_uuids, []) AS episode_uuids,
                   edge.valid_at AS valid_at
            ORDER BY relation ASC, source_uuid ASC, target_uuid ASC, uuid ASC
            LIMIT $limit
            """,
            group=group_id,
            node_uuids=node_uuids,
            limit=MAX_SNAPSHOT_EDGES + 1,
            database_=self.database,
        )
        edges_truncated = len(edge_records) > MAX_SNAPSHOT_EDGES
        edge_records = edge_records[:MAX_SNAPSHOT_EDGES]
        edges = [self._graph_edge(record) for record in edge_records]
        return EvidenceGraphSnapshotResponse(
            nodes=nodes,
            edges=edges,
            truncated=nodes_truncated or edges_truncated,
        )

    @staticmethod
    def _graph_node(record) -> EvidenceGraphNode:
        try:
            payload = json.loads(record["payload"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise EvidenceSnapshotDataError("Evidence payload is invalid.") from exc
        if not isinstance(payload, dict):
            raise EvidenceSnapshotDataError("Evidence payload must be an object.")
        analysis_payload = record.get("analysis_payload")
        if analysis_payload is not None:
            try:
                analysis = json.loads(analysis_payload)
            except (json.JSONDecodeError, TypeError) as exc:
                raise EvidenceSnapshotDataError("Formula analysis payload is invalid.") from exc
            if not isinstance(analysis, dict):
                raise EvidenceSnapshotDataError("Formula analysis payload must be an object.")
            # Response compatibility only. The persisted Equation payload stays raw.
            payload = {**payload, "formula_analysis": analysis}
        return EvidenceGraphNode(
            uuid=record["uuid"],
            kind=record["kind"],
            logical_id=record["logical_id"],
            paper_id=record["paper_id"],
            version=record["version"],
            valid_at=Neo4jEvidenceStore._native_datetime(record["valid_at"]),
            verification_status=record["verification_status"],
            payload=payload,
            episode_uuids=list(record["episode_uuids"]),
        )

    async def analysis_history(
        self, *, workspace_id: str, equation_uuid: str, after_uuid: str = "",
        limit: int = 50, include_retired: bool = False,
    ) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Analysis history limit must be between 1 and 100.")
        records, _, _ = await self.driver.execute_query(
            "MATCH (a:FormulaAnalysisVersion {group_id:$group})-[:ANALYZES]->"
            "(e:Evidence {uuid:$equation, group_id:$group, kind:'Equation'}) "
            "WHERE a.uuid > $after AND ($retired OR coalesce(a.retired,false)=false) "
            "RETURN a.uuid AS uuid, a.payload AS payload, a.source_hash AS source_hash, "
            "a.schema_version AS schema_version, a.analyzer_version AS analyzer_version, "
            "a.canonicalizer_version AS canonicalizer_version, "
            "coalesce(a.retired,false) AS retired ORDER BY a.uuid LIMIT $limit",
            group=workspace_group_id(workspace_id), equation=equation_uuid,
            after=after_uuid, retired=include_retired, limit=limit, database_=self.database,
        )
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in records]

    @staticmethod
    def _graph_edge(record) -> EvidenceGraphEdge:
        return EvidenceGraphEdge(
            uuid=record["uuid"],
            source_uuid=record["source_uuid"],
            target_uuid=record["target_uuid"],
            relation=record["relation"],
            source_anchor=record["source_anchor"],
            episode_uuids=list(record["episode_uuids"]),
            valid_at=Neo4jEvidenceStore._native_datetime(record["valid_at"]),
        )

    @staticmethod
    def _native_datetime(value):
        if hasattr(value, "to_native"):
            return value.to_native()
        return value

    @staticmethod
    def _search_parameters(filters: EvidenceSearchFilters) -> dict[str, object]:
        return {
            "group": filters.group_id,
            "paper_id": filters.paper_id,
            "version": filters.version,
            "entity_types": list(filters.entity_types),
            "verification_statuses": list(filters.verification_statuses),
            "as_of": filters.as_of.astimezone(UTC) if filters.as_of else None,
        }

    @staticmethod
    def _search_candidate(record) -> EvidenceCandidate:
        return EvidenceCandidate(
            uuid=record["uuid"],
            kind=record["kind"],
            logical_id=record["logical_id"],
            payload=record["payload"],
            paper_id=record["paper_id"],
            paper_version=record["paper_version"],
            valid_at=Neo4jEvidenceStore._native_datetime(record["valid_at"]),
            verification_status=record["verification_status"],
            episode_uuids=tuple(record["episode_uuids"]),
            lexical_score=record["lexical_score"],
            graph_distance=record["graph_distance"],
        )

    async def record_claim(
        self, claim: ReportedClaim, *, disagrees_with_uuid: str | None = None,
    ) -> None:
        async with self.driver.session(database=self.database) as session:
            await session.execute_write(self._record_claim, claim, disagrees_with_uuid)

    async def begin_enrichment(
        self, *, group_id: str, import_uuid: str, episode_uuid: str, attempt_uuid: str,
    ) -> EnrichmentAttempt:
        receipt_uuid = str(uuid5(
            NAMESPACE_URL, f"{group_id}/{import_uuid}/{episode_uuid}/graphiti-v1"
        ))
        async with self.driver.session(database=self.database) as session:
            record = await session.execute_write(
                self._begin_enrichment, group_id, import_uuid, episode_uuid,
                attempt_uuid, receipt_uuid,
            )
        status = record["status"]
        current_attempt = record["attempt_uuid"]
        if status == "completed":
            action = "completed"
        elif status == "running" and current_attempt == attempt_uuid:
            action = "run"
        else:
            action = "needs_reconciliation"
        return EnrichmentAttempt(action, receipt_uuid, attempt_uuid, status)

    @staticmethod
    async def _begin_enrichment(
        tx, group_id: str, import_uuid: str, episode_uuid: str,
        attempt_uuid: str, receipt_uuid: str,
    ):
        result = await tx.run(
            """
            MATCH (i:EvidenceImport {uuid:$import_uuid, group_id:$group, completed:true})
            MATCH (e:Episodic {uuid:$episode_uuid, group_id:$group})
            MERGE (r:SemanticEnrichment {uuid:$receipt_uuid})
            ON CREATE SET r.group_id=$group, r.import_uuid=$import_uuid,
                r.episode_uuid=$episode_uuid, r.status='running',
                r.attempt_uuid=$attempt_uuid, r.attempt_count=1,
                r.started_at=$now, r.created_at=$now
            WITH r
            FOREACH (_ IN CASE WHEN r.status='retry_safe' THEN [1] ELSE [] END |
                SET r.status='running', r.attempt_uuid=$attempt_uuid,
                    r.attempt_count=coalesce(r.attempt_count, 0)+1,
                    r.started_at=$now, r.finished_at=null, r.error_type=null)
            RETURN r.status AS status, r.attempt_uuid AS attempt_uuid
            """,
            group=group_id, import_uuid=import_uuid, episode_uuid=episode_uuid,
            receipt_uuid=receipt_uuid, attempt_uuid=attempt_uuid, now=datetime.now(UTC),
        )
        record = await result.single()
        if record is None:
            raise ValueError("Exact import and source episode must exist before enrichment.")
        return dict(record)

    async def complete_enrichment(
        self, *, receipt_uuid: str, attempt_uuid: str,
    ) -> None:
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (r:SemanticEnrichment {uuid:$receipt_uuid, status:'running',
                                         attempt_uuid:$attempt_uuid})
            SET r.status='completed', r.finished_at=$now
            RETURN r.uuid AS uuid
            """,
            receipt_uuid=receipt_uuid, attempt_uuid=attempt_uuid,
            now=datetime.now(UTC), database_=self.database,
        )
        if not records:
            raise ValueError("Enrichment receipt is not owned by this attempt.")

    async def mark_enrichment_uncertain(
        self, *, receipt_uuid: str, attempt_uuid: str, error_type: str,
    ) -> None:
        # Store only the exception type; messages may contain source/model content.
        safe_error = error_type[:120] if error_type.isascii() else "NonAsciiError"
        await self.driver.execute_query(
            """
            MATCH (r:SemanticEnrichment {uuid:$receipt_uuid, status:'running',
                                         attempt_uuid:$attempt_uuid})
            SET r.status='needs_reconciliation', r.finished_at=$now,
                r.error_type=$error_type
            """,
            receipt_uuid=receipt_uuid, attempt_uuid=attempt_uuid,
            error_type=safe_error, now=datetime.now(UTC), database_=self.database,
        )

    async def allow_enrichment_retry(
        self, *, receipt_uuid: str, operator_id: str, reason: str,
    ) -> None:
        operator_id, reason = operator_id.strip(), reason.strip()
        if not operator_id or len(operator_id) > 200 or not reason or len(reason) > 500:
            raise ValueError("A bounded operator ID and reconciliation reason are required.")
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (r:SemanticEnrichment {uuid:$receipt_uuid,
                                         status:'needs_reconciliation'})
            SET r.status='retry_safe', r.reconciled_by=$operator,
                r.reconciliation_reason=$reason, r.reconciled_at=$now
            RETURN r.uuid AS uuid
            """,
            receipt_uuid=receipt_uuid, operator=operator_id, reason=reason,
            now=datetime.now(UTC), database_=self.database,
        )
        if not records:
            raise ValueError("Only an uncertain enrichment can be approved for retry.")

    @staticmethod
    async def _record_claim(tx, claim: ReportedClaim, disagrees_with_uuid: str | None) -> None:
        found = await tx.run(
            """
            MATCH (v:Evidence {uuid:$version_uuid, group_id:$group, kind:'PaperVersion',
                               paper_id:$paper_id})
            MATCH (e:Episodic {uuid:$episode_uuid, group_id:$group})
            RETURN v.paper_version AS version, e.valid_at AS valid_at
            """,
            version_uuid=claim.paper_version_uuid, group=claim.group_id,
            paper_id=claim.paper_id, episode_uuid=claim.episode_uuid,
        )
        source = await found.single()
        if source is None or source["version"] != claim.paper_version:
            raise ValueError("Claim source version/episode is not present in this workspace.")
        result = await tx.run(
            """
            MERGE (c:Evidence {uuid:$uuid})
            ON CREATE SET c.group_id=$group, c.kind='Claim', c.logical_id=$logical_id,
                c.payload=$payload, c.paper_id=$paper_id, c.paper_version=$version,
                c.valid_at=$valid_at, c.verification_status='reported',
                c.created_at=$now
            RETURN c.group_id AS group_id, c.payload AS payload
            """,
            uuid=claim.uuid, group=claim.group_id, logical_id=claim.logical_id,
            payload=claim.payload, paper_id=claim.paper_id, version=claim.paper_version,
            valid_at=source["valid_at"], now=datetime.now(UTC),
        )
        existing = await result.single(strict=True)
        if existing["group_id"] != claim.group_id or existing["payload"] != claim.payload:
            raise ValueError("Claim identity conflicts with existing evidence.")
        relation_uuid = str(uuid5(NAMESPACE_URL, claim.paper_version_uuid + "/claim/" + claim.uuid))
        await tx.run(
            """
            MATCH (v:Evidence {uuid:$version_uuid, group_id:$group})
            MATCH (c:Evidence {uuid:$claim_uuid, group_id:$group})
            MATCH (e:Episodic {uuid:$episode_uuid, group_id:$group})
            MERGE (v)-[r:EVIDENCE_RELATION {uuid:$relation_uuid}]->(c)
            ON CREATE SET r.group_id=$group, r.relation='makes_claim',
                r.episode_uuids=[$episode_uuid], r.source_anchor=$anchor,
                r.valid_at=e.valid_at, r.created_at=$now
            MERGE (c)-[:EXTRACTED_IN]->(e)
            """,
            version_uuid=claim.paper_version_uuid, claim_uuid=claim.uuid,
            relation_uuid=relation_uuid, episode_uuid=claim.episode_uuid,
            group=claim.group_id, anchor=claim.source_anchor, now=datetime.now(UTC),
        )
        if disagrees_with_uuid is None:
            return
        target_result = await tx.run(
            """
            MATCH (target:Evidence {uuid:$target_uuid, group_id:$group, kind:'Claim'})
            RETURN target.uuid AS uuid
            """,
            target_uuid=disagrees_with_uuid, group=claim.group_id,
        )
        if await target_result.single() is None:
            raise ValueError("Disagreement target is not a claim in this workspace.")
        disagreement_uuid = str(uuid5(
            NAMESPACE_URL, claim.group_id + "/" + claim.uuid + "/disagrees/" + disagrees_with_uuid
        ))
        await tx.run(
            """
            MATCH (claim:Evidence {uuid:$claim_uuid, group_id:$group, kind:'Claim'})
            MATCH (target:Evidence {uuid:$target_uuid, group_id:$group, kind:'Claim'})
            MERGE (claim)-[r:EVIDENCE_RELATION {uuid:$uuid}]->(target)
            ON CREATE SET r.group_id=$group, r.relation='disagrees_with',
                r.episode_uuids=[$episode_uuid], r.source_anchor=$anchor,
                r.valid_at=$now, r.created_at=$now
            """,
            claim_uuid=claim.uuid, target_uuid=disagrees_with_uuid,
            uuid=disagreement_uuid, group=claim.group_id,
            episode_uuid=claim.episode_uuid, anchor=claim.source_anchor,
            now=datetime.now(UTC),
        )

    @staticmethod
    async def _write(tx, graph: EvidenceGraph) -> ImportReceipt:
        await lock_research_workspace(tx, graph.group_id)
        now = datetime.now(UTC)
        locked = await tx.run(
            """
            MERGE (i:EvidenceImport {uuid: $uuid})
            ON CREATE SET i.group_id=$group, i.paper_id=$paper_id,
                i.source_sha256=$source_hash, i.created_at=$now, i.completed=false
            SET i.lock_version=coalesce(i.lock_version, 0)+1
            RETURN i.completed AS completed, i.group_id AS group_id,
                   i.source_sha256 AS source_sha256
            """,
            uuid=graph.import_uuid, group=graph.group_id, paper_id=graph.paper_id,
            source_hash=graph.source_sha256, now=now,
        )
        state = await locked.single(strict=True)
        if state["group_id"] != graph.group_id or state["source_sha256"] != graph.source_sha256:
            raise ValueError("Import identity conflicts with workspace/source.")
        receipt = ImportReceipt(
            graph.import_uuid, len(graph.nodes), len(graph.edges), len(graph.episodes),
            bool(state["completed"]),
        )
        if state["completed"]:
            await Neo4jEvidenceStore._write_formula_analyses(
                tx, graph.group_id, graph.analyses, now
            )
            return receipt

        episodes = [
            {"uuid": e.uuid, "name": e.name, "content": e.body, "valid_at": e.reference_time,
             "previous_uuid": e.previous_uuid}
            for e in graph.episodes
        ]
        await tx.run(
            """
            UNWIND $episodes AS item
            MERGE (e:Episodic {uuid:item.uuid})
            ON CREATE SET e.name=item.name, e.group_id=$group, e.content=item.content,
                e.source='json', e.source_description='Source-bound arXiv HTML extraction',
                e.created_at=$now, e.valid_at=item.valid_at, e.entity_edges=[]
            """,
            episodes=episodes, group=graph.group_id, now=now,
        )
        await tx.run(
            """
            UNWIND $nodes AS item
            MERGE (n:Evidence {uuid:item.uuid})
            ON CREATE SET n.group_id=$group, n.kind=item.kind, n.logical_id=item.logical_id,
                n.payload=item.payload, n.paper_id=item.paper_id,
                n.paper_version=item.paper_version,
                n.verification_status='reported', n.created_at=$now
            WITH n, item
            MATCH (e:Episodic {uuid:item.episode_uuid, group_id:$group})
            SET n.paper_id=coalesce(n.paper_id, item.paper_id),
                n.paper_version=coalesce(n.paper_version, item.paper_version),
                n.verification_status=coalesce(n.verification_status, 'reported'),
                n.valid_at=coalesce(n.valid_at, e.valid_at)
            MERGE (n)-[:EXTRACTED_IN]->(e)
            """,
            nodes=graph.nodes, group=graph.group_id, now=now,
        )
        collisions = await tx.run(
            """
            UNWIND $nodes AS item
            MATCH (n:Evidence {uuid:item.uuid})
            WHERE n.group_id <> $group OR n.kind <> item.kind
               OR n.logical_id <> item.logical_id OR n.payload <> item.payload
               OR n.paper_id <> item.paper_id
               OR coalesce(n.paper_version, -1) <> coalesce(item.paper_version, -1)
            RETURN count(n) AS conflicts
            """,
            nodes=graph.nodes, group=graph.group_id,
        )
        if (await collisions.single(strict=True))["conflicts"]:
            raise ValueError("Evidence UUID conflicts with existing immutable content.")
        await Neo4jEvidenceStore._write_formula_analyses(
            tx, graph.group_id, graph.analyses, now
        )
        await tx.run(
            """
            MATCH (v:Evidence {uuid:$version_uuid, group_id:$group, kind:'PaperVersion'})
            SET v.paper_id=$paper_id, v.paper_version=$version, v.valid_at=$valid_at,
                v.source_sha256=$source_hash, v.import_uuid=$import_uuid
            """,
            version_uuid=graph.paper_version_uuid, group=graph.group_id,
            paper_id=graph.paper_id, version=graph.version,
            valid_at=graph.episodes[0].reference_time,
            source_hash=graph.source_sha256, import_uuid=graph.import_uuid,
        )
        await tx.run(
            """
            UNWIND $edges AS item
            MATCH (a:Evidence {uuid:item.source_uuid, group_id:$group})
            MATCH (b:Evidence {uuid:item.target_uuid, group_id:$group})
            MERGE (a)-[r:EVIDENCE_RELATION {uuid:item.uuid}]->(b)
            ON CREATE SET r.group_id=$group, r.relation=item.relation,
                r.episode_uuids=item.episode_uuids, r.source_anchor=item.source_anchor,
                r.valid_at=$valid_at, r.created_at=$now
            """,
            edges=graph.edges, group=graph.group_id,
            valid_at=graph.episodes[0].reference_time, now=now,
        )
        saga_uuid = str(uuid5(NAMESPACE_URL, graph.group_id + "/" + graph.episodes[0].saga))
        await tx.run(
            """
            MERGE (s:Saga {uuid:$saga_uuid})
            ON CREATE SET s.name=$name, s.group_id=$group, s.created_at=$now,
                s.first_episode_uuid=$first
            SET s.last_episode_uuid=$last
            WITH s
            UNWIND $episodes AS item
            MATCH (e:Episodic {uuid:item.uuid, group_id:$group})
            MERGE (s)-[r:HAS_EPISODE {uuid:item.uuid}]->(e)
            ON CREATE SET r.group_id=$group, r.created_at=$now
            """,
            saga_uuid=saga_uuid, name=graph.episodes[0].saga, group=graph.group_id,
            now=now, first=graph.episodes[0].uuid, last=graph.episodes[-1].uuid, episodes=episodes,
        )
        await tx.run(
            """
            UNWIND $episodes AS item
            WITH item WHERE item.previous_uuid IS NOT NULL
            MATCH (a:Episodic {uuid:item.previous_uuid, group_id:$group})
            MATCH (b:Episodic {uuid:item.uuid, group_id:$group})
            MERGE (a)-[r:NEXT_EPISODE {uuid:item.uuid}]->(b)
            ON CREATE SET r.group_id=$group, r.created_at=$now
            """,
            episodes=episodes, group=graph.group_id, now=now,
        )
        # Rebuild the direct revision chain so out-of-order imports still produce
        # v3 -> v2 -> v1. The scope includes only one workspace and paper identity.
        await tx.run(
            """
            MATCH (newer:Evidence {group_id:$group, kind:'PaperVersion',
                                   paper_id:$paper_id})
                  -[old:EVIDENCE_RELATION {relation:'supersedes'}]->
                  (older:Evidence {group_id:$group, kind:'PaperVersion',
                                   paper_id:$paper_id})
            DELETE old
            """,
            group=graph.group_id, paper_id=graph.paper_id,
        )
        await tx.run(
            """
            MATCH (v:Evidence {group_id:$group, kind:'PaperVersion',
                               paper_id:$paper_id})-[:EXTRACTED_IN]->
                  (episode:Episodic {group_id:$group})
            WHERE v.paper_version IS NOT NULL
            WITH v, episode ORDER BY v.paper_version ASC
            WITH collect({node:v, episode_uuid:episode.uuid}) AS versions
            UNWIND range(1, size(versions)-1) AS index
            WITH versions[index].node AS newer,
                 versions[index].episode_uuid AS newer_episode_uuid,
                 versions[index-1].node AS older
            MERGE (newer)-[r:EVIDENCE_RELATION {relation:'supersedes'}]->(older)
            ON CREATE SET r.uuid=newer.uuid + ':supersedes:' + older.uuid,
                r.group_id=$group, r.episode_uuids=[newer_episode_uuid],
                r.source_anchor='', r.created_at=$now
            SET r.valid_at=newer.valid_at
            """,
            group=graph.group_id, paper_id=graph.paper_id, now=now,
        )
        await tx.run(
            """
            MATCH (i:EvidenceImport {uuid:$uuid, group_id:$group})
            SET i.completed=true, i.completed_at=$now,
                i.node_count=$nodes, i.edge_count=$edges, i.episode_count=$episodes
            """,
            uuid=graph.import_uuid, group=graph.group_id, now=now, nodes=len(graph.nodes),
            edges=len(graph.edges), episodes=len(graph.episodes),
        )
        return receipt

    @staticmethod
    async def _write_formula_analyses(
        tx,
        group_id: str,
        analyses: list[dict],
        now: datetime,
    ) -> None:
        if not analyses:
            return
        sources = await tx.run(
            """
            UNWIND $analyses AS item
            OPTIONAL MATCH (equation:Evidence {uuid:item.equation_uuid, group_id:$group,
                                                kind:'Equation'})
            RETURN item.equation_uuid AS uuid, equation.payload AS payload
            """,
            analyses=analyses,
            group=group_id,
        )
        source_records = await sources.data()
        hashes = {}
        for record in source_records:
            if record["payload"] is None:
                raise ValueError("Formula analysis source equation is missing from this workspace.")
            hashes[record["uuid"]] = source_hash(record["payload"])
        if any(hashes.get(item["equation_uuid"]) != item["source_hash"] for item in analyses):
            raise ValueError("Formula analysis does not match its immutable source.")
        await tx.run(
            """
            UNWIND $analyses AS item
            MATCH (equation:Evidence {uuid:item.equation_uuid, group_id:$group,
                                      kind:'Equation'})
            MERGE (analysis:FormulaAnalysisVersion {uuid:item.uuid})
            ON CREATE SET analysis.group_id=$group,
                analysis.equation_uuid=item.equation_uuid,
                analysis.payload=item.payload,
                analysis.schema_version=item.schema_version,
                analysis.analyzer_version=item.analyzer_version,
                analysis.canonicalizer_version=item.canonicalizer_version,
                analysis.syntax_hash=item.syntax_hash,
                analysis.source_hash=item.source_hash,
                analysis.retired=false,
                analysis.created_at=$now
            MERGE (analysis)-[relation:ANALYZES {uuid:item.uuid}]->(equation)
            ON CREATE SET relation.group_id=$group, relation.created_at=$now
            """,
            analyses=analyses,
            group=group_id,
            now=now,
        )
        conflicts = await tx.run(
            """
            UNWIND $analyses AS item
            MATCH (analysis:FormulaAnalysisVersion {uuid:item.uuid})
            WHERE analysis.group_id <> $group
               OR analysis.equation_uuid <> item.equation_uuid
               OR analysis.payload <> item.payload
               OR analysis.schema_version <> item.schema_version
               OR analysis.analyzer_version <> item.analyzer_version
               OR analysis.source_hash <> item.source_hash
               OR coalesce(analysis.canonicalizer_version, '') <>
                  coalesce(item.canonicalizer_version, '')
               OR coalesce(analysis.syntax_hash, '') <> coalesce(item.syntax_hash, '')
            RETURN count(analysis) AS conflicts
            """,
            analyses=analyses,
            group=group_id,
        )
        if (await conflicts.single(strict=True))["conflicts"]:
            raise ValueError("Formula analysis identity conflicts with immutable content.")
