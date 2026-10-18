# EB replacement loop, 2026-10-18

Production environment: `interoves-web-green-lb`, `eu-central-1`.
All times below are UTC. Application version at investigation start:
`app-green-1d4dc0ce-20261018033735`.

## Evidence

By 10:49, Auto Scaling had terminated 22 instances since 00:00:
20 after ELB health check failures and two after user requests during releases.
The group maintained two `t3.small` instances, with 1,913 MiB usable RAM per
host and no swap. ALB health checks used `/health/live/`, a five-second timeout,
a 15-second interval, and five failures before marking a target unhealthy.

CloudWatch showed 753 ELB-generated 5xx and 106 target-generated 5xx against
36,969 requests by approximately 10:53. These are counts, not unique users.

On `i-09a55663de23a4581`:

- Application logs stopped at 10:34:58. Host metrics stopped after 10:34.
- Network input during 10:30–10:35 was 1,722,908,834 bytes.
- EBS reads rose from 38.6 MB during 10:30–10:35 to 31.1 GB during
  10:35–10:40 and 39.4 GB during 10:40–10:45.
- Auto Scaling terminated the instance at 10:43:42 after an ELB check failure.
- The last memory sample still had about 521 MB available; minute samples
  cannot establish the subsequent peak. No OOM kill was captured in the
  retained kernel logs or console.

RDS was available, with daily CPU maximum around 14%, at most 36 connections,
read latency at most 5 ms, and negligible disk queue depth. RDS saturation
does not explain the observed host failures.

## Query amplification

RDS slow logs immediately before the failure contain repeated queries from
the affected host selecting an actor's Censorly attempts with a join to
`games_task`, including the complete `tags` JSON on every result row.
At 10:34:56 and 10:35:04, each query returned 117 rows for the same task.
The task's `tags` value measured 1,192,652 bytes in MySQL. That is
139,540,284 bytes of repeated task JSON per history query, before Python
decodes each copy into separate dictionaries and lists.

The query comes from `AttemptManager.get_all_attempts()` using
`select_related('task')`. Censorly `_meta_context()` calls this method through
`get_attempts_info()` while building the page/state meta bar.

The repeated payloads are a confirmed application defect. Host memory pressure
and file-page reclaim are the strongly supported explanation for the freeze
and EBS read surge. A retained OOM event or a stack from a frozen host was not
available at the time of the initial diagnosis; avoid claiming a proven kernel
bug or an event-loop deadlock.

## Fix

When the caller already supplies a `Task` instance, load only attempt rows
and attach that same task object to each attempt. This removes the join,
repeated transfer, and repeated JSON decoding while retaining eager
`attempt.task` access and the existing actor, game, replay, skip, and ordering
behavior. Calls supplying a task ID retain their previous eager-loading path.

Validation: two dedicated history-loading tests and 54 existing Censorly,
wall, mode-isolation, and chain-recheck tests passed. The dedicated tests
assert that task-instance history uses one query without a task join and
that reading `attempt.task` does not cause additional queries.

## Production verification

Released commit `cf443724` as
`app-green-cf443724-20261018110923` through `./deploy.sh --deploy` after user
approval. EB completed the update at 11:23:23; the release script completed
after the Green health wait, all 34 page smoke checks, and refresh-gate
cleanup. Both retained instances run this version and report `Ok`;
the public version endpoint reports `cf443724` with `deploying: false`.

At 11:17, a read-only check on the first updated production instance loaded
the same 117-attempt history in one query without a task join: 16.0 ms,
with 0.77 MiB increase in process peak RSS. All returned attempts shared
the supplied task object. The measurement excludes loading that one task
before the history query, which is also how the normal caller uses it.

At 11:22 both updated hosts answered local health checks with 200 and had
approximately 760 MiB and 1,056 MiB of available RAM. The last old instance
was intentionally terminated as excess deployment capacity, not as another
health-failure replacement.

The initial postdeployment observation is short. Continued absence of the
replacement loop under normal traffic is needed for longer-term confidence;
the query amplification itself has been verified as removed in production.

At 11:28:29, EB remained `Ready/Green` with both instances `Ok`, and there
were no new Auto Scaling activities since 11:24. CloudWatch had published
355 requests for minute buckets 11:24–11:27 and no ELB- or target-generated
5xx in the postdeployment window. The public health endpoint returned 200
in 0.111 seconds. These are initial observations, not a guarantee against
unrelated future failures.

## Recurrence at 14:59–15:01 UTC

The environment was running `app-green-a1ab52ba-20261018135332` with three
instances. Two hosts stopped publishing application/system logs and metrics:

- `i-0d22e364aadb73f10`: last application log at 14:59:37; terminated by
  Auto Scaling at 15:01:19 following an ELB health check failure.
- `i-04bc046c192ee57ca`: last application log at 15:01:30; SSM commands
  remained pending while the host became unhealthy and was replaced.

Both hosts had around 680–694 MB of available RAM in their last minute
samples. During the 15:00 five-minute bucket, EBS reads jumped to 38.6 GB
and 20.1 GB respectively. Retained EC2 consoles did not contain an OOM kill.

The RDS slow log identifies a second heavy query path immediately before
each freeze. `new_anon_migrate_count()` loads an anonymous actor's entire
attempt history with `select_related('task', 'game')`, including task JSON
and attempt payloads, to count transferable attempts and choose one example
link. This public endpoint for authenticated users is polled by the UI.

At 14:59:42, the query from the first host returned 270 attempt rows. A
read-only SQL aggregate measured 218,960,843 bytes of repeated Censorly task
JSON across 248 of those attempts, covering only five distinct puzzles.
At 15:01:30, the query from the second host returned 1,538 attempt rows;
137 Censorly rows repeated 140,734,617 bytes of task JSON across four puzzles.
The aggregate returned sizes and counts only; the expensive history query
was not rerun during investigation.

The morning fix removed this amplification from `get_all_attempts()`, but
the migration-prompt counter still had its own full-task join. The new query
timings, volumes, and disk behavior strongly support memory exhaustion and
file-page reclaim as the recurrence mechanism; no OOM event was captured.

The counter fix performs the unsolved-attempt count in SQL and streams only
attempt identity, task-group identity, game identity, and game name until
it finds a usable example link. It excludes task JSON, checker text, attempt
text, and attempt state. Existing counting, solved-task exclusion, example
ordering, and legacy game resolution are retained. Regression coverage also
checks a missing example link followed by a usable one.
