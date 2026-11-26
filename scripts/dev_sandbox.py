"""Launch the local API with session-only credentials for the Sites dev researcher."""
import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = root / ".env"
    if settings.exists():
        for line in settings.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                os.environ.setdefault(key, value.strip())
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.exit(1, f"API port {args.port} is occupied. Select another API port.\n")
    service = root / "services" / "graph-api"
    tag = "fgl-sandbox-local:latest"
    subprocess.run(["docker", "build", "-f", str(service / "Dockerfile.sandbox"),
                    "-t", tag, str(service)], check=True)
    digest = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                                     text=True).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise SystemExit("Could not resolve an immutable sandbox image.")
    workspace = "ws_" + hashlib.sha256(b"local_seedy").hexdigest()[:48]
    os.environ.update(FGL_SANDBOX_IMAGE=digest, FGL_ENABLE_RESEARCH_CHECKS="true")
    os.environ.setdefault("FGL_RESEARCH_QUEUE_SECRET", secrets.token_hex(32))
    os.environ.setdefault("FGL_WORKER_IDENTITIES", json.dumps([{
        "identity": "local-verifier", "role": "verifier", "token": secrets.token_hex(32),
        "workspaces": [workspace],
    }]))
    uvicorn.run("app.main:app", app_dir=str(service), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
