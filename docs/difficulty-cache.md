# Daily-game difficulty cache

Status: implemented 2026-08-27.

This document describes how Interoves stores and refreshes the 1–5 star
difficulty badges for the scheduled daily games (Лесенка / ladder,
Алфавитка / alphabetty, Салатик / salad, Цензурка / censorly). It is the source of truth for the
scheduler; the code comments only mark the non-obvious race and locking bits.

## 1. Purpose

Difficulty is an aggregate over every player of one edition: active time,
errors, help, unfinished rate. Each component is compared twice — with the
same-type historical baseline, and every returning player with their own
earlier editions (section 14) — and the two ratios are blended. That
aggregation is too expensive to run when someone opens a page, and it gets
more expensive as the archive grows.

Pages therefore **only read** a stored snapshot (`DailyGameDifficulty`).
They never call `calculate_game_difficulty()`. If no visible snapshot exists,
the badge is omitted.

A minute cron on Elastic Beanstalk refreshes snapshots in the background.

## 2. Architecture

Production uses **MySQL on RDS**, not PostgreSQL. The scheduler still follows
the same claim design (`SELECT … FOR UPDATE SKIP LOCKED` is supported on
MySQL 8). `flock` on a single EB instance is extra local protection only.

```mermaid
flowchart TD
    event[Attempt / hint / completion / task edit]
    mark[Increment data_revision, set dirty]
    scheduler[Minute difficulty cron]
    db[(MySQL RDS)]
    claim[Claim due row + lease + token]
    calc[calculate_game_difficulty]
    norm[GameDifficultyNorm cache]
    snapshot[DailyGameDifficulty snapshot]
    page[Game / hub / archive page]

    event --> mark
    mark --> db

    scheduler --> claim
    claim --> db
    claim --> calc

    norm --> calc
    calc --> snapshot
    snapshot --> db
    snapshot --> page
```

No Celery, Redis queue, SQS, or Lambda is involved. The database row is the
queue.

## 3. Lifecycle of one edition

1. A `GameTaskGroup` for a supported daily game is created (support
   console, admin, or backfill). `ensure_daily_difficulty_row` inserts
   `DailyGameDifficulty` with `data_revision=1`, `calculated_revision=0`,
   `dirty=True`, `refresh_not_before=published_at` (or `now` if unknown).
2. Until `published_at`, the row is not due. Pre-created future editions sit
   quietly.
   If the publication schedule changes before the first calculation, both
   `published_at` and `refresh_not_before` are synchronized. This prevents an
   edition moved earlier from remaining asleep until its former date. A
   `Game` save that changes the type's `*_publish_start` tag synchronizes the
   complete queue in the same database transaction; the minute worker remains
   a fallback repair for recent rows.
3. After publish, the next cron tick can claim the row and calculate. Until
   `n >= 5` the public badge stays hidden; the snapshot still exists.
4. A later attempt / hint / letter reveal / completion runs
   `mark_game_difficulty_changed`: cheap `data_revision = data_revision + 1`,
   `dirty=True`. No aggregation in the request. Editing one of the
   edition's tasks runs `mark_game_difficulty_content_changed`, which also
   stamps `content_changed_at` and, for a published edition, releases the
   current throttle so the next minute tick recalculates.
5. Cron will not calculate again until `refresh_not_before`. That timestamp
   is **throttle**, not a scheduled rebuild of clean games.
6. When the throttle expires and the row is still dirty, cron claims it,
   recalculates, writes the snapshot, and sets the next `refresh_not_before`
   from the cadence table.

## 4. Cadence

Defined in `games/difficulty.py` as `REFRESH_INTERVALS` /
`REFRESH_INTERVAL_OLD`.

Age counts from `published_at` or from `content_changed_at` (the last task
edit), whichever is newer.

| Age | Max refresh frequency |
|---|---|
| < 1 hour | 1 minute |
| 1–6 hours | 10 minutes |
| 6–24 hours | 30 minutes |
| 1–3 days | 2 hours |
| 3–7 days | 6 hours |
| 7–30 days | 24 hours |
| > 30 days | 7 days |
| unknown `published_at` | 7 days |

