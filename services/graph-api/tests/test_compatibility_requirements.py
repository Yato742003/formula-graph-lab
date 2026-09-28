"""Regression cases where missing contracts used to silently produce success."""

import pytest

from app.compatibility import CompatibilityEvidence, assess_mapping
from tests.test_compatibility import (
    make_base_consumer,
    make_base_producer,
    make_deps,
    make_mapping,
)


@pytest.mark.parametrize(
    ("producer", "consumer", "status", "diagnostic"),
    [
        ({"domain": "unknown"}, {}, "unknown", "unknown_domain"),
        ({}, {"domain": "unknown"}, "unknown", "unknown_domain"),
        ({}, {"domain_is_reviewed": False}, "unknown", "unreviewed_inferred_domain"),
        ({"domain": "non_negative_real"}, {}, "incompatible", "domain_violation"),
        ({"domain": "unit_interval"}, {}, "incompatible", "domain_violation"),
        ({"domain": "real"}, {"domain": "integer"}, "incompatible", "domain_violation"),
        ({"normalization": "unknown"}, {}, "unknown", "unknown_normalization"),
        ({}, {"normalization": "unknown"}, "unknown", "unknown_normalization"),
        ({"normalization": "softmax"}, {"normalization": "none"},
         "incompatible", "normalization_mismatch"),
        ({"mask": "padding"}, {}, "incompatible", "mask_mismatch"),
        ({"mask": "custom"}, {"mask": "custom"}, "unknown", "custom_mask_requires_evidence"),
        ({"shape": ("n", 3)}, {"shape": ("n", 3)}, "unknown", "unknown_dimension"),
        ({"shape": ("n", 2)}, {"shape": ("m", 3)}, "incompatible", "dimension_mismatch"),
    ],
)
def test_missing_or_incompatible_contract_never_passes(producer, consumer, status, diagnostic):
    result = assess_mapping(
        make_mapping(make_base_producer(**producer), make_base_consumer(**consumer)), make_deps()
    )
    assert result.status == status
    assert not result.usable
    assert diagnostic in (*result.reasons, *result.unresolved_requirements)


def test_scalar_shape_is_known_and_distinct_from_unknown():
    mapping = make_mapping(make_base_producer(shape=()), make_base_consumer(shape=()))
    assert assess_mapping(mapping, make_deps()).status == "compatible"
    unknown = mapping.model_copy(update={"producer_port": make_base_producer(shape=None)})
    assert assess_mapping(unknown, make_deps()).status == "unknown"


def test_unknown_causality_cannot_be_refuted_or_approved():
    producer = make_base_producer(causal=None)
    consumer = make_base_consumer(causal=True)
    assessment = assess_mapping(make_mapping(producer, consumer), make_deps())
    assert assessment.status == "unknown"
    assert "unknown_causality" in assessment.unresolved_requirements
    assert "causality_violation" not in assessment.reasons
    assert assess_mapping(
        make_mapping(make_base_producer(causal=False), consumer), make_deps()
    ).status == "incompatible"


def test_resource_class_is_reviewed_not_defaulted_to_compatible():
    producer = make_base_producer(resource_class="unknown")
    consumer = make_base_consumer()
    assessment = assess_mapping(make_mapping(producer, consumer), make_deps())
    assert assessment.status == "unknown"
    assert "unknown_resource_class" in assessment.unresolved_requirements
    mismatch = assess_mapping(
        make_mapping(make_base_producer(resource_class="cpu"),
                     make_base_consumer(resource_class="gpu")), make_deps(),
    )
    assert mismatch.status == "incompatible"
    assert "resource_class_mismatch" in mismatch.reasons


@pytest.mark.parametrize(
    ("producer", "consumer"),
    [("positive_integer", "strictly_positive_real"), ("integer", "real"),
     ("real", "complex"), ("unit_interval", "non_negative_real")],
)
def test_reviewed_domain_subset_is_compatible(producer, consumer):
    mapping = make_mapping(
        make_base_producer(domain=producer), make_base_consumer(domain=consumer)
    )
    assert assess_mapping(mapping, make_deps()).status == "compatible"


def test_negative_evidence_or_absent_dependencies_cannot_pass():
    mapping = make_mapping(make_base_producer(), make_base_consumer())
    evidence = CompatibilityEvidence(
        evidence_id="negative-check", scope_equation_id="eq_producer_1", scope_version=1,
        check_type="domain", passed=False,
    )
    result = assess_mapping(mapping, make_deps(), evidence=(evidence,))
    assert result.status == "unknown"
    assert "negative_evidence_requires_review" in result.unresolved_requirements
    assert not result.usable
    assert assess_mapping(mapping, ()).status == "unknown"


def test_conversion_string_cannot_bypass_normalization_check():
    mapping = make_mapping(
        make_base_producer(normalization="l2"), make_base_consumer(),
        conversion_rule="trust-me",
    )
    result = assess_mapping(mapping, make_deps())
    assert not result.usable
    assert "normalization_mismatch" in result.reasons
    assert "unverified_conversion_rule" in result.unresolved_requirements
