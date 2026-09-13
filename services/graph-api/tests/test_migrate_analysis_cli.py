from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app import migrate_analysis


def test_missing_env_vars_exits_with_error(monkeypatch, capsys):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USER", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setattr(
        "sys.argv", ["app.migrate_analysis", "inventory", "--workspace", "ws-1"]
    )

    exit_code = migrate_analysis.main()
    assert exit_code == 1
    out, _ = capsys.readouterr()
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "Missing required environment variables" in payload["detail"]


def test_migrate_requires_run_id(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["app.migrate_analysis", "migrate", "--workspace", "ws-1"]
    )
    with pytest.raises(SystemExit):
        migrate_analysis.main()


def test_verify_requires_receipt(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["app.migrate_analysis", "verify", "--workspace", "ws-1"]
    )
    with pytest.raises(SystemExit):
        migrate_analysis.main()


def test_inventory_action_success(monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setattr(
        "sys.argv", ["app.migrate_analysis", "inventory", "--workspace", "ws-1"]
    )

    mock_store = AsyncMock()
    mock_store.close = AsyncMock()
    mock_migration = AsyncMock()
    mock_migration.inventory = AsyncMock(
        return_value={"total_equations": 5, "equations_with_analysis": 5}
    )

    with patch("app.migrate_analysis.Neo4jEvidenceStore.connect", return_value=mock_store), \
         patch("app.migrate_analysis.AnalysisMigration", return_value=mock_migration):
        exit_code = migrate_analysis.main()

    assert exit_code == 0
    out, _ = capsys.readouterr()
    result = json.loads(out.strip())
    assert result == {"equations_with_analysis": 5, "total_equations": 5}
    mock_store.close.assert_awaited_once()


def test_verify_action_success(monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setattr(
        "sys.argv",
        ["app.migrate_analysis", "verify", "--workspace", "ws-1", "--receipt", "rcpt-123"],
    )

    mock_store = AsyncMock()
    mock_store.close = AsyncMock()
    mock_migration = AsyncMock()
    mock_migration.verify_receipt = AsyncMock(
        return_value={"receipt_id": "rcpt-123", "verified": True}
    )

    with patch("app.migrate_analysis.Neo4jEvidenceStore.connect", return_value=mock_store), \
         patch("app.migrate_analysis.AnalysisMigration", return_value=mock_migration):
        exit_code = migrate_analysis.main()

    assert exit_code == 0
    out, _ = capsys.readouterr()
    result = json.loads(out.strip())
    assert result == {"receipt_id": "rcpt-123", "verified": True}
    mock_store.close.assert_awaited_once()
