"""Retention policy contract and safe transient-only cleanup helpers for Neo4j.

Guarantees that automated retention pruning can ONLY delete expired transient
infrastructure state (``ServiceRequestNonce``, ``WorkspaceRate``) and can NEVER
delete or mutate immutable scientific evidence, human review records, verification
receipts, job reservations, or replayable campaign history.
"""

from __future__ import annotations

import time
from typing import Any

RETENTION_POLICY_VERSION = "retention-policy.v1"

TRANSIENT_PRUNABLE_LABELS: frozenset[str] = frozenset(
    {
        "ServiceRequestNonce",
        "WorkspaceRate",
    }
)

IMMUTABLE_PROTECTED_LABELS: frozenset[str] = frozenset(
    {
        "Evidence",
        "EvidenceImport",
        "Episodic",
        "Saga",
        "SemanticEnrichment",
        "ContractReview",
        "CheckResult",
        "FormulaAnalysisVersion",
        "AnalysisMigration",
        "ProblemSpec",
        "ResearchProposal",
        "ProposalReview",
        "CompiledCandidate",
        "TransformationActivity",
        "AdmissionDecision",
        "NumericalFixtureResult",
        "ResearchCaseResult",
        "ResearchProtocolReview",
        "ResearchJob",
        "EvolutionCampaign",
    }
)

RETENTION_TARGETS: dict[str, object] = {
    "policy_version": RETENTION_POLICY_VERSION,
    "target_rpo_minutes": 15,
    "target_rto_minutes": 30,
    "transient_nonce_grace_seconds": 300,
    "transient_rate_window_seconds": 60,
    "evidence_and_reviews_retention": "indefinite_append_only",
    "backup_checksum_algorithm": "sha256",
}


def assert_prunable_label(label: str) -> str:
    """Fail closed unless ``label`` is strictly in the transient-only allowlist."""
    if label in IMMUTABLE_PROTECTED_LABELS or label not in TRANSIENT_PRUNABLE_LABELS:
        raise ValueError(
            f"Label {label!r} is protected or unrecognized and cannot be pruned by retention."
        )
    return label


def build_transient_prune_queries() -> tuple[tuple[str, str], ...]:
    """Return allowlisted Cypher statements that delete strictly expired transient records."""
    nonce_label = assert_prunable_label("ServiceRequestNonce")
    rate_label = assert_prunable_label("WorkspaceRate")
    return (
        (
            nonce_label,
            f"MATCH (n:{nonce_label}) WHERE n.expires_at < $cutoff_epoch_s "
            "WITH n LIMIT $batch_limit DELETE n RETURN count(n) AS deleted",
        ),
        (
            rate_label,
            f"MATCH (w:{rate_label}) WHERE w.expires_at < $now_ms "
            "WITH w LIMIT $batch_limit DELETE w RETURN count(w) AS deleted",
        ),
    )


async def prune_expired_transient_records(
    driver: Any,
    *,
    database: str | None = None,
    cutoff_epoch_s: int | None = None,
    now_ms: int | None = None,
    batch_limit: int = 500,
) -> dict[str, int]:
    """Delete strictly expired transient replay-nonce and rate-limit records in bounded batches."""
    if type(batch_limit) is not int or not 1 <= batch_limit <= 5_000:
        raise ValueError("batch_limit must be an integer between 1 and 5000.")

    effective_cutoff_s = int(time.time()) - 300 if cutoff_epoch_s is None else cutoff_epoch_s
    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    queries = dict(build_transient_prune_queries())

    deleted_nonces = 0
    deleted_rates = 0
    async with driver.session(database=database) as session:
        nonce_rows = await session.run(
            queries["ServiceRequestNonce"],
            cutoff_epoch_s=effective_cutoff_s,
            batch_limit=batch_limit,
        )
        nonce_rec = await nonce_rows.single()
        deleted_nonces = int(nonce_rec["deleted"]) if nonce_rec else 0

        rate_rows = await session.run(
            queries["WorkspaceRate"],
            now_ms=effective_now_ms,
            batch_limit=batch_limit,
        )
        rate_rec = await rate_rows.single()
        deleted_rates = int(rate_rec["deleted"]) if rate_rec else 0

    return {
        "deleted_service_nonces": deleted_nonces,
        "deleted_workspace_rates": deleted_rates,
    }
