"""Offline paired retrieval benchmark; gold answers never enter the controller."""

import asyncio
import hashlib
import math
import re
import statistics
import time

from app.analysis_versions import canonical_json
from app.models import EvidenceSearchRequest
from app.search import EvidenceSearchService

CONTROLLER_VERSION = "bounded-source-neighbors.v1"


def _source_key(hit):
    anchor = hit.payload.get("anchor")
    if hit.kind != "Equation" or not hit.payload.get("anchor_is_source") or not anchor:
        return None
    return f"{hit.paper_id}v{hit.version}#{anchor}"


async def controlled_retrieval(service: EvidenceSearchService, request: EvidenceSearchRequest):
    """At most two existing searches; no model, generated query, or mutable memory."""
    if request.cursor is not None:
        raise ValueError("Experimental retrieval does not support pagination.")
    async with asyncio.timeout(4):
        first = await service.search(request.model_copy(update={"limit": 16}))
        tokens = set(re.findall(r"[\w.]+", request.query.casefold()))

        def rank(hit):
            text = canonical_json(hit.payload).casefold()
            anchor = str(hit.payload.get("anchor", "")).casefold()
            return (
                bool(anchor and anchor in tokens),
                bool(_source_key(hit)),
                sum(token in text for token in tokens),
                hit.score,
                hit.uuid,
            )

        ranked = sorted(first.hits, key=rank, reverse=True)
        center = next((hit for hit in ranked if _source_key(hit)), None)
        merged = {hit.uuid: hit for hit in first.hits}
        calls = 1
        if center is not None:
            second = await service.search(
                request.model_copy(
                    update={
                        "center_node_uuid": center.uuid,
                        "limit": 16,
                    }
                )
            )
            calls += 1
            for hit in second.hits:
                merged.setdefault(hit.uuid, hit)
        # ponytail: bounded evidence reranking, ceiling: 32 hits/two calls;
        # upgrade: replace only after a held-out benchmark beats this measured baseline.
        return sorted(merged.values(), key=rank, reverse=True)[: request.limit], calls


async def benchmark_retrieval(service, cases, *, repeats=3):
    """Matched queries/k, alternating order, measured wall time, explicit zero model usage."""
    if not 1 <= repeats <= 10 or not 1 <= len(cases) <= 32:
        raise ValueError("Benchmark size exceeds its offline limit.")
    manifest = []
    for request, gold in cases:
        request = EvidenceSearchRequest.model_validate(request.model_dump())
        if request.cursor is not None or not gold or len(gold) > 50:
            raise ValueError("Every frozen query needs bounded source-anchor gold answers.")
        manifest.append({"request": request.model_dump(mode="json"), "gold": sorted(gold)})
    rows = []
    for repeat in range(repeats):
        for index, (request, gold) in enumerate(cases):
            order = (
                ("baseline", "controller")
                if (repeat + index) % 2 == 0
                else ("controller", "baseline")
            )
            for variant in order:
                started = time.perf_counter()
                error = None
                try:
                    async with asyncio.timeout(4):
                        if variant == "baseline":
                            response = await service.search(request)
                            hits, calls = response.hits, 1
                        else:
                            hits, calls = await controlled_retrieval(service, request)
                except (TimeoutError, ValueError) as exc:
                    hits, calls, error = [], 0, type(exc).__name__
                elapsed = (time.perf_counter() - started) * 1000
                retrieved = {_source_key(hit) for hit in hits} - {None}
                correct = len(retrieved & set(gold))
                context_bytes = len(
                    canonical_json(
                        [
                            {"uuid": hit.uuid, "source": _source_key(hit), "payload": hit.payload}
                            for hit in hits
                        ]
                    ).encode("utf-8")
                )
                rows.append(
                    {
                        "case": index,
                        "repeat": repeat,
                        "variant": variant,
                        "latency_ms": elapsed,
                        "precision_at_k": correct / request.limit,
                        "recall_at_k": correct / len(gold),
                        "correct_sources": correct,
                        "source_keys": sorted(retrieved),
                        "search_calls": calls,
                        "error": error,
                        "context_bytes": context_bytes,
                        "estimated_context_tokens": math.ceil(context_bytes / 4),
                        "provider_input_tokens": 0,
                        "provider_output_tokens": 0,
                        "api_cost_usd": 0,
                    }
                )
    summaries = {}
    for variant in ("baseline", "controller"):
        selected = [row for row in rows if row["variant"] == variant]
        latency = sorted(row["latency_ms"] for row in selected)
        summaries[variant] = {
            "runs": len(selected),
            "errors": sum(row["error"] is not None for row in selected),
            "mean_precision_at_k": statistics.fmean(row["precision_at_k"] for row in selected),
            "mean_recall_at_k": statistics.fmean(row["recall_at_k"] for row in selected),
            "latency_p50_ms": statistics.median(latency),
            "latency_p95_ms": latency[math.ceil(0.95 * len(latency)) - 1],
            "mean_estimated_context_tokens": statistics.fmean(
                row["estimated_context_tokens"] for row in selected
            ),
            "provider_input_tokens": 0,
            "provider_output_tokens": 0,
            "api_cost_usd": 0,
        }
    return {
        "schema_version": "retrieval-ab.v1",
        "controller_version": CONTROLLER_VERSION,
        "manifest": manifest,
        "manifest_hash": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
        "scope": "offline_lexical_and_graph_no_llm_no_jev_mem_claim",
        "token_accounting": "context estimate = ceil(UTF-8 bytes/4), not tokenizer usage",
        "promotion": "not_promoted_requires_larger_independent_heldout_corpus",
        "summary": summaries,
        "rows": rows,
    }
