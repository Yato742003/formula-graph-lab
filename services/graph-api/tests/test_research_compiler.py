"""D2 golden replay and trust-boundary checks for the first compiler slice."""

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.admission import (
    AdmissionContext,
    AdmissionReplayInput,
    decide_admission,
    make_admission_replay_input,
)
from app.analysis_versions import canonical_json
from app.candidate_verification import CandidateCheckResult, verify_compiled_candidate
from app.compatibility import (
    CompatibilityReview,
    PortDescriptor,
    PortMapping,
    ResolvedDependency,
    assess_mapping,
    compute_dependency_fingerprint,
)
from app.numerical_verification import (
    NumericalFixtureReceipt,
    build_numerical_suite_input,
    make_numerical_fixture_receipt,
    numerical_fixture_input_hash,
)
from app.numerical_worker import SUITE_VERSION, TOLERANCES, _evaluate
from app.problem_spec import (
    ProblemDefinition,
    ProblemSpecSnapshot,
    definition_hash,
)
from app.replay_bundle import (
    ReplaySourceReference,
    make_compiler_replay_bundle,
    replay_candidate_from_bundle,
)
from app.research_compiler import (
    CompileContext,
    ParentRef,
    ResolvedFeatureMap,
    TransformationActivity,
    compile_transform,
    replay_compiled_candidate,
)
from app.research_report import build_compiler_replay_report
from app.symbol_contracts import ContractReview, ReviewedContractValue

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
MIX = {
    "operator": "mix_positive_feature_maps",
    "operator_version": "1",
    "target_node_id": "attention-kernel",
    "parameters": {"lambda": 0.25},
    "bindings": {"left": "symbol-left", "right": "symbol-right"},
}


def _spec() -> ProblemSpecSnapshot:
    definition = ProblemDefinition.model_validate(
        {
            "task": "Test feature-map mixture",
            "method_family": "linear-attention",
            "metrics": [{"name": "latency", "unit": "ms", "direction": "minimize"}],
            "baselines": [{"name": "baseline", "version": "1", "sha256": HASH_A}],
            "dataset": {
                "artifact_hash": HASH_B,
                "version": "1",
                "splits": [{"role": "search", "split_hash": HASH_C}],
            },
            "model": {"name": "model", "version": "1", "sha256": HASH_A},
            "tokenizer": {"status": "not_applicable", "reason": "No tokenizer in unit test."},
            "hardware": {"target": "cpu"},
            "backend": {"name": "numpy", "version": "2"},
            "dtype": "float64",
            "evaluator": {
                "name": "test-evaluator",
                "protocol_version": "1",
                "implementation_hash": HASH_B,
                "config_hash": HASH_C,
            },
            "seeds": [1],
            "budget": {
                "max_candidates": 2,
                "max_generations": 1,
                "wall_time_ms": 1000,
                "compute_budget": 1,
                "compute_unit": "CPU-seconds",
            },
            "allowed_transforms": [
                {"name": "mix_positive_feature_maps", "version": "1"},
                {"name": "lower_mixture_to_concatenation", "version": "1"},
            ],
        }
    )
    return ProblemSpecSnapshot(
        content_hash=definition_hash(definition),
        spec_id="spec-test",
        workspace_id="ws-test",
        campaign_id="campaign-test",
        created_by="reviewer",
        created_at="2026-09-25T00:00:00Z",
        definition=definition,
    )


