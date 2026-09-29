# Production monitoring runbook

This runbook covers the production-only CloudWatch monitoring for Interoves.
Do not create or enable a validation environment as part of these checks.

## Alarm groups

- `interoves-elb-*`: public web target health and target 5xx responses.
- `interoves-rds-*`: RDS CPU, connections, free memory, and oldest InnoDB
  transaction metric emitted by the recheck worker.
- `interoves-recheck-*`: SQS age/DLQ, dispatcher errors, and DB outbox age.

All alarms are standard-resolution alarms. `OK` means the observed metric is
below its threshold; `INSUFFICIENT_DATA` is expected briefly after creation or
after a worker restart.

## Read-only audit

Use the permanent profile for normal operations:

```bash
AWS_PROFILE=interoves AWS_DEFAULT_REGION=eu-central-1 \
  aws cloudwatch describe-alarms --alarm-name-prefix interoves
```

The SNS topic currently has no subscriptions. Alarm actions may point at the
topic, but no email or chat notification is delivered until a recipient is
explicitly added.

## Incident checks

1. Check the alarm state and its metric dimensions.
2. Check Green health and 5xx before touching workers.
3. For recheck alarms, inspect ECS service `interoves-recheck-ecs`, its task
   logs, SQS and DLQ, then the custom outbox/transaction metrics.
4. For RDS alarms, avoid running migrations or broad repair jobs until active
   transactions and connection count return to normal.

Useful production checks:

```bash
AWS_PROFILE=interoves AWS_DEFAULT_REGION=eu-central-1 \
  aws ecs describe-services --cluster interoves-workers \
  --services interoves-recheck-ecs

AWS_PROFILE=interoves AWS_DEFAULT_REGION=eu-central-1 \
  aws elasticbeanstalk describe-environments --application-name interoves \
  --environment-names interoves-web-green
```

## Recheck rollback

Keep the previous immutable image digest. If the new recheck task is unhealthy:

1. Stop applying new changes.
2. Deploy the previous digest with `scripts/deploy_ecs_worker.sh` and the same
   `normal` / `ecs-fargate-spot` profile.
3. Confirm desired/running is `1/1`, deployment rollout is `COMPLETED`, SQS is
   draining, and all recheck alarms return to `OK`.
4. Investigate the failed image separately before retrying the cutover.

Do not turn the legacy `interoves-recheck-worker` EB environment back on while
ECS is consuming the same queue; that can create duplicate consumers and
unplanned cost.

## Profile rule

`interoves` is the permanent profile for routine commands. `default` is only an
explicit, temporary privileged override when `interoves` lacks a required AWS
control-plane permission; it must not be written into deployment scripts or
used as their default.
