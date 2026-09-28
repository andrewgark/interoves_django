"""Safe, runtime-neutral worker configuration validation.

Values are intentionally read from the process environment only.  The next
configuration phase can populate that environment from Secrets Manager or SSM
without changing worker code or exposing secret values in deployment bundles.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from games.worker_contract import WORKER_REGISTRY, WorkerSpec


MODE_ALIASES = {
    "eb-worker": "eb",
    "fargate": "ecs-fargate",
    "fargate-spot": "ecs-fargate-spot",
}


@dataclass(frozen=True)
class WorkerRuntimeConfig:
    worker: WorkerSpec
    mode: str
    profile: str
    missing: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.missing


def infer_worker_name(environ=None) -> str:
    if environ is None:
        environ = os.environ
    explicit = (environ.get("INTEROVES_WORKER_NAME") or "").strip().lower()
    if explicit:
        return explicit
    role = (environ.get("INTEROVES_RUNTIME_ROLE") or "").strip().lower()
    by_role = {
        "background-worker": "background",
        "identity-worker": "identity",
        "integration-worker": "integrations",
        "worker": "recheck",
    }
    return by_role.get(role, "")


def normalize_mode(value: str | None) -> str:
    raw = (value or "eb").strip().lower()
    return MODE_ALIASES.get(raw, raw)


def load_worker_config(*, worker_name=None, mode=None, profile=None, environ=None):
    if environ is None:
        environ = os.environ
    name = (worker_name or infer_worker_name(environ)).strip().lower()
    if not name:
        raise ValueError(
            "worker is not configured; set INTEROVES_WORKER_NAME or "
            "INTEROVES_RUNTIME_ROLE"
        )
    worker = WORKER_REGISTRY.get(name)
    selected_mode = normalize_mode(
        mode or environ.get("INTEROVES_DEPLOYMENT_MODE") or "eb"
    )
    if selected_mode not in worker.allowed_modes:
        raise ValueError(
            "worker {} does not support deployment mode {}; supported: {}".format(
                name, selected_mode, ", ".join(worker.allowed_modes),
            )
        )
    selected_profile = (
        profile or environ.get("INTEROVES_CONFIG_PROFILE") or "production"
    ).strip()
    missing = tuple(
        name for name in worker.required_env
        if not (environ.get(name) or "").strip()
    )
    return WorkerRuntimeConfig(
        worker=worker,
        mode=selected_mode,
        profile=selected_profile,
        missing=missing,
    )