def _context(
    *,
    assessment_status="compatible",
    workspace_id="ws-test",
    target_node_id="attention-kernel",
    left_hash=HASH_A,
    right_hash=HASH_B,
    left_dependency_hash=None,
    right_dependency_hash=None,
) -> CompileContext:
    left_dependency_hash = left_dependency_hash or left_hash
    right_dependency_hash = right_dependency_hash or right_hash
    left = PortDescriptor(
        equation_id="eq-left",
        version=1,
        scoped_symbol_id="symbol-left",
        symbol_name="phi_a",
        domain="strictly_positive_real",
        domain_is_reviewed=True,
        shape=(64,),
        normalization="none",
        mask="causal",
        causal=True,
        resource_class="not_applicable",
    )
    right = PortDescriptor(
        equation_id="eq-right",
        version=1,
        scoped_symbol_id="symbol-right",
        symbol_name="phi_b",
        domain="strictly_positive_real",
        domain_is_reviewed=True,
        shape=(64,),
        normalization="none",
        mask="causal",
        causal=True,
        resource_class="not_applicable",
    )
    dependencies = (
        ResolvedDependency(
            entity_id="eq-left:symbol-left", version=1, content_hash=left_dependency_hash
        ),
        ResolvedDependency(
            entity_id="eq-right:symbol-right", version=1, content_hash=right_dependency_hash
        ),
    )
    mapping = PortMapping(
        mapping_id="mapping-test",
        workspace_id=workspace_id,
        producer_port=left,
        consumer_port=right,
        explicit_binding_reviewed=True,
    )
    assessment = assess_mapping(mapping, dependencies)
    if assessment_status != "compatible":
        assessment = assessment.model_copy(
            update={
                "status": assessment_status,
                "usable": False,
            }
        )
    return CompileContext(
        workspace_id=workspace_id,
        target_node_id=target_node_id,
        target_parent=ParentRef(entity_id=target_node_id, version=1, content_hash=HASH_C),
        target_dependency=ResolvedDependency(
            entity_id=target_node_id, version=1, content_hash=HASH_C
        ),
        spec=_spec(),
        left=ResolvedFeatureMap(
            workspace_id=workspace_id,
            port=left,
            source_hash=left_hash,
            contract_hash=HASH_C,
            dependency_hash=left_dependency_hash,
            feature_rank=16,
        ),
        right=ResolvedFeatureMap(
            workspace_id=workspace_id,
            port=right,
            source_hash=right_hash,
            contract_hash=HASH_C,
            dependency_hash=right_dependency_hash,
            feature_rank=24,
        ),
        mapping=mapping,
        assessment=assessment,
        dependencies=dependencies,
        current_dependency_fingerprint=compute_dependency_fingerprint(dependencies),
    )


def _activity(candidate, context, request) -> TransformationActivity:
    context_json = canonical_json(context.model_dump(mode="json"))
    return TransformationActivity(
        activity_id="act_" + "1" * 32,
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.content_hash,
        workspace_id=candidate.workspace_id,
        actor_id="researcher",
        operator=candidate.operator,
        operator_version=candidate.operator_version,
        mapping_id=candidate.mapping_id,
        semantics_class=candidate.semantics_class,
        request_json=canonical_json(request),
        parents=candidate.parents,
        created_at="2026-09-27T00:00:00Z",
        schema_version="transformation-activity.v2",
        compiler_context_json=context_json,
        compiler_context_hash=hashlib.sha256(context_json.encode("utf-8")).hexdigest(),
    )


def test_mix_compilation_is_replayable_scoped_and_keeps_unresolved_obligations():
    context = _context()
    first = compile_transform(MIX, context=context)
    replay = compile_transform(deepcopy(MIX), context=context)

    assert first == replay
    assert first.semantics_class == "hypothesis_changing"
    assert first.mapping_id == context.mapping.mapping_id
    assert first.candidate_id == f"cand_{first.content_hash[:32]}"
    assert first.parents[0].entity_id == "attention-kernel"
    ir = json.loads(first.ir_json)
    assert ir["output_rank"] == 40
    assert ir["kernel"]["op"] == "add"
    assert {item.name: item.status for item in first.obligations}[
        "nonzero_normalization_denominator"
    ] == "unresolved"
    assert {item.name: item.status for item in first.obligations}[
        "causal_mask_preserved"
    ] == "unresolved"
    assert canonical_json(ir) == first.ir_json


def test_concatenation_lowering_keeps_parent_and_is_separate_activity():
    context = _context()
    parent = compile_transform(MIX, context=context)
    parent_before = parent.model_dump(mode="json")
    lowering = {
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": parent.candidate_id,
        "parameters": {},
        "bindings": {"mixture": parent.candidate_id},
    }

    child = compile_transform(lowering, context=context, parent_candidate=parent)

    assert child.semantics_class == "preserving"
    assert child.mapping_id == parent.mapping_id
    assert child.parents[0].entity_id == parent.candidate_id
    assert child.parents[0].content_hash == parent.content_hash
    assert child.operator != parent.operator
    assert child.model_dump(mode="json") != parent_before
    assert parent.model_dump(mode="json") == parent_before
    assert all(item.status == "unresolved" for item in child.obligations)


