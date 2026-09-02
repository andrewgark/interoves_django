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
email, username, or other PII. Any failed mandatory invariant raises
`CommandError`, which gives `manage.py` a non-zero process exit status.

## Checked invariants

The bounded candidate set is selected by `started_at`, `completed_at`, or
`PlayerAnalyticsState.updated_at`. It checks:

- exactly one of `user_id`, `team_id`, and `anon_key` on start, completion, and
  lifecycle-state rows;
- a later duplicate start or completion for the same actor and placement;
- completion without a matching start for the same actor, placement, and
  `game_instance_id`;
- live v2 completion timestamp earlier than its first matching start;
- missing `GameTaskGroup(game, task_group)` placement;
- `game_instance_id` inconsistent with `game.id + ":" + task_group.id`;
- `game_kind` inconsistent with the current mapping;
- start/completion more than five minutes in the future;
- physical instrumentation version outside `NULL` or `2`;
- a backfilled row incorrectly marked version 2.

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

No index is added in 1A. Production `EXPLAIN` results and any index recommendation
must be recorded in the implementation report. If a plan is unsafe at production
volume, schedule a separate additive-index change rather than widening this
stage.

### Production plan snapshot, 2026-09-02

Read-only `EXPLAIN FORMAT=JSON` against the current MySQL structure, using an
18-day window, showed:

- start candidate scan: range/skip-scan, about 951 rows examined;
- registered-user duplicate-start check: about 3,849 outer index rows and about
  34 task-group-index rows per matching inner probe; estimated query cost about
  11,951;
- registered-user completion-without-start: about 6,999 outer index rows, with a
  materialized anti-join over about 3,849 eligible start rows; estimated cost
  about 1,829;
- completion-placement check: optimizer chose a full scan of about 13,093
  completion rows plus the 1,428-row unique placement index; estimated cost
  about 2,277;
- lifecycle-state window: full scan of 655 rows; estimated cost about 67.

At the audited sizes these are bounded operational checks, not heavy production
scans. They are not good long-term plans: the user duplicate branch in particular
lacks `(user_id, game_instance_id)` or equivalent uniqueness/lookup support, and
the optimizer may materialize a sizeable eligible-start subset for the anti-join.
The three actor-specific unique indexes evaluated in [stage 1B](1b-mysql-deduplication.md)
would also provide the missing lookup prefixes. Their creation remains separately
gated; until then, run short post-deploy windows and monitor duration before using
the 31-day maximum.

## Interpretation

A clean result means no checked invariant violation was found among candidates in
that window. It does not prove:

- ownership or continuity of anonymous identity;
- absence of actors split between anon and user namespaces;
- database-enforced uniqueness under concurrent writes;
- complete client-to-Metrika delivery;
- correctness of historical periods before instrumentation coverage.

Version 2 means known write semantics and eligibility for these checks, not full
analytics reliability.

## Post-deploy runbook

After migration and application rollout, operators should:

1. record the deployment timestamp and code revision;
2. confirm migration `0195_product_analytics_instrumentation_version` is applied;
3. verify new live start and completion rows are version `2`, while old and
   backfilled rows remain `NULL` and physical value `1` is absent;
4. during a rolling deploy, accept temporary new `NULL` rows from old instances;
5. after all instances run the new code, run the command on a short window wholly
   after rollout, then daily windows, never exceeding 31 days;
6. compare backend live completions with Metrika acknowledgement coverage using
   `report_yandex_goals`, understanding that blocked/unavailable Metrika is not a
   backend invariant violation;
7. investigate failures without editing or backfilling production data.

Local tests do not complete this production verification.

## Rollback

Application rollback is safe while the nullable columns remain: old code ignores
them and writes `NULL`. Do not reverse migration `0195` during a rolling rollback,
because new instances may still reference the columns. A later, separately
approved cleanup can remove them only after all code is rolled back and no reader
depends on them. The quality command can simply be omitted from scheduling during
rollback; it never mutates data.
