"""Append-only advertising audit log. Never write secrets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from games.ads_direct.client import redact

AUDIT_RELATIVE = Path("ads") / "audit.jsonl"


def audit_path() -> Path:
    return Path(settings.BASE_DIR) / AUDIT_RELATIVE


def append_audit(
    *,
    action: str,
    object_type: str,
    object_id: Any = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str,
    metrics: dict | None = None,
    api_status: str | None = None,
    extra: dict | None = None,
) -> None:
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "object": object_type,
        "id": object_id,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "metrics": metrics or {},
        "api_status": api_status,
    }
    if extra:
        record["extra"] = extra
    line = redact(json.dumps(record, ensure_ascii=False))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
