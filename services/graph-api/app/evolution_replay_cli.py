"""python -m app.evolution_replay_cli campaign-bundle.json (no network/model)."""

import json
import sys
from pathlib import Path

from app.evolution import build_evolution_report
from app.evolution_replay import EvolutionReplayBundle, replay_evolution_bundle
from app.replay_bundle import MAX_BUNDLE_BYTES


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("One bounded bundle path is required.")
        with Path(sys.argv[1]).open("rb") as stream:
            raw = stream.read(MAX_BUNDLE_BYTES + 1)
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("Evolution bundle is too large.")
        campaign = replay_evolution_bundle(EvolutionReplayBundle.model_validate_json(raw))
        print(
            json.dumps(
                {
                    "replay": "reproduced",
                    "scope": "synthetic_operator_only_no_product_claim",
                    "report": build_evolution_report(campaign).model_dump(mode="json"),
                }
            )
        )
        return 0
    except (ValueError, OSError, KeyError, StopIteration, IndexError) as exc:
        print(json.dumps({"replay": "error", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
