# Future Word Salad split deployment

These files are templates only. They are intentionally outside `.ebextensions`
and are not applied by the current `interoves-env` deployment.

The future bundle must use one of these role-specific configurations:

- `web/`: `INTEROVES_RUNTIME_ROLE=web`, On-Demand `c7i.large`, Rolling batch 1;
- `worker/`: `INTEROVES_RUNTIME_ROLE=worker`, On-Demand `c7i.large`, one instance,
  `sqsd HttpConnections=1`.

The current Word Salad cron file must be excluded from the web bundle. The
runtime guard is defense-in-depth, not the primary isolation mechanism.

Apply only after the compatibility worker, outbox reconciliation, IAM, private
networking and failure tests are approved separately.
