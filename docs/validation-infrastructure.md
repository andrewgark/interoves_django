# Disposable validation infrastructure

This is a disposable, non-production Word Salad/MySQL concurrency test
environment. It is not Green production and it is not the Blue rollback target.
It uses `IS_PROD=false`, `USE_S3=false`, and a separate RDS, Redis, VPC, SQS
queue, web environment, and worker environment.

## Source templates

- `infra/validation/network-rds.yaml` — VPC, one NAT Gateway, private subnets,
  and disposable MySQL RDS.
- `infra/validation/sqs.yaml` — validation queue and DLQ.
- `infra/validation/worker-iam.yaml` — worker role and instance profile.
- `infra/validation/web-application.config` — validation web settings.
- `infra/validation/worker-application.config` — validation worker settings.

## Current resource names

CloudFormation stacks:

- `interoves-validation-vpc`
- `interoves-validation-word-salad`
- `interoves-validation-worker-iam`

Elastic Beanstalk environments:

- `interoves-validation-web`
- `interoves-validation-web-alb`
- `interoves-validation-worker`
- `interoves-validation-worker-lt`

Other resources:

- RDS: `interoves-validation-rds`
- Redis: `interoves-validation-redis-001`, subnet group
  `interoves-validation-redis-subnets`
- SQS: `interoves-validation-word-salad` and its `-dlq`

## Recreate outline

Use `eu-central-1` and the `ai-bot` role. Create the VPC/RDS stack first:

```bash
./scripts/aws_with_role.sh aws cloudformation create-stack \
  --region eu-central-1 \
  --stack-name interoves-validation-vpc \
  --template-body file://infra/validation/network-rds.yaml
```

Then create the queue stack:

```bash
./scripts/aws_with_role.sh aws cloudformation create-stack \
  --region eu-central-1 \
  --stack-name interoves-validation-word-salad \
  --template-body file://infra/validation/sqs.yaml
```

Create one `cache.t4g.micro` Redis 7.x node in the VPC private subnets with
subnet group `interoves-validation-redis-subnets` and a validation-only
security group. Create the worker IAM stack after the queue and secrets exist,
passing its queue ARN and the three validation secret ARNs.

Finally create the validation EB web/worker environments from the two config
fragments above, supplying the generated RDS and Redis endpoints. The worker
must use the worker tier, one On-Demand `c7i.large`, and the validation SQS
queue. Run the MySQL concurrency and Word Salad recheck tests before keeping
the environments alive.

Do not point validation at the production RDS, Redis, SQS queues, or secrets.
