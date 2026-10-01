from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


@pytest.mark.asyncio
@pytest.mark.parametrize("opt_in", ["false", "true"])
async def test_proposal_key_cannot_enable_unmetered_production_graphiti(monkeypatch, opt_in):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NEO4J_URI", "bolt://test-db:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unit-test-only")
    monkeypatch.setenv("OPENAI_API_KEY", "proposal-only-test-key")
    monkeypatch.setenv("FGL_ENABLE_SEMANTIC_ENRICHMENT", opt_in)
    evidence = SimpleNamespace(
        driver=object(), database="neo4j", initialize=AsyncMock(), close=AsyncMock(),
    )
    connect = Mock()
    monkeypatch.setattr("app.main.Neo4jEvidenceStore.connect", lambda *args: evidence)
    monkeypatch.setattr("app.main.Neo4jResearchStore.initialize", AsyncMock())
    monkeypatch.setattr("app.main.GraphitiResearchStore.connect", connect)
    async with lifespan(app):
        assert app.state.evidence_store is evidence and app.state.semantic_store is None
    connect.assert_not_called()
