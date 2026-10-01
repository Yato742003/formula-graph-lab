from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import EvidenceSearchHit, EvidenceSearchRequest
from app.retrieval_experiment import benchmark_retrieval, controlled_retrieval


@pytest.mark.asyncio
async def test_controller_is_bounded_keeps_source_and_never_receives_gold():
    source = EvidenceSearchHit(
        uuid=str(uuid4()),
        kind="Equation",
        logical_id="equation",
        paper_id="2006.16236",
        version=3,
        valid_at=None,
        verification_status="reported",
        payload={"anchor": "S3.E4", "anchor_is_source": True},
        episode_uuids=[],
        score=10,
        match_sources=["lexical"],
        score_components={},
    )

    class Search:
        calls = []

        async def search(self, request):
            self.calls.append(request)
            return SimpleNamespace(hits=[source])

    service = Search()
    request = EvidenceSearchRequest(workspace_id="ws", query="linear attention S3.E4", limit=1)
    hits, calls = await controlled_retrieval(service, request)
    assert calls == 2 and hits == [source]
    assert service.calls[1].center_node_uuid == source.uuid
    report = await benchmark_retrieval(service, [(request, {"2006.16236v3#S3.E4"})], repeats=2)
    assert report["summary"]["controller"]["mean_recall_at_k"] == 1
    assert report["summary"]["controller"]["provider_input_tokens"] == 0
    assert report["summary"]["controller"]["api_cost_usd"] == 0
    assert len(report["rows"]) == 4
    assert all(call.workspace_id == "ws" and call.limit <= 16 for call in service.calls)
    with pytest.raises(ValueError):
        await benchmark_retrieval(service, [], repeats=2)
