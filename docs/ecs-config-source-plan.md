# ECS worker configuration migration

## Current state (2026-11-06)

- Production consumers are the four `interoves-*-ecs` services. Legacy EB worker
  environments are not consumers, and `scripts/deploy_ecs_worker.sh` no longer
  reads their option settings.
- Secrets already live at `/interoves/production/<NAME>` in SSM Parameter Store;
  the RDS-managed database secret remains in Secrets Manager.
- The integrations EB ASG was scaled to `0/0/0` after all four ECS services
  were redeployed from the versioned config source. Its EB environment remains
  available for rollback; other legacy worker ASGs are also at zero.
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
6. **Retire legacy compute:** complete. The integrations EB ASG is `0/0/0`; its
   EB environment is retained for rollback. Decide separately whether to delete
   legacy EB environments after checking any remaining config references.

## Reliability follow-up

Recurring payment admin alerts are recorded in `ClubPaymentAdminAlertOutbox`
inside the billing transaction and dispatched by background ECS with bounded
retries. Each payment and alert type has one row; the queue message carries a
payment-specific deduplication key. A process crash after SQS accepts a message
but before the row is marked sent can still produce a retry; the integrations
consumer's delivery cache suppresses normal short-window duplicates.

## Progress

- The payment notification routing change and webhook audit fix are committed.
- `production-worker-config.json` now contains the current effective plain
  values from all four ECS task definitions. Three Telegram chat identifiers
  were moved to SSM SecureStrings without printing their values.
- The deploy script reads the versioned manifest and SSM parameter names, with
  no EB configuration query. Plan-only runs succeeded for all four workers.
- All four production ECS services were redeployed from the new source and are
  healthy at `1/1`; integrations admin alert delivery was verified through SQS.
- The integrations EB worker ASG is `0/0/0`; its environment is retained.
- A database-backed outbox now records recurring payment success, rejection,
  and unknown-submission alerts atomically with the payment state. The
  background worker retries SQS publication with bounded backoff.
- Migration `games.0304` is applied in production, Green runs release
  `app-green-8e6d4e0a`, and background ECS runs task definition `:35` from image
  commit `8e6d4e0a` at `1/1`. The dispatcher is active in production.
