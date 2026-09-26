# Integrations worker

Live since 26 Sep 2026.

- `telegram.announcements` and `telegram.admin_report` both send.
- Green cron no longer runs either command. The admin report uses `scheduled_for`, so a 00:25–00:29 message still sends if it is read within 20 minutes.

- environment `interoves-integrations-worker`, version `app-int1g-260926_151828`
- `social.publish` runs every minute on its own message and its own lock `social_queue_publish`. Announcements no longer publish the social queue.
- schedule `interoves-social-publish`, `cron(* * * * ? *)`, `Europe/Moscow`
- A social message older than 3 minutes is `skipped_stale`.
- `instagram.token_refresh` runs the daily check. A token younger than 30 days is `skipped` and the API is not called. Green no longer has the 03:17 cron line.
- schedule `interoves-instagram-token-refresh`, `cron(17 3 * * ? *)`, `Europe/Moscow`
- A token-refresh message older than 12 hours is `skipped_stale`.
- Green version `app-bg1o-260926_170808`
- role `INTEROVES_RUNTIME_ROLE=integration-worker`
- queue `https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-integrations`
- DLQ `interoves-integrations-dlq`, `maxReceiveCount` 3, visibility 200s
- `HttpPath=/internal/worker/integrations/`, `HttpConnections=1`
- `InactivityTimeout` 150, `VisibilityTimeout` 200, `ErrorVisibilityTimeout` 60, `MaxRetries` 3
- schedule `interoves-telegram-announcements`, `cron(* * * * ? *)`, `Europe/Moscow`
- schedule `interoves-telegram-admin-report`, `cron(25-29 0 * * ? *)`, `Europe/Moscow`

Announcements older than 3 minutes are `skipped_stale`. An admin-report
message whose `scheduled_for` is inside 00:25–00:29 still runs if it is read
within 20 minutes.
