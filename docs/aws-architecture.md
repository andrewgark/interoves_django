# AWS production architecture

This is the current production topology. Verify live AWS state before making
an operational change; this document describes ownership and intended routing,
not a replacement for health checks.

## Request path

```text
Players
  -> Cloudflare (interoves.com / www)
  -> Green Application Load Balancer
  -> Elastic Beanstalk: interoves-web-green-lb
  -> RDS MySQL + ElastiCache Redis + S3-backed static/media
```

`interoves-env` is Blue rollback capacity. It is not the public origin and is
not a normal deployment target. Direct public access to Blue is blocked at its
security group; administration remains available through SSM. A DNS rollback
must be deliberate and must first verify that Blue background jobs are held.

## Asynchronous work

```text
Django web -> SQS queue -> ECS service in interoves-workers -> RDS/Redis/APIs
```

The four active consumers are:

| Worker | ECS service | Queue | Capacity |
|---|---|---|---|
| identity | `interoves-identity-ecs` | `interoves-identity` | Fargate On-Demand |
| background | `interoves-background-ecs` | `interoves-background` | Fargate Spot |
| integrations | `interoves-integrations-ecs` | `interoves-integrations` | Fargate On-Demand |
| recheck | `interoves-recheck-ecs` | `interoves-recheck` | Fargate Spot |

The normal desired count is `1` per service. The game-day profile moves the
Spot-capable workers to On-Demand; it does not create an EB worker or a second
consumer for the same queue. Queue, DLQ and log-group details are in
`docs/worker-queues.md`.

## Configuration and observability

Worker tasks receive non-secret settings and Secrets Manager references in the
task definition. Their task role is scoped to the exact queue and secret ARNs.
Images are immutable ECR digest references and carry `INTEROVES_IMAGE_COMMIT`.
Web and workers write application logs to CloudWatch; production 5xx alerts
include the explicit `INTEROVES_ENVIRONMENT` label, instance and deploy
version so Green and Blue incidents cannot be confused.

Use `interoves` for routine AWS commands. Use `default` only as a temporary
privileged profile when granting a missing permission, never as an operational
default.
