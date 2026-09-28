import os

import pytest

from app.experiment_harness import run_attention_synthetic_pilot
from app.experiment_worker import PROTOCOL_VERSION, SEEDS, evaluate

IMAGE = "sha256:" + "a" * 64


def test_pilot_freezes_scope_runs_every_seed_and_reports_uncertainty():
    calls = []

    def runner(payload, *, timeout_ms, image):
        calls.append((payload, timeout_ms, image))
        return evaluate(payload["seed"])

    report = run_attention_synthetic_pilot(image=IMAGE, worker_runner=runner)

    assert len(report["protocol_hash"]) == 64
    assert report["report_hash"]
    assert report["run_status"] == "completed"
    assert report["empirical_outcome"] == "not_run"
    assert report["protocol"]["claims"]["empirical_vector"] == "not_run"
    assert report["scope"] == "synthetic_operator_diagnostic_no_performance_claim"
    assert not {"candidate_id", "candidate_hash", "parent_refs"} & report.keys()
    assert [item["seed"] for item in report["trials"]] == list(SEEDS)
    assert [call[0]["protocol_version"] for call in calls] == [PROTOCOL_VERSION] * len(SEEDS)
    assert all(call[1] <= 5_000 and call[2] == IMAGE for call in calls)
    assert report["search_cost"]["cost_usd"] == 0
    assert report["search_cost"]["completed_trial_count"] == len(SEEDS)
    for length in ("16", "64", "256"):
        stats = report["summary"][length]["candidate_mean_abs_error_vs_exact"]
        assert stats["n"] == len(SEEDS)
        assert stats["ci95_low"] <= stats["mean"] <= stats["ci95_high"]


def test_infrastructure_failure_is_retained_and_never_filled_as_zero():
    def runner(payload, *, timeout_ms, image):
        if payload["seed"] == SEEDS[0]:
            return evaluate(payload["seed"])
        return {"outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT"}

    report = run_attention_synthetic_pilot(image=IMAGE, worker_runner=runner)

    assert report["run_status"] == "incomplete"
    assert report["summary"] is None
    assert report["trials"][1] == {
        "seed": SEEDS[1], "outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT",
    }
    assert report["search_cost"]["completed_trial_count"] == 1


def test_worker_exception_is_recorded_and_later_seeds_still_run():
    attempted = []

    def runner(payload, *, timeout_ms, image):
        attempted.append(payload["seed"])
        if payload["seed"] == SEEDS[1]:
            raise RuntimeError("worker transport failed")
        return evaluate(payload["seed"])

    report = run_attention_synthetic_pilot(image=IMAGE, worker_runner=runner)

    assert attempted == list(SEEDS)
    assert report["run_status"] == "incomplete"
    assert report["summary"] is None
    assert report["trials"][1] == {
        "seed": SEEDS[1],
        "outcome": "error",
        "error_code": "EXPERIMENT_WORKER_FAILED",
    }
    assert report["search_cost"]["completed_trial_count"] == len(SEEDS) - 1


@pytest.mark.parametrize("bad_result", [None, {"unexpected": float("nan")}])
def test_malformed_worker_output_is_recorded_and_later_seeds_still_run(bad_result):
    attempted = []

    def runner(payload, *, timeout_ms, image):
        attempted.append(payload["seed"])
        if payload["seed"] == SEEDS[1]:
            return bad_result
        return evaluate(payload["seed"])

    report = run_attention_synthetic_pilot(image=IMAGE, worker_runner=runner)

    assert attempted == list(SEEDS)
    assert report["run_status"] == "incomplete"
    assert report["summary"] is None
    assert report["trials"][1] == {
        "seed": SEEDS[1],
        "outcome": "error",
        "error_code": "INVALID_EXPERIMENT_WORKER_RESPONSE",
    }
    assert report["search_cost"]["completed_trial_count"] == len(SEEDS) - 1


@pytest.mark.sandbox
def test_frozen_cpu_pilot_runs_in_the_networkless_container():
    image = os.getenv("TEST_SANDBOX_IMAGE")
    if not image:
        pytest.skip("Run test-sandbox.ps1 to build the pinned worker image.")

    report = run_attention_synthetic_pilot(image=image)

    assert report["run_status"] == "completed"
    assert report["empirical_outcome"] == "not_run"
    assert report["search_cost"]["completed_trial_count"] == len(SEEDS)