Cadence is the **maximum** frequency for a **dirty** game. A clean historical
edition is not recalculated just because a week passed. If nobody plays it,
it generates no cron work.

If a month-old edition suddenly gets a new player, `data_revision` increases.
When `refresh_not_before` is already in the past, the next tick may claim it
immediately, then throttle at the 7-day interval.

## 5. Revisions

`DailyGameDifficulty` keeps two counters:

- `data_revision` — incremented atomically (`F()` expression) on relevant
  events.
- `calculated_revision` — the revision the last successful snapshot used.

`dirty` is denormalized `data_revision > calculated_revision`, so MySQL can
index due rows (`games_dgd_due_idx` on `(dirty, refresh_not_before)`). MySQL
has no PostgreSQL-style partial index `WHERE dirty`.

Race the counters exist to close:

```text
worker claims revision 10
→ a new attempt commits, data_revision = 11
→ worker writes calculated_revision = 10
→ dirty stays true because data_revision != 10
```

The new attempt is not lost. The next allowed tick recalculates.

## 6. Distributed claim

`games/difficulty_refresh.py` / `claim_due_daily_difficulties`:

1. Short transaction.
2. Select due rows (`dirty`, `refresh_not_before <= now`, lease missing or
   expired), newest `published_at` first, `LIMIT`.
3. `select_for_update(skip_locked=True)` when the backend supports it.
4. Set `refresh_claim_token = uuid4()` and `refresh_claimed_until = now + 5
   minutes` (`REFRESH_CLAIM_LEASE`).
5. Commit. Calculation runs **outside** that transaction so a slow aggregate
   does not hold row locks.

A second `UPDATE … WHERE lease expired` is applied per row so SQLite tests
(and a backend without `SKIP LOCKED`) still cannot double-claim.

`flock` in `scripts/difficulty_cron.sh` only prevents two processes on **one**
machine. Rolling deploys run two instances; they coordinate through MySQL.

## 7. Crash recovery

| Situation | What happens |
|---|---|
| Worker dies after claim | Lease expires (5 minutes). Another instance may claim. Snapshot is unchanged. |
| Calculation raises | Snapshot, revisions, and payload are left alone. `refresh_fail_count` increases, `refresh_last_error` is stored, claim is released, `refresh_not_before` moves by retry backoff: 5 min, 15 min, 1 hour, then cap 6 hours. |
| Stale worker returns after a newer worker wrote | Final `UPDATE` is `WHERE refresh_claim_token = this_worker_token`. Zero rows updated; B's snapshot is kept. |
| Success | `refresh_fail_count = 0`, claim cleared, cadence applied, `dirty` cleared only if `data_revision` is still the claimed revision. |

## 8. Historical norm

`GameDifficultyNorm` stores one cached baseline per game type:

- `typical_time`, `typical_errors`, `typical_help_rate`, `typical_unfinished_rate`
- `version`, `calculated_at`, `payload` (includes per-metric sources)

The minute cron refreshes a type when the cache is missing or older than
`NORM_REFRESH_INTERVAL` (24 hours). Rating a single edition reads this row; it
does **not** re-aggregate the whole archive.

`DailyGameDifficulty.norm_version` records which baseline was used. Changing
the cached typical values does **not** dirty the historical archive. A 4-second
drift in typical time must not rebuild hundreds of old badges.

A real formula or baseline change is a **manual rebuild** (section 10).

## 9. Cron / infrastructure

> **Operational hold (2026-10-03): keep the combined production reset disabled.**
> The legacy `difficulty.refresh` path invokes the daily progress reset before
> calculating snapshots. The reset can hold `DailyGameDifficulty` row locks
> while rebuilding chain state. The production `interoves-difficulty-refresh`
> schedule therefore sends the minute tick (`difficulty.minute.refresh`, or its
> historical name `difficulty.censorly.refresh`), which skips the reset. Do not
> change it back to `difficulty.refresh` until the lock incident is resolved.
> Keep the background ECS worker running; it also handles projection
> refresh/reconcile and health checks.
>
> Until 2026-10-21 the censorly-named tick refreshed **only** censorly, so
> ladder, alphabetty and salad were recalculated just by the hourly health
> check. The minute tick now covers every game (`run_minute_difficulty_refresh`).

