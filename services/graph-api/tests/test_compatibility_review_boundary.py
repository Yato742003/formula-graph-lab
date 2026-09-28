"""A binding review cannot discharge unrelated mathematical requirements."""

import pytest

from app.compatibility import assess_mapping
from app.research_store import Neo4jResearchStore
from tests.test_compatibility import (
    make_base_consumer,
    make_base_producer,
    make_deps,
    make_mapping,
)


@pytest.mark.parametrize(
    ("producer_changes", "status", "requirement"),
    [
        ({"shape": None}, "unknown", "unknown_dimension"),
        ({"mask": "missing"}, "unknown", "missing_mask_metadata"),
        ({"domain_is_reviewed": False}, "unknown", "unreviewed_inferred_domain"),
        ({"shape": (3, 2)}, "incompatible", "dimension_mismatch"),
    ],
)
def test_binding_review_preserves_unresolved_requirements_and_contradictions(
    producer_changes, status, requirement,
):
    mapping = make_mapping(
        make_base_producer(scoped_symbol_id="producer-scope", **producer_changes),
        make_base_consumer(scoped_symbol_id="consumer-scope"),
    )
    dependencies = make_deps()
    initial = assess_mapping(mapping, dependencies)
    review = {
        "review_id": "review-1",
        "mapping_id": mapping.mapping_id,
        "reviewer_id": "reviewer-1",
        "reviewer_role": "reviewer",
        "decision": "reviewed",
        "notes": "Confirmed only the binding between these ports.",
        "reviewed_at": "2026-09-19T00:00:00+00:00",
        "dependency_fingerprint": initial.dependency_fingerprint,
    }
    _, assessment = Neo4jResearchStore._effective_compatibility(
        mapping, initial, dependencies, [review],
    )
    assert assessment.status == status
    assert not assessment.usable
    assert requirement in (*assessment.reasons, *assessment.unresolved_requirements)
    assert "unbound_symbol_scope" not in assessment.unresolved_requirements
    assert mapping.explicit_binding_reviewed is False


@pytest.mark.parametrize("fingerprint", [None, "0" * 64])
def test_review_without_matching_dependency_snapshot_cannot_approve_binding(fingerprint):
    mapping = make_mapping(
        make_base_producer(scoped_symbol_id="producer"),
        make_base_consumer(scoped_symbol_id="consumer"),
    )
    dependencies = make_deps()
    initial = assess_mapping(mapping, dependencies)
    review = {
        "review_id": "old-review", "mapping_id": mapping.mapping_id,
        "reviewer_id": "human", "reviewer_role": "reviewer", "decision": "reviewed",
        "notes": "Legacy or wrong snapshot", "reviewed_at": "2026-09-20T00:00:00+00:00",
        "dependency_fingerprint": fingerprint,
    }
    effective, assessment = Neo4jResearchStore._effective_compatibility(
        mapping, initial, dependencies, [review],
    )
    assert effective.explicit_binding_reviewed is False
    assert assessment.status == "unknown"
    assert "unbound_symbol_scope" in assessment.unresolved_requirements


def test_old_policy_assessment_remains_historical_and_unusable():
    mapping = make_mapping(make_base_producer(), make_base_consumer())
    dependencies = make_deps()
    historical = assess_mapping(mapping, dependencies).model_copy(
        update={"policy_version": "compatibility-policy.v1"}
    )
    effective, assessment = Neo4jResearchStore._effective_compatibility(
        mapping, historical, dependencies, [],
    )
    assert effective == mapping
    assert assessment.status == historical.status
    assert assessment.policy_version == "compatibility-policy.v1"
    assert assessment.freshness == "stale"
    assert not assessment.usable
    assert "policy_version_changed" in assessment.reasons
