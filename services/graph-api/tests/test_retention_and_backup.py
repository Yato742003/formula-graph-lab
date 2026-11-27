"""Offline safety tests for Neo4j transient retention pruning and policy invariants."""

from __future__ import annotations

import pytest

from app.retention import (
    IMMUTABLE_PROTECTED_LABELS,
    RETENTION_POLICY_VERSION,
    RETENTION_TARGETS,
    TRANSIENT_PRUNABLE_LABELS,
    assert_prunable_label,
    build_transient_prune_queries,
    prune_expired_transient_records,
)


def test_transient_and_protected_labels_are_strictly_disjoint():
    assert frozenset({"ServiceRequestNonce", "WorkspaceRate"}) == TRANSIENT_PRUNABLE_LABELS
    assert TRANSIENT_PRUNABLE_LABELS.isdisjoint(IMMUTABLE_PROTECTED_LABELS)
    assert RETENTION_TARGETS["policy_version"] == RETENTION_POLICY_VERSION
    assert RETENTION_TARGETS["evidence_and_reviews_retention"] == "indefinite_append_only"


@pytest.mark.parametrize("label", sorted(IMMUTABLE_PROTECTED_LABELS))
def test_assert_prunable_label_rejects_every_protected_label(label: str):
    with pytest.raises(ValueError, match="protected or unrecognized"):
        assert_prunable_label(label)


@pytest.mark.parametrize(
    "bad_label",
    ["", "Node", "Evidence", "User", "ServiceRequestNonce:Evidence"],
)
def test_assert_prunable_label_rejects_unrecognized_labels(bad_label: str):
    with pytest.raises(ValueError, match="protected or unrecognized"):
        assert_prunable_label(bad_label)


def test_build_transient_prune_queries_only_targets_allowlisted_labels():
    queries = build_transient_prune_queries()
    assert len(queries) == 2
    labels = {label for label, _ in queries}
    assert labels == TRANSIENT_PRUNABLE_LABELS
    for _label, cypher in queries:
        assert "DETACH DELETE" not in cypher.upper()
        assert "LIMIT $batch_limit" in cypher
        for protected in IMMUTABLE_PROTECTED_LABELS:
            assert f":{protected}" not in cypher


class _FakeResult:
    def __init__(self, deleted: int) -> None:
        self._deleted = deleted

    async def single(self):
        return {"deleted": self._deleted}


class _FakeSession:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.executed: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def run(self, query: str, **params):
        self.executed.append((query, params))
        if ":ServiceRequestNonce" in query:
            return _FakeResult(self._counts["ServiceRequestNonce"])
        if ":WorkspaceRate" in query:
            return _FakeResult(self._counts["WorkspaceRate"])
        raise AssertionError(f"Unexpected retention query: {query}")


class _FakeDriver:
    def __init__(self, counts: dict[str, int]) -> None:
        self.session_obj = _FakeSession(counts)

    def session(self, database: str | None = None):
        _ = database
        return self.session_obj


@pytest.mark.asyncio
async def test_prune_expired_transient_records_runs_bounded_allowlisted_queries():
    driver = _FakeDriver({"ServiceRequestNonce": 4, "WorkspaceRate": 2})
    result = await prune_expired_transient_records(
        driver,
        cutoff_epoch_s=1_700_000_000,
        now_ms=1_700_000_300_000,
        batch_limit=250,
    )
    assert result == {
        "deleted_service_nonces": 4,
        "deleted_workspace_rates": 2,
    }
    assert len(driver.session_obj.executed) == 2
    assert driver.session_obj.executed[0][1] == {
        "cutoff_epoch_s": 1_700_000_000,
        "batch_limit": 250,
    }
    assert driver.session_obj.executed[1][1] == {
        "now_ms": 1_700_000_300_000,
        "batch_limit": 250,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_limit", [0, -1, 5001, True])
async def test_prune_expired_transient_records_rejects_out_of_bounds_batch_limit(bad_limit):
    driver = _FakeDriver({"ServiceRequestNonce": 0, "WorkspaceRate": 0})
    with pytest.raises(ValueError, match="batch_limit"):
        await prune_expired_transient_records(driver, batch_limit=bad_limit)
