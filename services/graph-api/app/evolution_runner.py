"""Bounded, resumable CPU search on the existing DSL, queue, and campaign ledger."""

from __future__ import annotations

import json
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.admission import AdmissionEvaluationRequest
from app.evolution import EvolutionCampaign, EvolutionGenerationRequest
from app.research_case import ResearchCaseReceiptResponse, phase_budget_ms, validate_registered_spec
from app.research_compiler import CompileCandidateRequest, compile_transform
from app.research_store import Neo4jResearchStore, ResearchValidationError

ExecuteCase = Callable[..., Awaitable[ResearchCaseReceiptResponse]]


async def run_generation(
    store: Neo4jResearchStore,
    workspace_id: str,
    evolution_id: str,
    actor_id: str,
    key: str,
    request: EvolutionGenerationRequest,
    execute: ExecuteCase,
) -> tuple[EvolutionCampaign, bool]:
    store._validate_evolution_write(actor_id, key)
    if len(key) > 120:
        raise ResearchValidationError(
            "Evolution request keys must leave room for bounded substeps."
        )
    campaign = await store.get_evolution_campaign(
        workspace_id=workspace_id,
        evolution_id=evolution_id,
    )
    if campaign is None:
        raise ResearchValidationError("Evolution campaign was not found.")
    validate_registered_spec(campaign.spec)
    if campaign.spec.definition.budget.max_candidates > 32:
        raise ResearchValidationError(
            "The registered synchronous CPU pilot supports 32 candidates."
        )
    if len(campaign.metrics) != 1 or campaign.metrics[0].name != "candidate_regret_vs_best_parent":
        raise ResearchValidationError("No runner is registered for these frozen metrics.")
    if campaign.generation == 0 and not campaign.reservations:
        if request.seed_candidate_id is None:
            raise ResearchValidationError("The first generation needs a reviewed seed.")
        _, spec, _, _ = await store.evolution_candidate_context(
            workspace_id=workspace_id,
            candidate_id=request.seed_candidate_id,
        )
        if spec.spec_id != campaign.spec.spec_id or spec.content_hash != campaign.spec.content_hash:
            raise ResearchValidationError("Seed candidate is outside the frozen campaign.")
        gate = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=request.seed_candidate_id,
            actor_id=actor_id,
            idempotency_key=key + ":seed-gate",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
        )
        if not gate.decision.allowed:
            raise ResearchValidationError(
                "Seed needs a passed search protocol and human scope review."
            )
    # ponytail: one candidate per durable step, ceiling: synthetic CPU family;
    # upgrade: a registered evaluator needs measured parallel search throughput.
    reserved, _ = await store.reserve_evolution_generation(
        workspace_id=workspace_id,
        evolution_id=evolution_id,
        candidate_slots=1,
        compute_reserved=phase_budget_ms("search") / 1000,
        actor_id=actor_id,
        idempotency_key=key + ":reserve",
        request_context=request.model_dump(mode="json"),
    )
    reservation = next((r for r in reserved.reservations if r.status == "active"), None)
    if reservation is None:
        return reserved, False
    parents: tuple[str, ...] = ()
    if reservation.generation == 1:
        if request.seed_candidate_id is None:
            raise ResearchValidationError("The first generation needs a human-reviewed seed.")
        candidate_id = request.seed_candidate_id
        candidate, spec, _, _ = await store.evolution_candidate_context(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
        )
        if spec.content_hash != campaign.spec.content_hash:
            raise ResearchValidationError("Seed candidate is outside the frozen campaign.")
        gate = await store.evaluate_candidate_admission(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            actor_id=actor_id,
            idempotency_key=key + ":seed-gate",
            request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
        )
        if not gate.decision.allowed:
            raise ResearchValidationError(
                "Seed needs a passed search protocol and human scope review."
            )
        result_id = next(ref for ref in gate.decision.input_result_ids if ref.startswith("exp_"))
    else:
        if request.seed_candidate_id is not None:
            raise ResearchValidationError("Later generations use server-selected parents only.")
        selected, _ = await store.select_evolution_parents(
            workspace_id=workspace_id,
            evolution_id=evolution_id,
            count=min(2, len(reserved.pareto_archive)),
            seed=campaign.spec.definition.seeds[0] + reservation.generation,
            actor_id=actor_id,
            idempotency_key=key + ":select",
        )
        parents = selected.selections[-1].candidate_ids
        for index, parent_id in enumerate(parents):
            current_gate = await store.evaluate_candidate_admission(
                workspace_id=workspace_id,
                candidate_id=parent_id,
                actor_id=actor_id,
                idempotency_key=key + f":parent-gate:{index}",
                request=AdmissionEvaluationRequest(action="can_enter_parent_pool"),
            )
            if not current_gate.decision.allowed:
                raise ResearchValidationError("Selected parent is no longer eligible for search.")
        resolved = [
            await store.evolution_candidate_context(
                workspace_id=workspace_id,
                candidate_id=parent_id,
            )
            for parent_id in parents
        ]
        context = resolved[0][3]
        if any(item[3].mapping != context.mapping for item in resolved):
            raise ResearchValidationError(
                "Crossover parents must share the reviewed feature-map family."
            )
        weights = [float(json.loads(item[2].ir_json)["lambda"]) for item in resolved]
        generator = random.Random(campaign.spec.definition.seeds[0] + reservation.generation)
        center = sum(weights) / len(weights)
        seen = {item.candidate_id for item in reserved.evaluations}
        for _ in range(20):
            weight = round(generator.uniform(max(0.01, center - 0.15), min(0.99, center + 0.15)), 6)
            transform = {
                "operator": "mix_positive_feature_maps",
                "operator_version": "1",
                "target_node_id": context.target_parent.entity_id,
                "parameters": {"lambda": weight},
                "bindings": {
                    "left": context.left.port.scoped_symbol_id,
                    "right": context.right.port.scoped_symbol_id,
                },
            }
            parent = compile_transform(transform, context=context)
            preview = compile_transform(
                {
                    "operator": "lower_mixture_to_concatenation",
                    "operator_version": "1",
                    "target_node_id": parent.candidate_id,
                    "parameters": {},
                    "bindings": {"mixture": parent.candidate_id},
                },
                context=context,
                parent_candidate=parent,
            )
            if preview.candidate_id not in seen:
                break
        else:
            raise ResearchValidationError("Bounded mutation attempts produced only duplicates.")
        mixture = await store.compile_candidate(
            request=CompileCandidateRequest(
                workspace_id=workspace_id,
                spec_id=campaign.spec.spec_id,
                mapping_id=context.mapping.mapping_id,
                transform=transform,
            ),
            actor_id=actor_id,
            idempotency_key=key + ":mix",
        )
        lowering = await store.compile_candidate(
            request=CompileCandidateRequest(
                workspace_id=workspace_id,
                spec_id=campaign.spec.spec_id,
                mapping_id=context.mapping.mapping_id,
                parent_candidate_id=mixture.candidate.candidate_id,
                transform={
                    "operator": "lower_mixture_to_concatenation",
                    "operator_version": "1",
                    "target_node_id": mixture.candidate.candidate_id,
                    "parameters": {},
                    "bindings": {"mixture": mixture.candidate.candidate_id},
                },
            ),
            actor_id=actor_id,
            idempotency_key=key + ":lower",
        )
        candidate_id = lowering.candidate.candidate_id
        await store.verify_candidate(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            idempotency_key=key + ":check",
        )
        result = await execute(
            store,
            workspace_id,
            candidate_id,
            key + ":trial",
            evaluation_role="search",
            evolution_id=evolution_id,
        )
        result_id = result.result.result_id
    return await store.settle_evolution_from_results(
        workspace_id=workspace_id,
        evolution_id=evolution_id,
        reservation_id=reservation.reservation_id,
        candidate_results=((candidate_id, result_id),),
        search_parent_ids=parents,
        actor_id=actor_id,
        idempotency_key=key + ":settle",
    )


