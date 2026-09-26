#!/usr/bin/env bash
set -euo pipefail

if [[ "${INTEROVES_RUNTIME_ROLE:-}" != "worker" ]]; then
  exit 0
fi

DISPATCHER_ENABLED="${WORD_SALAD_DISPATCHER_ENABLED:-false}"
case "${DISPATCHER_ENABLED,,}" in
  true|1|yes|on) ;;
  *)
    # Safe default: a newly provisioned Worker must not publish DB outbox work
    # until migrations and reconciliation have been explicitly completed.
    systemctl disable --now interoves-word-salad-dispatcher.service 2>/dev/null || true
    exit 0
    ;;
esac

cat >/usr/local/bin/interoves-word-salad-dispatcher <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
APP=/var/app/current
PYTHON=$(find -L /var/app/venv -path '*/bin/python' -print -quit)
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "validation/worker dispatcher: Python executable not found" >&2
  exit 1
fi
exec "$PYTHON" "$APP/manage.py" dispatch_word_salad_recheck_outbox_loop --limit "${WORD_SALAD_OUTBOX_DISPATCH_LIMIT:-25}" --interval "${WORD_SALAD_OUTBOX_DISPATCH_INTERVAL:-15}"
RUNNER
chmod 0755 /usr/local/bin/interoves-word-salad-dispatcher

cat >/etc/systemd/system/interoves-word-salad-dispatcher.service <<'UNIT'
[Unit]
Description=Inter Oves Word Salad transactional outbox dispatcher
After=network-online.target
Wants=network-online.target
Requires=web-secrets-populate.service
After=web-secrets-populate.service

[Service]
Type=simple
User=webapp
WorkingDirectory=/var/app/current
EnvironmentFile=/opt/elasticbeanstalk/deployment/env
EnvironmentFile=/opt/elasticbeanstalk/deployment/secrets/web
ExecStart=/usr/local/bin/interoves-word-salad-dispatcher
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now interoves-word-salad-dispatcher.service
