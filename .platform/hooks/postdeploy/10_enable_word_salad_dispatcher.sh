#!/usr/bin/env bash
set -euo pipefail

# EB application settings are available to systemd through this file, but are
# not guaranteed to be exported in the platform-hook shell environment.
if [[ -r /opt/elasticbeanstalk/deployment/env ]]; then
  set -a
  # shellcheck disable=SC1091
  . /opt/elasticbeanstalk/deployment/env
  set +a
fi

WORKER_BUNDLE_MARKER=/var/app/current/.platform/recheck-worker.marker
if [[ -f "$WORKER_BUNDLE_MARKER" ]]; then
  INTEROVES_RUNTIME_ROLE=worker
  RECHECK_DISPATCHER_ENABLED=true
fi
if [[ "${FORCE_RECHECK_DISPATCHER:-}" == "true" ]]; then
  INTEROVES_RUNTIME_ROLE=worker
  RECHECK_DISPATCHER_ENABLED=true
fi
printf 'role=%q enabled=%q force=%q marker=%s\n' \
  "${INTEROVES_RUNTIME_ROLE:-}" "${RECHECK_DISPATCHER_ENABLED:-}" \
  "${FORCE_RECHECK_DISPATCHER:-}" "$WORKER_BUNDLE_MARKER" \
  >>/var/log/app/recheck-dispatcher-hook.log

# The dispatcher is enabled explicitly per EB environment.  Some worker
# environments do not expose INTEROVES_RUNTIME_ROLE to platform hooks, so an
# absent role must not silently disable the service.  Only an explicit web
# role is forbidden from installing the dispatcher.
if [[ "${INTEROVES_RUNTIME_ROLE:-}" == "web" && ! -f "$WORKER_BUNDLE_MARKER" ]]; then
  exit 0
fi

DISPATCHER_ENABLED="${RECHECK_DISPATCHER_ENABLED:-${WORD_SALAD_DISPATCHER_ENABLED:-false}}"
case "${DISPATCHER_ENABLED,,}" in
  true|1|yes|on)
    echo 'dispatcher branch=enabled' >>/var/log/app/recheck-dispatcher-hook.log
    ;;
  *)
    echo 'dispatcher branch=disabled' >>/var/log/app/recheck-dispatcher-hook.log
    # Safe default: a newly provisioned Worker must not publish DB outbox work
    # until migrations and reconciliation have been explicitly completed.
    systemctl disable --now interoves-recheck-dispatcher.service 2>/dev/null || true
    systemctl disable --now interoves-word-salad-dispatcher.service 2>/dev/null || true
    exit 0
    ;;
esac

cat >/usr/local/bin/interoves-recheck-dispatcher <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
APP=/var/app/current
PYTHON=$(find -L /var/app/venv -path '*/bin/python' -print -quit)
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "validation/worker dispatcher: Python executable not found" >&2
  exit 1
fi
exec "$PYTHON" "$APP/manage.py" dispatch_recheck_outbox_loop --limit "${RECHECK_OUTBOX_DISPATCH_LIMIT:-${WORD_SALAD_OUTBOX_DISPATCH_LIMIT:-25}}" --interval "${RECHECK_OUTBOX_DISPATCH_INTERVAL:-${WORD_SALAD_OUTBOX_DISPATCH_INTERVAL:-15}}"
RUNNER
chmod 0755 /usr/local/bin/interoves-recheck-dispatcher

cat >/etc/systemd/system/interoves-recheck-dispatcher.service <<'UNIT'
[Unit]
Description=Inter Oves recheck transactional outbox dispatcher
After=network-online.target
Wants=network-online.target
Requires=web-secrets-populate.service
After=web-secrets-populate.service

[Service]
Type=simple
User=webapp
WorkingDirectory=/var/app/current
EnvironmentFile=-/opt/elasticbeanstalk/deployment/env
EnvironmentFile=-/opt/elasticbeanstalk/deployment/secrets/web
ExecStart=/usr/local/bin/interoves-recheck-dispatcher
StandardOutput=append:/var/log/app/recheck-dispatcher.log
StandardError=append:/var/log/app/recheck-dispatcher.log
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl disable --now interoves-word-salad-dispatcher.service 2>/dev/null || true
systemctl enable --now interoves-recheck-dispatcher.service
