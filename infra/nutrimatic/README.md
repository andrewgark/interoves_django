# Nutrimatic Lambda

`/nutrimatic-ru/` searches run in the Lambda `interoves-nutrimatic`, not on
Green. Django (`microsites.views.nutrimatic_search`) invokes it when
`NUTRIMATIC_LAMBDA_FUNCTION` is set in the Green EB environment; unset, it
falls back to a local `find-expr` subprocess (needs the index hook on Green).

Why: one heavy `find-expr` takes 0.8–1.6 GB. Inside Daphne's memory cgroup it
OOM-killed the web service (2026-11-19 21:39 UTC, 502s through the ALB).

- Image: `public.ecr.aws/lambda/python:3.12` (AL2023, same glibc as Green) +
  `nutrimatic_bundle/build/find-expr`, `nutrimatic_bundle/cgi_scripts/cgi-search.py`,
  `handler.py` and the prod index `s3://interoves-django-static/private/nutrimatic/wiki-merged.packed.index`.
- Stack `interoves-nutrimatic` (`template.yaml`): ECR repo, function (2048 MB,
  25 s, reserved concurrency 4), invoke policy on `interoves-green-web-role`,
  5-minute warmup rule, alarms `interoves-nutrimatic-errors` and
  `interoves-nutrimatic-compute-time` → `interoves-eb-health-alerts`.
- Cost: ~200 searches/day × ~1 s × 2 GB ≈ $0.3/month. Worst case at the
  concurrency cap is ~$350/month, hence the compute-time alarm.

## Deploy

Any change to `cgi-search.py`, `find-expr`, `handler.py` or the S3 index needs:

```bash
./scripts/deploy_nutrimatic_lambda.sh   # uses the `default` profile
```

A Green deploy alone does not update the Lambda.

## Logs

`/aws/lambda/interoves-nutrimatic` has one line per search with status,
duration and the query string; Green logs `nutrimatic finish backend=lambda`.

## Rollback

Remove `NUTRIMATIC_LAMBDA_FUNCTION` from the Green environment (local
`find-expr` comes back on the next restart; keep `03_sync_nutrimatic_index.sh`
until the local path is deleted).
