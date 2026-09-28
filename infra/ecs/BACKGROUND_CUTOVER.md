# Background worker cutover

The EB background worker currently owns `interoves-background`. The ECS
service must remain at `DesiredCount=0` until EB consumption is stopped; do not
run both consumers during the cutover.

## Preflight

Use the `default` AWS profile if the normal role lacks permission:

```bash
AWS_PROFILE=default AWS_DEFAULT_REGION=eu-central-1 \
  aws elasticbeanstalk describe-environments \
  --application-name interoves \
  --environment-names interoves-background-worker

AWS_PROFILE=default AWS_DEFAULT_REGION=eu-central-1 \
  aws sqs get-queue-attributes \
  --queue-url https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-background \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

Confirm EB is `Ready/Green`, ECS is `desired=0/running=0`, and record the
queue depth. If `ApproximateNumberOfMessagesNotVisible` is non-zero, wait for
the 90-second visibility timeout after stopping the EB consumer before
starting ECS.

## Cutover

1. Set the discovered EB background ASG directly to `MinSize=0`, `MaxSize=0`,
   and `DesiredCapacity=0`; wait until its instance is terminated. EB worker
   environments may normalize persisted option settings back to `1/1`, so the
   ASG state itself is the handoff control.
2. Confirm the queue has no in-flight messages and that EB is no longer
   receiving messages.
3. Start one ECS Spot task using the immutable image digest:

   ```bash
   ./scripts/deploy_ecs_worker.sh background \
     916000456640.dkr.ecr.eu-central-1.amazonaws.com/interoves-workers@sha256:<digest> \
     --profile normal --desired-count 1 --apply
   ```

4. Check ECS service events, CloudWatch `/interoves/workers/background`, and
   SQS visible/not-visible counts for at least one normal processing interval.

## Rollback

1. Run the deploy script with `--desired-count 0` and the same image digest.
2. Restore the EB background ASG to its recorded `MinSize`/`MaxSize` values
   and desired capacity.
3. Wait for EB to become `Ready/Green` and verify queue consumption resumes.

Do not change the queue visibility timeout or delete production messages as
part of rollback. The queue remains the source of truth during the handoff.
