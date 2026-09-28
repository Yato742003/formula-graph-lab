"""Tests for G3 Pure Compatibility Decision Function (T4).

Validates the complete 12-row truth table from
GEMINI_SPRINT_5A_IMPLEMENTATION_HANDOFF.md.
"""

from app.compatibility import (
    CompatibilityEvidence,
    PortDescriptor,
    PortMapping,
    ResolvedDependency,
    assess_mapping,
    compute_dependency_fingerprint,
    generate_mapping_id,
)

DUMMY_HASH = "1" * 64
DUMMY_HASH_2 = "2" * 64


def make_base_producer(
    *,
    shape: tuple[int, ...] | None = (2, 3),
    domain: str = "strictly_positive_real",
    domain_is_reviewed: bool = True,
    causal: bool = True,
    normalization: str = "softmax",
    mask: str = "causal",
    resource_class: str = "not_applicable",
    scoped_symbol_id: str = "paper_a:eq_1:head_dim",
) -> PortDescriptor:
    return PortDescriptor(
        equation_id="eq_producer_1",
        version=1,
        scoped_symbol_id=scoped_symbol_id,
        symbol_name="head_dim",
        domain=domain,
        domain_is_reviewed=domain_is_reviewed,
        shape=shape,
        normalization=normalization,
        mask=mask,
        causal=causal,
        resource_class=resource_class,
    )


def make_base_consumer(
    *,
    shape: tuple[int, ...] | None = (2, 3),
    domain: str = "strictly_positive_real",
    domain_is_reviewed: bool = True,
    causal: bool = True,
    normalization: str = "softmax",
    mask: str = "causal",
    resource_class: str = "not_applicable",
    scoped_symbol_id: str = "paper_a:eq_1:head_dim",
) -> PortDescriptor:
    return PortDescriptor(
        equation_id="eq_consumer_1",
        version=1,
        scoped_symbol_id=scoped_symbol_id,
        symbol_name="head_dim",
        domain=domain,
        domain_is_reviewed=domain_is_reviewed,
        shape=shape,
        normalization=normalization,
        mask=mask,
        causal=causal,
        resource_class=resource_class,
    )


def make_mapping(producer: PortDescriptor, consumer: PortDescriptor, **kwargs) -> PortMapping:
    mapping_id = generate_mapping_id("ws_alpha", producer, consumer)
    return PortMapping(
        mapping_id=mapping_id,
        workspace_id="ws_alpha",
        producer_port=producer,
        consumer_port=consumer,
        **kwargs,
    )


def make_deps() -> tuple[ResolvedDependency, ...]:
    return (
        ResolvedDependency(entity_id="eq_producer_1", version=1, content_hash=DUMMY_HASH),
        ResolvedDependency(entity_id="eq_consumer_1", version=1, content_hash=DUMMY_HASH_2),
    )


# Row 1: trusted [2,3] -> [3,2] c?ng rank -> incompatible: dimension mismatch
def test_row1_dimension_mismatch():
    p = make_base_producer(shape=(2, 3))
    c = make_base_consumer(shape=(3, 2))
    mapping = make_mapping(p, c)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "incompatible"
    assert "dimension_mismatch" in assessment.reasons
    assert not assessment.usable


# Row 2: unknown dimension -> required 3 -> unknown
def test_row2_unknown_dimension():
    p = make_base_producer(shape=None)
    c = make_base_consumer(shape=(3,))
    mapping = make_mapping(p, c)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "unknown"
    assert "unknown_dimension" in assessment.unresolved_requirements
    assert not assessment.usable


# Row 3: inferred domain real ch?a review -> positive required -> unknown
def test_row3_unreviewed_inferred_domain():
    p = make_base_producer(domain="real", domain_is_reviewed=False)
    c = make_base_consumer(domain="strictly_positive_real", domain_is_reviewed=True)
    mapping = make_mapping(p, c)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "unknown"
    assert "unreviewed_inferred_domain" in assessment.unresolved_requirements
    assert not assessment.usable


# Row 4: trusted domain g?m s? ?m -> strictly positive required -> incompatible
def test_row4_domain_violation_negative_to_strictly_positive():
    p = make_base_producer(domain="real", domain_is_reviewed=True)
    c = make_base_consumer(domain="strictly_positive_real", domain_is_reviewed=True)
    mapping = make_mapping(p, c)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "incompatible"
    assert "domain_violation" in assessment.reasons
    assert not assessment.usable


# Row 5: noncausal output -> causal required -> incompatible: causality violation
def test_row5_causality_violation():
    p = make_base_producer(causal=False)
    c = make_base_consumer(causal=True)
    mapping = make_mapping(p, c)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "incompatible"
    assert "causality_violation" in assessment.reasons
    assert not assessment.usable


