"""FGL-E1 deterministic Pareto search, budget, lineage, and holdout gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from app.admission import AdmissionDecision
from app.analysis_versions import canonical_json
from app.evolution import (
    EvolutionCampaign,
    MetricSample,
    build_evolution_report,
    freeze_finalists,
    make_confirmation_evaluation,
    make_evaluation,
    record_confirmation,
    reserve_generation,
    select_parents,
    settle_generation,
    start_campaign,
    stop_campaign,
)
from app.problem_spec import ProblemDefinition, ProblemSpecSnapshot, definition_hash
from app.research_compiler import CompiledCandidate, Obligation, ParentRef

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _spec(
    *,
    max_candidates: int = 8,
    max_generations: int = 3,
    compute_budget: float = 12,
    wall_time_ms: int = 60_000,
    stop_conditions: list[dict[str, object]] | None = None,
) -> ProblemSpecSnapshot:
    definition = ProblemDefinition.model_validate(
        {
            "task": "Bounded attention search",
            "method_family": "attention",
            "metrics": [
                {"name": "quality", "unit": "score", "direction": "maximize"},
                {"name": "latency", "unit": "ms", "direction": "minimize"},
                {"name": "memory", "unit": "MiB", "direction": "minimize"},
            ],
            "baselines": [{"name": "baseline", "version": "1", "sha256": HASH_A}],
            "dataset": {
                "artifact_hash": HASH_B,
                "version": "1",
                "splits": [
                    {"role": "search", "split_hash": HASH_A},
                    {"role": "validation", "split_hash": HASH_B},
                    {"role": "holdout", "split_hash": HASH_C},
                ],
            },
            "model": {"name": "model", "version": "1", "sha256": HASH_A},
            "tokenizer": {"status": "not_applicable", "reason": "Synthetic fixture."},
            "hardware": {"target": "cpu"},
            "backend": {"name": "numpy", "version": "2"},
            "dtype": "float64",
            "evaluator": {
                "name": "evaluator",
                "protocol_version": "1",
                "implementation_hash": HASH_B,
                "config_hash": HASH_C,
            },
            "seeds": [7, 11],
            "budget": {
                "max_candidates": max_candidates,
                "max_generations": max_generations,
                "wall_time_ms": wall_time_ms,
                "compute_budget": compute_budget,
                "compute_unit": "CPU-seconds",
            },
            "allowed_transforms": [
                {"name": "mix_positive_feature_maps", "version": "1"},
                {"name": "lower_mixture_to_concatenation", "version": "1"},
            ],
            "stop_conditions": stop_conditions or [],
        }
    )
    return ProblemSpecSnapshot(
        content_hash=definition_hash(definition),
        spec_id="spec-evolution",
        workspace_id="ws-evolution",
        campaign_id="problem-campaign",
        created_by="researcher",
        created_at=NOW.isoformat(),
        definition=definition,
    )


def _candidate(
    spec: ProblemSpecSnapshot,
    suffix: str,
    *,
    operator: str = "mix_positive_feature_maps",
    semantics: str = "hypothesis_changing",
    parents: tuple[ParentRef, ...] | None = None,
) -> CompiledCandidate:
    parent_refs = parents or (
        ParentRef(entity_id=f"parent-{suffix}-a", version=1, content_hash=HASH_A),
        ParentRef(entity_id=f"parent-{suffix}-b", version=2, content_hash=HASH_B),
    )
    obligations = (Obligation(name="kernel_identity", status="discharged"),)
    payload = {
        "workspace_id": spec.workspace_id,
        "problem_spec_id": spec.spec_id,
        "problem_spec_hash": spec.content_hash,
        "operator": operator,
        "operator_version": "1",
        "semantics_class": semantics,
        "compiler_version": "research-compiler.v1",
        "ir_version": "research-ir.v1",
        "ir_json": canonical_json({"kind": "fixture", "suffix": suffix}),
        "parents": [item.model_dump(mode="json") for item in parent_refs],
        "obligations": [item.model_dump(mode="json") for item in obligations],
    }
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    return CompiledCandidate(
        candidate_id=f"cand_{digest[:32]}",
        content_hash=digest,
        workspace_id=spec.workspace_id,
        problem_spec_id=spec.spec_id,
        problem_spec_hash=spec.content_hash,
        operator=operator,
        operator_version="1",
        semantics_class=semantics,
        ir_json=payload["ir_json"],
        parents=parent_refs,
        obligations=obligations,
    )


def _admission(candidate: CompiledCandidate, *, allowed: bool = True) -> AdmissionDecision:
    return AdmissionDecision(
        decision_id="pol_" + sha256(candidate.content_hash.encode()).hexdigest()[:32],
        action="can_enter_parent_pool",
        candidate_id=candidate.candidate_id,
        workspace_id=candidate.workspace_id,
        allowed=allowed,
        outcome="allowed" if allowed else "denied",
        rule_id="FGL-V0-PARENT-ELIGIBLE" if allowed else "FGL-V0-DENY-DEFAULT",
        reasons=("eligible",) if allowed else ("empirical_protocol_not_supported",),
        input_result_ids=("symbolic", "numerical", "empirical"),
        actor_id="controller",
        decided_at=NOW,
    )


def _metrics(quality: float, latency: float, memory: float) -> tuple[MetricSample, ...]:
    return (
        MetricSample(name="quality", value=quality, unit="score", direction="maximize"),
        MetricSample(name="latency", value=latency, unit="ms", direction="minimize"),
        MetricSample(name="memory", value=memory, unit="MiB", direction="minimize"),
    )


def _eligible(
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
    metrics: tuple[MetricSample, ...],
    *,
    cost: float = 1,
    attempt: int = 1,
):
    return make_evaluation(
        candidate,
        spec=spec,
        outcome="eligible",
        metrics=metrics,
        result_ids=(f"result-{candidate.candidate_id[-6:]}-{attempt}",),
        compute_cost=cost,
        attempt=attempt,
        admission=_admission(candidate),
    )


def test_reservation_and_ledger_share_one_clock_when_now_is_not_supplied(monkeypatch):
    from app import evolution

    campaign = start_campaign(_spec(), actor_id="researcher", now=NOW)
    ticks = iter(NOW + timedelta(microseconds=i) for i in range(1, 20))
    monkeypatch.setattr(evolution, "_utc", lambda value: value if value else next(ticks))
    reserved = reserve_generation(
        campaign, candidate_slots=1, compute_reserved=1, actor_id="controller"
    )
    assert reserved.reservations[-1].reserved_at == reserved.events[-1].occurred_at


def test_pareto_archive_is_seeded_deterministic_and_preserves_full_parentage():
    spec = _spec()
    campaign = start_campaign(spec, actor_id="researcher", now=NOW)
    candidates = (
        _candidate(spec, "quality"),
        _candidate(
            spec,
            "speed",
            operator="lower_mixture_to_concatenation",
            semantics="preserving",
        ),
        _candidate(spec, "dominated"),
    )
    campaign = reserve_generation(
        campaign,
        candidate_slots=3,
        compute_reserved=4,
        actor_id="controller",
        now=NOW,
    )
    evaluations = (
        _eligible(candidates[0], spec, _metrics(0.95, 11, 90)),
        _eligible(candidates[1], spec, _metrics(0.90, 7, 70)),
        _eligible(candidates[2], spec, _metrics(0.80, 15, 120)),
    )
    campaign = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=evaluations,
        resolved_candidates=candidates,
        actor_id="controller",
        now=NOW,
    )

    assert set(campaign.pareto_archive) == {
        candidates[0].candidate_id,
        candidates[1].candidate_id,
    }
    recorded, first = select_parents(
        campaign, count=2, seed=42, actor_id="controller", now=NOW
    )
    replay, second = select_parents(
        campaign, count=2, seed=42, actor_id="controller", now=NOW
    )
    assert first == second
    assert recorded == replay
    assert recorded.selections == (first,)
    by_candidate = {item.candidate_id: item for item in evaluations}
    assert {by_candidate[item].semantics_class for item in first.candidate_ids} == {
        "preserving",
        "hypothesis_changing",
    }

    replayed = EvolutionCampaign.model_validate_json(recorded.model_dump_json())
    by_id = {item.candidate_id: item for item in replayed.evaluations}
    assert by_id[candidates[0].candidate_id].parents == candidates[0].parents
    assert by_id[candidates[1].candidate_id].parents == candidates[1].parents


def test_budget_reservation_counts_in_flight_and_failures_never_win():
    spec = _spec(max_candidates=2, max_generations=4, compute_budget=3)
    campaign = start_campaign(spec, actor_id="researcher", now=NOW)
    campaign = reserve_generation(
        campaign,
        candidate_slots=2,
        compute_reserved=3,
        actor_id="controller",
        now=NOW,
    )
    with pytest.raises(ValueError, match="already in flight"):
        reserve_generation(
            campaign,
            candidate_slots=1,
            compute_reserved=1,
            actor_id="controller",
            now=NOW,
        )
    good = _candidate(spec, "good")
    broken = _candidate(spec, "broken")
    failed = make_evaluation(
        broken,
        spec=spec,
        outcome="infrastructure_error",
        failure_reason="worker transport failed",
        compute_cost=1,
    )
    campaign = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=(_eligible(good, spec, _metrics(0.9, 8, 64), cost=2), failed),
        resolved_candidates=(good, broken),
        actor_id="controller",
        now=NOW,
    )

    assert campaign.status == "search_stopped"
    assert campaign.search_stop_reason == "candidate_budget_exhausted"
    assert campaign.pareto_archive == (good.candidate_id,)
    assert broken.candidate_id not in campaign.pareto_archive
    with pytest.raises(ValueError, match="not open"):
        reserve_generation(
            campaign,
            candidate_slots=1,
            compute_reserved=1,
            actor_id="controller",
            now=NOW,
        )


def test_denied_or_metric_mismatched_candidate_cannot_become_a_parent():
    spec = _spec()
    candidate = _candidate(spec, "denied")
    with pytest.raises(ValueError, match="V0 parent-pool"):
        make_evaluation(
            candidate,
            spec=spec,
            outcome="eligible",
            metrics=_metrics(0.9, 8, 64),
            admission=_admission(candidate, allowed=False),
        )

    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        now=NOW,
    )
    other = _candidate(spec, "other")
    with pytest.raises(ValueError, match="unresolved candidate"):
        settle_generation(
            campaign,
            reservation_id=campaign.reservations[0].reservation_id,
            evaluations=(_eligible(candidate, spec, _metrics(0.9, 8, 64)),),
            resolved_candidates=(other,),
            actor_id="controller",
            now=NOW,
        )
    with pytest.raises(ValueError, match="exactly match"):
        make_evaluation(
            candidate,
            spec=spec,
            outcome="eligible",
            metrics=_metrics(0.9, 8, 64)[:2],
            admission=_admission(candidate),
        )
    with pytest.raises(ValueError, match="cannot carry selection metrics"):
        make_evaluation(
            candidate,
            spec=spec,
            outcome="timeout",
            failure_reason="wall clock limit",
            metrics=_metrics(0, 0, 0),
        )


def test_retry_cap_and_reserved_compute_are_enforced_without_scoring_errors():
    spec = _spec()
    candidate = _candidate(spec, "retry")
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=2,
        actor_id="controller",
        now=NOW,
    )
    timeout = make_evaluation(
        candidate,
        spec=spec,
        outcome="timeout",
        failure_reason="attempt one timed out",
        compute_cost=0.5,
        attempt=1,
    )
    infra = make_evaluation(
        candidate,
        spec=spec,
        outcome="infrastructure_error",
        failure_reason="attempt two lost its worker",
        compute_cost=0.5,
        attempt=2,
    )
    passed = _eligible(candidate, spec, _metrics(0.9, 8, 64), cost=1, attempt=3)
    settled = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=(timeout, infra, passed),
        resolved_candidates=(candidate,),
        actor_id="controller",
        now=NOW,
    )
    assert settled.pareto_archive == (candidate.candidate_id,)
    assert [item.outcome for item in settled.evaluations] == [
        "timeout",
        "infrastructure_error",
        "eligible",
    ]

    overspend = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        now=NOW,
    )
    with pytest.raises(ValueError, match="Actual compute"):
        settle_generation(
            overspend,
            reservation_id=overspend.reservations[0].reservation_id,
            evaluations=(_eligible(candidate, spec, _metrics(0.9, 8, 64), cost=1.1),),
            resolved_candidates=(candidate,),
            actor_id="controller",
            now=NOW,
        )


def test_user_stop_cancels_in_flight_reservation_without_spending_it():
    spec = _spec()
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=2,
        compute_reserved=4,
        actor_id="controller",
        now=NOW,
    )

    stopped = stop_campaign(campaign, actor_id="researcher", now=NOW)

    assert stopped.status == "stopped"
    assert stopped.stop_reason == "user_stop"
    assert stopped.reservations[0].status == "cancelled"
    assert stopped.reservations[0].actual_compute is None


def test_holdout_is_locked_to_frozen_finalists_and_cannot_start_a_generation():
    spec = _spec()
    candidate = _candidate(spec, "finalist")
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=2,
        actor_id="controller",
        now=NOW,
    )
    campaign = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=(_eligible(candidate, spec, _metrics(0.9, 8, 64)),),
        resolved_candidates=(candidate,),
        actor_id="controller",
        now=NOW,
    )
    campaign = freeze_finalists(
        campaign, (candidate.candidate_id,), actor_id="reviewer", now=NOW
    )
    holdout = make_confirmation_evaluation(
        candidate,
        spec=spec,
        metrics=_metrics(0.89, 8.2, 64),
        result_ids=("holdout-result",),
        compute_cost=1,
        admission=_admission(candidate),
    )
    with pytest.raises(ValueError, match="not open"):
        reserve_generation(
            campaign,
            candidate_slots=1,
            compute_reserved=1,
            actor_id="controller",
            now=NOW,
        )
    confirmed = record_confirmation(
        campaign, (holdout,), actor_id="experiment-worker", now=NOW
    )
    assert confirmed.status == "stopped"
    assert confirmed.stop_reason == "confirmation_completed"
    assert confirmed.confirmation_evaluations == (holdout,)
    assert confirmed.pareto_archive == (candidate.candidate_id,)
    assert confirmed.winner_ids == (candidate.candidate_id,)

    report = build_evolution_report(confirmed)
    assert report.compared_candidates == confirmed.evaluations
    assert report.search_compute_cost == 1
    assert report.confirmation_compute_cost == 1
    assert report.winner_ids == (candidate.candidate_id,)
    assert report.report_hash == build_evolution_report(confirmed).report_hash
    tampered = report.model_dump(mode="python")
    tampered["search_compute_cost"] = 0
    with pytest.raises(ValueError, match="report hash"):
        type(report).model_validate(tampered)


def test_frozen_stop_condition_stops_on_a_recorded_pareto_metric():
    spec = _spec(
        stop_conditions=[{"metric": "latency", "relation": "<=", "target_value": 5}]
    )
    candidate = _candidate(spec, "target")
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=2,
        actor_id="controller",
        now=NOW,
    )
    campaign = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=(_eligible(candidate, spec, _metrics(0.9, 5, 64)),),
        resolved_candidates=(candidate,),
        actor_id="controller",
        now=NOW,
    )
    assert campaign.status == "search_stopped"
    assert campaign.search_stop_reason == "stop_condition_met"


def test_search_stop_can_freeze_finalist_and_retain_confirmation():
    spec = _spec(max_candidates=1)
    candidate = _candidate(spec, "budget-finalist")
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=2,
        actor_id="controller",
        now=NOW,
    )
    campaign = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=(_eligible(candidate, spec, _metrics(0.9, 8, 64)),),
        resolved_candidates=(candidate,),
        actor_id="controller",
        now=NOW,
    )
    campaign = freeze_finalists(
        campaign, (candidate.candidate_id,), actor_id="reviewer", now=NOW
    )
    holdout = make_confirmation_evaluation(
        candidate,
        spec=spec,
        metrics=_metrics(0.88, 8.3, 65),
        result_ids=("holdout-budget",),
        compute_cost=1,
        admission=_admission(candidate),
    )
    campaign = record_confirmation(
        campaign, (holdout,), actor_id="experiment-worker", now=NOW
    )

    assert campaign.status == "stopped"
    assert campaign.search_stop_reason == "candidate_budget_exhausted"
    assert campaign.stop_reason == "confirmation_completed"


def test_wall_time_and_repeated_candidate_cannot_bypass_frozen_budget():
    spec = _spec(wall_time_ms=10)
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        now=NOW + timedelta(milliseconds=10),
    )
    assert campaign.status == "search_stopped"
    assert campaign.search_stop_reason == "wall_time_exhausted"
    assert campaign.reservations == ()

    spec = _spec()
    candidate = _candidate(spec, "repeat")
    campaign = reserve_generation(
        start_campaign(spec, actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        now=NOW,
    )
    campaign = settle_generation(
        campaign,
        reservation_id=campaign.reservations[0].reservation_id,
        evaluations=(_eligible(candidate, spec, _metrics(0.9, 8, 64)),),
        resolved_candidates=(candidate,),
        actor_id="controller",
        now=NOW,
    )
    campaign = reserve_generation(
        campaign,
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        now=NOW,
    )
    with pytest.raises(ValueError, match="already evaluated"):
        settle_generation(
            campaign,
            reservation_id=campaign.reservations[-1].reservation_id,
            evaluations=(_eligible(candidate, spec, _metrics(0.91, 7.9, 63)),),
            resolved_candidates=(candidate,),
            actor_id="controller",
            now=NOW,
        )


def test_reservation_and_event_receipts_reject_tampering():
    campaign = reserve_generation(
        start_campaign(_spec(), actor_id="researcher", now=NOW),
        candidate_slots=1,
        compute_reserved=1,
        actor_id="controller",
        now=NOW,
    )
    reservation = campaign.reservations[0].model_dump(mode="python")
    reservation["candidate_slots"] = 2
    with pytest.raises(ValueError, match="reservation identity"):
        type(campaign.reservations[0]).model_validate(reservation)

    event = campaign.events[0].model_dump(mode="python")
    event["occurred_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        type(campaign.events[0]).model_validate(event)