async def confirm_finalists(
    store: Neo4jResearchStore,
    workspace_id: str,
    evolution_id: str,
    actor_id: str,
    key: str,
    execute: ExecuteCase,
) -> tuple[EvolutionCampaign, bool]:
    store._validate_evolution_write(actor_id, key)
    if len(key) > 120:
        raise ResearchValidationError("Confirmation key is too long.")
    campaign = await store.get_evolution_campaign(
        workspace_id=workspace_id,
        evolution_id=evolution_id,
    )
    if campaign is not None and campaign.stop_reason == "confirmation_completed":
        return campaign, True
    if campaign is None or campaign.status != "finalists_frozen":
        raise ResearchValidationError("Confirmation requires frozen finalists.")
    if len(campaign.finalist_ids) > 4:
        raise ResearchValidationError("A synchronous pilot can confirm at most four finalists.")
    spent = sum(
        Decimal(str(r.actual_compute))
        for r in campaign.reservations
        if r.actual_compute is not None
    )
    allowance = Decimal(len(campaign.finalist_ids) * phase_budget_ms("holdout")) / 1000
    if spent + allowance > Decimal(str(campaign.spec.definition.budget.compute_budget)):
        raise ResearchValidationError("Confirmation cannot exceed the frozen compute budget.")
    elapsed = (datetime.now(UTC) - campaign.events[0].occurred_at).total_seconds() * 1000
    if elapsed + float(allowance) * 1000 > campaign.spec.definition.budget.wall_time_ms:
        raise ResearchValidationError("The frozen wall-time budget cannot fit confirmation.")
    results = []
    for index, candidate_id in enumerate(campaign.finalist_ids):
        receipt = await execute(
            store,
            workspace_id,
            candidate_id,
            key + f":holdout:{index}",
            evaluation_role="holdout",
            evolution_id=evolution_id,
        )
        results.append((candidate_id, receipt.result.result_id))
    return await store.settle_evolution_from_results(
        workspace_id=workspace_id,
        evolution_id=evolution_id,
        reservation_id=None,
        candidate_results=tuple(results),
        actor_id=actor_id,
        idempotency_key=key + ":confirm",
        confirmation=True,
    )
