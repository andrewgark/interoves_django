# AWS / Elastic Beanstalk — lifehacks & useful commands

## Agent playbook (Cursor / automation)

Use this order when operating on **prod** (account `916000456640`, region **`eu-central-1`**).

### Prerequisites

1. **Python:** `../venv/interoves_django/bin/python` (see [`agents/AGENTS.md`](AGENTS.md)).
2. **AWS credentials:** `secrets/aws.env` (gitignored) — at minimum `export AWS_PROFILE=interoves` and `export INTEROVES_AWS_ROLE_ARN=arn:aws:iam::916000456640:role/ai-bot`. Scripts `with_rds.sh`, `eb_run.sh`, `rds_mysql.sh` call `scripts/interoves_aws_bootstrap.sh`, which loads `secrets/aws.env` and **assumes `ai-bot`** when base credentials allow.
3. **Tooling:** Shell commands that hit AWS, SSM, or SSH need **`required_permissions: ["network", "all"]`** in Cursor.

### Two IAM identities

| Identity | When |
|----------|------|
| **`interoves` IAM user** | Credentials in `~/.aws/credentials` / SSO profile. |
| **`ai-bot` role** (assumed) | After bootstrap; `aws sts get-caller-identity` shows `assumed-role/ai-bot/...`. Same policies are attached to **`ai-bot`** for EB/RDS/SSM so `./scripts/aws_with_role.sh eb status` works. |

### What to run for what

| Goal | Command |
|------|---------|
| **AWS CLI / EB** (as `ai-bot`) | `./scripts/aws_with_role.sh aws …` or `./scripts/aws_with_role.sh eb status` |
| **Same AWS CLI as `interoves` only** (no assume-role) | `AWS_PROFILE=interoves aws …` or `eb` after `export AWS_PROFILE=interoves` |
| **Prod Django + RDS** (same env as Daphne on the instance) **— preferred** | `./scripts/eb_run.sh manage.py check --database default`, `migrate --plan`, `shell`, etc. |
| **RDS from laptop** (SSM tunnel `localhost:13306`) | `./scripts/with_rds.sh manage.py dbshell` — requires `secrets/rds.env` with `RDS_HOSTNAME` + **`RDS_SECRET_ARN`** (same as EB `eb printenv`). Boto3 must resolve credentials (bootstrap + profile). |
| **MySQL one-off** (password from Secrets Manager) | `./scripts/rds_mysql.sh -e "SELECT 1"` (direct to RDS host; often blocked by SG from home — use **`with_rds`** or **`eb_run`** instead). |

### RDS access — verified pattern

- **Reliable:** `./scripts/eb_run.sh manage.py check --database default` — confirms MySQL from the EB app host (Secrets Manager + RDS in VPC).
- **`with_rds.sh`:** Can fail with MySQL **2013** / TLS handshake over the tunnel if Django does not use RDS SSL options for the tunneled path; if that happens, use **`eb_run`** for DB checks or run Django with MySQL SSL options for `127.0.0.1:13306` (advanced).

### Redis (ElastiCache) — “red” / channel layer

- **Not** exposed to the public internet. `REDIS_HOST` / `REDIS_TLS` / `REDIS_PORT` exist only on EB (`eb printenv`).
- **From an agent:** do **not** expect a Redis tunnel like `with_rds`. Use **`./scripts/eb_run.sh manage.py shell`** (or code paths that hit Redis on the instance) to validate behavior, or inspect Redis from a component inside the VPC.
- If Channels/WebSockets misbehave, check **`eb_run`** logs and `REDIS_*` env on the instance.

## AWS role for local CLI / agents

`secrets/aws.env` (copy from `secrets/aws.env.example`) can set `INTEROVES_AWS_ROLE_ARN` and a base `AWS_PROFILE` or keys. Scripts call `interoves_aws_bootstrap` to `sts:AssumeRole` before using the AWS CLI. Ad-hoc:

```bash
./scripts/aws_with_role.sh aws sts get-caller-identity
```

See `secrets/README.md`.

## Environment basics

```bash
eb status                          # health, version, last event
eb logs --zip                      # download all logs → .elasticbeanstalk/logs/
eb printenv                        # show all env vars on the instance
eb setenv KEY=value                # set / update env var (triggers redeploy of env config only)
eb deploy --timeout 15             # deploy; 15-min CLI wait (actual EB timeout set in .ebextensions)
```

Active instance ID:
```bash
aws ec2 describe-instances --region eu-central-1 \
  --filters "Name=tag:elasticbeanstalk:environment-name,Values=interoves-env" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text
```

## RDS password — Secrets Manager pattern

EB's RDS plugin injects `RDS_PASSWORD` from **stale CloudFormation metadata** on every
deploy, overwriting `eb setenv` values. Work around it in `settings.py`:

```python
if 'RDS_HOSTNAME' in os.environ:
    _pw = os.environ.get('RDS_PASSWORD', '')
    _arn = os.environ.get('RDS_SECRET_ARN', '')
    if _arn:
        import boto3, json
        _secret = boto3.client('secretsmanager', region_name='eu-central-1') \
                       .get_secret_value(SecretId=_arn)['SecretString']
        _pw = json.loads(_secret)['password']
    DATABASES = {'default': {'ENGINE': 'django.db.backends.mysql',
                             'PASSWORD': _pw, ...}}
```