def test_immutable_activity_snapshot_replays_both_registered_moves_and_rejects_tampering():
    context = _context()
    mixture = compile_transform(MIX, context=context)
    mixture_activity = _activity(mixture, context, MIX)
    assert replay_compiled_candidate(mixture, mixture_activity) == mixture

    lowering_request = {
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": mixture.candidate_id,
        "parameters": {},
        "bindings": {"mixture": mixture.candidate_id},
    }
    lowered = compile_transform(lowering_request, context=context, parent_candidate=mixture)
    lowering_activity = _activity(lowered, context, lowering_request)
    assert replay_compiled_candidate(
        lowered,
        lowering_activity,
        parent_candidate=mixture,
    ) == lowered

    changed_context = context.model_copy(
        update={"left": context.left.model_copy(update={"feature_rank": 17})}
    )
    changed_activity = _activity(mixture, changed_context, MIX)
    with pytest.raises(ValueError, match="differs from its persisted activity"):
        replay_compiled_candidate(mixture, changed_activity)

    tampered = mixture_activity.model_dump(mode="python")
    tampered["compiler_context_json"] = tampered["compiler_context_json"].replace(
        '"feature_rank":16', '"feature_rank":17', 1
    )
    with pytest.raises(ValueError, match="compiler context hash is invalid"):
        TransformationActivity.model_validate(tampered)

    legacy = mixture_activity.model_dump(mode="python")
    for key in ("candidate_hash", "compiler_context_json", "compiler_context_hash"):
        legacy.pop(key)
    legacy["schema_version"] = "transformation-activity.v1"
    assert TransformationActivity.model_validate(legacy).schema_version == (
        "transformation-activity.v1"
    )


