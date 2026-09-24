#!/usr/bin/env bash
set -euo pipefail

if [[ "${INTEROVES_RUNTIME_ROLE:-}" != "worker" ]]; then
  exit 0
fi

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

[Service]
Type=simple
User=webapp
WorkingDirectory=/var/app/current
EnvironmentFile=-/opt/elasticbeanstalk/deploy/configuration/containerconfiguration
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
