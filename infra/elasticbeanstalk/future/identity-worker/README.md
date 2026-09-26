# Identity worker

Not applied by `deploy.sh`.

Live since 26 Sep 2026. Web publishes `anonymous.merge` when `ANONYMOUS_MERGE_EVENTS=1`.
The 5-minute reconcile publishes due jobs that have no Redis mark and no live lease.
It does not claim them. A failed publish clears the mark.
Green no longer has the minute anonymous-merge cron.

- environment `interoves-identity-worker`, version `app-id1d-260926_152624`
- web `interoves-web-green` version `app-bg1o-260926_170808` has no anonymous-merge cron file
- schedule `interoves-anonymous-merge-reconcile`, `cron(2/5 * * * ? *)`, `Europe/Moscow`, enabled
- reconcile writes the `anonymous_merge` heartbeat
- `ANONYMOUS_MERGE_EVENTS=1` and `ANONYMOUS_MERGE_SQS_QUEUE_URL` set so a named job releases its lease and publishes the next batch
- role `INTEROVES_RUNTIME_ROLE=identity-worker`
- queue `https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-identity`
- DLQ `interoves-identity-dlq`, `maxReceiveCount` 3, visibility 180s
- `HttpPath=/internal/worker/identity/`, `HttpConnections=1`
- `InactivityTimeout` 150, `VisibilityTimeout` 180, `ErrorVisibilityTimeout` 60, `MaxRetries` 3
- SSH restricted to `127.0.0.1/32`

Visibility is 180s because the row lease is 2 minutes. A redelivery must not
arrive while that lease is still held. Domain backoff stays on the job row;
SQS retry is not a substitute for it.
