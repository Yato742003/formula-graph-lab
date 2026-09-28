import hashlib
import json

from app import performer_pilot
from app.paper_artifact_manifest import (
    PERFORMER_COMMIT,
    PERFORMER_LICENSE_SHA256,
    PERFORMER_LICENSE_SIZE,
    PERFORMER_SOURCE_SHA256,
    PERFORMER_SOURCE_SIZE,
)
from app.paper_artifacts import ResolvedPerformerArtifact
from app.paper_attention_worker import PROTOCOL_VERSION, SEEDS, SEQUENCE_LENGTHS


def _artifact(source: bytes, license_text: bytes) -> ResolvedPerformerArtifact:
    return ResolvedPerformerArtifact(
        paper_id="arXiv:2009.14794v4",
        repository="google-research/google-research",
        commit=PERFORMER_COMMIT,
        path="performer/fast_self_attention/fast_self_attention.py",
        source_sha256=hashlib.sha256(source).hexdigest(),
        source=source,
        license_id="Apache-2.0",
        license_sha256=hashlib.sha256(license_text).hexdigest(),
        license_text=license_text,
    )


def _configure_fake_artifact(monkeypatch):
    source = b"test source"
    license_text = b"test license"
    monkeypatch.setattr(
        performer_pilot, "PERFORMER_SOURCE_SHA256", hashlib.sha256(source).hexdigest()
    )
    monkeypatch.setattr(performer_pilot, "PERFORMER_SOURCE_SIZE", len(source))
    monkeypatch.setattr(
        performer_pilot, "PERFORMER_LICENSE_SHA256", hashlib.sha256(license_text).hexdigest()
    )
    monkeypatch.setattr(performer_pilot, "PERFORMER_LICENSE_SIZE", len(license_text))
    monkeypatch.setattr(performer_pilot, "validate_performer_worker_result", json.loads)
    return _artifact(source, license_text)


def test_pilot_freezes_synthetic_scope_and_records_all_trial_failures(monkeypatch):
    artifact = _configure_fake_artifact(monkeypatch)
    calls = []

    def runner(payload, *, timeout_ms, image):
        calls.append((payload, timeout_ms, image))
        return {"outcome": "timeout", "error_code": "TEST_TIMEOUT"}

    report = performer_pilot.run_performer_synthetic_diagnostic(
        artifact=artifact,
        image="sha256:" + "a" * 64,
        worker_runner=runner,
    )

    assert len(calls) == len(SEEDS) * len(SEQUENCE_LENGTHS)
    assert all(call[1] <= performer_pilot.TRIAL_TIMEOUT_MS for call in calls)
    assert all(call[0]["protocol_version"] == PROTOCOL_VERSION for call in calls)
    assert [(item["seed"], item["sequence_length"]) for item in report["trials"]] == [
        (seed, length) for seed in SEEDS for length in SEQUENCE_LENGTHS
    ]
    assert all(item["outcome"] == "timeout" for item in report["trials"])
    assert report["empirical_outcome"] == "not_run"
    assert report["scope"] == "paper_implementation_diagnostic_no_performance_claim"
    assert report["search_cost"]["cost_usd"] == 0
    assert report["search_cost"]["completed_trial_count"] == 0
    assert len(report["report_hash"]) == 64


def test_completed_diagnostic_never_claims_empirical_support(monkeypatch):
    artifact = _configure_fake_artifact(monkeypatch)

    def runner(payload, *, timeout_ms, image):
        return {
            "outcome": "passed_suite",
            "protocol_version": PROTOCOL_VERSION,
            "implementation_commit": PERFORMER_COMMIT,
            "source_sha256": performer_pilot.PERFORMER_SOURCE_SHA256,
            "seed": payload["seed"],
            "sequence_length": payload["sequence_length"],
            "input_hash": "a" * 64,
            "checks": {"causal": True},
            "measurements": {"mean_abs_error_vs_exact": 0.01},
            "environment": {"backend": "cpu"},
        }

    report = performer_pilot.run_performer_synthetic_diagnostic(
        artifact=artifact,
        image="sha256:" + "a" * 64,
        worker_runner=runner,
    )

    assert report["run_status"] == "completed"
    assert report["search_cost"]["completed_trial_count"] == len(SEEDS) * len(SEQUENCE_LENGTHS)
    assert report["search_cost"]["candidate_count"] == 0
    assert report["search_cost"]["implementation_count"] == 1
    assert report["empirical_outcome"] == "not_run"


def test_counterexamples_are_retained_when_the_trial_matrix_completes(monkeypatch):
    artifact = _configure_fake_artifact(monkeypatch)

    def runner(payload, *, timeout_ms, image):
        return {
            "outcome": "counterexample",
            "protocol_version": PROTOCOL_VERSION,
            "implementation_commit": PERFORMER_COMMIT,
            "source_sha256": performer_pilot.PERFORMER_SOURCE_SHA256,
            "seed": payload["seed"],
            "sequence_length": payload["sequence_length"],
            "input_hash": "b" * 64,
            "checks": {"causal_future_extreme_key_perturbation": False},
            "measurements": {"causal_future_extreme_key_perturbation_max_abs_error": 0.5},
            "environment": {"backend": "cpu"},
        }

    report = performer_pilot.run_performer_synthetic_diagnostic(
        artifact=artifact,
        image="sha256:" + "a" * 64,
        worker_runner=runner,
    )

    assert report["run_status"] == "completed"
    assert report["empirical_outcome"] == "not_run"
    assert len(report["trials"]) == len(SEEDS) * len(SEQUENCE_LENGTHS)
    assert all(item["outcome"] == "counterexample" for item in report["trials"])
    assert all(
        item["checks"]["causal_future_extreme_key_perturbation"] is False
        for item in report["trials"]
    )


def test_pilot_rejects_unverified_source_or_license_bytes():
    artifact = _artifact(b"unverified source", b"unverified license")
    try:
        performer_pilot._artifact_source(artifact)
    except ValueError as exc:
        assert "must be verified" in str(exc)
    else:
        raise AssertionError("Unverified paper artifacts must not reach the worker.")


def test_pin_constants_are_explicit_for_report_provenance():
    assert len(PERFORMER_SOURCE_SHA256) == 64
    assert len(PERFORMER_LICENSE_SHA256) == 64
    assert PERFORMER_SOURCE_SIZE > 0
    assert PERFORMER_LICENSE_SIZE > 0
