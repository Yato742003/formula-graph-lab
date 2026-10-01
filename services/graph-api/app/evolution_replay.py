"""Replay existing compiler evidence and the campaign's native state transitions."""

import hashlib

from pydantic import Field, model_validator

from app.analysis_versions import canonical_json
from app.evolution import (
    EvolutionCampaign,
    MetricSample,
    freeze_finalists,
    record_confirmation,
    reserve_generation,
    select_parents,
    settle_generation,
    start_campaign,
    stop_campaign,
)
from app.problem_spec import FrozenInput
from app.replay_bundle import MAX_BUNDLE_BYTES, CompilerReplayBundle, replay_candidate_from_bundle
from app.research_case import quality_summary


class EvolutionReplayBundle(FrozenInput):
    schema_version: str = "evolution-replay-bundle.v1"
    campaign: EvolutionCampaign
    candidates: tuple[CompilerReplayBundle, ...] = Field(min_length=1, max_length=32)
    bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_bundle(self):
        if self.schema_version != "evolution-replay-bundle.v1":
            raise ValueError("Unknown evolution replay version.")
        payload = canonical_json(self.model_dump(mode="json", exclude={"bundle_hash"}))
        if (
            len(payload.encode()) > MAX_BUNDLE_BYTES
            or hashlib.sha256(payload.encode()).hexdigest() != self.bundle_hash
        ):
            raise ValueError("Evolution bundle hash or size does not match.")
        expected = {e.candidate_id for e in self.campaign.evaluations}
        actual = {b.candidate.candidate_id for b in self.candidates}
        if expected != actual or len(actual) != len(self.candidates):
            raise ValueError("Evolution bundle needs every evaluated candidate exactly once.")
        for bundle in self.candidates:
            if (
                bundle.workspace_id != self.campaign.spec.workspace_id
                or bundle.candidate.problem_spec_id != self.campaign.spec.spec_id
                or bundle.candidate.problem_spec_hash != self.campaign.spec.content_hash
            ):
                raise ValueError("Evolution candidate is outside the frozen campaign.")
        return self


def make_evolution_bundle(campaign, candidates):
    payload = {
        "schema_version": "evolution-replay-bundle.v1",
        "campaign": campaign.model_dump(mode="json"),
        "candidates": [b.model_dump(mode="json") for b in candidates],
    }
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return EvolutionReplayBundle.model_validate(payload | {"bundle_hash": digest})


def replay_evolution_bundle(bundle: EvolutionReplayBundle):
    bundle = EvolutionReplayBundle.model_validate(bundle.model_dump(mode="json"))
    campaign = bundle.campaign
    candidates = {
        b.candidate.candidate_id: replay_candidate_from_bundle(b) for b in bundle.candidates
    }
    bundles = {b.candidate.candidate_id: b for b in bundle.candidates}
    for evaluation in (*campaign.evaluations, *campaign.confirmation_evaluations):
        evidence = bundles[evaluation.candidate_id]
        candidate = candidates[evaluation.candidate_id]
        result = next(
            (
                r
                for r in evidence.research_cases
                if r.result_id in evaluation.result_ids
                and r.evaluation_role == evaluation.data_role
            ),
            None,
        )
        decision = next(
            (
                d
                for d in evidence.admission_decisions
                if d.decision_id == evaluation.admission_decision_id
            ),
            None,
        )
        if result is None or decision is None or decision.action != "can_enter_parent_pool":
            raise ValueError("Evaluation is missing its exact result and admission evidence.")
        if (
            result.evolution_id not in {None, campaign.evolution_id}
            or (evaluation.data_role == "holdout" and result.evolution_id != campaign.evolution_id)
            or evaluation.candidate_hash != candidate.content_hash
            or evaluation.parents != candidate.parents
            or evaluation.operator != candidate.operator
            or evaluation.operator_version != candidate.operator_version
            or evaluation.semantics_class != candidate.semantics_class
            or evaluation.result_ids != (result.result_id, *decision.input_result_ids)
            or evaluation.compute_cost != float(result.search_cost["wall_time_ms"]) / 1000
        ):
            raise ValueError("Evaluation identity/accounting differs from server receipts.")
        eligible = result.quality_constraints_met and decision.allowed
        outcome = "eligible" if eligible else "invalid"
        if any(t.outcome == "timeout" for t in result.trials):
            outcome = "timeout"
        elif any(t.outcome == "error" for t in result.trials):
            outcome = "infrastructure_error"
        metrics = (
            (
                MetricSample(
                    name=result.primary_metric,
                    value=quality_summary(result.trials, result.evaluation_role)[0],
                    unit=campaign.metrics[0].unit,
                    direction=campaign.metrics[0].direction,
                ),
            )
            if eligible
            else ()
        )
        if evaluation.outcome != outcome or evaluation.metrics != metrics:
            raise ValueError("Evolution fitness/outcome does not reproduce from protocol evidence.")
    if not campaign.events or campaign.events[0].kind != "campaign_started":
        raise ValueError("Campaign must start with its recorded event.")
    first = campaign.events[0]
    replayed = start_campaign(campaign.spec, actor_id=first.actor_id, now=first.occurred_at)
    for index, event in enumerate(campaign.events):
        if index < len(replayed.events):
            if event != replayed.events[index]:
                raise ValueError("Campaign events do not reproduce in order.")
            continue
        kwargs = {"actor_id": event.actor_id, "now": event.occurred_at}
        if event.kind == "generation_reserved":
            reservation = next(
                r for r in campaign.reservations if r.reservation_id == event.reference_ids[0]
            )
            replayed = reserve_generation(
                replayed,
                candidate_slots=reservation.candidate_slots,
                compute_reserved=reservation.compute_reserved,
                **kwargs,
            )
        elif event.kind == "parents_selected":
            selection = next(
                s for s in campaign.selections if s.selection_id == event.reference_ids[0]
            )
            replayed, actual = select_parents(
                replayed, count=len(selection.candidate_ids), seed=selection.seed, **kwargs
            )
            if actual != selection:
                raise ValueError("Parent selection does not reproduce.")
        elif event.kind == "generation_settled":
            evaluations = tuple(
                e for e in campaign.evaluations if e.evaluation_id in event.reference_ids
            )
            reservation = next(r for r in replayed.reservations if r.status == "active")
            parents = replayed.selections[-1].candidate_ids if replayed.selections else ()
            if any(e.search_parent_ids != parents for e in evaluations):
                raise ValueError("Mutation parentage differs from the persisted selection.")
            replayed = settle_generation(
                replayed,
                reservation_id=reservation.reservation_id,
                evaluations=evaluations,
                resolved_candidates=tuple(candidates[e.candidate_id] for e in evaluations),
                **kwargs,
            )
        elif event.kind == "finalists_frozen":
            replayed = freeze_finalists(replayed, event.reference_ids, **kwargs)
        elif event.kind == "confirmation_recorded":
            replayed = record_confirmation(replayed, campaign.confirmation_evaluations, **kwargs)
        elif event.kind == "campaign_stopped" and campaign.stop_reason == "user_stop":
            replayed = stop_campaign(replayed, **kwargs)
        elif event.kind == "search_stopped":
            replayed = reserve_generation(
                replayed, candidate_slots=1, compute_reserved=18, **kwargs
            )
        else:
            raise ValueError("Unsupported standalone campaign event.")
        if replayed.events[index] != event:
            raise ValueError("Campaign transition does not reproduce its event.")
    if replayed != campaign:
        raise ValueError("Campaign budget/archive/finalists/winners do not reproduce.")
    return replayed
