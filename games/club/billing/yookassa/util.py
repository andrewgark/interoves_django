"""YooKassa SDK configuration. Credentials: env vars (prod) or secrets/*.txt under BASE_DIR (local)."""
from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from yookassa import Configuration


def configure_yookassa_from_env() -> None:
    """
    Elastic Beanstalk: set YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY in environment
    (Configuration → Software → Environment properties). The secrets/ folder is not deployed (gitignored).
    """
    shop_id = os.environ.get("YOOKASSA_SHOP_ID") or os.environ.get("YOO_KASSA_SHOP_ID")
    secret_key = os.environ.get("YOOKASSA_SECRET_KEY") or os.environ.get("YOO_KASSA_SECRET_KEY")

    secrets_dir = Path(settings.BASE_DIR) / "secrets"
    if not shop_id:
        try:
            shop_id = (secrets_dir / "yookassa_shop_id.txt").read_text(encoding="utf-8").strip()
        except OSError:
            shop_id = None
    if not secret_key:
        try:
            secret_key = (secrets_dir / "yookassa_secret_key.txt").read_text(encoding="utf-8").strip()
        except OSError:
            secret_key = None

    if not shop_id or not secret_key:
        raise RuntimeError(
            "Missing YooKassa credentials: set YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY on the server "
            "or add secrets/yookassa_shop_id.txt and secrets/yookassa_secret_key.txt under BASE_DIR."
        )
    Configuration.configure(shop_id, secret_key)