def test_source_authorized_compiler_bundle_replays_and_binds_provenance(tmp_path):
    contract_values = (
        ReviewedContractValue(
            name="phi_a", category="vector", shape=(64,), feature_rank=16,
            domain="positive", normalization="none", mask="causal",
            causal=True, resource_class="not_applicable",
        ),
        ReviewedContractValue(
            name="phi_b", category="vector", shape=(64,), feature_rank=24,
            domain="positive", normalization="none", mask="causal",
            causal=True, resource_class="not_applicable",
        ),
    )
    contract_hashes = tuple(
        hashlib.sha256(canonical_json(value.model_dump(mode="json")).encode()).hexdigest()
        for value in contract_values
    )
    base_context = _context(target_node_id="eq-left")
    context = base_context.model_copy(
        update={
            "left": base_context.left.model_copy(update={"contract_hash": contract_hashes[0]}),
            "right": base_context.right.model_copy(update={"contract_hash": contract_hashes[1]}),
        }
    )
    request = MIX | {"target_node_id": "eq-left"}
    candidate = compile_transform(request, context=context)
    activity = _activity(candidate, context, request)
    now = datetime.now(UTC)
    contract_reviews = tuple(
        ContractReview(
            review_id=f"contract-{index}",
            symbol_name=value.name,
            reviewer_id="reviewer",
            reviewer_role="reviewer",
            decision="accepted",
            scope=equation_id,
            reviewed_contract=value,
            reviewed_at=now,
        )
        for index, (equation_id, value) in enumerate(
            (("eq-left", contract_values[0]), ("eq-right", contract_values[1]))
        )
    )
    sources = tuple(
        ReplaySourceReference(
            equation_id=equation_id,
            equation_source_hash=source_hash,
            paper_id="2006.16236",
            paper_version=3,
            paper_version_id=f"paper-version-{equation_id}",
            paper_html_hash=HASH_A,
            source_url="https://arxiv.org/html/2006.16236v3",
            anchor=f"S1.E{index}",
            anchor_is_source=True,
        )
        for index, (equation_id, source_hash) in enumerate(
            (("eq-left", HASH_A), ("eq-right", HASH_B)), start=1
        )
    )
    candidate_check = verify_compiled_candidate(candidate)
    admission_context = AdmissionContext(
        candidate=candidate,
        spec=context.spec,
        verification=candidate_check.vector,
        symbolic_result_ids=(candidate_check.check_id,),
        mapping_freshness="current",
        mapping_usable=True,
    )
    admission = decide_admission(
        admission_context,
        action="can_run_experiment",
        actor_id="researcher",
        now=now,
    )
    admission_replay_input = make_admission_replay_input(admission_context, admission)
    bundle = make_compiler_replay_bundle(
        workspace_id="ws-test",
        candidate=candidate,
        activity=activity,
        parent_candidates=(),
        sources=sources,
        lineage_assertions=(),
        compatibility_reviews=(
            CompatibilityReview(
                review_id="mapping-review",
                mapping_id=context.mapping.mapping_id,
                reviewer_id="reviewer",
                reviewer_role="reviewer",
                decision="reviewed",
                notes="Reviewed the typed mapping.",
                reviewed_at=now.isoformat(),
                dependency_fingerprint=context.current_dependency_fingerprint,
            ),
        ),
        contract_reviews=contract_reviews,
        proposal=None,
        proposal_review=None,
        candidate_checks=(candidate_check,),
        admission_decisions=(admission,),
        admission_replay_inputs=(admission_replay_input,),
        numerical_fixtures=(),
    )

    assert replay_candidate_from_bundle(bundle) == candidate
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")
    replay = subprocess.run(
        [sys.executable, "-m", "app.replay_bundle_cli", str(bundle_path)],
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )
    assert replay.returncode == 0, replay.stderr
    replay_report = json.loads(replay.stdout)
    assert replay_report["scope"] == "compiler_replay_only"
    assert replay_report["compiler_replay"] == "reproduced"
    assert replay_report["candidate_hash"] == candidate.content_hash
    assert replay_report["empirical_experiment"] == "not_run"

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as output:
        output.truncate(2 * 1024 * 1024 + 1)
    rejected = subprocess.run(
        [sys.executable, "-m", "app.replay_bundle_cli", str(oversized)],
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout) == {"status": "error", "error_type": "ValueError"}

    report = build_compiler_replay_report(bundle)
    assert report.status == "partial"
    assert report.scope == "compiler_replay_only"
    assert report.compiler_replay == "reproduced"
    assert report.checks[0].outcome == "unknown"
    assert report.checks[0].replay_status == "replayed"
    assert report.checks[0].domain_status == "unresolved"
    assert any("hashes are not signatures" in item for item in report.limitations)
    assert report.policy_decisions[0].outcome == "denied"
    assert report.policy_decisions[0].replay_status == "replayed"
    assert report.empirical_experiment == "not_run"
    assert report.numerical_fixtures == ()
    assert len(report.report_hash) == 64
    with pytest.raises(ValueError, match="Report hash"):
        type(report).model_validate(
            report.model_dump(mode="json") | {"report_hash": HASH_B}
        )

    forged_policy_payload = admission_replay_input.model_dump(
        mode="json", exclude={"input_hash"}
    )
    forged_policy_payload["quota_available"] = True
    forged_policy_hash = hashlib.sha256(
        canonical_json(forged_policy_payload).encode("utf-8")
    ).hexdigest()
    forged_policy_input = AdmissionReplayInput.model_validate(
        forged_policy_payload | {"input_hash": forged_policy_hash}
    )
    forged_policy_bundle_values = bundle.__dict__.copy()
    forged_policy_bundle_values.pop("bundle_hash")
    forged_policy_bundle_values["admission_replay_inputs"] = (forged_policy_input,)
    with pytest.raises(ValueError, match="Admission decision does not reproduce"):
        make_compiler_replay_bundle(**forged_policy_bundle_values)
    tampered = bundle.model_copy(update={"bundle_hash": HASH_B})
    with pytest.raises(ValueError, match="bundle hash"):
        replay_candidate_from_bundle(tampered)

    altered_source = sources[0].model_copy(update={"equation_source_hash": HASH_C})
    tampered_payload = bundle.model_dump(mode="python")
    tampered_payload.pop("bundle_hash")
    tampered_payload["sources"] = (altered_source, sources[1])
    with pytest.raises(ValueError, match="source hash"):
        make_compiler_replay_bundle(**tampered_payload)

    # An attacker can make a forged receipt internally valid and rehash its
    # enclosing bundle. Replay must still compare it with the registered checker.
    original_check = bundle.candidate_checks[0]
    forged_identity = original_check.model_dump(mode="python")
    forged_identity["input_hashes"] = (HASH_B,)
    forged_identity_payload = {
        "version": forged_identity["checker_version"],
        "candidate_hash": forged_identity["candidate_hash"],
        "claim": forged_identity["claim"],
        "scope": forged_identity["scope"],
        "assumptions": forged_identity["assumptions"],
        "input_hashes": forged_identity["input_hashes"],
        "outcome": forged_identity["outcome"],
        "witness": forged_identity["witness"],
        "schema_version": forged_identity["schema_version"],
        "vector": original_check.vector.model_dump(mode="json"),
    }
    forged_identity["check_id"] = "chk_" + hashlib.sha256(
        canonical_json(forged_identity_payload).encode("utf-8")
    ).hexdigest()[:32]
    forged_check = CandidateCheckResult.model_validate(forged_identity)
    forged_bundle_values = bundle.__dict__.copy()
    forged_bundle_values.pop("bundle_hash")
    forged_bundle_values["candidate_checks"] = (forged_check,)
    forged_bundle = make_compiler_replay_bundle(**forged_bundle_values)
    with pytest.raises(ValueError, match="does not reproduce"):
        replay_candidate_from_bundle(forged_bundle)

    fixture_input = build_numerical_suite_input(candidate, context.spec, seed=1)
    fixture_result = {
        **_evaluate(fixture_input),
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.content_hash,
        "parent_refs": [item.model_dump(mode="json") for item in candidate.parents],
        "problem_spec_id": context.spec.spec_id,
        "problem_spec_hash": context.spec.content_hash,
        "suite_version": SUITE_VERSION,
        "input_hash": numerical_fixture_input_hash(fixture_input),
        "execution_image": "sha256:" + "a" * 64,
        "seed": 1,
        "dtype": context.spec.definition.dtype,
        "tolerance": TOLERANCES[context.spec.definition.dtype],
        "fixture_scope": "synthetic_feature_kernel_fixture",
        "performance_claim": False,
    }
    fixture = make_numerical_fixture_receipt(
        fixture_result,
        workspace_id="ws-test",
        actor_id="verifier",
        run_id=str(uuid4()),
    )
    fixture_bundle_values = bundle.__dict__.copy()
    fixture_bundle_values.pop("bundle_hash")
    fixture_bundle_values["numerical_fixtures"] = (fixture,)
    fixture_bundle = make_compiler_replay_bundle(**fixture_bundle_values)
    assert replay_candidate_from_bundle(fixture_bundle) == candidate
    fixture_report = build_compiler_replay_report(fixture_bundle)
    assert fixture_report.numerical_fixtures[0].replay_status == "replayed"

    if os.getenv("TEST_CLEAN_REPLAY") == "1":
        bundle_path.write_text(fixture_bundle.model_dump_json(), encoding="utf-8")
        clean_replay = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(Path(__file__).resolve().parents[3] / "replay-bundle.ps1"),
                "-Bundle",
                str(bundle_path),
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
        assert clean_replay.returncode == 0, clean_replay.stderr
        clean_report = json.loads(clean_replay.stdout.splitlines()[-1])
        assert clean_report["report_hash"] == fixture_report.report_hash
        assert clean_report["candidate_hash"] == candidate.content_hash
        assert clean_report["numerical_fixtures"][0]["replay_status"] == "replayed"

    forged_outcome_identity = fixture.model_dump(
        mode="json", exclude={"result_id", "result_hash"}
    )
    forged_outcome_identity["measurements"]["kernel_max_abs_error"] += 1.0
    forged_outcome_hash = hashlib.sha256(
        canonical_json(forged_outcome_identity).encode("utf-8")
    ).hexdigest()
    forged_outcome = NumericalFixtureReceipt.model_validate(
        forged_outcome_identity | {
            "result_hash": forged_outcome_hash,
            "result_id": f"num_{forged_outcome_hash[:32]}",
        }
    )
    forged_outcome_bundle_values = fixture_bundle.__dict__.copy()
    forged_outcome_bundle_values.pop("bundle_hash")
    forged_outcome_bundle_values["numerical_fixtures"] = (forged_outcome,)
    forged_outcome_bundle = make_compiler_replay_bundle(**forged_outcome_bundle_values)
    with pytest.raises(ValueError, match="numerical fixture outcome does not reproduce"):
        replay_candidate_from_bundle(forged_outcome_bundle)

    forged_fixture_identity = fixture.model_dump(
        mode="json", exclude={"result_id", "result_hash"}
    )
    forged_fixture_identity["input_hash"] = HASH_C
    forged_fixture_hash = hashlib.sha256(
        canonical_json(forged_fixture_identity).encode("utf-8")
    ).hexdigest()
    forged_fixture = NumericalFixtureReceipt.model_validate(
        forged_fixture_identity | {
            "result_hash": forged_fixture_hash,
            "result_id": f"num_{forged_fixture_hash[:32]}",
        }
    )
    forged_fixture_bundle_values = fixture_bundle.__dict__.copy()
    forged_fixture_bundle_values.pop("bundle_hash")
    forged_fixture_bundle_values["numerical_fixtures"] = (forged_fixture,)
    with pytest.raises(ValueError, match="fixture input does not match"):
        make_compiler_replay_bundle(**forged_fixture_bundle_values)

    # Older checker versions stay useful as historical evidence, but are not
    # mislabeled as recomputed by today's checker.
    historical_identity = original_check.model_dump(mode="python")
    historical_identity["checker_version"] = "candidate-static.v3"
    historical_identity["schema_version"] = "candidate-check.v3"
    historical_identity_payload = {
        "version": historical_identity["checker_version"],
        "candidate_hash": historical_identity["candidate_hash"],
        "claim": historical_identity["claim"],
        "scope": historical_identity["scope"],
        "assumptions": historical_identity["assumptions"],
        "input_hashes": historical_identity["input_hashes"],
        "outcome": historical_identity["outcome"],
        "witness": historical_identity["witness"],
        "schema_version": historical_identity["schema_version"],
        "vector": original_check.vector.model_dump(mode="json"),
    }
    historical_identity["check_id"] = "chk_" + hashlib.sha256(
        canonical_json(historical_identity_payload).encode("utf-8")
    ).hexdigest()[:32]
    historical_check = CandidateCheckResult.model_validate(historical_identity)
    historical_bundle_values = bundle.__dict__.copy()
    historical_bundle_values.pop("bundle_hash")
    historical_bundle_values["candidate_checks"] = (historical_check,)
    historical_bundle = make_compiler_replay_bundle(**historical_bundle_values)
    historical_report = build_compiler_replay_report(historical_bundle)
    assert historical_report.checks[0].replay_status == "historical"


