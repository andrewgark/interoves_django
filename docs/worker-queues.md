# Worker queues and current runtimes

This is the canonical map for production worker queues. When another document
mentions a different runtime, queue, or deployment path, verify it against
this file and the live AWS resources before changing production.

## Current production map

| Worker | Current runtime | Queue | DLQ | CloudWatch log group | Message types |
|---|---|---|---|---|---|
| identity | ECS service `interoves-identity-ecs` | `interoves-identity` | `interoves-identity-dlq` | `/interoves/workers/identity` | `anonymous.merge`, `anonymous.merge_reconcile`, `account.merge` |
| background | ECS service `interoves-background-ecs` (Fargate Spot) | `interoves-background` | `interoves-background-dlq` | `/interoves/workers/background` | `difficulty.refresh`, `difficulty.health_check`, `projection.reconcile`, `projection.refresh` |
| integrations | ECS service `interoves-integrations-ecs` (Fargate On-Demand) | `interoves-integrations` | `interoves-integrations-dlq` | `/interoves/workers/integrations` | `telegram.announcements`, `telegram.admin_report`, `instagram.token_refresh`, `social.publish` |
| recheck | ECS service `interoves-recheck-ecs` (Fargate Spot) | `interoves-recheck` | `interoves-recheck-dlq` | `/interoves/workers/recheck` | `word_salad.recheck` |

The identity queue URL in production is:

```text
https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-identity
```

All four ECS services are currently the active consumers, normally at desired
count `1`. `background` and `recheck` use Fargate Spot; `identity` and
`integrations` use Fargate On-Demand. The old Elastic Beanstalk worker
environments are legacy/compatibility state and must not be re-enabled against
the same queues.

## Where the contract lives

- `games/worker_contract.py` — worker names, queue names, endpoints and message types;
- `scripts/deploy_ecs_worker.sh` — resolves `interoves-<worker>` and passes its URL;
- `infra/ecs/worker-service.yaml` — injects `QueueUrl` as `WORKER_QUEUE_URL` and,
  for identity, `ANONYMOUS_MERGE_SQS_QUEUE_URL`;
- `games/anonymous_merge_events.py` — publishes identity merge events;
- `games/models.py` — durable merge state in `AccountMergeJob`,
  `AnonymousMergeJob`, and `AnonymousMergeReconcileItem`.

ECS worker deployment is plan-only unless `--apply` is passed. An apply must
include `--desired-count` explicitly so a missing argument cannot scale a live
consumer to zero. For example, a normal recheck release at one task is:

```bash
./scripts/deploy_ecs_worker.sh recheck "$IMAGE_URI" \
  --profile normal --desired-count 1 --apply
```

The SQS message is transport state, not the full merge queue history. For
history and retries, query the two database tables; for delivery failures,
inspect the queue and DLQ; for execution timing and exceptions, inspect the
worker log group.

## Read-only checks

Use the `ai-bot` wrapper and the production region:

```bash
./scripts/aws_with_role.sh aws sqs get-queue-attributes \
  --region eu-central-1 \
  --queue-url https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-identity \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed

./scripts/aws_with_role.sh aws ecs describe-services \
  --region eu-central-1 \
  --cluster interoves-workers \
  --services interoves-identity-ecs
```

For merge state, use the read-only Django ORM through the RDS tunnel. Do not
use the legacy EB queue name or a local database to decide whether a production
merge is pending.

## Legacy note

`infra/elasticbeanstalk/future/identity-worker/README.md` describes the former
EB identity worker. It is retained for migration history, but it is not the
canonical runtime document. New agents should start here, then verify the live
ECS service and SQS attributes.
