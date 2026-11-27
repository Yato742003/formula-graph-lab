"""Seed, prune, and post-restore parity verifier for the Neo4j Backup & Restore Drill.

Invoked by ``scripts/drill-backup-restore.ps1`` against isolated temporary Neo4j
containers so the drill proves real end-to-end persistence of immutable
``Evidence``, ``ContractReview``, ``CheckResult``, and ``EvidenceSearchService``
records across ``neo4j-admin database dump`` -> destroy -> ``neo4j-admin database load``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

GRAPH_API_ROOT = Path(__file__).resolve().parents[1] / "services" / "graph-api"
if str(GRAPH_API_ROOT) not in sys.path:
    sys.path.insert(0, str(GRAPH_API_ROOT))

from app.evidence import build_evidence_graph  # noqa: E402
from app.evidence_store import Neo4jEvidenceStore  # noqa: E402
from app.models import EvidenceSearchRequest  # noqa: E402
from app.retention import (  # noqa: E402
    IMMUTABLE_PROTECTED_LABELS,
    RETENTION_POLICY_VERSION,
    prune_expired_transient_records,
)
from app.search import EvidenceSearchService, SearchCursorCodec  # noqa: E402
from app.symbol_contracts import (  # noqa: E402
    ContractReview,
    ReviewedContractValue,
    contract_review_state_hash,
)
from app.tex_extractor import extract_tex  # noqa: E402
from app.verification import CheckResult, VerificationVector  # noqa: E402

DRILL_WORKSPACE_ID = "ws_backup_restore_drill_v1"
DRILL_TEX_SOURCE = (
    "\\title{Backup Restore Verification Corpus}\n"
    "\\author{FormulaGraph Lab Drill}\n"
    "\\newcommand{\\inv}[1]{\\frac{1}{#1}}\n"
    "\\section{Logarithmic Scaling}\n"
    "\\begin{equation}\\label{eq:log-scale}\n"
    "\\log(x^2) + \\inv{x}\n"
    "\\end{equation}\n"
    "\\section{Quadratic Expansion}\n"
    "\\begin{equation}\\label{eq:quad}\n"
    "(x + 1)^2 = x^2 + 2 x + 1\n"
    "\\end{equation}\n"
)
DRILL_REVIEW_KEY = "drill-review-key-0001"
DRILL_CHECK_KEY = "drill-check-key-0001"
DRILL_CURSOR_SECRET = "drill-search-cursor-secret-0123456789abcdef"


async def _collect_label_counts(store: Neo4jEvidenceStore) -> dict[str, int]:
    records, _, _ = await store.driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS lbl RETURN lbl, count(n) AS c ORDER BY lbl",
        database_=store.database,
    )
    return {str(rec["lbl"]): int(rec["c"]) for rec in records}


async def _seed_and_prune(uri: str, user: str, password: str, out_path: Path) -> None:
    store = Neo4jEvidenceStore.connect(uri, user, password)
    try:
        await store.initialize()

        # 1. Extract & ingest immutable exact evidence graph from .tex
        paper = extract_tex(DRILL_TEX_SOURCE, filename="drill_paper.tex")
        graph = build_evidence_graph(paper, workspace_id=DRILL_WORKSPACE_ID)
        ingest_receipt = await store.ingest(graph)
        eq_node = next(n for n in graph.nodes if n["kind"] == "Equation")
        eq_uuid = str(eq_node["uuid"])

        # 2. Append immutable human ContractReview on E1
        review = ContractReview(
            review_id="pending-server-identity",
            symbol_name="x",
            reviewer_id="drill-operator",
            reviewer_role="reviewer",
            decision="accepted",
            scope=eq_uuid,
            reviewed_contract=ReviewedContractValue(
                name="x",
                category="scalar",
                shape=(),
                domain="positive",
                constraints=["x > 0"],
                scope=eq_uuid,
            ),
            evidence=["drill:positive_real_assumption"],
            reviewed_at=datetime(2026, 11, 27, 0, 0, 0, tzinfo=UTC),
        )
        review_receipt = await store.append_contract_review(
            workspace_id=DRILL_WORKSPACE_ID,
            equation_uuid=eq_uuid,
            idempotency_key=DRILL_REVIEW_KEY,
            review=review,
        )
        persisted_reviews = await store.equation_contract_reviews(
            workspace_id=DRILL_WORKSPACE_ID,
            equation_uuid=eq_uuid,
        )
        review_hash = contract_review_state_hash(persisted_reviews)
        review_ids = tuple(r.review_id for r in persisted_reviews)

        # 3. Append immutable CheckResult bound to ContractReview snapshot
        check = CheckResult(
            check_id="pending-server-identity",
            workspace_id=DRILL_WORKSPACE_ID,
            target_uuid=eq_uuid,
            request_hash="b" * 64,
            assumptions=["x: positive real scalar (x > 0)"],
            outcome="supported",
            vector=VerificationVector(
                parse="supported",
                type="well_typed",
                domain="discharged",
                symbolic="supported",
            ),
            witness={"difference": "0"},
            duration_ms=12,
            created_at=datetime(2026, 11, 27, 0, 1, 0, tzinfo=UTC),
            contract_review_ids=review_ids,
            contract_review_hash=review_hash,
            schema_version="check-result.v2",
        )
        check_receipt = await store.append_check_result(
            workspace_id=DRILL_WORKSPACE_ID,
            target_uuid=eq_uuid,
            idempotency_key=DRILL_CHECK_KEY,
            result=check,
        )

        # 4. Seed expired + active transient records and execute safe retention pruning
        now_s = int(time.time())
        now_ms = now_s * 1000
        async with store.driver.session(database=store.database) as session:
            await session.run(
                "CREATE (:ServiceRequestNonce {id: 'expired-nonce-1', expires_at: $exp_s}) "
                "CREATE (:ServiceRequestNonce {id: 'active-nonce-1', expires_at: $act_s}) "
                "CREATE (:WorkspaceRate {id: 'expired-rate-1', expires_at: $exp_ms})",
                exp_s=now_s - 3_600,
                act_s=now_s + 3_600,
                exp_ms=now_ms - 600_000,
            )

        counts_before_prune = await _collect_label_counts(store)
        pruned = await prune_expired_transient_records(
            store.driver,
            database=store.database,
            cutoff_epoch_s=now_s - 300,
            now_ms=now_ms,
        )
        counts_after_prune = await _collect_label_counts(store)

        if pruned != {"deleted_service_nonces": 1, "deleted_workspace_rates": 1}:
            raise RuntimeError(f"Unexpected retention prune result: {pruned}")

        for protected_label in IMMUTABLE_PROTECTED_LABELS:
            if counts_before_prune.get(protected_label, 0) != counts_after_prune.get(
                protected_label, 0
            ):
                raise RuntimeError(f"Protected label {protected_label} changed during pruning!")

        expected = {
            "retention_policy_version": RETENTION_POLICY_VERSION,
            "workspace_id": DRILL_WORKSPACE_ID,
            "paper_id": paper.paper_id,
            "source_sha256": paper.source_sha256,
            "import_uuid": ingest_receipt.import_uuid,
            "node_count": ingest_receipt.node_count,
            "edge_count": ingest_receipt.edge_count,
            "equation_uuid": eq_uuid,
            "equation_latex": paper.equations[0].latex,
            "review_id": review_receipt.review.review_id,
            "review_hash": review_hash,
            "check_id": check_receipt.result.check_id,
            "check_outcome": check_receipt.result.outcome,
            "pruned_transient": pruned,
            "label_counts": counts_after_prune,
        }
        out_path.write_text(json.dumps(expected, indent=2, sort_keys=True), encoding="utf-8")
    finally:
        await store.close()


async def _verify_restored(
    uri: str, user: str, password: str, expected_path: Path, out_path: Path
) -> None:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    store = Neo4jEvidenceStore.connect(uri, user, password)
    try:
        actual_counts = await _collect_label_counts(store)
        if actual_counts != expected["label_counts"]:
            raise RuntimeError(
                f"Label count mismatch after restore: expected {expected['label_counts']}, "
                f"got {actual_counts}"
            )

        eq_source = await store.equation_source(
            workspace_id=expected["workspace_id"],
            equation_uuid=expected["equation_uuid"],
        )
        if eq_source["latex"] != expected["equation_latex"]:
            raise RuntimeError("Restored equation LaTeX does not match source!")

        reviews = await store.equation_contract_reviews(
            workspace_id=expected["workspace_id"],
            equation_uuid=expected["equation_uuid"],
        )
        actual_review_hash = contract_review_state_hash(reviews)
        if actual_review_hash != expected["review_hash"]:
            raise RuntimeError("Restored ContractReview state hash mismatch!")

        check_receipt = await store.lookup_check_result(
            workspace_id=expected["workspace_id"],
            target_uuid=expected["equation_uuid"],
            idempotency_key=DRILL_CHECK_KEY,
            request_hash="b" * 64,
        )
        if check_receipt is None or check_receipt.result.check_id != expected["check_id"]:
            raise RuntimeError("Restored CheckResult receipt missing or mismatched!")

        # Verify search queries work immediately on the restored database
        search_svc = EvidenceSearchService(
            repository=store,
            cursor_codec=SearchCursorCodec(DRILL_CURSOR_SECRET),
        )
        search_res = await search_svc.search(
            EvidenceSearchRequest(
                workspace_id=expected["workspace_id"],
                query="log",
                limit=10,
            )
        )
        if not search_res.hits:
            raise RuntimeError("EvidenceSearchService returned 0 hits on restored Neo4j!")

        verified = {
            "parity_verified": True,
            "label_counts": actual_counts,
            "equation_uuid": expected["equation_uuid"],
            "equation_latex": eq_source["latex"],
            "review_hash": actual_review_hash,
            "check_id": check_receipt.result.check_id,
            "check_outcome": check_receipt.result.outcome,
            "restored_search_hits": len(search_res.hits),
        }
        out_path.write_text(json.dumps(verified, indent=2, sort_keys=True), encoding="utf-8")
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Neo4j backup/restore drill helper.")
    parser.add_argument("--mode", choices=("seed-and-prune", "verify-restored"), required=True)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--expected", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.mode == "seed-and-prune":
        asyncio.run(_seed_and_prune(args.uri, args.user, args.password, args.out))
    else:
        if args.expected is None:
            raise ValueError("--expected is required in verify-restored mode.")
        asyncio.run(
            _verify_restored(args.uri, args.user, args.password, args.expected, args.out)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