Set `RDS_SECRET_ARN` via `option_settings` in `.ebextensions/django.config` so it
survives every deploy.

If `RDS_SECRET_ARN` is set, **`interoves_django/settings.py` refuses to fall back to
`RDS_PASSWORD`** when Secrets Manager fails (so a bad/stale EB password cannot silently
take effect). Fix IAM or the ARN; for local tunnel use without AWS creds, omit
`RDS_SECRET_ARN` and use `RDS_PASSWORD` in `secrets/rds.env` only.

## EC2 IAM role — grant Secrets Manager access

```bash
aws iam put-role-policy \
  --role-name aws-elasticbeanstalk-ec2-role \
  --policy-name interoves-rds-secret-read \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Action":"secretsmanager:GetSecretValue",
                  "Resource":"<secret-arn>"}]}'
```

## Reaching prod from local — `eb_run.sh` (preferred)

`scripts/eb_run.sh` uses **EC2 Instance Connect** — the proper "IAM as the key" approach:
1. Generates a throw-away RSA key pair
2. Pushes the public half to the instance for 60 s via `aws ec2-instance-connect send-ssh-public-key` (IAM-authenticated)
3. SSHes in, pipes a Python script over stdin, reads env from the running Daphne process's `/proc` entry (avoids quoting issues in the EB env file)
4. Temp key is deleted on exit via `trap`

No security group changes. No static secrets. Just IAM.

```bash
# Management commands (prod env injected automatically):
./scripts/eb_run.sh manage.py check_background_migrations
./scripts/eb_run.sh manage.py migrate --plan
./scripts/eb_run.sh manage.py shell

# Raw shell (read logs, run anything):
./scripts/eb_run.sh --raw "cat /var/log/app/background_migrations.log"
./scripts/eb_run.sh --raw "tail -50 /var/log/web.stdout.log"
```

**Agents**: use `./scripts/eb_run.sh` for all prod management commands.
Requires `required_permissions: ["all"]` in the Shell tool call.

## Connecting to RDS from local machine — `with_rds.sh`

Uses **SSM port forwarding**: tunnels `localhost:13306 → EC2 instance → RDS:3306`
via the SSM session. No security group changes, auth is IAM.
Requires `session-manager-plugin` on PATH (`sudo dpkg -i session-manager-plugin.deb`).

```bash
./scripts/with_rds.sh manage.py check_background_migrations
./scripts/with_rds.sh manage.py dbshell
./scripts/with_rds.sh --raw ./scripts/rds_mysql.sh -e "SHOW TABLES"
```

Django's `RDS_HOSTNAME` / `RDS_PORT` are overridden to `127.0.0.1:13306` automatically
so no code changes are needed.

## Slow / blocking migrations — background pattern

Long `manage.py migrate` steps kill EB deploys (single-instance = downtime).

**Fix:**
1. `SeparateDatabaseAndState(state_operations=[...], database_operations=[])` — Django
   records the migration as applied instantly; no SQL runs.
2. For data backfills: make the `RunPython` body `pass`.
3. Add `.platform/hooks/postdeploy/02_background_migrations.sh` — runs **after** Daphne
   is up; launches actual DDL/backfill with `nohup ... & disown`; idempotency checks
   prevent re-runs on subsequent deploys.

Check progress with:
```bash
../venv/interoves_django/bin/python manage.py check_background_migrations
```

## EB deploy troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Deploy stuck 60+ min | Long-running migration in `container_commands` | Background pattern above |
| "Must be Ready" on `eb deploy` | Env already updating | Wait, then retry |
| Site 000 after timeout | Single-instance: old app stopped, new one failed | Redeploy last good version label |
| `Access denied` for MySQL | Stale `RDS_PASSWORD` from CloudFormation | Secrets Manager pattern above |
| `eb logs --zip` HTTP 400 | Env in invalid/updating state | Wait until Ready |

Redeploy a specific known-good version:
```bash
aws elasticbeanstalk update-environment --region eu-central-1 \
  --environment-name interoves-env \
  --version-label app-XXXX-YYYYY
```

## Useful log paths on the instance

| Path | Contents |
|------|----------|
| `/var/log/eb-engine.log` | Deploy pipeline (container_commands, hooks) |
| `/var/log/cfn-init.log` | cfn-init / ebextension commands detail |
| `/var/log/web.stdout.log` | App stdout (Daphne / Django) |
| `/var/log/nginx/error.log` | Nginx errors |
| `/var/log/app/*.log` | Background migration logs (added via `.ebextensions/logs.config`) |

`eb logs --zip` downloads all of the above to `.elasticbeanstalk/logs/latest_v2/<instance-id>/`.

## Key resource IDs (eu-central-1)

| Resource | ID / ARN |
|----------|----------|
| EB environment | `interoves-env` |
| EB application | `interoves` |
| RDS security group | `sg-0631c0b9e45b0f6b3` |
| RDS secret ARN | `arn:aws:secretsmanager:eu-central-1:916000456640:secret:rds!db-ce1a594a-9964-4a32-a9d3-9483ada5368c-0O6ead` |
| EC2 IAM role | `aws-elasticbeanstalk-ec2-role` |
