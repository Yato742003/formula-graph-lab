"""Reproducible local benchmark for arXiv extraction, AST parsing, and Docker sandbox latency.

Writes `reports/benchmark-summary.json`. Designed to run offline against the frozen
14-paper arXiv excerpt corpus and (optionally) a pinned `Dockerfile.sandbox` image.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH_API_DIR = ROOT / "services" / "graph-api"
CORPUS_DIR = GRAPH_API_DIR / "tests" / "fixtures" / "corpus"
RETRIEVAL_AB_PATH = ROOT / "reports" / "phase5-retrieval-ab.json"
DEFAULT_OUT = ROOT / "reports" / "benchmark-summary.json"

sys.path.insert(0, str(GRAPH_API_DIR))

from app.extractor import extract_paper  # noqa: E402
from app.formula_ast import FormulaParseError, ast_node_count, parse_formula  # noqa: E402
from app.numerical_worker import SUITE_VERSION  # noqa: E402
from app.sandbox import run_numerical_sandbox, run_sandbox  # noqa: E402
from app.symbol_contracts import ContractReview, ReviewedContractValue  # noqa: E402
from app.verification import run_symbolic_check  # noqa: E402


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


def benchmark_corpus() -> dict[str, object]:
    expected_entries = json.loads((CORPUS_DIR / "expected.json").read_text(encoding="utf-8"))
    papers: list[dict[str, object]] = []
    extract_ms_all: list[float] = []
    parse_ms_all: list[float] = []
    expected_eq_total = 0
    extracted_eq_total = 0
    anchor_exact_matches = 0
    ast_supported = 0
    ast_rejected: list[dict[str, str]] = []

    for entry in expected_entries:
        paper_id = entry["paper_id"]
        source_url = entry["source_url"]
        html_text = (CORPUS_DIR / f"{paper_id}.html").read_text(encoding="utf-8")
        t0 = time.perf_counter()
        paper = extract_paper(html_text, source_url)
        extract_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        extract_ms_all.append(extract_ms)

        expected_anchors = [eq["anchor"] for eq in entry["equations"]]
        extracted_anchors = [eq.anchor for eq in paper.equations]
        expected_eq_total += len(expected_anchors)
        extracted_eq_total += len(extracted_anchors)
        anchor_exact_matches += sum(1 for a in expected_anchors if a in extracted_anchors)

        paper_ast_ok = 0
        paper_ast_fail = 0
        for eq in paper.equations:
            p0 = time.perf_counter()
            try:
                parsed = parse_formula(eq.latex, source_format="latex")
                parse_ms_all.append((time.perf_counter() - p0) * 1000.0)
                ast_node_count(parsed.root)
                ast_supported += 1
                paper_ast_ok += 1
            except FormulaParseError as exc:
                parse_ms_all.append((time.perf_counter() - p0) * 1000.0)
                paper_ast_fail += 1
                ast_rejected.append({
                    "paper_id": paper_id,
                    "anchor": eq.anchor,
                    "code": exc.code,
                })

        papers.append({
            "paper_id": paper_id,
            "expected_equations": len(expected_anchors),
            "extracted_equations": len(extracted_anchors),
            "extract_ms": extract_ms,
            "ast_supported": paper_ast_ok,
            "ast_fail_closed": paper_ast_fail,
            "warnings": sorted({w for eq in paper.equations for w in eq.warnings}),
        })

    return {
        "scope": "frozen_14_arxiv_html_excerpts_offline_regression_not_arxiv_wide_eval",
        "papers_total": len(papers),
        "expected_equations_total": expected_eq_total,
        "extracted_equations_total": extracted_eq_total,
        "anchor_match_rate": round(
            anchor_exact_matches / expected_eq_total if expected_eq_total else 1.0, 4
        ),
        "ast_supported_equations": ast_supported,
        "ast_fail_closed_equations": len(ast_rejected),
        "ast_parse_rate": round(
            ast_supported / extracted_eq_total if extracted_eq_total else 1.0, 4
        ),
        "extract_ms_median": round(statistics.median(extract_ms_all), 2),
        "extract_ms_p95": _percentile(extract_ms_all, 95),
        "ast_parse_ms_median": round(statistics.median(parse_ms_all), 2) if parse_ms_all else 0.0,
        "ast_parse_ms_p95": _percentile(parse_ms_all, 95),
        "ast_rejected_samples": ast_rejected,
        "papers": papers,
    }


def _scalar_review(symbol: str, *, domain: str = "real") -> ContractReview:
    return ContractReview(
        review_id=f"bench_rev_{symbol}",
        symbol_name=symbol,
        reviewer_id="benchmark_harness",
        reviewer_role="reviewer",
        decision="accepted",
        scope="benchmark:eq1",
        reviewed_contract=ReviewedContractValue(
            name=symbol,
            category="scalar",
            shape=(),
            domain=domain,  # type: ignore[arg-type]
        ),
        reviewed_at=datetime(2026, 11, 25, tzinfo=UTC),
    )


def benchmark_sandbox(image: str, *, symbolic_runs: int, numerical_runs: int) -> dict[str, object]:
    def docker_symbolic_runner(payload: dict[str, object], timeout_ms: int) -> dict[str, object]:
        return run_sandbox(payload, timeout_ms=timeout_ms, image=image)

    reviewed_scalars = (_scalar_review("a"), _scalar_review("b"), _scalar_review("x"))
    worked_cases_spec = [
        ("unreviewed_symbol_fail_closed", r"(a + b) - b", r"a", (), None),
        (
            "reviewed_algebraic_identity_supported",
            r"(a + b) - b",
            r"a",
            reviewed_scalars,
            "benchmark:eq1",
        ),
        (
            "reviewed_constant_mismatch_refuted",
            r"(a + 1) + 2",
            r"a + 4",
            reviewed_scalars,
            "benchmark:eq1",
        ),
        (
            "reviewed_real_denominator_conditional",
            r"\frac{1}{x}",
            r"x^{-1}",
            reviewed_scalars,
            "benchmark:eq1",
        ),
    ]
    worked_examples: list[dict[str, object]] = []
    for label, formula_a, formula_b, reviews, scope in worked_cases_spec:
        t0 = time.perf_counter()
        result = run_symbolic_check(
            formula_a,
            formula_b,
            source_format="latex",
            timeout_ms=5000,
            worker_runner=docker_symbolic_runner,
            contract_reviews=reviews,
            contract_review_scope=scope,
        )
        worked_examples.append({
            "case": label,
            "formula_a": formula_a,
            "formula_b": formula_b,
            "reviewed_symbols": [r.symbol_name for r in reviews],
            "outcome": result.outcome,
            "vector": result.vector.model_dump(mode="json"),
            "error_code": result.error_code,
            "witness": result.witness,
            "counterexample": result.counterexample,
            "wall_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

    sym_latencies: list[float] = []
    for _ in range(symbolic_runs):
        t0 = time.perf_counter()
        res = run_sandbox(
            {"formula_a": r"(a + b) - b", "formula_b": r"a", "format": "latex"},
            timeout_ms=5000,
            image=image,
        )
        assert res["outcome"] == "supported", res
        sym_latencies.append(round((time.perf_counter() - t0) * 1000.0, 1))

    num_latencies: list[float] = []
    for i in range(numerical_runs):
        t0 = time.perf_counter()
        res = run_numerical_sandbox(
            {
                "suite_version": SUITE_VERSION,
                "candidate_hash": "b" * 64,
                "seed": 17 + i,
                "dtype": "float32",
                "lambda": 0.37,
                "left_rank": 3,
                "right_rank": 5,
            },
            timeout_ms=5000,
            image=image,
        )
        assert res["outcome"] == "passed_suite", res
        num_latencies.append(round((time.perf_counter() - t0) * 1000.0, 1))

    return {
        "image_digest": image,
        "worked_examples": worked_examples,
        "symbolic_ephemeral_container_ms": {
            "runs": symbolic_runs,
            "samples_ms": sym_latencies,
            "min_ms": min(sym_latencies),
            "median_ms": round(statistics.median(sym_latencies), 1),
            "p95_ms": _percentile(sym_latencies, 95),
        },
        "numerical_ephemeral_container_ms": {
            "runs": numerical_runs,
            "samples_ms": num_latencies,
            "min_ms": min(num_latencies),
            "median_ms": round(statistics.median(num_latencies), 1),
            "p95_ms": _percentile(num_latencies, 95),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FormulaGraph Lab empirical benchmarks.")
    parser.add_argument("--sandbox-image", default="", help="Pinned sha256:... sandbox image ID.")
    parser.add_argument("--symbolic-runs", type=int, default=5)
    parser.add_argument("--numerical-runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus": benchmark_corpus(),
    }
    if args.sandbox_image:
        summary["sandbox"] = benchmark_sandbox(
            args.sandbox_image,
            symbolic_runs=args.symbolic_runs,
            numerical_runs=args.numerical_runs,
        )
    if RETRIEVAL_AB_PATH.exists():
        ab = json.loads(RETRIEVAL_AB_PATH.read_text(encoding="utf-8"))
        summary["retrieval_ab_manifest_hash"] = ab.get("manifest_hash")
        summary["retrieval_ab_promotion"] = ab.get("promotion")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote benchmark summary to {args.out}")
    corpus = summary["corpus"]
    print(
        f"Corpus: {corpus['papers_total']} papers, "
        f"{corpus['extracted_equations_total']}/{corpus['expected_equations_total']} equations "
        f"(anchor match {corpus['anchor_match_rate']*100:.1f}%), "
        f"AST parsed {corpus['ast_supported_equations']}/{corpus['extracted_equations_total']} "
        f"({corpus['ast_parse_rate']*100:.1f}%), "
        f"extract median={corpus['extract_ms_median']}ms"
    )
    if "sandbox" in summary:
        sb = summary["sandbox"]
        print(
            f"Sandbox symbolic ({sb['symbolic_ephemeral_container_ms']['runs']} runs): "
            f"median={sb['symbolic_ephemeral_container_ms']['median_ms']}ms, "
            f"p95={sb['symbolic_ephemeral_container_ms']['p95_ms']}ms"
        )
        print(
            f"Sandbox numerical ({sb['numerical_ephemeral_container_ms']['runs']} runs): "
            f"median={sb['numerical_ephemeral_container_ms']['median_ms']}ms, "
            f"p95={sb['numerical_ephemeral_container_ms']['p95_ms']}ms"
        )
        for ex in sb["worked_examples"]:
            print(
                f"  Example [{ex['case']}]: {ex['formula_a']} vs {ex['formula_b']} "
                f"-> outcome={ex['outcome']} domain={ex['vector']['domain']} "
                f"error={ex['error_code']} wall={ex['wall_ms']}ms"
            )


if __name__ == "__main__":
    main()
