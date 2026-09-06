from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class EnrichmentAttempt:
    action: Literal["run", "completed", "needs_reconciliation"]
    receipt_uuid: str
    attempt_uuid: str
    status: str


class EnrichmentReceiptStore(Protocol):
    async def begin_enrichment(
        self, *, group_id: str, import_uuid: str, episode_uuid: str, attempt_uuid: str,
    ) -> EnrichmentAttempt: ...

    async def complete_enrichment(
        self, *, receipt_uuid: str, attempt_uuid: str,
    ) -> None: ...

    async def mark_enrichment_uncertain(
        self, *, receipt_uuid: str, attempt_uuid: str, error_type: str,
    ) -> None: ...


class EnrichmentNeedsReconciliation(RuntimeError):
    """A previous attempt may have changed the semantic graph."""
