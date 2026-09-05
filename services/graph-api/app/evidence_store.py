from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from neo4j import AsyncGraphDatabase

from app.evidence import EvidenceGraph


@dataclass(frozen=True)
class ImportReceipt:
    import_uuid: str
    node_count: int
    edge_count: int
    episode_count: int
    replayed: bool


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
