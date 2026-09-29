# Worker runtime contract

Workers have one application contract and multiple delivery adapters.  The
domain handler must not know whether a message arrived through Elastic
Beanstalk `sqsd`, an ECS SQS poller, Lambda SQS event source mapping, or a
supervised process on Green.  The production queue/runtime map is maintained
in `docs/worker-queues.md`; check it before operating a worker.

The registry lives in `games/worker_contract.py`.  It defines the worker name,
runtime role, queue, endpoint, accepted message types, supported deployment
modes, and required worker-specific environment names.  `games/worker_config.py`
normalizes the mode and validates configuration without ever printing values.

## Current runtime state

Identity merge is currently consumed by the ECS service
`interoves-identity-ecs` from the `interoves-identity` queue.  The old EB
identity environment is legacy/compatibility state.  Other workers may still
use EB or ECS depending on the live deployment; do not infer their runtime
from the directory name alone.

The registry and configuration checks are read-only:

```bash
../venv/interoves_django/bin/python manage.py worker_config list
../venv/interoves_django/bin/python manage.py worker_config check --worker background
../venv/interoves_django/bin/python manage.py worker_config check --worker integrations --strict
```

`INTEROVES_WORKER_NAME`, `INTEROVES_DEPLOYMENT_MODE`, and
`INTEROVES_CONFIG_PROFILE` are optional compatibility controls.  If the worker
name is absent, it is inferred from the existing `INTEROVES_RUNTIME_ROLE`.

The first non-EB adapter is now available as a compatibility SQS poller:

```bash
../venv/interoves_django/bin/python manage.py run_worker \
  --worker background --mode ecs-fargate --once --queue-url "$QUEUE_URL"
```

It invokes the existing private worker view and applies a conservative
acknowledgement policy: 2xx deletes the message, ordinary 4xx drops a poison
message, 409 and 5xx leave a message for SQS retry.  This is suitable for a controlled ECS/Green
process proof of concept; it is not a production deployment until the task IAM
role, queue URL delivery, visibility timeout, alarms, and graceful shutdown
are provisioned.

Lambda has the same compatibility boundary through
`games.worker_lambda.handle_sqs_event`.  It returns partial batch failures for
5xx and 409 responses, while treating ordinary 4xx responses as poison
messages.  Only `background` is enabled for Lambda in the registry for now;
the other workers need duration/memory and browser compatibility measurements.

## Configuration boundary

Every runtime can now load configuration from one opt-in Secrets Manager JSON
secret by setting only:

```text
INTEROVES_CONFIG_SECRET_ID=arn:aws:secretsmanager:eu-central-1:...:secret:...
INTEROVES_CONFIG_PROFILE=production
```

The secret may be flat:

```json
{"TELEGRAM_BOT_TOKEN": "...", "TELEGRAM_API_ID": "..."}
```

or contain named profiles:

```json
{"production": {"TELEGRAM_BOT_TOKEN": "..."}, "game-day": {"TELEGRAM_BOT_TOKEN": "..."}}
```

Explicit environment variables always win.  The deployment artifact contains
only the secret reference and profile name, never secret values.  The loader
fails closed without logging the secret when the AWS call, JSON, profile, or
value shape is invalid.

The process IAM role must have `secretsmanager:GetSecretValue` for the selected
secret.  The same loader is imported before Django settings are evaluated, so
settings, EB sqsd, ECS, Lambda, and a Green supervisor see the same values.

SSM Parameter Store remains a later addition for non-secret configuration; it
is intentionally not mixed into this first loader so a missing optional
parameter cannot silently change production behavior.

Do not switch a production worker to a new mode until its adapter, IAM policy,
queue acknowledgement behavior, and configuration check have been tested on a
non-production queue.
