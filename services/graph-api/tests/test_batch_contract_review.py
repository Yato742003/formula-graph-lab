import pytest
from pydantic import ValidationError

from app.models import BatchContractReviewCreateRequest


def test_duplicate_batch_rejected_before_persistence():
    item = {"symbol": "x", "reviewed_contract": {"name": "x", "category": "scalar"}}
    with pytest.raises(ValidationError, match="Duplicate symbol"):
        BatchContractReviewCreateRequest(
            workspace_id="lab", equation_uuid="1bd23cd6-01a4-495e-980d-32fdb2b81d34",
            rationale="Reviewed source", items=[item, item],
        )
