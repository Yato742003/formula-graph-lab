from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.main import app, lifespan


@pytest.mark.asyncio
async def test_failed_research_constraints_do_not_expose_a_writable_store(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://test-db:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unit-test-only")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    evidence = SimpleNamespace(
        driver=object(), database="neo4j", initialize=AsyncMock(), close=AsyncMock(),
    )
    monkeypatch.setattr("app.main.Neo4jEvidenceStore.connect", lambda *args: evidence)
    monkeypatch.setattr("app.main.Neo4jResearchStore.initialize", AsyncMock(side_effect=OSError()))
    async with lifespan(app):
        assert app.state.evidence_store is evidence
        assert app.state.research_store is None
    evidence.close.assert_awaited_once()
