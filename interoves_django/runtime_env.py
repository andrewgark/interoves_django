"""Opt-in runtime environment loading for all worker deployment modes.

The deployment only supplies ``INTEROVES_CONFIG_SECRET_ID`` and a profile.
The process IAM role reads a JSON object from Secrets Manager.  Explicit
environment variables always win, which keeps local development and the
current EB configuration backwards compatible.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping


class RuntimeEnvironmentError(RuntimeError):
    pass


_RESERVED_ENV_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "DJANGO_SETTINGS_MODULE",
    "INTEROVES_CONFIG_SECRET_ID",
    "INTEROVES_CONFIG_PROFILE",
    "PYTHONPATH",
}


def load_runtime_environment(environ=None, *, client=None):
    """Populate missing environment keys from an opt-in Secrets Manager secret.

    The secret is expected to contain either a flat JSON object of environment
    values or a mapping keyed by ``INTEROVES_CONFIG_PROFILE``.  Secret values
    are never logged.  Returns a small metadata dict useful for diagnostics.
    """
    if environ is None:
        environ = os.environ
    secret_id = (environ.get("INTEROVES_CONFIG_SECRET_ID") or "").strip()
    if not secret_id:
        return {"source": "environment", "loaded": 0}

    if client is None:
        import boto3

        client = boto3.client(
            "secretsmanager",
            region_name=(
                environ.get("AWS_REGION")
                or environ.get("AWS_DEFAULT_REGION")
                or "eu-central-1"
            ),
        )
    try:
        response = client.get_secret_value(SecretId=secret_id)
        raw = response.get("SecretString")
        if not raw:
            raise RuntimeEnvironmentError(
                "runtime configuration secret has no SecretString"
            )
        payload = json.loads(raw)
    except RuntimeEnvironmentError:
        raise
    except Exception as exc:
        raise RuntimeEnvironmentError(
            "could not load runtime configuration from Secrets Manager"
        ) from exc

    profile = (environ.get("INTEROVES_CONFIG_PROFILE") or "production").strip()
    values = _profile_values(payload, profile)
    loaded = 0
    for name, value in values.items():
        if (
            not isinstance(name, str)
            or not name
            or not _is_env_name(name)
            or name in _RESERVED_ENV_NAMES
        ):
            raise RuntimeEnvironmentError(
                "runtime configuration contains an invalid environment name"
            )
        if not isinstance(value, (str, int, float, bool)):
            raise RuntimeEnvironmentError(
                "runtime configuration value for {} is not scalar".format(name)
            )
        if name not in environ:
            environ[name] = str(value)
            loaded += 1
    return {"source": "secrets-manager", "loaded": loaded, "profile": profile}


def _profile_values(payload, profile):
    if not isinstance(payload, Mapping):
        raise RuntimeEnvironmentError("runtime configuration secret must be a JSON object")
    profile_value = payload.get(profile)
    if profile_value is not None:
        if not isinstance(profile_value, Mapping):
            raise RuntimeEnvironmentError(
                "runtime configuration profile is not a JSON object"
            )
        return profile_value
    if any(isinstance(value, Mapping) for value in payload.values()):
        raise RuntimeEnvironmentError(
            "runtime configuration profile {} is missing".format(profile)
        )
    return payload


def _is_env_name(name):
    return all(part and (part[0].isalpha() or part[0] == "_") and all(
        char.isalnum() or char == "_" for char in part
    ) for part in (name,))
