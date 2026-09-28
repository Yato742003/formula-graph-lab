"""Bounded V1 checker for the first compiled research-move IRs."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput
from app.research_compiler import (
    CompiledCandidate,
    Obligation,
    ParentRef,
    _verify_candidate_identity,
)
from app.verification import VerificationVector

CANDIDATE_CHECKER_VERSION = "candidate-static.v4"
_HASH = r"^[0-9a-f]{64}$"


class CandidateCheckResult(FrozenInput):
    check_id: str = Field(pattern=r"^chk_[0-9a-f]{32}$")
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    candidate_hash: str = Field(pattern=_HASH)
    checker: Literal["static_symbolic"] = "static_symbolic"
    checker_version: Literal[
        "candidate-static.v1", "candidate-static.v2", "candidate-static.v3",
        "candidate-static.v4",
    ] = (
        CANDIDATE_CHECKER_VERSION
    )
    claim: str = Field(min_length=1, max_length=500)
    scope: Literal["candidate_structure", "feature_kernel_identity"]
    assumptions: tuple[str, ...] = Field(max_length=16)
    input_hashes: tuple[str, ...] = Field(min_length=1, max_length=4)
    outcome: Literal["supported", "refuted", "unknown", "unsupported", "timeout", "error"]
    vector: VerificationVector
    witness: dict[str, object] | None = None
    created_at: datetime
    schema_version: Literal[
        "candidate-check.v1", "candidate-check.v2", "candidate-check.v3",
        "candidate-check.v4",
    ] = "candidate-check.v4"

    @field_validator("input_hashes")
    @classmethod
    def validate_input_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        invalid = any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in value
        )
        if invalid:
            raise ValueError("Candidate check inputs must be lowercase SHA-256 hashes.")
        return value

    @model_validator(mode="after")
    def validate_check_identity(self) -> CandidateCheckResult:
        if self.vector.symbolic != self.outcome:
            raise ValueError("Candidate check outcome must match its verification vector.")
        if (
            self.vector.numerical != "not_run"
            or self.vector.empirical != "not_run"
            or self.vector.human_review != "pending"
        ):
            raise ValueError(
                "Static/symbolic checks cannot assert numerical, empirical, or review evidence."
            )
        expected_checker = {
            "candidate-check.v1": "candidate-static.v1",
            "candidate-check.v2": "candidate-static.v2",
            "candidate-check.v3": "candidate-static.v3",
            "candidate-check.v4": "candidate-static.v4",
        }[self.schema_version]
        if self.checker_version != expected_checker:
            raise ValueError("Candidate check schema and checker versions do not match.")
        identity = {
            "version": self.checker_version,
            "candidate_hash": self.candidate_hash,
            "claim": self.claim,
            "scope": self.scope,
            "assumptions": self.assumptions,
            "input_hashes": self.input_hashes,
            "outcome": self.outcome,
            "witness": self.witness,
        }
        if self.schema_version != "candidate-check.v1":
            identity["schema_version"] = self.schema_version
            identity["vector"] = self.vector.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        if self.check_id != f"chk_{digest[:32]}":
            raise ValueError("Candidate check ID does not match its immutable result content.")
        return self


class CandidateCheckResponse(FrozenInput):
    check: CandidateCheckResult
    replayed: bool


class CandidateCheckRequest(FrozenInput):
    pass


def verify_compiled_candidate(
    candidate: CompiledCandidate,
    *,
    parent_candidate: CompiledCandidate | None = None,
) -> CandidateCheckResult:
    """Check only the registered IR rules; never infer correctness from plausibility."""
    _verify_candidate_identity(candidate)
    ir = _load_ir(candidate.ir_json)

    if (
        candidate.operator in {"mix_positive_feature_maps", "lower_mixture_to_concatenation"}
        and candidate.operator_version != "1"
    ):
        return _result(
            candidate,
            claim="No independent checker rule is registered for this operator version.",
            scope="candidate_structure",
            assumptions=(),
            input_hashes=(candidate.content_hash,),
            outcome="unsupported",
            type_status="unknown",
            domain="unsupported",
            witness={
                "rule": "unregistered_operator_version",
                "operator": candidate.operator,
                "version": candidate.operator_version,
            },
        )

    if candidate.operator == "mix_positive_feature_maps":
        _validate_mixture(candidate, ir)
        left, right = _feature(ir["left"]), _feature(ir["right"])
        _require_obligations(
            candidate,
            (
                Obligation(
                    name="lambda_in_unit_interval",
                    status="discharged",
                    evidence_refs=("dsl-schema.v1",),
                ),
                Obligation(
                    name="compatible_ports",
                    status="discharged",
                    evidence_refs=(candidate.mapping_id or "",),
                ),
                Obligation(
                    name="positive_feature_maps",
                    status="discharged",
                    evidence_refs=(left["contract_hash"], right["contract_hash"]),
                ),
                Obligation(
                    name="output_rank_sum",
                    status="discharged",
                    evidence_refs=(left["contract_hash"], right["contract_hash"]),
                ),
                Obligation(name="nonzero_normalization_denominator", status="unresolved"),
                Obligation(name="causal_mask_preserved", status="unresolved"),
            ),
        )
        return _result(
            candidate,
            claim="The compiled weighted-kernel mixture has the declared typed structure.",
            scope="candidate_structure",
            assumptions=(),
            input_hashes=(candidate.content_hash,),
            outcome="unknown",
            type_status="unknown",
            domain="unresolved",
            witness={"rule": "positive_feature_map_mixture.v1", "symbolic_identity": False},
        )

    if candidate.operator != "lower_mixture_to_concatenation":
        return _result(
            candidate,
            claim="No independent checker rule is registered for this operator.",
            scope="candidate_structure",
            assumptions=(),
            input_hashes=(candidate.content_hash,),
            outcome="unsupported",
            type_status="unknown",
            domain="unsupported",
            witness=None,
        )

    if parent_candidate is None:
        raise ValueError("The exact mixture parent is required for this checker rule.")
    _validate_lowering(candidate, ir, parent_candidate)
    _require_obligations(
        candidate,
        (
            Obligation(name="kernel_identity", status="unresolved"),
            Obligation(name="normalization_domain", status="unresolved"),
        ),
    )
    return _result(
        candidate,
        claim=(
            "The concatenated feature map has the same kernel as the weighted sum "
            "of its two parent feature-map kernels."
        ),
        scope="feature_kernel_identity",
        assumptions=(
            "feature vectors are finite-dimensional over the real numbers",
            "the frozen mixture weight is in [0, 1]",
        ),
        input_hashes=(candidate.content_hash, parent_candidate.content_hash),
        outcome="supported",
        type_status="well_typed",
        domain="conditional",
        witness={
            "rule": "bilinear_inner_product_over_concatenation.v1",
            "identity": "<concat(sqrt(lambda)*phi_l,sqrt(1-lambda)*phi_r)(q),"
            "concat(sqrt(lambda)*phi_l,sqrt(1-lambda)*phi_r)(k)>"
            " = lambda*<phi_l(q),phi_l(k)> + "
            "(1-lambda)*<phi_r(q),phi_r(k)>",
        },
    )


def discharged_obligations(check: CandidateCheckResult | None) -> frozenset[str]:
    """Map only a registered, content-bound checker rule to its exact obligation."""
    if (
        check is not None
        and check.checker_version == CANDIDATE_CHECKER_VERSION
        and check.outcome == "supported"
        and check.scope == "feature_kernel_identity"
        and isinstance(check.witness, dict)
        and check.witness.get("rule") == "bilinear_inner_product_over_concatenation.v1"
    ):
        return frozenset({"kernel_identity"})
    return frozenset()


def _result(
    candidate: CompiledCandidate,
    *,
    claim: str,
    scope: Literal["candidate_structure", "feature_kernel_identity"],
    assumptions: tuple[str, ...],
    input_hashes: tuple[str, ...],
    outcome: Literal["supported", "refuted", "unknown", "unsupported", "timeout", "error"],
    type_status: Literal["well_typed", "ill_typed", "unknown"],
    domain: Literal["discharged", "conditional", "unresolved", "contradictory", "unsupported"],
    witness: dict[str, object] | None,
) -> CandidateCheckResult:
    vector = VerificationVector(
        parse="supported",
        type=type_status,
        domain=domain,
        symbolic=outcome,
    )
    identity = {
        "version": CANDIDATE_CHECKER_VERSION,
        "candidate_hash": candidate.content_hash,
        "claim": claim,
        "scope": scope,
        "assumptions": assumptions,
        "input_hashes": input_hashes,
        "outcome": outcome,
        "witness": witness,
        "schema_version": "candidate-check.v4",
        "vector": vector.model_dump(mode="json"),
    }
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return CandidateCheckResult(
        check_id=f"chk_{digest[:32]}",
        candidate_id=candidate.candidate_id,
        workspace_id=candidate.workspace_id,
        candidate_hash=candidate.content_hash,
        checker_version=CANDIDATE_CHECKER_VERSION,
        claim=claim,
        scope=scope,
        assumptions=assumptions,
        input_hashes=input_hashes,
        outcome=outcome,
        vector=vector,
        witness=witness,
        created_at=datetime.now(UTC),
        schema_version="candidate-check.v4",
    )


def _load_ir(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Candidate IR is not valid JSON.") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("Candidate IR must be a canonical JSON object.")
    return value


def _require_obligations(
    candidate: CompiledCandidate,
    expected: tuple[Obligation, ...],
) -> None:
    if candidate.obligations != expected:
        raise ValueError("Candidate obligations do not match the registered operator rule.")


def _validate_mixture(candidate: CompiledCandidate, ir: dict[str, object]) -> None:
    if candidate.semantics_class != "hypothesis_changing" or len(candidate.parents) != 3:
        raise ValueError("Mixture candidate has invalid semantics or parent lineage.")
    if set(ir) != {"kind", "target_node_id", "lambda", "left", "right", "kernel", "output_rank"}:
        raise ValueError("Mixture IR has an unsupported field set.")
    _weight(ir.get("lambda"))
    left, right = _feature(ir.get("left")), _feature(ir.get("right"))
    if ir.get("kind") != "positive_feature_map_mixture.v1":
        raise ValueError("Mixture IR kind is unsupported.")
    if type(ir.get("target_node_id")) is not str or not ir["target_node_id"]:
        raise ValueError("Mixture target is missing.")
    if ir.get("output_rank") != left["feature_rank"] + right["feature_rank"]:
        raise ValueError("Mixture output rank does not match its reviewed inputs.")
    if candidate.parents[0].entity_id != ir["target_node_id"]:
        raise ValueError("Mixture target does not match its source parent.")
    if not candidate.parents[1].entity_id.endswith(f":{left['symbol_id']}"):
        raise ValueError("Left feature-map parent does not match the IR binding.")
    if not candidate.parents[2].entity_id.endswith(f":{right['symbol_id']}"):
        raise ValueError("Right feature-map parent does not match the IR binding.")
    if candidate.parents[1].content_hash != left["dependency_hash"]:
        raise ValueError("Left feature-map snapshot does not match the IR binding.")
    if candidate.parents[2].content_hash != right["dependency_hash"]:
        raise ValueError("Right feature-map snapshot does not match the IR binding.")
    if ir.get("kernel") != _mixture_kernel():
        raise ValueError("Mixture kernel does not match the registered DSL rule.")


def _validate_lowering(
    candidate: CompiledCandidate,
    ir: dict[str, object],
    parent: CompiledCandidate,
) -> None:
    _verify_candidate_identity(parent)
    parent_ir = _load_ir(parent.ir_json)
    _validate_mixture(parent, parent_ir)
    if candidate.semantics_class != "preserving" or len(candidate.parents) != 1:
        raise ValueError("Lowering candidate has invalid semantics or parent lineage.")
    parent_ref = ParentRef(
        entity_id=parent.candidate_id,
        version=1,
        content_hash=parent.content_hash,
    )
    if (
        candidate.workspace_id != parent.workspace_id
        or candidate.problem_spec_hash != parent.problem_spec_hash
        or candidate.mapping_id != parent.mapping_id
        or candidate.parents != (parent_ref,)
        or parent.operator != "mix_positive_feature_maps"
    ):
        raise ValueError("Lowering candidate is not bound to this immutable mixture parent.")
    weight = _weight(parent_ir["lambda"])
    left, right = _feature(parent_ir["left"]), _feature(parent_ir["right"])
    expected = {
        "kind": "feature_concatenation.v1",
        "mixture_parent": parent.candidate_id,
        "lambda": weight,
        "left": left,
        "right": right,
        "output_rank": left["feature_rank"] + right["feature_rank"],
        "feature_map": {
            "op": "concat",
            "args": [
                {
                    "op": "scale",
                    "factor": {"op": "sqrt", "value": "lambda"},
                    "feature_map": "phi_left",
                },
                {
                    "op": "scale",
                    "factor": {"op": "sqrt", "value": "one_minus_lambda"},
                    "feature_map": "phi_right",
                },
            ],
        },
    }
    if ir != expected:
        raise ValueError("Lowering IR does not match the registered concatenation identity.")


def _weight(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Mixture weight must be finite and in [0, 1].")
    return float(value)


def _feature(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "symbol_id", "source_hash", "contract_hash", "dependency_hash", "feature_rank",
    }:
        raise ValueError("Feature-map IR has an unsupported field set.")
    for key in ("source_hash", "contract_hash", "dependency_hash"):
        item = value.get(key)
        if (
            type(item) is not str
            or len(item) != 64
            or any(char not in "0123456789abcdef" for char in item)
        ):
            raise ValueError("Feature-map IR contains an invalid content hash.")
    if type(value.get("symbol_id")) is not str or not value["symbol_id"]:
        raise ValueError("Feature-map symbol ID is invalid.")
    if type(value.get("feature_rank")) is not int or value["feature_rank"] <= 0:
        raise ValueError("Feature-map rank is invalid.")
    return value


def _mixture_kernel() -> dict[str, object]:
    return {
        "op": "add",
        "args": [
            {
                "op": "multiply",
                "weight": "lambda",
                "value": {"op": "dot", "left": "phi_left(query)", "right": "phi_left(key)"},
            },
            {
                "op": "multiply",
                "weight": "one_minus_lambda",
                "value": {"op": "dot", "left": "phi_right(query)", "right": "phi_right(key)"},
            },
        ],
    }
