---
name: interoves-deploy
description: Prepare and run an Interoves Django Elastic Beanstalk deploy. Use when a task asks to deploy, release, or decide whether deploy.sh should refresh generated microsite bundles.
---

# Interoves deploy

## Choose whether to rebuild microsites

`./deploy.sh` skips microsite bundling by default. Set `BUNDLE_MICROSITES=1` only when the requested release needs fresh generated inputs copied into this repository by `scripts/bundle_microsites.sh`:

- Nutrimatic runtime files from `NUTRIMATIC_SRC` (by default `~/nutrimatic-ru`), such as a newly built `build/find-expr` or updated CGI scripts.
- Eurovision booklet output from `BOOKLET_SRC` / `BOOKLET_HTML_SRC` (by default `~/eurovision2026booklet/dist`), or shared assets from its repository.

For ordinary Django, games, templates, CSS/JS, migrations, admin, or configuration changes, leave the variable unset. Those changes are deployed from the checkout as-is; bundling would only copy unrelated generated files and add work. If the task specifically updates a checked-in microsite bundle file directly, deploy it as-is; do not rebuild unless the source bundle also needs to be refreshed.

Typical commands:

```bash
./deploy.sh
BUNDLE_MICROSITES=1 ./deploy.sh
```

If an updated bundle was staged in an earlier step (for example by `stage_nutrimatic_find_expr_docker_al2023.sh`), use the normal deploy command unless the task also requires refreshing the booklet or other bundled inputs.

## Before deploying

- Read [`../../../agents/AGENTS.md`](../../../agents/AGENTS.md) and [`../../../agents/aws-eb.md`](../../../agents/aws-eb.md) for project and production deployment constraints.
- Inspect the working tree before changing generated bundle files. Preserve unrelated user changes and do not rebuild assets unless the task calls for them.
- `deploy.sh` runs the Elastic Beanstalk deploy and then `scripts/smoke_prod_pages.sh`. Run it only when the user asks for a deploy or has otherwise authorized a production release.
- Deploy policy is rolling, one instance at a time. Keep migrations compatible with old and new app versions during the rollout; use the background migration pattern in `agents/aws-eb.md` for blocking DDL or backfills.
