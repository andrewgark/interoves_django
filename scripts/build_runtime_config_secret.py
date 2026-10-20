#!/usr/bin/env python
"""Copy the per-variable production secrets into one JSON runtime bundle.

Every ``interoves/production/<ENV_NAME>`` secret becomes one key of
``interoves/production/runtime-config``, which ``runtime_env`` loads through
``INTEROVES_CONFIG_SECRET_ID``.  One secret costs $0.40/month instead of one
per variable.  Secret values are never printed; the plan lists names only.

Usage: build_runtime_config_secret.py [--apply] [--region eu-central-1]
"""

import argparse
import json
import re
import sys

import boto3


PREFIX = "interoves/production/"
BUNDLE_NAME = PREFIX + "runtime-config"
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def per_variable_secrets(client):
    names = []
    for page in client.get_paginator("list_secrets").paginate(
        Filters=[{"Key": "name", "Values": [PREFIX]}]
    ):
        for secret in page["SecretList"]:
            name = secret["Name"]
            if name.startswith(PREFIX) and ENV_NAME.match(name[len(PREFIX):]):
                names.append(name)
    return sorted(names)


def current_bundle(client):
    try:
        raw = client.get_secret_value(SecretId=BUNDLE_NAME)["SecretString"]
    except client.exceptions.ResourceNotFoundException:
        return None
    return json.loads(raw)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args(argv)

    client = boto3.client("secretsmanager", region_name=args.region)
    names = per_variable_secrets(client)
    if not names:
        raise SystemExit("No per-variable secrets under " + PREFIX)
    values = {}
    for name in names:
        raw = client.get_secret_value(SecretId=name).get("SecretString")
        if raw is None:
            raise SystemExit("Secret has no SecretString: " + name)
        values[name[len(PREFIX):]] = raw

    existing = current_bundle(client)
    if existing is None:
        print("bundle=missing")
    else:
        added = sorted(set(values) - set(existing))
        removed = sorted(set(existing) - set(values))
        changed = sorted(k for k in set(values) & set(existing) if values[k] != existing[k])
        print("bundle=present added={} removed={} changed={}".format(added, removed, changed))
        if not (added or removed or changed):
            print("up_to_date=true")
            return
    print("keys={} {}".format(len(values), " ".join(sorted(values))))
    if not args.apply:
        print("plan_only=true (pass --apply to write {})".format(BUNDLE_NAME))
        return

    payload = json.dumps(values, separators=(",", ":"), sort_keys=True)
    if existing is None:
        arn = client.create_secret(
            Name=BUNDLE_NAME,
            Description="Interoves production runtime environment (JSON object of env vars).",
            SecretString=payload,
            Tags=[{"Key": "Service", "Value": "interoves"}],
        )["ARN"]
    else:
        arn = client.put_secret_value(SecretId=BUNDLE_NAME, SecretString=payload)["ARN"]
    print("written={}".format(arn))


if __name__ == "__main__":
    main(sys.argv[1:])
