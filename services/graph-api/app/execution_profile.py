"""Controller-owned, versioned sandbox limits; never inferred for historical evidence."""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.security import worker_execution_limits

WorkerKind = Literal["symbolic", "numerical", "experiment", "performer", "research_case"]


class ExecutionProfile(BaseModel):
    policy_version: Literal["sandbox-policy.v2"] = "sandbox-policy.v2"
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    worker_kind: WorkerKind
    ram_bytes: int = Field(ge=64 * 1024**2, le=512 * 1024**2)
    cpu_cores: int = Field(ge=1, le=2)
    pids_limit: Literal[16] = 16
    timeout_cap_ms: int = Field(ge=1000, le=30_000)
    invocation_limit_ms: int = Field(ge=10, le=30_000)
    cpu_affinity: int | None = Field(default=None, ge=0)
    model_config = {"strict": True, "frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_limits(self):
        if (self.ram_bytes % 1024**2 or self.invocation_limit_ms > self.timeout_cap_ms
                or (self.worker_kind == "performer") != (self.cpu_affinity is not None)):
            raise ValueError("Invalid execution profile limits.")
        return self


def _affinity(worker_kind: WorkerKind) -> int | None:
    if worker_kind != "performer":
        return None
    return min(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else 0


def capture_execution_profile(
    image: str, worker_kind: WorkerKind, timeout_ms: int,
) -> ExecutionProfile:
    if type(timeout_ms) is not int or not 10 <= timeout_ms <= 30_000:
        raise ValueError("Invalid sandbox deadline.")
    limits = worker_execution_limits()
    return ExecutionProfile(
        image_id=image, worker_kind=worker_kind, ram_bytes=limits.ram_bytes,
        cpu_cores=limits.cpu_cores, timeout_cap_ms=limits.timeout_ms,
        invocation_limit_ms=min(timeout_ms, limits.timeout_ms),
        cpu_affinity=_affinity(worker_kind),
    )


def validate_live_profile(profile: ExecutionProfile, image: str, worker_kind: WorkerKind) -> None:
    """Reject drift, then launch using the snapshot (not another environment read)."""
    profile = ExecutionProfile.model_validate(profile.model_dump())
    limits = worker_execution_limits()
    if (profile.image_id != image or profile.worker_kind != worker_kind
            or profile.ram_bytes != limits.ram_bytes or profile.cpu_cores != limits.cpu_cores
            or profile.timeout_cap_ms != limits.timeout_ms
            or profile.cpu_affinity != _affinity(worker_kind)):
        raise ValueError("EXECUTION_PROFILE_DRIFT")
