"""Resumable workspace-local migration with source-integrity receipts.

Rollback retires only versions created by this migration. Source nodes, legacy
payloads, pre-existing analyses, and receipts remain available for audit.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from app.analysis_versions import (
    MIGRATION_VERSION,
    canonical_json,
    make_analysis_version,
    source_hash,
    source_payload,
)
from app.episodes import workspace_group_id
from app.evidence import analyze_equation
from app.evidence_store import Neo4jEvidenceStore
from app.models import ExtractedEquation

logger = logging.getLogger(__name__)
PAGE_SIZE = 100


class AnalysisMigration:
    def __init__(self, store: Neo4jEvidenceStore):
        self.store = store

    async def inventory(self, workspace_id: str) -> dict:
        """Stream exact stored bytes and source-only hashes in UUID order."""
        group = workspace_group_id(workspace_id)
        stored = hashlib.sha256()
        sources = hashlib.sha256()
        cursor = ""
        count = legacy = 0
        while True:
            rows, _, _ = await self.store.driver.execute_query(
                "MATCH (e:Evidence {group_id:$group, kind:'Equation'}) "
                "WHERE e.uuid > $cursor RETURN e.uuid AS uuid, e.payload AS payload "
                "ORDER BY e.uuid LIMIT $limit",
                group=group, cursor=cursor, limit=PAGE_SIZE, database_=self.store.database,
            )
            for row in rows:
                payload = json.loads(row["payload"])
                stored.update(canonical_json([row["uuid"], row["payload"]]).encode("utf-8"))
                sources.update(canonical_json([
                    row["uuid"], source_hash(row["payload"]),
                ]).encode("utf-8"))
                legacy += int(isinstance(payload.get("formula_analysis"), dict))
                count += 1
                cursor = row["uuid"]
            if len(rows) < PAGE_SIZE:
                break
        return {
            "equations": count, "legacy_embedded": legacy,
            "stored_payload_sha256": stored.hexdigest(),
            "source_sha256": sources.hexdigest(),
        }

    async def migrate_batch(
        self, workspace_id: str, *, run_id: str, limit: int = PAGE_SIZE,
    ) -> dict:
        if not run_id.strip() or len(run_id) > 200:
            raise ValueError("A bounded migration run ID is required.")
        if not 1 <= limit <= PAGE_SIZE:
            raise ValueError("Migration batch limit must be between 1 and 100.")
        group = workspace_group_id(workspace_id)
        receipt_id = str(uuid5(NAMESPACE_URL, canonical_json([group, MIGRATION_VERSION, run_id])))
        async with self.store.driver.session(database=self.store.database) as session:
            result = await session.execute_write(self._batch, group, receipt_id, limit)
        logger.info("analysis_migration_batch", extra={
            "event": "analysis_migration_batch", "receipt_id": receipt_id,
            "processed": result["processed"], "status": result["status"],
        })
        return result

    @staticmethod
    async def _batch(tx, group: str, receipt_id: str, limit: int) -> dict:
        now = datetime.now(UTC)
        state_result = await tx.run(
            "MERGE (m:AnalysisMigration {uuid:$uuid}) "
            "ON CREATE SET m.group_id=$group, m.version=$version, m.cursor='', "
            "m.status='running', m.created_at=$now, m.processed=0 "
            "SET m.lock_version=coalesce(m.lock_version,0)+1 "
            "RETURN m.group_id AS group_id, m.cursor AS cursor, m.status AS status, "
            "m.processed AS processed",
            uuid=receipt_id, group=group, version=MIGRATION_VERSION, now=now,
        )
        state = await state_result.single(strict=True)
        if state["group_id"] != group:
            raise ValueError("Migration receipt workspace mismatch.")
        if state["status"] == "rolled_back":
            raise ValueError("A rolled-back migration requires a new run ID.")
        if state["status"] == "completed":
            return {"receipt_id": receipt_id, "processed": state["processed"],
                    "cursor": state["cursor"], "status": "completed"}
        rows_result = await tx.run(
            "MATCH (e:Evidence {group_id:$group, kind:'Equation'}) WHERE e.uuid > $cursor "
            "RETURN e.uuid AS uuid, e.payload AS payload ORDER BY e.uuid LIMIT $limit",
            group=group, cursor=state["cursor"], limit=limit,
        )
        rows = await rows_result.data()
        versions = []
        source_receipts = []
        for row in rows:
            raw = row["payload"]
            equation = ExtractedEquation.model_validate(source_payload(raw))
            legacy = json.loads(raw).get("formula_analysis")
            if isinstance(legacy, dict):
                versions.append(make_analysis_version(
                    row["uuid"], raw, legacy, analyzer_version="legacy-embedded.v0",
                ))
            analysis, _, _ = analyze_equation(equation)
            versions.append(make_analysis_version(row["uuid"], raw, analysis))
            source_receipts.append({
                "uuid": row["uuid"], "source_hash": source_hash(raw),
                "stored_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            })
        # Acquire per-version locks before recording ownership. This prevents a
        # concurrent migration from incorrectly claiming pre-existing versions.
        new_ids = []
        for version in sorted(versions, key=lambda item: item["uuid"]):
            locked = await tx.run(
                "MERGE (a:FormulaAnalysisVersion {uuid:$uuid}) "
                "ON CREATE SET a.migration_owner=$owner "
                "SET a.lock_version=coalesce(a.lock_version,0)+1 "
                "RETURN a.payload AS payload, a.group_id AS group_id, "
                "a.retired AS retired, a.migration_owner AS owner",
                uuid=version["uuid"], owner=receipt_id,
            )
            record = await locked.single(strict=True)
            if record["payload"] is None:
                # Populate the newly reserved node in the same transaction;
                # normal writer remains responsible for collision validation.
                await tx.run(
                    "MATCH (a:FormulaAnalysisVersion {uuid:$uuid}) SET a += $properties",
                    uuid=version["uuid"], properties={
                        **{key: value for key, value in version.items() if key != "uuid"},
                        "group_id": group, "retired": False, "created_at": now,
                    },
                )
                new_ids.append(version["uuid"])
            elif record["retired"]:
                if record["group_id"] != group or record["payload"] != version["payload"]:
                    raise ValueError("Retired analysis identity mismatch.")
                await tx.run(
                    "MATCH (a:FormulaAnalysisVersion {uuid:$uuid, group_id:$group}) "
                    "SET a.retired=false, a.migration_owner=$owner, a.restored_at=$now",
                    uuid=version["uuid"], group=group, owner=receipt_id, now=now,
                )
                new_ids.append(version["uuid"])
        await Neo4jEvidenceStore._write_formula_analyses(tx, group, versions, now)
        await tx.run(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group}) "
            "UNWIND $ids AS id MATCH (a:FormulaAnalysisVersion {uuid:id, group_id:$group}) "
            "MERGE (m)-[:CREATED_ANALYSIS]->(a)",
            uuid=receipt_id, group=group, ids=new_ids,
        )
        await tx.run(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group}) "
            "UNWIND $sources AS item "
            "MATCH (e:Evidence {uuid:item.uuid, group_id:$group, kind:'Equation'}) "
            "MERGE (m)-[r:MIGRATED_SOURCE]->(e) "
            "ON CREATE SET r.source_hash=item.source_hash, r.stored_hash=item.stored_hash",
            uuid=receipt_id, group=group, sources=source_receipts,
        )
        cursor = rows[-1]["uuid"] if rows else state["cursor"]
        status = "completed" if len(rows) < limit else "running"
        processed = state["processed"] + len(rows)
        await tx.run(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group}) "
            "SET m.cursor=$cursor, m.status=$status, m.processed=$processed, m.updated_at=$now",
            uuid=receipt_id, group=group, cursor=cursor, status=status,
            processed=processed, now=now,
        )
        return {"receipt_id": receipt_id, "cursor": cursor,
                "status": status, "processed": processed}

    async def verify_receipt(self, workspace_id: str, *, receipt_id: str) -> dict:
        group = workspace_group_id(workspace_id)
        exists, _, _ = await self.store.driver.execute_query(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group}) "
            "RETURN m.processed AS processed",
            uuid=receipt_id, group=group, database_=self.store.database,
        )
        if not exists:
            raise ValueError("Migration receipt is absent from this workspace.")
        cursor = ""
        count = 0
        while True:
            rows, _, _ = await self.store.driver.execute_query(
                "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group})"
                "-[r:MIGRATED_SOURCE]->(e:Evidence {group_id:$group, kind:'Equation'}) "
                "WHERE e.uuid > $cursor RETURN e.uuid AS uuid, e.payload AS payload, "
                "r.source_hash AS source_hash, r.stored_hash AS stored_hash "
                "ORDER BY e.uuid LIMIT $limit",
                uuid=receipt_id, group=group, cursor=cursor, limit=PAGE_SIZE,
                database_=self.store.database,
            )
            for row in rows:
                raw = row["payload"]
                if (source_hash(raw) != row["source_hash"]
                        or hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["stored_hash"]):
                    raise ValueError("Migration source no longer matches its receipt.")
                cursor = row["uuid"]
                count += 1
            if len(rows) < PAGE_SIZE:
                break
        if count != exists[0]["processed"]:
            raise ValueError("Migration source count no longer matches its receipt.")
        return {"receipt_id": receipt_id, "verified_sources": count, "source_preserved": True}

    async def rollback(self, workspace_id: str, *, receipt_id: str) -> int:
        async with self.store.driver.session(database=self.store.database) as session:
            retired = await session.execute_write(
                self._rollback, workspace_group_id(workspace_id), receipt_id,
            )
        logger.info("analysis_migration_rollback", extra={
            "event": "analysis_migration_rollback", "receipt_id": receipt_id,
            "retired_versions": retired,
        })
        return retired

    @staticmethod
    async def _rollback(tx, group: str, receipt_id: str) -> int:
        found = await tx.run(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group}) "
            "SET m.lock_version=coalesce(m.lock_version,0)+1 RETURN m.status AS status",
            uuid=receipt_id, group=group,
        )
        state = await found.single()
        if state is None:
            raise ValueError("Migration receipt is absent from this workspace.")
        if state["status"] == "rolled_back":
            return 0
        result = await tx.run(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group})"
            "-[:CREATED_ANALYSIS]->(a:FormulaAnalysisVersion {group_id:$group}) "
            "WHERE a.migration_owner=$uuid AND coalesce(a.retired,false)=false "
            "SET a.retired=true, a.retired_at=$now RETURN count(a) AS count",
            uuid=receipt_id, group=group, now=datetime.now(UTC),
        )
        count = (await result.single(strict=True))["count"]
        await tx.run(
            "MATCH (m:AnalysisMigration {uuid:$uuid, group_id:$group}) "
            "SET m.status='rolled_back', m.rolled_back_at=$now",
            uuid=receipt_id, group=group, now=datetime.now(UTC),
        )
        return count
