"""Validate and replay an exported compiler bundle without network access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.replay_bundle import MAX_BUNDLE_BYTES, CompilerReplayBundle
from app.research_report import build_compiler_replay_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="path to compiler-replay-bundle.v1 JSON")
    args = parser.parse_args()

    try:
        with args.bundle.open("rb") as source:
            raw = source.read(MAX_BUNDLE_BYTES + 1)
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("Replay bundle exceeds the export size limit.")
        bundle = CompilerReplayBundle.model_validate_json(raw)
        report = build_compiler_replay_report(bundle)
    except (OSError, ValidationError, ValueError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        return 1

    print(json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
