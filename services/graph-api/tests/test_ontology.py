import pytest
from graphiti_core.utils.ontology_utils.entity_types_utils import validate_entity_types
from pydantic import ValidationError

from app.ontology import (
    ALLOWED_RELATION_PAIRS,
    EDGE_TYPE_MAP,
    EDGE_TYPES,
    ENTITY_TYPES,
    RelationContract,
)


def test_all_planned_entities_and_relation_schemas_are_sdk_compatible():
    assert set(ENTITY_TYPES) == {
        "Paper", "PaperVersion", "Section", "Equation", "Symbol", "Assumption",
        "Claim", "Concept", "Method", "Experiment", "Hypothesis",
    }
    assert validate_entity_types(ENTITY_TYPES)
    for entity in ENTITY_TYPES.values():
        assert entity.model_json_schema()["additionalProperties"] is False
    for pair, models in EDGE_TYPE_MAP.items():
        for name in models:
            schema = EDGE_TYPES[name].model_json_schema()
            relation = schema["properties"]["relation"]["const"]
            assert pair in ALLOWED_RELATION_PAIRS[relation]


@pytest.mark.parametrize("source,target,relation", [
    ("Paper", "PaperVersion", "contains"),
    ("Unknown", "Equation", "uses"),
    ("Equation", "Equation", "proves_everything"),
])
def test_unknown_or_illegal_relation_is_rejected(source, target, relation):
    with pytest.raises(ValidationError):
        RelationContract(
            source_type=source, target_type=target, relation=relation,
            source_anchor="S1.E1", confidence=0.9,
        )


def test_equivalence_needs_conditions():
    with pytest.raises(ValidationError, match="conditions"):
        RelationContract(
            source_type="Equation", target_type="Equation", relation="equivalent_under",
            source_anchor="S1.E1", confidence=0.9,
        )
    relation = RelationContract(
        source_type="Equation", target_type="Equation", relation="equivalent_under",
        source_anchor="S1.E1", confidence=0.9, conditions=["x is positive"],
    )
    assert relation.conditions == ["x is positive"]


def test_disagreement_keeps_claims_as_separate_endpoints():
    relation = RelationContract(
        source_type="Claim", target_type="Claim", relation="disagrees_with",
        source_anchor="S1", confidence=1,
    )
    assert relation.relation == "disagrees_with"
