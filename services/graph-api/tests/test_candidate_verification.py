"""V1 bounded checks for the compiled candidate IRs."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from app import numerical_verification
from app.analysis_versions import canonical_json
from app.candidate_verification import (
    CandidateCheckResult,
    discharged_obligations,
    verify_compiled_candidate,
)
from app.numerical_verification import (
    build_numerical_suite_input,
    make_numerical_fixture_receipt,
    run_candidate_numerical_fixture,
)
from app.numerical_worker import _evaluate
from app.research_compiler import CompiledCandidate, compile_transform
from tests.test_research_compiler import MIX, _context


def _candidates():
    context = _context()
    mixture = compile_transform(MIX, context=context)
    lowering = compile_transform(
        {
            "operator": "lower_mixture_to_concatenation",
            "operator_version": "1",
            "target_node_id": mixture.candidate_id,
            "parameters": {},
            "bindings": {"mixture": mixture.candidate_id},
        },
        context=context,
        parent_candidate=mixture,
    )
    return mixture, lowering


def _rehash(
    candidate: CompiledCandidate,
    ir: dict,
    *,
    operator_version: str | None = None,
    obligations: tuple | None = None,
) -> CompiledCandidate:
    raw = candidate.model_dump(mode="json")
    raw["ir_json"] = canonical_json(ir)
    if obligations is not None:
        raw["obligations"] = [item.model_dump(mode="json") for item in obligations]
    if operator_version is not None:
        raw["operator_version"] = operator_version
    identity = {
        "workspace_id": candidate.workspace_id,
        "problem_spec_id": candidate.problem_spec_id,
        "problem_spec_hash": candidate.problem_spec_hash,
        "mapping_id": candidate.mapping_id,
        "operator": candidate.operator,
        "operator_version": raw["operator_version"],
        "semantics_class": candidate.semantics_class,
        "compiler_version": candidate.compiler_version,
        "ir_version": candidate.ir_version,
        "ir_json": raw["ir_json"],
        "parents": [item.model_dump(mode="json") for item in candidate.parents],
        "obligations": raw["obligations"],
    }
    digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    raw["content_hash"] = digest
    raw["candidate_id"] = f"cand_{digest[:32]}"
    return CompiledCandidate.model_validate(raw)


def test_hypothesis_candidate_is_structurally_checked_but_remains_unknown():
    mixture, _ = _candidates()

    result = verify_compiled_candidate(mixture)

    assert result.candidate_id == mixture.candidate_id
    assert result.scope == "candidate_structure"
    assert result.outcome == "unknown"
    assert result.vector.symbolic == "unknown"
    assert result.vector.domain == "unresolved"
    assert result.checker_version == "candidate-static.v4"
    assert result.schema_version == "candidate-check.v4"
    assert result.witness == {"rule": "positive_feature_map_mixture.v1", "symbolic_identity": False}


def test_registered_concatenation_rule_supports_only_its_scoped_identity():
    mixture, lowering = _candidates()

    result = verify_compiled_candidate(lowering, parent_candidate=mixture)

    assert result.outcome == "supported"
    assert result.scope == "feature_kernel_identity"
    assert result.vector.symbolic == "supported"
    assert result.vector.domain == "conditional"
    assert result.input_hashes == (lowering.content_hash, mixture.content_hash)
    assert "[0, 1]" in result.assumptions[1]
    assert result.check_id == verify_compiled_candidate(
        lowering, parent_candidate=mixture
    ).check_id


def test_only_registered_concatenation_proof_discharges_kernel_identity():
    mixture, lowering = _candidates()

    assert discharged_obligations(
        verify_compiled_candidate(lowering, parent_candidate=mixture)
    ) == frozenset({"kernel_identity"})
    assert discharged_obligations(verify_compiled_candidate(mixture)) == frozenset()


def test_checker_rejects_rehashed_ir_outside_registered_identity_rule():
    mixture, lowering = _candidates()
    ir = json.loads(lowering.ir_json)
    ir["feature_map"]["args"][0]["factor"] = {"op": "sqrt", "value": "one_minus_lambda"}
    forged = _rehash(lowering, ir)

    with pytest.raises(ValueError, match="registered concatenation identity"):
        verify_compiled_candidate(forged, parent_candidate=mixture)


@pytest.mark.parametrize("mutation", ["missing", "self_discharged", "extra"])
def test_checker_rejects_lowering_with_incomplete_or_promoted_obligations(mutation):
    from app.research_compiler import Obligation

    mixture, lowering = _candidates()
    obligations = list(lowering.obligations)
    if mutation == "missing":
        obligations = [item for item in obligations if item.name != "normalization_domain"]
    elif mutation == "self_discharged":
        obligations[1] = Obligation(
            name="normalization_domain",
            status="discharged",
            evidence_refs=("ai-proposed-assumption",),
        )
    else:
        obligations.append(Obligation(name="invented", status="discharged"))
    forged = _rehash(
        lowering,
        json.loads(lowering.ir_json),
        obligations=tuple(obligations),
    )

    with pytest.raises(ValueError, match="do not match the registered operator rule"):
        verify_compiled_candidate(forged, parent_candidate=mixture)


def test_checker_rejects_mixture_with_missing_domain_or_causal_obligation():
    from app.research_compiler import Obligation

    mixture, _ = _candidates()
    obligations = tuple(
        item for item in mixture.obligations
        if item.name != "nonzero_normalization_denominator"
    ) + (Obligation(name="nonzero_normalization_denominator", status="discharged"),)
    forged = _rehash(mixture, json.loads(mixture.ir_json), obligations=obligations)

    with pytest.raises(ValueError, match="do not match the registered operator rule"):
        verify_compiled_candidate(forged)


@pytest.mark.parametrize("operator", ["mixture", "lowering"])
def test_checker_does_not_reuse_a_rule_for_an_unregistered_operator_version(operator):
    mixture, lowering = _candidates()
    source = mixture if operator == "mixture" else lowering
    forged = _rehash(source, json.loads(source.ir_json), operator_version="2")

    result = verify_compiled_candidate(
        forged,
        parent_candidate=mixture if operator == "lowering" else None,
    )

    assert result.outcome == "unsupported"
    assert result.vector.symbolic == "unsupported"
    assert result.witness == {
        "rule": "unregistered_operator_version",
        "operator": source.operator,
        "version": "2",
    }


def test_persisted_check_receipt_rejects_content_or_input_hash_tampering():
    mixture, _ = _candidates()
    result = verify_compiled_candidate(mixture)
    payload = result.model_dump(mode="json")

    with pytest.raises(ValueError, match="ID does not match"):
        CandidateCheckResult.model_validate(
            payload
            | {
                "outcome": "supported",
                "vector": payload["vector"] | {"symbolic": "supported"},
            }
        )
    with pytest.raises(ValueError, match="SHA-256 hashes"):
        CandidateCheckResult.model_validate(payload | {"input_hashes": ["not-a-hash"]})
    with pytest.raises(ValueError, match="ID does not match"):
        CandidateCheckResult.model_validate(
            payload
            | {
                "vector": payload["vector"]
                | {"type": "well_typed", "domain": "discharged"}
            }
        )
    with pytest.raises(ValueError, match="must match its verification vector"):
        CandidateCheckResult.model_validate(
            payload
            | {"vector": payload["vector"] | {"symbolic": "supported"}}
        )
    with pytest.raises(ValueError, match="cannot assert numerical"):
        CandidateCheckResult.model_validate(
            payload
            | {
                "vector": payload["vector"]
                | {"numerical": "passed_suite", "human_review": "accepted_scope"}
            }
        )


def test_v1_candidate_check_receipts_remain_readable_after_v2_hash_upgrade():
    mixture, _ = _candidates()
    current = verify_compiled_candidate(mixture)
    payload = current.model_dump(mode="json")
    legacy_identity = {
        "version": "candidate-static.v1",
        "candidate_hash": current.candidate_hash,
        "claim": current.claim,
        "scope": current.scope,
        "assumptions": current.assumptions,
        "input_hashes": current.input_hashes,
        "outcome": current.outcome,
        "witness": current.witness,
    }
    legacy_digest = hashlib.sha256(canonical_json(legacy_identity).encode()).hexdigest()
    legacy = CandidateCheckResult.model_validate(
        payload
        | {
            "check_id": f"chk_{legacy_digest[:32]}",
            "checker_version": "candidate-static.v1",
            "schema_version": "candidate-check.v1",
        }
    )

    assert legacy.check_id == f"chk_{legacy_digest[:32]}"
    assert legacy.vector == current.vector


def test_v2_candidate_check_receipts_remain_readable_after_v4_upgrade():
    mixture, _ = _candidates()
    current = verify_compiled_candidate(mixture)
    payload = current.model_dump(mode="json")
    identity = {
        "version": "candidate-static.v2",
        "candidate_hash": current.candidate_hash,
        "claim": current.claim,
        "scope": current.scope,
        "assumptions": current.assumptions,
        "input_hashes": current.input_hashes,
        "outcome": current.outcome,
        "witness": current.witness,
        "schema_version": "candidate-check.v2",
        "vector": current.vector.model_dump(mode="json"),
    }
    digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    old = CandidateCheckResult.model_validate(
        payload
        | {
            "check_id": f"chk_{digest[:32]}",
            "checker_version": "candidate-static.v2",
            "schema_version": "candidate-check.v2",
        }
    )

    assert old.check_id == f"chk_{digest[:32]}"
    assert old.vector == current.vector
    assert discharged_obligations(old) == frozenset()


def test_v3_candidate_check_receipts_remain_readable_after_v4_upgrade():
    mixture, _ = _candidates()
    current = verify_compiled_candidate(mixture)
    payload = current.model_dump(mode="json")
    identity = {
        "version": "candidate-static.v3",
        "candidate_hash": current.candidate_hash,
        "claim": current.claim,
        "scope": current.scope,
        "assumptions": current.assumptions,
        "input_hashes": current.input_hashes,
        "outcome": current.outcome,
        "witness": current.witness,
        "schema_version": "candidate-check.v3",
        "vector": current.vector.model_dump(mode="json"),
    }
    digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    old = CandidateCheckResult.model_validate(
        payload
        | {
            "check_id": f"chk_{digest[:32]}",
            "checker_version": "candidate-static.v3",
            "schema_version": "candidate-check.v3",
        }
    )

    assert old.check_id == f"chk_{digest[:32]}"
    assert old.vector == current.vector
    assert discharged_obligations(old) == frozenset()


def test_lowering_requires_exact_parent_and_candidate_integrity():
    mixture, lowering = _candidates()
    wrong_parent = mixture.model_copy(update={"content_hash": "0" * 64})

    with pytest.raises(ValueError, match="content hash is invalid"):
        verify_compiled_candidate(lowering, parent_candidate=wrong_parent)


def test_numerical_fixture_inputs_are_bound_to_candidate_and_frozen_spec():
    mixture, lowering = _candidates()
    spec = _context().spec

    payload = build_numerical_suite_input(lowering, spec, seed=1, parent_candidate=mixture)

    assert payload == {
        "suite_version": "feature-kernel-fixture.v1",
        "candidate_hash": lowering.content_hash,
        "seed": 1,
        "dtype": "float64",
        "lambda": 0.25,
        "left_rank": 16,
        "right_rank": 24,
    }


def test_numerical_fixture_receipt_binds_input_hash_and_exact_parent_refs(monkeypatch):
    mixture, lowering = _candidates()
    spec = _context().spec

    def run_fixed_suite(payload, **_kwargs):
        return _evaluate(payload)

    monkeypatch.setattr(numerical_verification, "run_numerical_sandbox", run_fixed_suite)
    monkeypatch.setattr(
        numerical_verification, "configured_image", lambda: "sha256:" + "a" * 64
    )
    receipt = run_candidate_numerical_fixture(
        lowering,
        spec,
        seed=1,
        parent_candidate=mixture,
    )

    assert receipt["outcome"] in {"passed_suite", "counterexample"}
    assert receipt["input_hash"] == _evaluate(
        build_numerical_suite_input(lowering, spec, seed=1, parent_candidate=mixture)
    )["input_hash"]
    assert receipt["parent_refs"] == [
        parent.model_dump(mode="json") for parent in lowering.parents
    ]
    assert receipt["execution_image"] == "sha256:" + "a" * 64
    assert receipt["fixture_scope"] == "synthetic_feature_kernel_fixture"
    assert receipt["performance_claim"] is False


def test_numerical_fixture_rejects_worker_result_for_another_input(monkeypatch):
    mixture, _ = _candidates()
    spec = _context().spec

    def wrong_input_result(payload, **_kwargs):
        return _evaluate(payload) | {"input_hash": "0" * 64}

    monkeypatch.setattr(numerical_verification, "run_numerical_sandbox", wrong_input_result)
    monkeypatch.setattr(
        numerical_verification, "configured_image", lambda: "sha256:" + "a" * 64
    )
    receipt = run_candidate_numerical_fixture(mixture, spec, seed=1)

    assert receipt["outcome"] == "error"
    assert receipt["error_code"] == "NUMERICAL_RESULT_SCOPE_MISMATCH"


def test_numerical_fixture_receipt_is_immutable_and_cannot_claim_performance(monkeypatch):
    mixture, lowering = _candidates()
    spec = _context().spec
    monkeypatch.setattr(
        numerical_verification,
        "run_numerical_sandbox",
        lambda payload, **_kwargs: _evaluate(payload),
    )
    monkeypatch.setattr(
        numerical_verification, "configured_image", lambda: "sha256:" + "a" * 64
    )
    run = run_candidate_numerical_fixture(
        lowering, spec, seed=1, parent_candidate=mixture
    )

    receipt = make_numerical_fixture_receipt(
        run,
        workspace_id=spec.workspace_id,
        actor_id="verifier-1",
        run_id=str(uuid4()),
    )

    assert receipt.parent_refs == lowering.parents
    assert receipt.problem_spec_id == spec.spec_id
    assert receipt.execution_image == "sha256:" + "a" * 64
    assert receipt.performance_claim is False
    with pytest.raises(ValueError):
        type(receipt).model_validate(
            receipt.model_dump(mode="json") | {"performance_claim": True}
        )


def test_legacy_v1_fixture_receipt_identity_remains_readable(monkeypatch):
    mixture, _ = _candidates()
    spec = _context().spec
    monkeypatch.setattr(
        numerical_verification,
        "run_numerical_sandbox",
        lambda payload, **_kwargs: _evaluate(payload),
    )
    monkeypatch.setattr(
        numerical_verification, "configured_image", lambda: "sha256:" + "a" * 64
    )
    result = run_candidate_numerical_fixture(mixture, spec, seed=1)
    receipt = make_numerical_fixture_receipt(
        result,
        workspace_id=spec.workspace_id,
        actor_id="verifier-1",
        run_id=str(uuid4()),
    )
    payload = receipt.model_dump(mode="json")
    payload.pop("execution_image")
    identity = {
        key: value for key, value in payload.items()
        if key not in {"result_id", "result_hash"}
    }
    digest = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    legacy = type(receipt).model_validate(
        payload | {"result_id": f"num_{digest[:32]}", "result_hash": digest}
    )

    assert legacy.execution_image is None
    assert legacy.result_id == f"num_{digest[:32]}"


@pytest.mark.parametrize("invalid", [
    "workspace", "spec_hash", "seed", "missing_parent", "wrong_parent",
])
def test_numerical_fixture_rejects_scope_or_lineage_mismatch(invalid):
    mixture, lowering = _candidates()
    spec = _context().spec
    candidate = lowering if invalid in {"missing_parent", "wrong_parent"} else mixture
    parent = None
    if invalid == "workspace":
        candidate = candidate.model_copy(update={"workspace_id": "other-workspace"})
    elif invalid == "spec_hash":
        candidate = candidate.model_copy(update={"problem_spec_hash": "0" * 64})
    elif invalid == "seed":
        with pytest.raises(ValueError, match="seed"):
            build_numerical_suite_input(mixture, spec, seed=999)
        return
    elif invalid == "wrong_parent":
        parent = mixture.model_copy(update={"content_hash": "0" * 64})
    elif invalid == "missing_parent":
        parent = None

    with pytest.raises(ValueError):
        build_numerical_suite_input(candidate, spec, seed=1, parent_candidate=parent)


def test_numerical_fixture_rejects_rank_above_fixed_suite_limit():
    mixture, _ = _candidates()
    ir = json.loads(mixture.ir_json)
    ir["left"]["feature_rank"] = 257
    ir["output_rank"] = 257 + ir["right"]["feature_rank"]
    oversized = _rehash(mixture, ir)

    with pytest.raises(ValueError, match="rank"):
        build_numerical_suite_input(oversized, _context().spec, seed=1)
