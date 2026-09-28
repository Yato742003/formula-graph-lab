"""FGL-E1 bounded evolution state machine over already-verified candidates.

The controller never calls a model, compiler, checker, or holdout evaluator. It
only selects server-resolved eligible parents, reserves frozen search budget,
records every outcome, and maintains a deterministic Pareto archive.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.admission import AdmissionDecision
from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput, MetricDefinition, ProblemSpecSnapshot
from app.research_compiler import CompiledCandidate, ParentRef, _verify_candidate_identity

EVOLUTION_SCHEMA_VERSION = "evolution-campaign.v1"
MAX_CAMPAIGN_CANDIDATES = 10_000
MAX_RETRIES_PER_CANDIDATE = 2
EventKind = Literal[
    "campaign_started",
    "generation_reserved",
    "generation_settled",
    "parents_selected",
    "search_stopped",
    "finalists_frozen",
    "confirmation_recorded",
    "campaign_stopped",
]


class EvolutionStartRequest(FrozenInput):
    spec_id: str = Field(min_length=1, max_length=200)


class EvolutionFinalistsRequest(FrozenInput):
    finalist_ids: tuple[str, ...] = Field(min_length=1, max_length=128)


class MetricSample(FrozenInput):
    name: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=50)
    direction: Literal["minimize", "maximize"]

    @field_validator("value", mode="before")
    @classmethod
    def finite_value(cls, value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("Metric value must be a finite number.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Metric value must be a finite number.")
        return number


class EvolutionEvaluation(FrozenInput):
    evaluation_id: str = Field(pattern=r"^eev_[0-9a-f]{32}$")
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parents: tuple[ParentRef, ...] = Field(min_length=1, max_length=8)
    operator: str = Field(min_length=1, max_length=100)
    operator_version: str = Field(min_length=1, max_length=50)
    semantics_class: Literal["preserving", "approximation", "hypothesis_changing"]
    admission_decision_id: str | None = Field(
        default=None, pattern=r"^pol_[0-9a-f]{32}$"
    )
    admission_policy_version: str | None = Field(default=None, min_length=1, max_length=50)
    outcome: Literal[
        "eligible", "invalid", "refuted", "timeout", "infrastructure_error"
    ]
    failure_reason: str | None = Field(default=None, min_length=1, max_length=500)
    metrics: tuple[MetricSample, ...] = Field(default=(), max_length=32)
    result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    compute_cost: float = Field(ge=0)
    attempt: int = Field(default=1, ge=1, le=1 + MAX_RETRIES_PER_CANDIDATE)
    data_role: Literal["search", "validation", "holdout"]

    @model_validator(mode="after")
    def validate_evaluation(self) -> EvolutionEvaluation:
        if not math.isfinite(self.compute_cost):
            raise ValueError("Compute cost must be finite.")
        if self.outcome == "eligible":
            if (
                not self.metrics
                or self.admission_decision_id is None
                or self.admission_policy_version is None
            ):
                raise ValueError("An eligible candidate needs metrics and an admission decision.")
            if self.failure_reason is not None:
                raise ValueError("An eligible candidate cannot contain a failure reason.")
        elif self.metrics:
            raise ValueError("Failed or incomplete candidates cannot contribute search metrics.")
        elif self.failure_reason is None:
            raise ValueError("A non-eligible outcome requires a bounded reason.")
        identity = self.model_dump(mode="json", exclude={"evaluation_id"})
        digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        if self.evaluation_id != f"eev_{digest[:32]}":
            raise ValueError("Evolution evaluation identity does not match its content.")
        return self


class GenerationReservation(FrozenInput):
    reservation_id: str = Field(pattern=r"^evr_[0-9a-f]{32}$")
    evolution_id: str = Field(pattern=r"^evo_[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    candidate_slots: int = Field(ge=1)
    retry_slots: int = Field(ge=0)
    compute_reserved: float = Field(gt=0)
    actor_id: str = Field(min_length=1, max_length=200)
    reserved_at: datetime
    status: Literal["active", "settled", "cancelled"] = "active"
    actual_compute: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_reservation(self) -> GenerationReservation:
        if self.reserved_at.tzinfo is None or self.reserved_at.utcoffset() is None:
            raise ValueError("Reservation time must be timezone-aware.")
        if not math.isfinite(self.compute_reserved) or (
            self.actual_compute is not None and not math.isfinite(self.actual_compute)
        ):
            raise ValueError("Reservation costs must be finite.")
        if self.status == "active" and self.actual_compute is not None:
            raise ValueError("An active reservation cannot report actual compute.")
        if self.status == "settled" and self.actual_compute is None:
            raise ValueError("A settled reservation requires actual compute.")
        if self.status == "cancelled" and self.actual_compute is not None:
            raise ValueError("A cancelled reservation cannot report actual compute.")
        identity = [
            self.evolution_id,
            self.generation,
            self.candidate_slots,
            self.retry_slots,
            str(_decimal(self.compute_reserved)),
            self.actor_id,
            self.reserved_at.isoformat(),
        ]
        digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        if self.reservation_id != f"evr_{digest[:32]}":
            raise ValueError("Generation reservation identity does not match its content.")
        return self


class ParentSelection(FrozenInput):
    selection_id: str = Field(pattern=r"^sel_[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    seed: int
    actor_id: str = Field(min_length=1, max_length=200)
    selected_at: datetime

    @model_validator(mode="after")
    def validate_selection(self) -> ParentSelection:
        if self.selected_at.tzinfo is None or self.selected_at.utcoffset() is None:
            raise ValueError("Selection time must be timezone-aware.")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("Selected parents must be unique.")
        identity = [
            self.generation,
            self.candidate_ids,
            self.seed,
            self.actor_id,
            self.selected_at.isoformat(),
        ]
        digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        if self.selection_id != f"sel_{digest[:32]}":
            raise ValueError("Parent selection identity does not match its content.")
        return self


class CampaignEvent(FrozenInput):
    event_id: str = Field(pattern=r"^eve_[0-9a-f]{32}$")
    kind: EventKind
    generation: int = Field(ge=0)
    actor_id: str = Field(min_length=1, max_length=200)
    reference_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_event_identity(self) -> CampaignEvent:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Campaign event time must be timezone-aware.")
        identity = [
            self.kind,
            self.generation,
            self.actor_id,
            self.reference_ids,
            self.occurred_at.isoformat(),
        ]
        digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        if self.event_id != f"eve_{digest[:32]}":
            raise ValueError("Campaign event identity does not match its content.")
        return self


class EvolutionCampaign(FrozenInput):
    schema_version: Literal["evolution-campaign.v1"] = EVOLUTION_SCHEMA_VERSION
    evolution_id: str = Field(pattern=r"^evo_[0-9a-f]{32}$")
    spec: ProblemSpecSnapshot
    metrics: tuple[MetricDefinition, ...] = Field(min_length=1, max_length=32)
    generation: int = Field(default=0, ge=0)
    status: Literal["active", "search_stopped", "finalists_frozen", "stopped"] = "active"
    search_stop_reason: Literal[
        "none",
        "candidate_budget_exhausted",
        "generation_budget_exhausted",
        "compute_budget_exhausted",
        "wall_time_exhausted",
        "stop_condition_met",
    ] = "none"
    stop_reason: Literal[
        "none",
        "user_stop",
        "confirmation_completed",
    ] = "none"
    evaluations: tuple[EvolutionEvaluation, ...] = Field(
        default=(), max_length=MAX_CAMPAIGN_CANDIDATES * (1 + MAX_RETRIES_PER_CANDIDATE)
    )
    reservations: tuple[GenerationReservation, ...] = Field(default=(), max_length=10_000)
    selections: tuple[ParentSelection, ...] = Field(default=(), max_length=10_000)
    pareto_archive: tuple[str, ...] = Field(default=(), max_length=MAX_CAMPAIGN_CANDIDATES)
    finalist_ids: tuple[str, ...] = Field(default=(), max_length=128)
    confirmation_evaluations: tuple[EvolutionEvaluation, ...] = Field(
        default=(), max_length=128
    )
    winner_ids: tuple[str, ...] = Field(default=(), max_length=128)
    events: tuple[CampaignEvent, ...] = Field(default=(), max_length=40_000)

    @model_validator(mode="after")
    def validate_campaign(self) -> EvolutionCampaign:
        expected = hashlib.sha256(
            canonical_json(
                [
                    EVOLUTION_SCHEMA_VERSION,
                    self.spec.workspace_id,
                    self.spec.spec_id,
                    self.spec.content_hash,
                    self.spec.campaign_id,
                ]
            ).encode()
        ).hexdigest()
        if self.evolution_id != f"evo_{expected[:32]}":
            raise ValueError("Evolution campaign identity does not match its ProblemSpec.")
        if self.metrics != self.spec.definition.metrics:
            raise ValueError("Evolution metrics must come from the frozen ProblemSpec.")
        if self.status == "active" and (
            self.search_stop_reason != "none" or self.stop_reason != "none"
        ):
            raise ValueError("An active campaign cannot have a stop reason.")
        if self.status == "search_stopped" and self.search_stop_reason == "none":
            raise ValueError("A search-stopped campaign requires a search stop reason.")
        if self.status != "stopped" and self.stop_reason != "none":
            raise ValueError("Only a terminal campaign can have a terminal stop reason.")
        if self.status == "stopped" and self.stop_reason == "none":
            raise ValueError("A terminal campaign requires a terminal stop reason.")
        known = {item.candidate_id for item in self.evaluations if item.outcome == "eligible"}
        if set(self.pareto_archive) - known or set(self.finalist_ids) - set(
            self.pareto_archive
        ):
            raise ValueError("Archive and finalists must reference eligible recorded candidates.")
        if any(
            item.candidate_id not in self.finalist_ids
            for item in self.confirmation_evaluations
        ):
            raise ValueError("Confirmation results are restricted to frozen finalists.")
        confirmed = {
            item.candidate_id
            for item in self.confirmation_evaluations
            if item.outcome == "eligible"
        }
        if set(self.winner_ids) - confirmed:
            raise ValueError("Winners must reference eligible confirmation results.")
        if len({item.evaluation_id for item in self.evaluations}) != len(self.evaluations):
            raise ValueError("Campaign evaluations must be append-only unique receipts.")
        if len({item.reservation_id for item in self.reservations}) != len(self.reservations):
            raise ValueError("Campaign reservations must have unique identities.")
        if any(item.evolution_id != self.evolution_id for item in self.reservations):
            raise ValueError("Campaign reservations must belong to this evolution campaign.")
        if len({item.selection_id for item in self.selections}) != len(self.selections):
            raise ValueError("Parent selections must have unique identities.")
        if any(set(item.candidate_ids) - known for item in self.selections):
            raise ValueError("Parent selections must reference eligible recorded candidates.")
        if len({item.event_id for item in self.events}) != len(self.events):
            raise ValueError("Campaign events must have unique identities.")
        if self.status != "active" and any(
            item.status == "active" for item in self.reservations
        ):
            raise ValueError("A closed campaign cannot retain an active reservation.")
        return self


class EvolutionCampaignResponse(FrozenInput):
    campaign: EvolutionCampaign
    replayed: bool


def start_campaign(
    spec: ProblemSpecSnapshot,
    *,
    actor_id: str,
    now: datetime | None = None,
) -> EvolutionCampaign:
    if spec.definition.budget.max_candidates > MAX_CAMPAIGN_CANDIDATES:
        raise ValueError("Evolution candidate budget exceeds the controller ceiling.")
    identity = [
        EVOLUTION_SCHEMA_VERSION,
        spec.workspace_id,
        spec.spec_id,
        spec.content_hash,
        spec.campaign_id,
    ]
    evolution_id = "evo_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:32]
    event = _event("campaign_started", 0, actor_id, (spec.spec_id,), now)
    return EvolutionCampaign(
        evolution_id=evolution_id,
        spec=spec,
        metrics=spec.definition.metrics,
        events=(event,),
    )


def reserve_generation(
    campaign: EvolutionCampaign,
    *,
    candidate_slots: int,
    compute_reserved: float,
    actor_id: str,
    now: datetime | None = None,
) -> EvolutionCampaign:
    _require_active(campaign)
    reason = _automatic_stop_reason(campaign, now=now)
    if reason:
        return _stop_search(campaign, reason, actor_id=actor_id, now=now)
    if any(item.status == "active" for item in campaign.reservations):
        raise ValueError("A generation reservation is already in flight.")
    if type(candidate_slots) is not int or candidate_slots <= 0:
        raise ValueError("Candidate slots must be a positive integer.")
    if isinstance(compute_reserved, bool) or not math.isfinite(compute_reserved):
        raise ValueError("Reserved compute must be finite.")
    budget = campaign.spec.definition.budget
    used_candidates = len({item.candidate_id for item in campaign.evaluations})
    if used_candidates + candidate_slots > budget.max_candidates:
        raise ValueError("Candidate budget would be exceeded.")
    if campaign.generation + 1 > budget.max_generations:
        raise ValueError("Generation budget would be exceeded.")
    spent = _spent_compute(campaign)
    if compute_reserved <= 0 or spent + _decimal(compute_reserved) > _decimal(
        budget.compute_budget
    ):
        raise ValueError("Compute budget would be exceeded.")
    reserved_at = _utc(now)
    generation = campaign.generation + 1
    retry_slots = candidate_slots * MAX_RETRIES_PER_CANDIDATE
    identity = [
        campaign.evolution_id,
        generation,
        candidate_slots,
        retry_slots,
        str(_decimal(float(compute_reserved))),
        actor_id,
        reserved_at.isoformat(),
    ]
    reservation = GenerationReservation(
        reservation_id="evr_"
        + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:32],
        evolution_id=campaign.evolution_id,
        generation=generation,
        candidate_slots=candidate_slots,
        retry_slots=retry_slots,
        compute_reserved=compute_reserved,
        actor_id=actor_id,
        reserved_at=reserved_at,
    )
    event = _event(
        "generation_reserved", generation, actor_id, (reservation.reservation_id,), now
    )
    return _update_campaign(
        campaign,
        update={
            "reservations": (*campaign.reservations, reservation),
            "events": (*campaign.events, event),
        },
    )


def make_evaluation(
    candidate: CompiledCandidate,
    *,
    spec: ProblemSpecSnapshot,
    outcome: Literal[
        "eligible", "invalid", "refuted", "timeout", "infrastructure_error"
    ],
    metrics: tuple[MetricSample, ...] = (),
    result_ids: tuple[str, ...] = (),
    compute_cost: float = 0,
    attempt: int = 1,
    data_role: Literal["search", "validation"] = "search",
    admission: AdmissionDecision | None = None,
    failure_reason: str | None = None,
) -> EvolutionEvaluation:
    _verify_candidate_identity(candidate)
    if (
        candidate.workspace_id != spec.workspace_id
        or candidate.problem_spec_id != spec.spec_id
        or candidate.problem_spec_hash != spec.content_hash
    ):
        raise ValueError("Candidate is outside the frozen evolution campaign.")
    if outcome == "eligible":
        if (
            admission is None
            or admission.action != "can_enter_parent_pool"
            or not admission.allowed
            or admission.outcome != "allowed"
            or admission.candidate_id != candidate.candidate_id
            or admission.workspace_id != candidate.workspace_id
        ):
            raise ValueError("Only a current V0 parent-pool decision can admit a candidate.")
        _validate_metrics(metrics, spec.definition.metrics)
    elif metrics:
        raise ValueError("Non-eligible outcomes cannot carry selection metrics.")
    payload = {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.content_hash,
        "parents": [item.model_dump(mode="json") for item in candidate.parents],
        "operator": candidate.operator,
        "operator_version": candidate.operator_version,
        "semantics_class": candidate.semantics_class,
        "admission_decision_id": admission.decision_id if admission else None,
        "admission_policy_version": admission.policy_version if admission else None,
        "outcome": outcome,
        "failure_reason": failure_reason,
        "metrics": [item.model_dump(mode="json") for item in metrics],
        "result_ids": result_ids,
        "compute_cost": float(compute_cost),
        "attempt": attempt,
        "data_role": data_role,
    }
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return EvolutionEvaluation(evaluation_id=f"eev_{digest[:32]}", **payload)


def make_confirmation_evaluation(
    candidate: CompiledCandidate,
    *,
    spec: ProblemSpecSnapshot,
    metrics: tuple[MetricSample, ...],
    result_ids: tuple[str, ...],
    compute_cost: float,
    admission: AdmissionDecision,
) -> EvolutionEvaluation:
    """Build a holdout receipt that cannot be fed back into search settlement."""
    evaluation = make_evaluation(
        candidate,
        spec=spec,
        outcome="eligible",
        metrics=metrics,
        result_ids=result_ids,
        compute_cost=compute_cost,
        admission=admission,
    )
    payload = evaluation.model_dump(mode="json", exclude={"evaluation_id"})
    payload["data_role"] = "holdout"
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return EvolutionEvaluation(evaluation_id=f"eev_{digest[:32]}", **payload)


def settle_generation(
    campaign: EvolutionCampaign,
    *,
    reservation_id: str,
    evaluations: tuple[EvolutionEvaluation, ...],
    resolved_candidates: tuple[CompiledCandidate, ...],
    actor_id: str,
    now: datetime | None = None,
) -> EvolutionCampaign:
    _require_active(campaign)
    reservation = next(
        (
            item
            for item in campaign.reservations
            if item.reservation_id == reservation_id and item.status == "active"
        ),
        None,
    )
    if reservation is None:
        raise ValueError("Active generation reservation was not found.")
    if not evaluations:
        raise ValueError("A settled generation must retain at least one outcome.")
    candidate_ids = {item.candidate_id for item in evaluations}
    if candidate_ids & {item.candidate_id for item in campaign.evaluations}:
        raise ValueError("A candidate already evaluated in this campaign cannot be repeated.")
    if len(candidate_ids) > reservation.candidate_slots:
        raise ValueError("Generation produced more unique candidates than reserved.")
    retries = len(evaluations) - len(candidate_ids)
    if retries > reservation.retry_slots:
        raise ValueError("Generation retry budget was exceeded.")
    if len({item.evaluation_id for item in evaluations}) != len(evaluations):
        raise ValueError("Duplicate evaluation receipts are not allowed.")
    candidates = {item.candidate_id: item for item in resolved_candidates}
    if len(candidates) != len(resolved_candidates):
        raise ValueError("Resolved candidate inputs must be unique.")
    for item in evaluations:
        if item.data_role not in {"search", "validation"}:
            raise ValueError("Holdout feedback cannot settle a search generation.")
        candidate = candidates.get(item.candidate_id)
        if candidate is None:
            raise ValueError("Evolution result references an unresolved candidate.")
        _validate_evaluation_scope(item, campaign, candidate=candidate)
    for candidate_id in candidate_ids:
        attempts = [item.attempt for item in evaluations if item.candidate_id == candidate_id]
        if len(attempts) > 1 + MAX_RETRIES_PER_CANDIDATE or len(set(attempts)) != len(
            attempts
        ):
            raise ValueError("Per-candidate retry attempts are invalid or exhausted.")
    actual = sum((_decimal(item.compute_cost) for item in evaluations), Decimal(0))
    if actual > _decimal(reservation.compute_reserved):
        raise ValueError("Actual compute exceeds the reserved allowance.")
    settled = reservation.model_copy(
        update={"status": "settled", "actual_compute": float(actual)}
    )
    reservations = tuple(
        settled if item.reservation_id == reservation_id else item
        for item in campaign.reservations
    )
    all_evaluations = (*campaign.evaluations, *evaluations)
    archive = _pareto_archive(all_evaluations, campaign.metrics)
    event = _event(
        "generation_settled",
        reservation.generation,
        actor_id,
        tuple(item.evaluation_id for item in evaluations),
        now,
    )
    updated = _update_campaign(
        campaign,
        update={
            "generation": reservation.generation,
            "evaluations": all_evaluations,
            "reservations": reservations,
            "pareto_archive": archive,
            "events": (*campaign.events, event),
        },
    )
    reason = _automatic_stop_reason(updated, now=now)
    return _stop_search(updated, reason, actor_id=actor_id, now=now) if reason else updated


def select_parents(
    campaign: EvolutionCampaign,
    *,
    count: int,
    seed: int,
    actor_id: str,
    now: datetime | None = None,
) -> tuple[EvolutionCampaign, ParentSelection]:
    _require_active(campaign)
    if type(count) is not int or count <= 0:
        raise ValueError("Parent count must be a positive integer.")
    records = {
        item.candidate_id: item
        for item in campaign.evaluations
        if item.outcome == "eligible" and item.candidate_id in campaign.pareto_archive
    }
    if count > len(records):
        raise ValueError("Not enough eligible Pareto parents are available.")
    ranked = sorted(
        records.values(),
        key=lambda item: hashlib.sha256(
            canonical_json([seed, item.operator, item.semantics_class, item.candidate_id]).encode()
        ).hexdigest(),
    )
    selected: list[EvolutionEvaluation] = []
    seen_shapes: set[tuple[str, str]] = set()
    for item in ranked:
        shape = (item.operator, item.semantics_class)
        if shape not in seen_shapes:
            selected.append(item)
            seen_shapes.add(shape)
        if len(selected) == count:
            break
    for item in ranked:
        if item not in selected:
            selected.append(item)
        if len(selected) == count:
            break
    selected_at = _utc(now)
    payload = {
        "generation": campaign.generation + 1,
        "candidate_ids": tuple(item.candidate_id for item in selected),
        "seed": seed,
        "actor_id": actor_id,
        "selected_at": selected_at,
    }
    identity = [
        payload["generation"],
        payload["candidate_ids"],
        payload["seed"],
        payload["actor_id"],
        selected_at.isoformat(),
    ]
    digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    selection = ParentSelection(selection_id=f"sel_{digest[:32]}", **payload)
    event = _event(
        "parents_selected",
        selection.generation,
        actor_id,
        (selection.selection_id, *selection.candidate_ids),
        now,
    )
    return (
        _update_campaign(
            campaign,
            update={
                "selections": (*campaign.selections, selection),
                "events": (*campaign.events, event),
            },
        ),
        selection,
    )


def freeze_finalists(
    campaign: EvolutionCampaign,
    finalist_ids: tuple[str, ...],
    *,
    actor_id: str,
    now: datetime | None = None,
) -> EvolutionCampaign:
    if campaign.status not in {"active", "search_stopped"}:
        raise ValueError("Evolution campaign is not open for finalist selection.")
    if any(item.status == "active" for item in campaign.reservations):
        raise ValueError("Cannot freeze finalists while a generation is in flight.")
    if not finalist_ids or len(set(finalist_ids)) != len(finalist_ids):
        raise ValueError("Finalists must be a non-empty unique selection.")
    if set(finalist_ids) - set(campaign.pareto_archive):
        raise ValueError("Only Pareto-eligible candidates can become finalists.")
    event = _event(
        "finalists_frozen", campaign.generation, actor_id, finalist_ids, now
    )
    return _update_campaign(
        campaign,
        update={
            "status": "finalists_frozen",
            "finalist_ids": finalist_ids,
            "events": (*campaign.events, event),
        },
    )


def record_confirmation(
    campaign: EvolutionCampaign,
    evaluations: tuple[EvolutionEvaluation, ...],
    *,
    actor_id: str,
    now: datetime | None = None,
) -> EvolutionCampaign:
    if campaign.status != "finalists_frozen" or not evaluations:
        raise ValueError("Confirmation requires frozen finalists and retained results.")
    if len({item.evaluation_id for item in evaluations}) != len(evaluations):
        raise ValueError("Confirmation results must be unique receipts.")
    if (
        {item.candidate_id for item in evaluations} != set(campaign.finalist_ids)
        or len(evaluations) != len(campaign.finalist_ids)
    ):
        raise ValueError("Confirmation must retain exactly one result per frozen finalist.")
    for item in evaluations:
        if item.candidate_id not in campaign.finalist_ids:
            raise ValueError("Holdout confirmation is restricted to frozen finalists.")
        if item.data_role != "holdout":
            raise ValueError("Confirmation results must use the protected holdout role.")
        source = next(
            (
                prior
                for prior in campaign.evaluations
                if prior.candidate_id == item.candidate_id and prior.outcome == "eligible"
            ),
            None,
        )
        if source is None or (
            item.candidate_hash,
            item.parents,
            item.operator,
            item.operator_version,
            item.semantics_class,
        ) != (
            source.candidate_hash,
            source.parents,
            source.operator,
            source.operator_version,
            source.semantics_class,
        ):
            raise ValueError("Confirmation result does not match its frozen finalist.")
        _validate_evaluation_scope(item, campaign)
    event = _event(
        "confirmation_recorded",
        campaign.generation,
        actor_id,
        tuple(item.evaluation_id for item in evaluations),
        now,
    )
    return _update_campaign(
        campaign,
        update={
            "status": "stopped",
            "stop_reason": "confirmation_completed",
            "confirmation_evaluations": evaluations,
            "winner_ids": _pareto_archive(evaluations, campaign.metrics),
            "events": (*campaign.events, event),
        },
    )


class EvolutionReport(FrozenInput):
    schema_version: Literal["evolution-report.v1"] = "evolution-report.v1"
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evolution_id: str = Field(pattern=r"^evo_[0-9a-f]{32}$")
    problem_spec_id: str
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "search_stopped", "finalists_frozen", "stopped"]
    search_stop_reason: str
    stop_reason: str
    search_compute_cost: float = Field(ge=0)
    confirmation_compute_cost: float = Field(ge=0)
    compute_unit: str
    compared_candidates: tuple[EvolutionEvaluation, ...]
    pareto_archive: tuple[str, ...]
    finalist_ids: tuple[str, ...]
    winner_ids: tuple[str, ...]
    selections: tuple[ParentSelection, ...]
    events: tuple[CampaignEvent, ...]

    @model_validator(mode="after")
    def validate_report(self) -> EvolutionReport:
        if not math.isfinite(self.search_compute_cost) or not math.isfinite(
            self.confirmation_compute_cost
        ):
            raise ValueError("Evolution report costs must be finite.")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        if self.report_hash != digest:
            raise ValueError("Evolution report hash does not match its content.")
        return self


def build_evolution_report(campaign: EvolutionCampaign) -> EvolutionReport:
    payload = {
        "evolution_id": campaign.evolution_id,
        "problem_spec_id": campaign.spec.spec_id,
        "problem_spec_hash": campaign.spec.content_hash,
        "status": campaign.status,
        "search_stop_reason": campaign.search_stop_reason,
        "stop_reason": campaign.stop_reason,
        "search_compute_cost": float(_spent_compute(campaign)),
        "confirmation_compute_cost": float(
            sum(
                (_decimal(item.compute_cost) for item in campaign.confirmation_evaluations),
                Decimal(0),
            )
        ),
        "compute_unit": campaign.spec.definition.budget.compute_unit,
        "compared_candidates": campaign.evaluations,
        "pareto_archive": campaign.pareto_archive,
        "finalist_ids": campaign.finalist_ids,
        "winner_ids": campaign.winner_ids,
        "selections": campaign.selections,
        "events": campaign.events,
    }
    serializable = EvolutionReport.model_construct(
        schema_version="evolution-report.v1",
        report_hash="0" * 64,
        **payload,
    ).model_dump(mode="json", exclude={"report_hash"})
    digest = hashlib.sha256(canonical_json(serializable).encode()).hexdigest()
    return EvolutionReport(report_hash=digest, **payload)


def stop_campaign(
    campaign: EvolutionCampaign,
    *,
    actor_id: str,
    now: datetime | None = None,
) -> EvolutionCampaign:
    if campaign.status == "stopped":
        raise ValueError("Evolution campaign is already stopped.")
    return _stop(campaign, "user_stop", actor_id=actor_id, now=now)


def _pareto_archive(
    evaluations: tuple[EvolutionEvaluation, ...],
    definitions: tuple[MetricDefinition, ...],
) -> tuple[str, ...]:
    by_hash: dict[str, EvolutionEvaluation] = {}
    for item in sorted(evaluations, key=lambda value: value.evaluation_id):
        if item.outcome == "eligible":
            by_hash.setdefault(item.candidate_hash, item)
    candidates = list(by_hash.values())
    archive = [
        item
        for item in candidates
        if not any(
            other.candidate_id != item.candidate_id
            and _dominates(other, item, definitions)
            for other in candidates
        )
    ]
    return tuple(sorted(item.candidate_id for item in archive))


def _dominates(
    left: EvolutionEvaluation,
    right: EvolutionEvaluation,
    definitions: tuple[MetricDefinition, ...],
) -> bool:
    left_values = {item.name: item.value for item in left.metrics}
    right_values = {item.name: item.value for item in right.metrics}
    weak = []
    strict = []
    for definition in definitions:
        left_value = left_values[definition.name]
        right_value = right_values[definition.name]
        if definition.direction == "minimize":
            weak.append(left_value <= right_value)
            strict.append(left_value < right_value)
        else:
            weak.append(left_value >= right_value)
            strict.append(left_value > right_value)
    return all(weak) and any(strict)


def _validate_metrics(
    samples: tuple[MetricSample, ...], definitions: tuple[MetricDefinition, ...]
) -> None:
    expected = {
        item.name: (item.unit, item.direction)
        for item in definitions
    }
    actual = {item.name: (item.unit, item.direction) for item in samples}
    if len(actual) != len(samples) or actual != expected:
        raise ValueError("Candidate metrics must exactly match the frozen ProblemSpec.")


def _validate_evaluation_scope(
    evaluation: EvolutionEvaluation,
    campaign: EvolutionCampaign,
    *,
    candidate: CompiledCandidate | None = None,
) -> None:
    if candidate is not None:
        _verify_candidate_identity(candidate)
        if (
            candidate.workspace_id != campaign.spec.workspace_id
            or candidate.problem_spec_id != campaign.spec.spec_id
            or candidate.problem_spec_hash != campaign.spec.content_hash
            or evaluation.candidate_hash != candidate.content_hash
            or evaluation.parents != candidate.parents
            or evaluation.operator != candidate.operator
            or evaluation.operator_version != candidate.operator_version
            or evaluation.semantics_class != candidate.semantics_class
        ):
            raise ValueError("Evolution result does not match the resolved compiled candidate.")
    if evaluation.outcome == "eligible":
        _validate_metrics(evaluation.metrics, campaign.metrics)


def _automatic_stop_reason(
    campaign: EvolutionCampaign,
    *,
    now: datetime | None,
) -> Literal[
    "candidate_budget_exhausted",
    "generation_budget_exhausted",
    "compute_budget_exhausted",
    "wall_time_exhausted",
    "stop_condition_met",
] | None:
    budget = campaign.spec.definition.budget
    current = _utc(now)
    started = campaign.events[0].occurred_at
    if current < started:
        raise ValueError("Evolution time cannot move before campaign start.")
    if (current - started).total_seconds() * 1000 >= budget.wall_time_ms:
        return "wall_time_exhausted"
    if len({item.candidate_id for item in campaign.evaluations}) >= budget.max_candidates:
        return "candidate_budget_exhausted"
    if campaign.generation >= budget.max_generations:
        return "generation_budget_exhausted"
    if _spent_compute(campaign) >= _decimal(budget.compute_budget):
        return "compute_budget_exhausted"
    for condition in campaign.spec.definition.stop_conditions:
        if any(
            sample.name == condition.metric
            and _compare(sample.value, condition.relation, condition.target_value)
            for item in campaign.evaluations
            if item.candidate_id in campaign.pareto_archive and item.outcome == "eligible"
            for sample in item.metrics
        ):
            return "stop_condition_met"
    return None


def _compare(left: float, relation: str, right: float) -> bool:
    return {
        "<=": left <= right,
        ">=": left >= right,
        "<": left < right,
        ">": left > right,
        "==": left == right,
    }[relation]


def _spent_compute(campaign: EvolutionCampaign) -> Decimal:
    return sum(
        (
            _decimal(item.actual_compute)
            for item in campaign.reservations
            if item.status == "settled" and item.actual_compute is not None
        ),
        Decimal(0),
    )


def _stop_search(
    campaign: EvolutionCampaign,
    reason: Literal[
        "candidate_budget_exhausted",
        "generation_budget_exhausted",
        "compute_budget_exhausted",
        "wall_time_exhausted",
        "stop_condition_met",
    ],
    *,
    actor_id: str,
    now: datetime | None,
) -> EvolutionCampaign:
    event = _event("search_stopped", campaign.generation, actor_id, (reason,), now)
    return _update_campaign(
        campaign,
        update={
            "status": "search_stopped",
            "search_stop_reason": reason,
            "events": (*campaign.events, event),
        },
    )


def _stop(
    campaign: EvolutionCampaign,
    reason: Literal["user_stop"],
    *,
    actor_id: str,
    now: datetime | None,
) -> EvolutionCampaign:
    event = _event("campaign_stopped", campaign.generation, actor_id, (reason,), now)
    reservations = tuple(
        item.model_copy(update={"status": "cancelled"})
        if item.status == "active"
        else item
        for item in campaign.reservations
    )
    return _update_campaign(
        campaign,
        update={
            "status": "stopped",
            "stop_reason": reason,
            "reservations": reservations,
            "events": (*campaign.events, event),
        },
    )


def _event(
    kind: EventKind,
    generation: int,
    actor_id: str,
    reference_ids: tuple[str, ...],
    now: datetime | None,
) -> CampaignEvent:
    occurred_at = _utc(now)
    identity = [kind, generation, actor_id, reference_ids, occurred_at.isoformat()]
    return CampaignEvent(
        event_id="eve_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:32],
        kind=kind,
        generation=generation,
        actor_id=actor_id,
        reference_ids=reference_ids,
        occurred_at=occurred_at,
    )


def _require_active(campaign: EvolutionCampaign) -> None:
    if campaign.status != "active":
        raise ValueError("Evolution campaign is not open for search feedback.")


def _update_campaign(
    campaign: EvolutionCampaign, *, update: dict[str, object]
) -> EvolutionCampaign:
    return EvolutionCampaign.model_validate(
        campaign.model_copy(update=update).model_dump(mode="python")
    )


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Evolution timestamps must be timezone-aware.")
    return result.astimezone(UTC)
