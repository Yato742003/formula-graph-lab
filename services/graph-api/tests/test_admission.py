"""V0 admission-policy matrix; policy context is trusted server-resolved data."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.admission import AdmissionContext, decide_admission
from app.analysis_versions import canonical_json
from app.problem_spec import ProblemDefinition, ProblemSpecSnapshot, definition_hash
from app.research_compiler import CompiledCandidate, Obligation, ParentRef
from app.verification import VerificationVector

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _spec() -> ProblemSpecSnapshot:
    definition = ProblemDefinition.model_validate(
        {
            "task": "Admission test",
            "method_family": "attention",
            "metrics": [{"name": "latency", "unit": "ms", "direction": "minimize"}],
            "baselines": [{"name": "baseline", "version": "1", "sha256": HASH_A}],
            "dataset": {
                "artifact_hash": HASH_B,
                "version": "1",
                "splits": [{"role": "search", "split_hash": HASH_C}],
            },
            "model": {"name": "model", "version": "1", "sha256": HASH_A},
            "tokenizer": {"status": "not_applicable", "reason": "No tokenizer."},
            "hardware": {"target": "cpu"},
            "backend": {"name": "numpy", "version": "2"},
            "dtype": "float64",
            "evaluator": {
                "name": "evaluator",
                "protocol_version": "1",
                "implementation_hash": HASH_B,
                "config_hash": HASH_C,
            },
            "seeds": [7],
            "budget": {
                "max_candidates": 2,
                "max_generations": 1,
                "wall_time_ms": 1000,
                "compute_budget": 1,
                "compute_unit": "CPU-seconds",
            },
        }
    )
    return ProblemSpecSnapshot(
        content_hash=definition_hash(definition),
        spec_id="spec-test",
        workspace_id="ws-test",
        campaign_id="campaign-test",
        created_by="researcher",
        created_at=NOW.isoformat(),
        definition=definition,
    )


def _candidate(*, obligations=("discharged",), obligation_names=None) -> CompiledCandidate:
    names = obligation_names or tuple(
        f"obligation-{index}" for index in range(len(obligations))
    )
    refs = tuple(
        Obligation(name=name, status=status)
        for name, status in zip(names, obligations, strict=True)
    )
    payload = {
        "workspace_id": "ws-test",
        "problem_spec_id": "spec-test",
        "problem_spec_hash": definition_hash(_spec().definition),
        "operator": "mix_positive_feature_maps",
        "operator_version": "1",
        "semantics_class": "hypothesis_changing",
        "compiler_version": "research-compiler.v1",
        "ir_version": "research-ir.v1",
        "ir_json": '{"kind":"test.v1"}',
        "parents": [{"entity_id": "eq:scope:x", "version": 1, "content_hash": HASH_A}],
        "obligations": [item.model_dump(mode="json") for item in refs],
    }
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    return CompiledCandidate(
        candidate_id=f"cand_{digest[:32]}",
        content_hash=digest,
        workspace_id=payload["workspace_id"],
        problem_spec_id=payload["problem_spec_id"],
        problem_spec_hash=payload["problem_spec_hash"],
        operator=payload["operator"],
        operator_version=payload["operator_version"],
        semantics_class=payload["semantics_class"],
        ir_json=payload["ir_json"],
        parents=(ParentRef.model_validate(payload["parents"][0]),),
        obligations=refs,
    )


def _vector(**updates) -> VerificationVector:
    values = {
        "parse": "supported",
        "type": "well_typed",
        "domain": "discharged",
        "symbolic": "supported",
        "numerical": "passed_suite",
        "empirical": "supported_on_protocol",
        "human_review": "accepted_scope",
    }
    values.update(updates)
    return VerificationVector(**values)


def _context(**updates) -> AdmissionContext:
    values = {
        "candidate": _candidate(),
        "spec": _spec(),
        "verification": _vector(),
        "symbolic_result_ids": ("check-1",),
        "numerical_result_ids": ("run-1",),
        "empirical_result_ids": ("experiment-1",),
        "mapping_freshness": "current",
        "mapping_usable": True,
        "runtime_artifacts_verified": True,
        "protocol_frozen": True,
        "quota_available": True,
        "restricted_domain_reviewed": False,
        "quality_constraints_met": True,
    }
    values.update(updates)
    return AdmissionContext(**values)


def test_numerical_run_denied_without_server_verified_runtime_artifacts():
    result = decide_admission(
        _context(runtime_artifacts_verified=False),
        action="can_run_numerical",
        actor_id="reviewer-1",
        now=NOW,
    )
    assert not result.allowed
    assert "runtime_artifacts_not_verified" in result.reasons


def test_unresolved_obligation_blocks_even_well_typed_candidate():
    result = decide_admission(
        _context(candidate=_candidate(obligations=("unresolved",))),
        action="can_run_numerical",
        actor_id="reviewer-1",
        now=NOW,
    )
    assert not result.allowed
    assert result.reasons == ("unresolved_obligation:obligation-0",)


def test_checker_discharge_is_exact_and_does_not_clear_other_obligations():
    result = decide_admission(
        _context(
            candidate=_candidate(
                obligations=("unresolved", "unresolved"),
                obligation_names=("kernel_identity", "normalization_domain"),
            ),
            verification=_vector(domain="conditional"),
            discharged_obligation_names=("kernel_identity",),
        ),
        action="can_run_numerical",
        actor_id="reviewer-1",
        now=NOW,
    )

    assert not result.allowed
    assert "unresolved_obligation:kernel_identity" not in result.reasons
    assert "unresolved_obligation:normalization_domain" in result.reasons
    assert "conditional_domain_not_reviewed" in result.reasons


def test_checker_discharge_requires_a_result_reference_and_known_obligation():
    with pytest.raises(ValidationError, match="reference its checker result"):
        _context(discharged_obligation_names=("kernel_identity",), symbolic_result_ids=())
    with pytest.raises(ValidationError, match="registered checker obligations"):
        _context(discharged_obligation_names=("normalization_domain",))


def test_conditional_numerical_run_requires_review_and_stays_conditional():
    context = _context(
        candidate=_candidate(obligations=("conditional",)),
        verification=_vector(domain="conditional"),
    )
    denied = decide_admission(context, action="can_run_numerical", actor_id="reviewer-1", now=NOW)
    allowed = decide_admission(
        context.model_copy(update={"restricted_domain_reviewed": True}),
        action="can_run_numerical",
        actor_id="reviewer-1",
        now=NOW,
    )
    assert not denied.allowed
    assert allowed.allowed and allowed.outcome == "conditional"
    assert allowed.rule_id == "FGL-V0-NUMERICAL-RESTRICTED"


def test_proposed_assumptions_are_not_an_admission_input():
    with pytest.raises(ValidationError):
        _context(assumptions=["denominator != 0"])


def test_timeout_allows_bounded_retry_but_refutation_does_not():
    retry = decide_admission(
        _context(retry_reason="timeout", retry_count=1),
        action="can_run_numerical",
        actor_id="worker-1",
        now=NOW,
    )
    exhausted = decide_admission(
        _context(retry_reason="timeout", retry_count=2),
        action="can_run_numerical",
        actor_id="worker-1",
        now=NOW,
    )
    refuted = decide_admission(
        _context(retry_reason="refuted"),
        action="can_run_numerical",
        actor_id="worker-1",
        now=NOW,
    )
    assert retry.allowed
    assert not exhausted.allowed and "retry_limit_reached" in exhausted.reasons
    assert not refuted.allowed and "scientific_refutation_is_not_retryable" in refuted.reasons


def test_experiment_requires_frozen_protocol_and_passed_numerical_suite():
    denied = decide_admission(
        _context(verification=_vector(numerical="not_run")),
        action="can_run_experiment",
        actor_id="worker-1",
        now=NOW,
    )
    allowed = decide_admission(
        _context(), action="can_run_experiment", actor_id="worker-1", now=NOW
    )
    assert not denied.allowed and "numerical_suite_not_passed" in denied.reasons
    assert allowed.allowed


def test_unknown_or_failed_experiment_never_enters_parent_pool():
    unknown = decide_admission(
        _context(verification=_vector(empirical="inconclusive")),
        action="can_enter_parent_pool",
        actor_id="controller-1",
        now=NOW,
    )
    refuted = decide_admission(
        _context(verification=_vector(empirical="failed_on_protocol")),
        action="can_enter_parent_pool",
        actor_id="controller-1",
        now=NOW,
    )
    assert not unknown.allowed and not refuted.allowed


def test_only_scoped_evidence_can_publish_a_claim():
    global_claim = decide_admission(
        _context(verification=_vector(domain="conditional")),
        action="can_publish_claim",
        actor_id="researcher-1",
        claim_scope="mathematical",
        now=NOW,
    )
    restricted_claim = decide_admission(
        _context(
            candidate=_candidate(obligations=("conditional",)),
            verification=_vector(domain="conditional"),
            restricted_domain_reviewed=True,
        ),
        action="can_publish_claim",
        actor_id="researcher-1",
        claim_scope="restricted_domain",
        now=NOW,
    )
    assert not global_claim.allowed
    assert restricted_claim.allowed and restricted_claim.outcome == "conditional"
    assert restricted_claim.claim_scope == "restricted_domain"


def test_decision_receipt_pins_rule_policy_evidence_actor_and_time():
    result = decide_admission(
        _context(), action="can_run_numerical", actor_id="reviewer-1", now=NOW
    )
    assert result.decision_id.startswith("pol_")
    assert result.policy_version == "admission-policy.v1"
    assert result.rule_id == "FGL-V0-NUMERICAL-READY"
    assert result.input_result_ids == ("check-1",)
    assert result.actor_id == "reviewer-1"
    assert result.decided_at == NOW


def test_decision_rejects_cross_problem_or_tampered_candidate():
    with pytest.raises(ValueError, match="scope do not match"):
        decide_admission(
            _context(spec=_spec().model_copy(update={"workspace_id": "other"})),
            action="can_run_numerical",
            actor_id="reviewer-1",
            now=NOW,
        )
    with pytest.raises(ValueError, match="content hash is invalid"):
        tampered = _candidate().model_copy(update={"ir_json": '{"kind":"forged"}'})
        decide_admission(
            _context(candidate=tampered),
            action="can_run_numerical",
            actor_id="reviewer-1",
            now=NOW,
        )
