# Nutrimatic Lambda

`/nutrimatic-ru/` searches run in the Lambda `interoves-nutrimatic`, never on
Green. Django (`microsites.views.nutrimatic_search`) invokes the function named
by `NUTRIMATIC_LAMBDA_FUNCTION` (Green EB environment property); unset, the
page returns 503. Green no longer has the index or runs `find-expr`.
`nutrimatic_bundle/` in git is only the image's source.

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

Green cannot search locally any more (no index hook, no subprocess path).
To get local search back, redeploy the EB application version
`app-green-dcb5adfc-20261119225703` (its zip still carries the index hook and
the subprocess path); a fresh `deploy.sh` of an older commit does not restore
the hook, because Green bundles inherit `.platform` from the live version.

Never deploy the stack with an empty `ImageTag` once it exists: the
`HasImage` condition then deletes the function and Green's invoke policy
(2026-11-19 23:33–23:40, searches returned "Search unavailable"). The deploy
script only does that on the very first run.
