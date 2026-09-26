# Background worker templates

Not applied by `deploy.sh`. `deploy.sh` updates `interoves-env` (Blue), not
`interoves-web-green`.

Phase 1 worker:

- role `INTEROVES_RUNTIME_ROLE=background-worker`
- one On-Demand `t3.medium`
- `sqsd` `HttpConnections=1`
- `HttpPath=/internal/worker/background/`
- Standard queue `interoves-background` plus a DLQ
- `maxReceiveCount` 3

Measured 26 Sep 2026 from Green `cron_finished job=daily_difficulty_refresh`
(712 samples, p95 445ms, max 1749ms, real refreshes included):

- `InactivityTimeout` 60
- `VisibilityTimeout` 90
- `ErrorVisibilityTimeout` 30
- `MaxRetries` 3

Live since 26 Sep 2026:

- queue `https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-background`
- DLQ `interoves-background-dlq`, `maxReceiveCount` 3, visibility 90s
- environment `interoves-background-worker`, version `app-bg1i-260926_132121`
- schedule `interoves-difficulty-refresh`, `cron(* * * * ? *)`, `Europe/Moscow`, enabled
- schedule `interoves-difficulty-health-check`, `cron(7 * * * ? *)`, `Europe/Moscow`, enabled
- schedule `interoves-projection-reconcile`, `cron(3/15 * * * ? *)`, `Europe/Moscow`, enabled, apply limit 5
- `projection.refresh` is published by web when a section release is marked dirty. The body is the release id, not the actor. Reconcile every 15 minutes is the safety net.
- Green version `app-bg1s-260926_183810` publishes `projection.refresh` and has no projection cron line

`difficulty.health_check` uses the same endpoint. A second run inside 50
minutes returns `skipped_recent`. Do not put either difficulty cron line back.

`projection.reconcile` applies, limit 5, same Redis lock as the old cron.
Green no longer runs that cron. A dry-run on 26 Sep 2026 matched the cron
summary (`scanned=538 valid=538 missing=0 stale=0`) before this cutover.
