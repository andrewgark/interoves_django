"""Runtime-neutral contracts shared by all worker launchers.

The contract deliberately knows nothing about Elastic Beanstalk, ECS or
Lambda.  Adapters translate their delivery format into :class:`WorkerMessage`
and translate :class:`WorkerOutcome` back into the platform's acknowledgement
semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol


class WorkerOutcome(str, Enum):
    """What a transport adapter should do with a delivered message."""

    ACK = "ack"
    RETRY = "retry"
    DROP = "drop"


@dataclass(frozen=True)
class WorkerMessage:
    """Normalized message independent of the delivery platform."""

    message_id: str
    body: Mapping[str, Any]
    receive_count: int = 1
    sent_at: datetime | None = None
    deadline: datetime | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)


class WorkerHandler(Protocol):
    def __call__(self, message: WorkerMessage) -> WorkerOutcome:
        """Process one message and return its acknowledgement decision."""


@dataclass(frozen=True)
class WorkerSpec:
    """Declarative identity and deployment capabilities of one worker."""

    name: str
    runtime_role: str
    queue_name: str
    endpoint: str
    message_types: tuple[str, ...]
    allowed_modes: tuple[str, ...]
    queue_url_env: str
    required_env: tuple[str, ...] = ()


class WorkerRegistry:
    """Small immutable-by-convention registry used by adapters and tooling."""

    def __init__(self, specs: tuple[WorkerSpec, ...]):
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> WorkerSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ValueError("unknown worker: {}".format(name)) from exc

    def all(self) -> tuple[WorkerSpec, ...]:
        return tuple(self._specs.values())


WORKER_REGISTRY = WorkerRegistry((
    WorkerSpec(
        name="background",
        runtime_role="background-worker",
        queue_name="interoves-background",
        endpoint="/internal/worker/background/",
        message_types=(
            "difficulty.refresh",
            "difficulty.health_check",
            "projection.reconcile",
            "projection.refresh",
        ),
        allowed_modes=("eb", "ecs-fargate", "ecs-fargate-spot", "lambda", "green-process"),
        queue_url_env="BACKGROUND_SQS_QUEUE_URL",
    ),
    WorkerSpec(
        name="identity",
        runtime_role="identity-worker",
        queue_name="interoves-identity",
        endpoint="/internal/worker/identity/",
        message_types=("anonymous.merge", "anonymous.merge_reconcile"),
        allowed_modes=("eb", "ecs-fargate", "ecs-fargate-spot", "green-process"),
        queue_url_env="IDENTITY_SQS_QUEUE_URL",
        required_env=(
            "ANONYMOUS_MERGE_EVENTS",
            "ANONYMOUS_MERGE_SQS_QUEUE_URL",
        ),
    ),
    WorkerSpec(
        name="integrations",
        runtime_role="integration-worker",
        queue_name="interoves-integrations",
        endpoint="/internal/worker/integrations/",
        message_types=(
            "telegram.announcements",
            "telegram.admin_report",
            "instagram.token_refresh",
            "social.publish",
        ),
        allowed_modes=("eb", "ecs-fargate", "ecs-fargate-spot"),
        queue_url_env="INTEGRATIONS_SQS_QUEUE_URL",
        required_env=(
            "TELEGRAM_API_ID",
            "TELEGRAM_API_HASH",
            "TELEGRAM_BOT_TOKEN",
        ),
    ),
    WorkerSpec(
        name="recheck",
        runtime_role="worker",
        queue_name="interoves-recheck",
        endpoint="/internal/worker/recheck/",
        message_types=("word_salad.recheck",),
        allowed_modes=("eb", "ecs-fargate", "ecs-fargate-spot"),
        queue_url_env="RECHECK_SQS_QUEUE_URL",
    ),
))
