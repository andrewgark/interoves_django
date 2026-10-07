---
name: interoves-deploy
description: Prepare an explicitly targeted Interoves Django Elastic Beanstalk release. Use when a task asks to deploy or release, and require an explicit environment before any deploy command.
---

# Interoves deploy

## Safety boundary

“prod”, “production”, “сайт” and an unqualified “давай задеплоим” mean the web
production environment `interoves-web-green-lb`. `./deploy.sh` is the canonical
Green entrypoint and accepts only that production target. `interoves-env` is
Blue, kept only for DNS rollback; Blue must not be deployed or repaired unless
the user explicitly requests a Blue operation.

The web entrypoint itself explicitly targets Green and rejects target overrides.
For worker deploys, require the exact environment name:

- production web: `interoves-web-green-lb`;
- workers: the exact named worker environment;
- rollback Blue: `interoves-env`, only with explicit authorization.

## Green web deployment

Do not run `eb deploy` from the git tree against Green. The repository does not
contain the live Green `.ebextensions/zzzz-green-web.config` and `.platform`
hooks; a normal checkout deploy can replace VPC, instance type, IAM profile, or
fail with `You cannot remove an environment from a VPC`.

For Green, use `./deploy.sh --dry-run` to prepare a bundle or `./deploy.sh` to
release it. The script downloads the current Green zip, overlays only the
intended application code, preserves live `.ebextensions` and `.platform`, and
updates only `interoves-web-green-lb`. Never use `rsync --delete` over those
directories. The collectstatic skip required by Green belongs only in the zip,
not in the repository. If EB application-version metadata has no SourceBundle,
the wrapper may use the standard retained EB S3 object for the current version
label; if that object is also absent, stop instead of using a plain checkout
deploy. Releases are assembled from committed HEAD; uncommitted working-tree
files are excluded.

Apply required schema changes through `./scripts/with_rds.sh` before releasing
code that reads new columns. Deployment migrations are disabled unless
`RUN_PRODUCTION_MIGRATIONS=true`.

## Worker deployment

Deploy each worker with `./scripts/deploy_worker.sh WORKER_ENVIRONMENT`; add
`--dry-run` for packaging only or `--deploy` for the AWS mutation. The Word Salad
worker is not the Green web bundle and must not receive Green web ebextensions.
Verify the target queue/environment explicitly before deploying.

## Microsites

`./deploy.sh` controls microsite bundling for the Green bundle. Set
`BUNDLE_MICROSITES=1` only
when the explicitly authorized release needs fresh generated inputs from
`NUTRIMATIC_SRC`, `BOOKLET_SRC`, or `BOOKLET_HTML_SRC`; ordinary Django/game/UI
changes leave it unset. Adapt the generated files into the explicitly prepared
  Green or worker zip instead.

## Before and after

- Read [`../../../agents/AGENTS.md`](../../../agents/AGENTS.md) and
  [`../../../agents/aws-eb.md`](../../../agents/aws-eb.md).
- Check the working tree and preserve unrelated changes.
- Confirm the exact EB environment and current status/version before acting.
- After an authorized Green release, check `/health/live/` and the deploy
  version on `https://interoves.com`; do not treat Blue status as production
  status.
- Run `scripts/smoke_prod_pages.sh` only as an explicitly authorized smoke
  check; it does not make `deploy.sh` safe for Green.