# Row 6: mask metadata thi?u -> unknown
def test_row6_missing_mask_metadata():
    p = make_base_producer(mask="missing")
    c = make_base_consumer(mask="causal")
    mapping = make_mapping(p, c)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "unknown"
    assert "missing_mask_metadata" in assessment.unresolved_requirements
    assert not assessment.usable


# Row 7: normalization kh?c, kh?ng c? conversion mapping -> incompatible
def test_row7_normalization_mismatch_without_conversion():
    p = make_base_producer(normalization="l2")
    c = make_base_consumer(normalization="softmax")
    mapping = make_mapping(p, c, conversion_rule=None)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "incompatible"
    assert "normalization_mismatch" in assessment.reasons
    assert not assessment.usable


# Row 8: hai symbol c?ng t?n kh?c scope -> unknown n?u ch?a explicit reviewed binding
def test_row8_unbound_symbol_scope():
    p = make_base_producer(scoped_symbol_id="paper_a:eq_1:d")
    c = make_base_consumer(scoped_symbol_id="paper_b:eq_5:d")
    mapping = make_mapping(p, c, explicit_binding_reviewed=False)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "unknown"
    assert "unbound_symbol_scope" in assessment.unresolved_requirements
    assert not assessment.usable


# Row 9: t?t c? requirement ??t, mapping review ??ng versions -> compatible, current
def test_row9_all_requirements_satisfied():
    p = make_base_producer(scoped_symbol_id="paper_a:eq_1:d")
    c = make_base_consumer(scoped_symbol_id="paper_b:eq_5:d")
    mapping = make_mapping(p, c, explicit_binding_reviewed=True)
    deps = make_deps()
    fp = compute_dependency_fingerprint(deps)
    assessment = assess_mapping(mapping, deps, expected_fingerprint=fp)
    assert assessment.status == "compatible"
    assert assessment.freshness == "current"
    assert assessment.usable
    assert not assessment.reasons
    assert not assessment.unresolved_requirements


# Row 10: gi?ng embedding=0.999, domain mismatch -> v?n incompatible
def test_row10_embedding_similarity_does_not_bypass_verification():
    p = make_base_producer(domain="real", domain_is_reviewed=True)
    c = make_base_consumer(domain="strictly_positive_real", domain_is_reviewed=True)
    mapping = make_mapping(p, c, embedding_similarity=0.999)
    assessment = assess_mapping(mapping, make_deps())
    assert assessment.status == "incompatible"
    assert "domain_violation" in assessment.reasons
    assert not assessment.usable


# Row 11: contract version ??i sau review -> status l?ch s? gi? nguy?n, stale, usable=false
def test_row11_contract_version_changed_becomes_stale():
    p = make_base_producer(scoped_symbol_id="paper_a:eq_1:d")
    c = make_base_consumer(scoped_symbol_id="paper_b:eq_5:d")
    mapping = make_mapping(p, c, explicit_binding_reviewed=True)

    initial_deps = make_deps()
    old_fingerprint = compute_dependency_fingerprint(initial_deps)

    # Dependency changed (e.g. producer upgraded version 1 -> 2)
    updated_deps = (
        ResolvedDependency(entity_id="eq_producer_1", version=2, content_hash="9" * 64),
        ResolvedDependency(entity_id="eq_consumer_1", version=1, content_hash=DUMMY_HASH_2),
    )
    assessment = assess_mapping(mapping, updated_deps, expected_fingerprint=old_fingerprint)
    assert assessment.status == "compatible"  # historical assessment
    assert assessment.freshness == "stale"
    assert not assessment.usable
    assert "stale_dependency" in assessment.reasons


# Row 12: evidence supported nh?ng sai parents/scope -> reject evidence, kh?ng compatible
def test_row12_evidence_wrong_scope_rejected():
    p = make_base_producer(scoped_symbol_id="paper_a:eq_1:d")
    c = make_base_consumer(scoped_symbol_id="paper_b:eq_5:d")
    # Missing explicit binding, but fake foreign evidence provided
    foreign_evidence = (
        CompatibilityEvidence(
            evidence_id="ev_foreign_1",
            scope_equation_id="unrelated_foreign_eq",
            scope_version=1,
            check_type="symbolic_equivalence",
            passed=True,
        ),
    )
    mapping = make_mapping(p, c, explicit_binding_reviewed=False)
    assessment = assess_mapping(mapping, make_deps(), evidence=foreign_evidence)
    assert assessment.status == "unknown"
    assert "invalid_evidence_scope" in assessment.unresolved_requirements
    assert not assessment.usable
    assert "ev_foreign_1" not in assessment.evidence_refs
