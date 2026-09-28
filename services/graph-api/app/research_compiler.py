"""Deterministic compiler for the first two, deliberately bounded research moves."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from app.analysis_versions import canonical_json
from app.compatibility import (
    CompatibilityAssessment,
    PortDescriptor,
    PortMapping,
    ResolvedDependency,
    compute_dependency_fingerprint,
)
from app.problem_spec import FrozenInput, ProblemSpecSnapshot
from app.transformation_dsl import (
    LowerMixtureToConcatenation,
    MixPositiveFeatureMaps,
    OperatorManifest,
    validate_transform,
)

COMPILER_VERSION = "research-compiler.v1"
IR_VERSION = "research-ir.v1"
_HASH = r"^[0-9a-f]{64}$"


class ResolvedFeatureMap(FrozenInput):
    """Server-resolved source and contract data; never accept this from a model."""

    workspace_id: str = Field(min_length=1, max_length=200)
    port: PortDescriptor
    source_hash: str = Field(pattern=_HASH)
    contract_hash: str = Field(pattern=_HASH)
    dependency_hash: str = Field(pattern=_HASH)
    feature_rank: int = Field(gt=0, le=100_000, strict=True)


class ParentRef(FrozenInput):
    """Pinned parent identity; for evidence sources, hash includes current analysis/contracts."""

    entity_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1, strict=True)
    content_hash: str = Field(pattern=_HASH)


class CompileContext(FrozenInput):
    workspace_id: str = Field(min_length=1, max_length=200)
    target_node_id: str = Field(min_length=1, max_length=200)
    target_parent: ParentRef
    target_dependency: ResolvedDependency
    spec: ProblemSpecSnapshot
    left: ResolvedFeatureMap
    right: ResolvedFeatureMap
    mapping: PortMapping
    assessment: CompatibilityAssessment
    dependencies: tuple[ResolvedDependency, ...] = Field(min_length=2, max_length=16)
    current_dependency_fingerprint: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_authoritative_snapshot(self) -> CompileContext:
        if self.spec.workspace_id != self.workspace_id:
            raise ValueError("ProblemSpec belongs to another workspace.")
        if self.target_parent.entity_id != self.target_node_id:
            raise ValueError("Target parent does not match the resolved target node.")
        if (
            self.target_parent.version != self.target_dependency.version
            or self.target_parent.content_hash != self.target_dependency.content_hash
        ):
            raise ValueError("Target parent does not match its current dependency snapshot.")
        if self.mapping.workspace_id != self.workspace_id:
            raise ValueError("Compatibility mapping belongs to another workspace.")
        if (
            self.left.workspace_id != self.workspace_id
            or self.right.workspace_id != self.workspace_id
        ):
            raise ValueError("Feature-map source belongs to another workspace.")
        if (
            self.mapping.producer_port != self.left.port
            or self.mapping.consumer_port != self.right.port
        ):
            raise ValueError("Compatibility mapping does not match the resolved feature-map ports.")
        if self.assessment.mapping_id != self.mapping.mapping_id:
            raise ValueError("Compatibility assessment does not belong to this mapping.")
        fingerprint = compute_dependency_fingerprint(self.dependencies)
        if fingerprint != self.current_dependency_fingerprint:
            raise ValueError("Current dependency fingerprint does not match resolved dependencies.")
        if self.assessment.dependency_fingerprint != self.current_dependency_fingerprint:
            raise ValueError("Compatibility assessment is stale for the current dependencies.")
        if self.assessment.freshness != "current":
            raise ValueError("Stale compatibility assessments cannot be compiled.")
        resolved = {(item.entity_id, item.version): item.content_hash for item in self.dependencies}
        for source in (self.left, self.right):
            key = (
                f"{source.port.equation_id}:{source.port.scoped_symbol_id}",
                source.port.version,
            )
            if resolved.get(key) != source.dependency_hash:
                raise ValueError(
                    "Feature-map source does not match the resolved dependency snapshot."
                )
        return self


class Obligation(FrozenInput):
    name: str = Field(min_length=1, max_length=100)
    status: Literal["discharged", "conditional", "unresolved"]
    evidence_refs: tuple[str, ...] = ()


class CompiledCandidate(FrozenInput):
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    problem_spec_id: str = Field(min_length=1, max_length=200)
    problem_spec_hash: str = Field(pattern=_HASH)
    mapping_id: str | None = Field(default=None, min_length=1, max_length=200)
    operator: str = Field(min_length=1, max_length=100)
    operator_version: str = Field(min_length=1, max_length=50)
    semantics_class: Literal["preserving", "approximation", "hypothesis_changing"]
    compiler_version: Literal["research-compiler.v1"] = COMPILER_VERSION
    ir_version: Literal["research-ir.v1"] = IR_VERSION
    ir_json: str = Field(min_length=2, max_length=32_768)
    parents: tuple[ParentRef, ...] = Field(min_length=1, max_length=8)
    obligations: tuple[Obligation, ...] = Field(max_length=32)
    content_hash: str = Field(pattern=_HASH)


class CompileCandidateRequest(FrozenInput):
    workspace_id: str = Field(min_length=1, max_length=200)
    spec_id: str = Field(min_length=1, max_length=200)
    mapping_id: str = Field(min_length=1, max_length=200)
    transform: dict[str, Any]
    parent_candidate_id: str | None = Field(default=None, pattern=r"^cand_[0-9a-f]{32}$")
    proposal_id: str | None = Field(default=None, pattern=r"^prop_[0-9a-f]{32}$")

    @model_validator(mode="after")
    def bound_transform(self) -> CompileCandidateRequest:
        try:
            size = len(canonical_json(self.transform).encode("utf-8"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Transform must be finite JSON data.") from exc
        if size > 8_192:
            raise ValueError("Transform exceeds the input size limit.")
        return self


class TransformationActivity(FrozenInput):
    activity_id: str = Field(pattern=r"^act_[0-9a-f]{32}$")
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    operator: str = Field(min_length=1, max_length=100)
    operator_version: str = Field(min_length=1, max_length=50)
    mapping_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_proposal_id: str | None = Field(default=None, pattern=r"^prop_[0-9a-f]{32}$")
    semantics_class: Literal["preserving", "approximation", "hypothesis_changing"]
    request_json: str = Field(min_length=2, max_length=8_192)
    parents: tuple[ParentRef, ...] = Field(min_length=1, max_length=8)
    created_at: str = Field(min_length=1, max_length=100)
    schema_version: Literal["transformation-activity.v1", "transformation-activity.v2"] = (
        "transformation-activity.v1"
    )
    candidate_hash: str | None = Field(default=None, pattern=_HASH)
    compiler_context_json: str | None = Field(default=None, max_length=65_536)
    compiler_context_hash: str | None = Field(default=None, pattern=_HASH)

    @model_validator(mode="after")
    def validate_replay_snapshot(self) -> TransformationActivity:
        if self.schema_version == "transformation-activity.v1":
            if any((self.candidate_hash, self.compiler_context_json, self.compiler_context_hash)):
                raise ValueError("Legacy transformation activity cannot contain v2 replay fields.")
            return self
        if not all((self.candidate_hash, self.compiler_context_json, self.compiler_context_hash)):
            raise ValueError("Version 2 activity requires candidate and compiler-context hashes.")
        if self.candidate_id != f"cand_{self.candidate_hash[:32]}":
            raise ValueError("Activity candidate identity does not match its candidate hash.")
        try:
            context = CompileContext.model_validate_json(self.compiler_context_json)
            context_json = canonical_json(context.model_dump(mode="json"))
            request = json.loads(self.request_json)
            if canonical_json(request) != self.request_json:
                raise ValueError("Activity transform request must be canonical JSON.")
            transform, manifest = validate_transform(
                request, context.spec.definition.allowed_transforms
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Activity replay snapshot is invalid.") from exc
        if (
            context.workspace_id != self.workspace_id
            or context.mapping.mapping_id != self.mapping_id
        ):
            raise ValueError("Activity replay snapshot has a different workspace or mapping.")
        if (
            transform.operator != self.operator
            or transform.operator_version != self.operator_version
            or manifest.semantics_class != self.semantics_class
        ):
            raise ValueError("Activity replay snapshot does not match its registered operator.")
        if self.compiler_context_json != context_json:
            raise ValueError("Activity compiler context must use canonical JSON.")
        if hashlib.sha256(context_json.encode("utf-8")).hexdigest() != self.compiler_context_hash:
            raise ValueError("Activity compiler context hash is invalid.")
        return self


class CompileCandidateResponse(FrozenInput):
    candidate: CompiledCandidate
    activity: TransformationActivity
    replayed: bool


def _parent_ref(feature_map: ResolvedFeatureMap) -> ParentRef:
    return ParentRef(
        entity_id=f"{feature_map.port.equation_id}:{feature_map.port.scoped_symbol_id}",
        version=feature_map.port.version,
        content_hash=feature_map.dependency_hash,
    )


def _finish(
    *,
    context: CompileContext,
    manifest: OperatorManifest,
    ir: dict,
    parents: tuple[ParentRef, ...],
    obligations: tuple[Obligation, ...],
) -> CompiledCandidate:
    ir_json = canonical_json(ir)
    identity = {
        "workspace_id": context.workspace_id,
        "problem_spec_id": context.spec.spec_id,
        "problem_spec_hash": context.spec.content_hash,
        "mapping_id": context.mapping.mapping_id,
        "operator": manifest.name,
        "operator_version": manifest.version,
        "semantics_class": manifest.semantics_class,
        "compiler_version": COMPILER_VERSION,
        "ir_version": IR_VERSION,
        "ir_json": ir_json,
        "parents": [item.model_dump(mode="json") for item in parents],
        "obligations": [item.model_dump(mode="json") for item in obligations],
    }
    content_hash = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return CompiledCandidate(
        candidate_id=f"cand_{content_hash[:32]}",
        workspace_id=context.workspace_id,
        problem_spec_id=context.spec.spec_id,
        problem_spec_hash=context.spec.content_hash,
        mapping_id=context.mapping.mapping_id,
        operator=manifest.name,
        operator_version=manifest.version,
        semantics_class=manifest.semantics_class,
        ir_json=ir_json,
        parents=parents,
        obligations=obligations,
        content_hash=content_hash,
    )


def _verify_candidate_identity(candidate: CompiledCandidate) -> None:
    identity = {
        "workspace_id": candidate.workspace_id,
        "problem_spec_id": candidate.problem_spec_id,
        "problem_spec_hash": candidate.problem_spec_hash,
        "operator": candidate.operator,
        "operator_version": candidate.operator_version,
        "semantics_class": candidate.semantics_class,
        "compiler_version": candidate.compiler_version,
        "ir_version": candidate.ir_version,
        "ir_json": candidate.ir_json,
        "parents": [item.model_dump(mode="json") for item in candidate.parents],
        "obligations": [item.model_dump(mode="json") for item in candidate.obligations],
    }
    # Legacy persisted candidates did not record their compatibility mapping.
    if candidate.mapping_id is not None:
        identity["mapping_id"] = candidate.mapping_id
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    if digest != candidate.content_hash or candidate.candidate_id != f"cand_{digest[:32]}":
        raise ValueError("Parent candidate content hash is invalid.")


def compile_transform(
    raw: object,
    *,
    context: CompileContext,
    parent_candidate: CompiledCandidate | None = None,
) -> CompiledCandidate:
    """Compile using server-resolved snapshots; never accepts client-authored verdicts."""
    transform, manifest = validate_transform(raw, context.spec.definition.allowed_transforms)
    if isinstance(transform, MixPositiveFeatureMaps):
        if parent_candidate is not None:
            raise ValueError("A new mixture cannot claim an unrelated candidate parent.")
        if transform.bindings.left != context.left.port.scoped_symbol_id:
            raise ValueError("Left binding does not resolve to the declared source port.")
        if transform.bindings.right != context.right.port.scoped_symbol_id:
            raise ValueError("Right binding does not resolve to the declared source port.")
        if transform.target_node_id != context.target_node_id:
            raise ValueError("Target node does not match the server-resolved parent node.")
        if context.assessment.status != "compatible" or not context.assessment.usable:
            raise ValueError("Feature-map ports are not currently verified compatible.")
        for source in (context.left, context.right):
            if not source.port.domain_is_reviewed or source.port.domain != "strictly_positive_real":
                raise ValueError(
                    "Feature-map positivity requires a reviewed positive-real contract."
                )
        if context.left.feature_rank + context.right.feature_rank > 100_000:
            raise ValueError("Compiled feature rank exceeds the operator limit.")
        ir = {
            "kind": "positive_feature_map_mixture.v1",
            "target_node_id": context.target_node_id,
            "lambda": transform.parameters.lambda_weight,
            "left": {
                "symbol_id": context.left.port.scoped_symbol_id,
                "source_hash": context.left.source_hash,
                "contract_hash": context.left.contract_hash,
                "dependency_hash": context.left.dependency_hash,
                "feature_rank": context.left.feature_rank,
            },
            "right": {
                "symbol_id": context.right.port.scoped_symbol_id,
                "source_hash": context.right.source_hash,
                "contract_hash": context.right.contract_hash,
                "dependency_hash": context.right.dependency_hash,
                "feature_rank": context.right.feature_rank,
            },
            "kernel": {
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
                        "value": {
                            "op": "dot",
                            "left": "phi_right(query)",
                            "right": "phi_right(key)",
                        },
                    },
                ],
            },
            "output_rank": context.left.feature_rank + context.right.feature_rank,
        }
        obligations = (
            Obligation(
                name="lambda_in_unit_interval",
                status="discharged",
                evidence_refs=("dsl-schema.v1",),
            ),
            Obligation(
                name="compatible_ports",
                status="discharged",
                evidence_refs=(context.assessment.mapping_id,),
            ),
            Obligation(
                name="positive_feature_maps",
                status="discharged",
                evidence_refs=(context.left.contract_hash, context.right.contract_hash),
            ),
            Obligation(
                name="output_rank_sum",
                status="discharged",
                evidence_refs=(context.left.contract_hash, context.right.contract_hash),
            ),
            Obligation(name="nonzero_normalization_denominator", status="unresolved"),
            Obligation(name="causal_mask_preserved", status="unresolved"),
        )
        return _finish(
            context=context,
            manifest=manifest,
            ir=ir,
            parents=(
                context.target_parent,
                _parent_ref(context.left),
                _parent_ref(context.right),
            ),
            obligations=obligations,
        )

    if not isinstance(transform, LowerMixtureToConcatenation):
        raise ValueError("No compiler is registered for this transform.")
    if parent_candidate is None:
        raise ValueError("Concatenation lowering requires its compiled mixture parent.")
    _verify_candidate_identity(parent_candidate)
    if parent_candidate.workspace_id != context.workspace_id:
        raise ValueError("Parent candidate belongs to another workspace.")
    if parent_candidate.problem_spec_hash != context.spec.content_hash:
        raise ValueError("Parent candidate uses a different ProblemSpec snapshot.")
    if parent_candidate.mapping_id != context.mapping.mapping_id:
        raise ValueError("Parent candidate uses a different compatibility mapping.")
    if parent_candidate.operator != "mix_positive_feature_maps":
        raise ValueError("Only a positive feature-map mixture can be lowered here.")
    if parent_candidate.parents != (
        context.target_parent,
        _parent_ref(context.left),
        _parent_ref(context.right),
    ):
        raise ValueError("Parent candidate source snapshots are stale or do not match these ports.")
    if transform.bindings.mixture != parent_candidate.candidate_id:
        raise ValueError("Mixture binding does not match the supplied parent candidate.")
    if transform.target_node_id != parent_candidate.candidate_id:
        raise ValueError("Target node does not match the supplied parent candidate.")
    parent_ir = json.loads(parent_candidate.ir_json)
    if parent_ir.get("kind") != "positive_feature_map_mixture.v1":
        raise ValueError("Parent IR is not a supported positive feature-map mixture.")
    ir = {
        "kind": "feature_concatenation.v1",
        "mixture_parent": parent_candidate.candidate_id,
        "lambda": parent_ir["lambda"],
        "left": parent_ir["left"],
        "right": parent_ir["right"],
        "output_rank": parent_ir["output_rank"],
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
    obligations = (
        Obligation(name="kernel_identity", status="unresolved"),
        Obligation(name="normalization_domain", status="unresolved"),
    )
    return _finish(
        context=context,
        manifest=manifest,
        ir=ir,
        parents=(
            ParentRef(
                entity_id=parent_candidate.candidate_id,
                version=1,
                content_hash=parent_candidate.content_hash,
            ),
        ),
        obligations=obligations,
    )


def replay_compiled_candidate(
    candidate: CompiledCandidate,
    activity: TransformationActivity,
    *,
    parent_candidate: CompiledCandidate | None = None,
) -> CompiledCandidate:
    """Recompile from the immutable activity snapshot and require exact identity."""
    _verify_candidate_identity(candidate)
    if (
        activity.schema_version != "transformation-activity.v2"
        or activity.candidate_id != candidate.candidate_id
        or activity.candidate_hash != candidate.content_hash
        or activity.workspace_id != candidate.workspace_id
    ):
        raise ValueError("Activity does not provide a current replay snapshot for this candidate.")
    if activity.compiler_context_json is None:
        raise ValueError("Activity compiler context is unavailable.")
    context = CompileContext.model_validate_json(activity.compiler_context_json)
    replayed = compile_transform(
        json.loads(activity.request_json),
        context=context,
        parent_candidate=parent_candidate,
    )
    if (
        replayed != candidate
        or activity.parents != candidate.parents
        or activity.operator != candidate.operator
        or activity.operator_version != candidate.operator_version
        or activity.semantics_class != candidate.semantics_class
    ):
        raise ValueError("Replayed candidate differs from its persisted activity.")
    return replayed
