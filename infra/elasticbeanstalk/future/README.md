# Future Word Salad split deployment

These files are templates only. They are intentionally outside `.ebextensions`
and are not applied by the current `interoves-env` deployment.

The future bundle must use one of these role-specific configurations:

- `web/`: `INTEROVES_RUNTIME_ROLE=web`, On-Demand `c7i.large`, RollingWithAdditionalBatch batch 1;
- `worker/`: `INTEROVES_RUNTIME_ROLE=worker`, On-Demand `c7i.large`, one instance,
  `sqsd HttpConnections=1`.  Create it explicitly as an EB Worker/SQS tier;
  `worker/environment-tier.yaml` documents that requirement.

The dispatcher is a separate supervised service on the worker instance
(`worker/dispatcher.service`).  It must receive the same application
environment as the worker before being enabled; it is never installed on the
web bundle.

Native EB `sqsd` delivery cannot add a custom HMAC header.  The endpoint
accepts native sqsd only on the worker runtime and requires its sqsd message-id
and user-agent; private networking/security groups remain mandatory.  The
HMAC path is retained for a signed reverse proxy or other future transport.

The current Word Salad cron file must be excluded from the web bundle. The
runtime guard is defense-in-depth, not the primary isolation mechanism.
The same file must be excluded from the worker bundle: the worker is driven by
sqsd plus the supervised dispatcher, not by the legacy minute cron.  The exact
exclusions are listed in `web/excluded-bundle-files.txt` and
`worker/excluded-bundle-files.txt`.

Apply only after the compatibility worker, outbox reconciliation, IAM, private
networking and failure tests are approved separately.
