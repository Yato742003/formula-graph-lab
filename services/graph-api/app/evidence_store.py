from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from neo4j import AsyncGraphDatabase

from app.enrichment import EnrichmentAttempt
from app.episodes import workspace_group_id
from app.evidence import EvidenceGraph, ReportedClaim


@dataclass(frozen=True)
class ImportReceipt:
    import_uuid: str
    node_count: int
    edge_count: int
    episode_count: int
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
        for label, constraint in [
            ("Evidence", "fgl_evidence_uuid"),
            ("EvidenceImport", "fgl_import_uuid"),
            ("Episodic", "fgl_episode_uuid"),
            ("Saga", "fgl_saga_uuid"),
            ("SemanticEnrichment", "fgl_semantic_enrichment_uuid"),
        ]:
            # Labels/names are code-owned constants, never request inputs.
            await self.driver.execute_query(
                f"CREATE CONSTRAINT {constraint} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.uuid IS UNIQUE",
                database_=self.database,
            )

    async def close(self) -> None:
        await self.driver.close()

    async def ingest(self, graph: EvidenceGraph) -> ImportReceipt:
        async with self.driver.session(database=self.database) as session:
            return await session.execute_write(self._write, graph)

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
                c.valid_at=$valid_at, c.created_at=$now
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
                n.payload=item.payload, n.created_at=$now
            WITH n, item
            MATCH (e:Episodic {uuid:item.episode_uuid, group_id:$group})
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
            RETURN count(n) AS conflicts
            """,
            nodes=graph.nodes, group=graph.group_id,
        )
        if (await collisions.single(strict=True))["conflicts"]:
            raise ValueError("Evidence UUID conflicts with existing immutable content.")
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