def test_compiler_rejects_unusable_or_misbound_sources_and_forged_parent():
    with pytest.raises(ValueError, match="not currently verified compatible"):
        compile_transform(MIX, context=_context(assessment_status="unknown"))
    with pytest.raises(ValueError, match="Left binding"):
        compile_transform(
            {**MIX, "bindings": {"left": "attacker", "right": "symbol-right"}}, context=_context()
        )
    with pytest.raises(ValueError, match="Target node"):
        compile_transform({**MIX, "target_node_id": "unresolved-target"}, context=_context())

    context = _context()
    parent = compile_transform(MIX, context=context)
    forged = parent.model_copy(update={"ir_json": parent.ir_json + " "})
    lowering = {
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": parent.candidate_id,
        "parameters": {},
        "bindings": {"mixture": parent.candidate_id},
    }
    with pytest.raises(ValueError, match="content hash is invalid"):
        compile_transform(lowering, context=context, parent_candidate=forged)


def test_compiler_context_rejects_cross_workspace_and_stale_assessment():
    context = _context()
    with pytest.raises(ValueError, match="another workspace"):
        CompileContext.model_validate(
            context.model_dump(mode="json") | {"workspace_id": "ws-other"}
        )
    stale = context.assessment.model_copy(update={"freshness": "stale", "usable": False})
    with pytest.raises(ValueError, match="Stale compatibility"):
        CompileContext.model_validate(
            context.model_dump(mode="json") | {"assessment": stale.model_dump(mode="json")}
        )


def test_lowering_rejects_parent_when_source_dependency_has_changed():
    parent = compile_transform(MIX, context=_context())
    changed_context = _context(right_dependency_hash="d" * 64)
    lowering = {
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": parent.candidate_id,
        "parameters": {},
        "bindings": {"mixture": parent.candidate_id},
    }

    with pytest.raises(ValueError, match="source snapshots are stale"):
        compile_transform(lowering, context=changed_context, parent_candidate=parent)


def test_lowering_rejects_parent_from_a_different_mapping():
    context = _context()
    parent = compile_transform(MIX, context=context)
    changed_mapping = context.mapping.model_copy(update={"mapping_id": "mapping-other"})
    changed_context = context.model_copy(update={"mapping": changed_mapping})
    lowering = {
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": parent.candidate_id,
        "parameters": {},
        "bindings": {"mixture": parent.candidate_id},
    }
    with pytest.raises(ValueError, match="different compatibility mapping"):
        compile_transform(lowering, context=changed_context, parent_candidate=parent)
