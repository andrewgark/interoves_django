# ECS worker configuration migration

## Current state (2026-11-06)

- Production consumers are the four `interoves-*-ecs` services. Legacy EB worker
  environments are not consumers, but `scripts/deploy_ecs_worker.sh` reads their
  option settings to build ECS task definitions.
- Secrets already live at `/interoves/production/<NAME>` in SSM Parameter Store;
  the RDS-managed database secret remains in Secrets Manager.
- The integrations EB ASG still has min/max/desired `1/1/1`; other legacy worker
  ASGs are at zero. Keep the EB environments until the deploy script no longer
  reads them.
- A YooKassa recurring payment can succeed synchronously in background ECS.
  That path previously attempted a direct admin Telegram send, while background
  ECS has no admin chat configuration. The webhook may arrive afterward and
  skip the already-succeeded payment.

## Target sources

| Setting | Authoritative source | Deployment output |
|---|---|---|
| Worker-specific plain values and required-variable contract | Versioned production worker manifest in this repository | `INTEROVES_CONFIG_VALUES` and explicit ECS task environment |
| Telegram bot credentials, chat identifiers, other secrets | SSM Parameter Store under `/interoves/production/` | ECS task `Secrets` references |
| RDS secret | Existing RDS-managed Secrets Manager secret | Existing RDS secret reference |
| Queue URLs, IAM, networking, sizing | CloudFormation and `worker-profiles.yaml` | ECS service and task definition |

EB option settings and a running ECS task definition are deployment results,
not configuration sources. The migration must preserve their current effective
values before removing the EB reads.

## Migration sequence

1. **Payment alert:** send every synchronous renewal outcome through the
   integrations queue. Verify success, cancellation, and unknown-submission
   paths. Deploy background ECS separately from any configuration migration.
   Stop writing `last_webhook_*` for an outcome confirmed by the synchronous
   API response; those fields must describe actual webhook processing.
2. **Inventory:** export only variable *names* and non-sensitive effective
   values from all four running ECS task definitions. Compare them with the
   legacy EB option settings and Green's two YooKassa flags. Resolve each
   difference explicitly; never copy bot tokens or chat identifiers into git.
3. **Declare configuration:** add a versioned per-worker manifest for plain
   values, a schema of required keys, and SSM references for chat identifiers.
   Keep existing SSM secret parameters and the RDS secret. Add missing chat ID
   parameters without exposing their values in logs or source control.
4. **Switch deploy:** make `deploy_ecs_worker.sh` read the manifest and SSM
   parameter *names*, validate every required parameter, and stop querying EB.
   Its plan-only output must show the worker, source revision, queue, and the
   names of injected variables, never their values.
5. **Cut over one worker at a time:** compare rendered ECS task configuration
   with the current task, then deploy and confirm service `1/1`, queue/DLQ
   health, Telegram delivery where relevant, and application logs. Roll back
   to the previous task definition if any check fails.
6. **Retire legacy compute:** scale the integrations EB ASG to `0/0/0`. Keep
   the EB environments until a fresh deploy of each ECS worker succeeds without
   EB configuration reads. Only then decide whether to delete those environments.

## Reliability follow-up

The queue publisher currently runs in a `transaction.on_commit` callback. A
failed SQS call after the payment commit can still lose an admin alert. Record
the alert intent in the same database transaction as the payment, dispatch it
from a retrying outbox process, and use a payment-specific deduplication key.
The migration is complete when a simulated SQS failure leaves a pending outbox
row that is delivered after recovery, with one Telegram message.

## Progress

- The payment notification routing change and webhook audit fix are committed.
- `production-worker-config.json` now contains the current effective plain
  values from all four ECS task definitions. Three Telegram chat identifiers
  were moved to SSM SecureStrings without printing their values.
- The deploy script reads the versioned manifest and SSM parameter names, with
  no EB configuration query. Plan-only runs succeeded for all four workers.
- A production ECS deployment from the new source and the database-backed
  alert outbox remain to be done. Keep legacy EB environments until the four
  services have been redeployed successfully.
