"""Opt-in runtime environment loading for all worker deployment modes.

The deployment supplies either ``INTEROVES_CONFIG_SECRET_ID`` for one JSON
bundle or ``INTEROVES_CONFIG_SECRET_MAP`` for the current per-variable AWS
Secrets Manager layout. Explicit environment variables always win, which
keeps local development and the current EB configuration backwards compatible.
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

    The bundle secret contains a flat JSON object or a mapping keyed by
    ``INTEROVES_CONFIG_PROFILE``. The map contains environment names mapped to
    individual Secrets Manager IDs. Secret values are never logged.
    """
    if environ is None:
        environ = os.environ
    secret_id = (environ.get("INTEROVES_CONFIG_SECRET_ID") or "").strip()
    secret_map_raw = (environ.get("INTEROVES_CONFIG_SECRET_MAP") or "").strip()
    if not secret_id and not secret_map_raw:
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
    profile = (environ.get("INTEROVES_CONFIG_PROFILE") or "production").strip()
    values = {}
    if secret_id:
        values.update(_load_bundle(client, secret_id, profile))
    if secret_map_raw:
        values.update(_load_secret_map(client, secret_map_raw))
    loaded = _apply_values(environ, values)
    return {"source": "secrets-manager", "loaded": loaded, "profile": profile}


def _load_bundle(client, secret_id, profile):
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
    return _profile_values(payload, profile)


def _load_secret_map(client, raw_map):
    try:
        secret_map = json.loads(raw_map)
    except (TypeError, ValueError) as exc:
        raise RuntimeEnvironmentError(
            "INTEROVES_CONFIG_SECRET_MAP must be valid JSON"
        ) from exc
    if not isinstance(secret_map, Mapping):
        raise RuntimeEnvironmentError("INTEROVES_CONFIG_SECRET_MAP must be a JSON object")
    values = {}
    for name, secret_id in secret_map.items():
        if not isinstance(name, str) or not isinstance(secret_id, str) or not secret_id.strip():
            raise RuntimeEnvironmentError("runtime secret map contains an invalid entry")
        try:
            response = client.get_secret_value(SecretId=secret_id.strip())
            raw = response.get("SecretString")
            if raw is None:
                raise RuntimeEnvironmentError(
                    "runtime secret {} has no SecretString".format(name)
                )
            values[name] = raw
        except RuntimeEnvironmentError:
            raise
        except Exception as exc:
            raise RuntimeEnvironmentError(
                "could not load runtime secret {} from Secrets Manager".format(name)
            ) from exc
    return values


def _apply_values(environ, values):
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
    return loaded


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
