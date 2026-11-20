#!/usr/bin/env bash
set -euo pipefail

# The dedicated recheck bundle intentionally omits django.config, which would
# otherwise ask EB to inject the full web secret set.  Fetch only the one
# secret needed to initialise Django on this worker.
if [[ ! -f /var/app/current/.platform/recheck-worker.marker ]]; then
  exit 0
fi

SECRET_DIR=/var/app/current/secrets
mkdir -p "$SECRET_DIR"
/usr/bin/aws ssm get-parameter --with-decryption \
  --name /interoves/production/DJANGO_SECRET_KEY \
  --query Parameter.Value --output text --region eu-central-1 \
  >"$SECRET_DIR/django_secret_key.txt"
chown webapp:webapp "$SECRET_DIR/django_secret_key.txt"
chmod 0600 "$SECRET_DIR/django_secret_key.txt"
