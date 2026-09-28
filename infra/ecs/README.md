# ECS worker deployment

`worker-service.yaml` is one parameterized CloudFormation template for all
workers. It creates an ECS service and task definition with the same command:

```text
python manage.py run_worker --worker <name> --mode <ecs-fargate|ecs-fargate-spot>
```

The template does not create a cluster, VPC, subnets, security groups, queues,
or IAM roles. Those resources are intentionally supplied as parameters or
managed separately so deploying a worker cannot accidentally modify the
production web network.

## Required task role permissions

Render `worker-task-role-policy.json` for one worker and replace the two
placeholders with the exact queue ARN and configuration secret ARN. Keep one
task role per worker where practical. Do not grant `sqs:*` on `*` or read access
to the common secret unless that worker actually needs it.

## First deployment sequence

1. Build and publish an immutable application image.
2. Create a dedicated ECS task role with the rendered policy.
3. Verify the role can read only the selected queue and secret.
4. Deploy with `DesiredCount=0` and run one task manually against a test queue.
5. Run `manage.py worker_config check --worker <name> --mode ecs-fargate --strict`.
6. Process a synthetic message and inspect CloudWatch logs/DLQ behavior.
7. Set `DesiredCount=1` only after the smoke test.

For `ecs-fargate-spot`, the workload must tolerate interruption. Start with
`background` or `recheck`; keep `integrations` on On-Demand until its retry and
external API side effects have been verified.

The task definition passes only references and non-secret runtime metadata as
environment variables. The application loads the actual JSON configuration
through the task role using `INTEROVES_CONFIG_SECRET_ID`.
