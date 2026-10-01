from pathlib import Path

import yaml


def test_security_ci_and_deployment_keep_frozen_inputs_and_fail_closed_scan_gates():
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.load((root / ".github/workflows/security-gates.yml").read_text(),
                         Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                ref = step["uses"].split("@")[1].split(" ")[0]
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)
            assert "continue-on-error" not in step
    scans = " ".join(step.get("run", "") for step in workflow["jobs"]["images"]["steps"])
    assert "--exit-code 1" in scans and "--severity HIGH,CRITICAL" in scans
    assert "--ignore-unfixed" not in scans and "sha256:" in scans
    dockerfile = (root / "services/graph-api/Dockerfile").read_text()
    assert "COPY pyproject.toml uv.lock" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER 65534:65534" in dockerfile and '"--limit-concurrency", "32"' in dockerfile
    assert "GRAPHITI_TELEMETRY_ENABLED=false" in dockerfile
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert compose["services"]["graph-api"]["read_only"] is True
    for service in compose["services"].values():
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
