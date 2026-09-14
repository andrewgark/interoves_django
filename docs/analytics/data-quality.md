# Product analytics data quality

## Bounded read-only check

Run the stage-1A checker with an inclusive lower and exclusive upper bound:

```bash
../venv/interoves_django/bin/python manage.py check_product_analytics \
  --since 2026-09-02T00:00:00+03:00 \
  --until 2026-09-03T00:00:00+03:00
```

Both arguments are mandatory. The interval must be positive and no greater than
31 days. Naive timestamps are interpreted in the configured project timezone;
explicit offsets are preferred.

The command prints only check name, `PASS`/`FAIL`, aggregate count, and the
requested window. It never prints row IDs, user/team values, full `anon_key`,
email, username, or other PII. A failed **FAIL** invariant raises `CommandError` (non-zero exit). **WARN**
lines do not fail the process. The command never prints row IDs, user/team
values, full `anon_key`, email, username, or other PII.

## Checked invariants

The bounded candidate set is selected by `started_at`, `completed_at`, or
`PlayerAnalyticsState.updated_at`. It checks:

- exactly one of `user_id`, `team_id`, and `anon_key` on start, completion, and
  lifecycle-state rows;
- a later duplicate start or completion for the same actor and placement;
- live v2 completion without a matching start (**FAIL**);
- legacy/backfill completion without a matching start (**WARN**);
- live v2 completion timestamp earlier than its first matching start;
- missing `GameTaskGroup(game, task_group)` placement, except unpublished
  `LadderOffer` / `WordSaladOffer` drafts (`status` draft or sent and no
  `accepted_link`); those flows write live analytics before a catalog
  placement exists;
- `game_instance_id` inconsistent with `game.id + ":" + task_group.id`;
- `game_kind` inconsistent with the current mapping;
- start/completion more than five minutes in the future;
- physical instrumentation version outside `NULL` or `2`;
- a backfilled row incorrectly marked version 2;
- anonymous start/completion in the window whose `anon_key` already has an
  `AnonAccountClaim` (**WARN**; indexed Exists on the unique claim key).

For “completion without start”, only completions inside the requested window are
candidates, but the matching indexed lookup is allowed to find a start earlier
than `--since`. It does not aggregate the entire start history.

“Completion before start” is intentionally restricted to non-backfilled v2
completions. Legacy and backfilled completion timestamps do not necessarily
represent the original gameplay time and are not failed by this ordering check.

## Read-only guarantee

The command is read-only by construction: it contains no `save`, `create`,
`update`, `delete`, backfill, healing, acknowledgement, or analytics registration
call. It imports mapping constants and models only. Stage 1A intentionally does
not issue `SET TRANSACTION READ ONLY`; that backend-specific session/transaction
state is unnecessary for the current query-only implementation and can be
fragile in SQLite tests and pooled MySQL connections.

Tests snapshot the event rows before and after a successful run. This is a
regression guard, not permission to add side effects later.

## Query bounds and indexes

Candidate selection uses existing indexes on `started_at`, `completed_at`, and
`updated_at` (the lifecycle state table is small; `updated_at` is not currently
indexed). Duplicate and counterpart checks are correlated only from bounded
candidates. Their inner lookups use existing actor, game/task-group foreign-key,
and `game_instance_id` indexes. Game-kind and instance-id consistency is evaluated
while streaming only candidate rows.

No index is added in this stage. The nine player-analytics UNIQUE indexes from
stage 1B.2 also provide `(actor, game_instance_id)` lookup prefixes for
duplicate and counterpart checks. Keep using short post-deploy windows and
monitor duration before using the 31-day maximum.

### Production plan snapshot, 2026-09-02

Read-only `EXPLAIN FORMAT=JSON` against the then-current MySQL structure, using
an 18-day window, showed bounded operational checks rather than full-history
scans. Unique indexes from 1B.2 later supplied the missing actor+instance
prefixes. Re-run EXPLAIN if the command is widened.

## Interpretation

A clean **FAIL** result means no checked mandatory invariant was violated among
candidates in that window. WARN lines are informational. The command does not
prove:

- ownership of anonymous identity before the recorded identity cutover;
- absence of actors split between anon and user namespaces unless claimed;
- complete client-to-Metrika delivery;
- correctness of historical periods before instrumentation coverage.

`instrumentation_version=2` means known write semantics, not identity cutover
and not full analytics reliability.

Trusted identity cutover is SHA `6c53989`, validation completed
2026-09-13T21:24:38Z. See [identity.md](identity.md). Anonymous ownership
before that SHA is not retroactively trusted. Phase E (mandatory signature) is
implemented in code; unknown unsigned cookies are not adopted, but a returning
unsigned key with existing history is upgraded in place. Do not treat it as
production-trusted until the live check after deploy.

Canonical product KPIs (players, new players, completion rate, DAU/WAU/MAU,
retention, core segments) are **not** produced by this quality command. They
live in [definitions.md](definitions.md) and `manage.py product_metrics`.
`product_metrics` warns and sets `legacy_contaminated` if `--since` is before
the cutover; this checker does not compute those KPIs.

## Post-deploy runbook

After application rollout, operators should:

1. record the deployment timestamp and code revision;
2. confirm migrations `0195` and `0204_player_analytics_physical_uniques` are
   applied (`0204` is state-only; do not recreate physical indexes);
3. confirm the nine EXACT unique indexes are unchanged;
4. verify new live start/completion rows are version `2`;
5. after all instances run the new identity code, run this command on a short
   window wholly after rollout;
6. manually confirm issuance, revisit, header-only spoof rejection, signup
   auto-claim, login without auto-claim, logout rotation;
7. compare backend live completions with Metrika acknowledgement coverage using
   `report_yandex_goals`;
8. investigate failures without editing or backfilling production data;
9. only then record the identity cutover SHA/timestamp in the operations log.

This runbook was completed for Stage 1C on SHA `6c53989` at
2026-09-13T21:24:38Z (account flows + short-window QC). Step 7 (Metrika
coverage) is not part of the identity cutover gate.

Local tests do not complete this production verification.

## Rollback

Application rollback reverts to trusting client header/POST actor fields and
stops logout rotation. HMAC cookies are ignored by old code. Do not reverse
`0204` or drop the nine unique indexes. Do not reverse `0195` during a rolling
rollback. Already-migrated signup claims are not automatically undone. The
quality command can be omitted from scheduling during rollback; it never
mutates data.