| Item | Location |
|---|---|
| Command | `python manage.py refresh_daily_difficulty --limit 10` |
| Schedule | EventBridge Scheduler `interoves-difficulty-refresh` → `interoves-background` |
| EB config | `.ebextensions/difficulty_cron.config` |
| Script (repo) | `scripts/difficulty_cron.sh` |
| Script (instance) | `/opt/interoves/difficulty_cron.sh` |
| Log | `/var/log/difficulty_cron.log` |
| Telegram cron | `.ebextensions/telegram_cron.config` — **separate** job |

The minute tick (`MINUTE_REFRESH_BATCHES`) claims up to 10 due ladder,
alphabetty and salad editions, then at most one censorly edition, to stay
within the background worker's 1 GiB memory limit. Censorly attempt
state/comment blobs are deferred during aggregation, and the tick skips the
daily progress reset.

An hourly health check runs at minute 7 through the same EB script with a
separate local lock. It repairs queue metadata, runs an extra refresh tick,
then checks for missing snapshots, published editions never calculated,
remaining due work, and calculation failures. If the queue is still unhealthy,
it sends an admin Telegram message. Command: `manage.py check_daily_difficulty`.

Difficulty refresh is not invoked from `telegram_cron.sh`. A hung Playwright
screenshot must not block ratings; a difficulty exception must not block the
ladder channel post.

`flock` on `/var/lock/difficulty_cron.lock` is local-only.

## 10. Manual operations

Use the project venv (`../venv/interoves_django/bin/python`).

```bash
# One cron tick (also what EB runs)
python manage.py refresh_daily_difficulty --limit 10

# Same, but only one game type
python manage.py refresh_daily_difficulty --game ladder --limit 10
python manage.py refresh_daily_difficulty --game-type salad --limit 5

# Show due rows without claiming
python manage.py refresh_daily_difficulty --dry-run --limit 20

# Rebuild one placement (clears a stale claim first)
python manage.py recalculate_daily_difficulty --placement-id 123

# Rebuild every edition of one type (two-pass: metrics, then ratings)
python manage.py recalculate_daily_difficulty --game ladder

# Rebuild the whole supported archive
python manage.py recalculate_daily_difficulty --all

# Create missing snapshot rows for historical editions
python manage.py backfill_daily_difficulties
python manage.py backfill_daily_difficulties --game alphabetty --dry-run
```

Django admin: open `DailyGameDifficulty` and click «Состояние очереди». The
dashboard shows the minute-worker heartbeat, due / delayed / leased / failed /
never-calculated / missing counts, a per-game breakdown and the first 100 dirty
rows. It provides two recovery operations:

- «Восстановить метаданные очереди» creates missing rows, synchronizes
  never-calculated rows with the current publication schedule and releases
  expired leases. It does not run heavy calculations.
- «Запустить один такт» synchronously claims and calculates up to 10 due rows.

The regular changelist also has «Вернуть выбранные в очередь сейчас» and the
existing immediate «Пересчитать выбранные оценки сложности» action.
`GameDifficultyNorm` remains read-only. The singleton
`DailyDifficultyQueueStatus` stores heartbeat timestamps; one row is updated
per cron tick, so it does not grow as a log table would.

## 11. Troubleshooting

**Badge missing on a published game**

- No row: run `backfill_daily_difficulties` or check that `GameTaskGroup`
  save created one (`game_id` must be `ladder` / `alphabetty` / `salad`).
- Row exists but `n < 5`: badge is hidden by design.
- Row exists, `stars` is null: same.
- `calculated_at` is null and `refresh_not_before` is later than an already
  elapsed `published_at`: publication schedule drift. Use the admin recovery
  operation; current workers also repair recent rows automatically.

