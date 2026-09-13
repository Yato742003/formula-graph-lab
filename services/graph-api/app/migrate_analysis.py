"""Operator CLI. Credentials are read from environment and never printed."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

from app.analysis_migration import AnalysisMigration
from app.evidence_store import Neo4jEvidenceStore


async def run(args: argparse.Namespace) -> dict:
    missing = [
        var for var in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
        if not os.environ.get(var)
    ]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    store = Neo4jEvidenceStore.connect(
        os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"],
    )
    try:
        migration = AnalysisMigration(store)
        if args.action == "inventory":
            return await migration.inventory(args.workspace)
        if args.action == "verify":
            return await migration.verify_receipt(args.workspace, receipt_id=args.receipt)
        await store.initialize()
        if args.action == "rollback":
            before = await migration.inventory(args.workspace)
            count = await migration.rollback(args.workspace, receipt_id=args.receipt)
            after = await migration.inventory(args.workspace)
            if before != after:
                raise ValueError("Workspace source inventory changed during rollback.")
            return {"retired_versions": count, "source_preserved": True, "inventory": after}
        before = await migration.inventory(args.workspace)
        while True:
            receipt = await migration.migrate_batch(args.workspace, run_id=args.run_id)
            if receipt["status"] == "completed":
                break
        after = await migration.inventory(args.workspace)
        if before != after:
            raise ValueError("Workspace source inventory changed during migration.")
        await migration.verify_receipt(args.workspace, receipt_id=receipt["receipt_id"])
        return {"receipt": receipt, "source_preserved": True, "inventory": after}
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "migrate", "verify", "rollback"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if args.action == "migrate" and not args.run_id:
        parser.error("migrate requires --run-id")
    if args.action in {"verify", "rollback"} and not args.receipt:
        parser.error("verify/rollback requires --receipt")
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        # Driver exceptions can contain URLs/credentials; expose the type only.
        detail = str(exc) if isinstance(exc, ValueError) else None
        logging.getLogger(__name__).error("analysis_migration_failed", extra={
            "event": "analysis_migration_failed", "error_type": type(exc).__name__,
        })
        payload = {"status": "error", "error_type": type(exc).__name__}
        if detail:
            payload["detail"] = detail
        print(json.dumps(payload))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