**Stars not updating after new plays**

1. `data_revision` vs `calculated_revision` — if equal, the event did not
   mark the row (unexpected sender? hidden player?).
2. `refresh_not_before` — still in the future means throttle.
3. `refresh_claimed_until` — another worker holds the lease.
4. `refresh_last_error` / `refresh_fail_count` — calculation is failing;
   see `/var/log/difficulty_cron.log`.

**Row retries forever**

Look at `refresh_fail_count` and `refresh_last_error`. Backoff caps at 6
hours; the row stays dirty until a success.

**Snapshot looks too old**

If `dirty` is false, there have been no counted events since the last write.
If `dirty` is true, check the scheduler filters above. Clean games older than
30 days are **not** rebuilt weekly.

## 12. Changing cadence

Edit `REFRESH_INTERVALS` and `REFRESH_INTERVAL_OLD` in `games/difficulty.py`.
Keep the comment that cadence throttles dirty rows only. Already-written
`refresh_not_before` values stay until the next successful calculation; they
are not rewritten globally.

`REFRESH_CLAIM_LEASE`, `REFRESH_RETRY_INTERVALS`, `NORM_REFRESH_INTERVAL`,
and `DUE_REFRESH_LIMIT` live in the same module.

## 13. Changing the formula

Two different operations:

- **Refresh existing snapshot** — the minute cron / `refresh_daily_difficulty`
  recalculates due dirty rows with the current code and the cached norm.
- **Full historical rebuild after a formula change** —
  `recalculate_daily_difficulty --all` (or `--game …`). That two-pass rebuild
  re-observes every edition, refreshes `GameDifficultyNorm`, then re-rates.
  Do this after changing weights, bounds, or how metrics are collected.
  Updating the cached norm alone will not rebuild the archive.

## 14. Formula details

Calibration constants live at the top of `games/difficulty_domain/core.py`.

**Per-player rows.** `observe_actor_results` produces one row per visible
player: completed, abandoned, time, attempts, errors, help, unfinished.
`aggregate_actor_results` turns them into the edition metrics. The rows are
stored in `DailyDifficultyActorResult` with every snapshot write (same claim
guard) and by pass one of `recalculate_daily_difficulty`.

**Time counts players who have not finished.** The median solve time is a
Kaplan–Meier median: a player still on the puzzle is right-censored at the
active time played so far, and a player idle for 30 minutes without solving
(abandoned) never solves. If at most half have solved, the stored
`median_time` is a lower bound (`median_time_censored = true`). Such a bound
counts only when it is already at or above the norm; below it, time is
unknown. Right after release this shows a hard edition as hard before anyone
finishes it.

**A rating needs an outcome.** A badge is visible only with `n >= 5` and a
time or unfinished score. Help alone (the first minutes) is not enough.

**Within-player comparison.** For each player with at least
`PAIRED_MIN_HISTORY` earlier editions of the same game (latest
`PAIRED_HISTORY_WINDOW` before this edition's `published_at`):

| Component | Paired ratio |
|---|---|
| time | censored median of `log(time / own median solve time)`, exponentiated |
| attempts, errors | median of `(value + 1) / (own median + 1)` |
| help, unfinished | observed share against the players' own average shares, with the usual epsilon |

A ratio of 1 means "as usual for the players who came". Each component is
blended with the classic ratio geometrically, weight
`paired_n / (paired_n + PAIRED_PRIOR_N)`. The payload stores
`ratios_classic`, `ratios_paired`, `paired_n` and `blend_weights`; `ratios` is
the blend that is scored.

**Task edits.** A published task edit rotates `attempt_revision`. Each player
is judged on one revision per task: the one they first solved, else the one
they played last. Before 2026-10-21 every older-revision attempt was dropped,
which removed most of an edition's players after a fix.

Changing any of this is a formula change: run
`recalculate_daily_difficulty --all` (section 13). The rebuild writes the
per-player rows of every edition before rating, so the paired comparison is
complete from the first run.
