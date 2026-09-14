# Product analytics definitions

This document describes the product analytics that the current application can
actually support. It does not reinterpret historical rows from their names.
Unless stated otherwise, calendar metrics use `Europe/Moscow`, the configured
project timezone (`TIME_ZONE`), while timestamps are stored as timezone-aware
values.

## Identity terms

### Visitor

A visitor is an identity defined by an external web-analytics system, currently
Yandex Metrika. A Metrika visitor is not equivalent to a backend analytics
actor: cookies may be blocked or reset, Metrika may be unavailable, and backend
identity may change between `anon_key`, `user_id`, and `team_id`.

### Current backend actor

There is no `AnalyticsActor` model. For each `PlayerStartedGame`,
`PlayerCompletedGame`, or `PlayerAnalyticsState` row, the actor is the one and
only non-null identity among:

- `user_id`: a registered Django user;
- `anon_key`: an anonymous browser-held bearer key;
- `team_id`: a team identity supported by the schema.

Rows with zero or more than one populated identity are invalid. In current game
write paths, an authenticated `analytics_user` is preferred even while playing
in team mode, so most new personal product events are user- or anon-attributed.
The three identity namespaces are distinct. They become one history only when
the existing explicit anonymous-progress migration physically reassigns or
merges rows.

This is a reporting key, not proof of a person. After Stage 1C the anonymous
`anon_key` is a server-issued opaque UUID stored in cookie `interoves_anon`,
with an HttpOnly HMAC signature cookie. After Phase E an unsigned cookie is
adopted only when that key already has history; otherwise a fresh identity is
issued. POST, header, and URL values are not authority for attribution. See
[identity](identity.md).

## Game and audience metrics

### Placement and started game instance

A placement is a `GameTaskGroup` linking one `Game` to one canonical
`TaskGroup`. Its stored analytics instance identifier is:

```text
game_instance_id = game.id + ":" + task_group.id
```

The current model has no separate playthrough or attempt-run identifier. The
supported started game instance is therefore one actor plus one placement. A
second genuine replay of the same placement by the same actor is not represented
as a second started instance.

### Player

A player is a unique current backend actor with at least one valid
`PlayerStartedGame` row. Count actors by the tagged union of identity type and
value, not by coalescing raw numeric/string values across namespaces.

Source of truth: backend `PlayerStartedGame`.

Deduplication: one `(identity_type, identity_value, game_id, task_group_id)`.
Rows failing the identity, placement, instance-id, or game-kind checks in
`check_product_analytics` are not valid inputs.

### New player

A new player in period `[from, to)` is an actor whose earliest **trusted**
`PlayerStartedGame` (`started_at >=` Stage 1C cutover `6c53989` /
`2026-09-13T21:24:38Z`, valid identity, not backfilled) lies in that period.
The lookup is not restricted to the reporting window, but it **is** restricted
to trusted history. Pre-cutover play is invisible here, so an actor who only
played before the cutover and then starts again after it looks new. That is
intentional; do not paper over it with Attempt heuristics.

This is “new in trusted backend history”, not first-ever human visit.
Anonymous and registered histories stay separate until an explicit claim
physically reassigns rows. Phase E (signed cookies) does not move this
cutover.

### Active day

An active day is a `Europe/Moscow` calendar date on which an actor has at least
one valid backend `game_start`. Multiple starts on that date count once for the
actor. Calendar conversion is `astimezone(Europe/Moscow).date()`, not UTC.

### DAU, WAU, MAU

These are computed by `games.product_metrics` / `manage.py product_metrics`,
not by `check_product_analytics`.

- **DAU average**: mean unique actors with an active day, over **complete**
  Moscow calendar days contained in `[since, until)`. A partial first or last
  day is omitted.
- **WAU**: unique actors with a trusted start in the rolling 7 days ending at
  `until`.
- **MAU**: the same with 30 days.
- Unless the window is explicitly legacy-contaminated (`since` before cutover),
  WAU/MAU clip their lookback to the trusted identity cutover rather than
  inventing pre-cutover audience.

### `game_start`

The canonical backend record is `PlayerStartedGame`. Page view alone is not a
start. The first qualifying server request for an actor and placement creates
the row:

- Salad: first submitted non-empty path, including a wrong/off-topic path, or a
  letter hint;
- Ladder/raddle: first submitted word or raddle hint;
- Alphabetty: first submitted word or letter hint;
- Replacements: first submitted line;
- other supported task formats: first persisted attempt or real hint routed
  through the generic instrumentation.

The logical key is actor plus placement. The application uses `create_or_reread` plus nine physical MySQL UNIQUE indexes
(actor + `game_instance_id` for starts/completions; actor for lifecycle state).
Django models declare matching unconditional `UniqueConstraint`s. Migration
`0204_player_analytics_physical_uniques` records that schema in Django state
without repeating DDL.

### `game_complete`

The canonical backend record is `PlayerCompletedGame`. Completion is confirmed
by server-side state logic, not by a client or Metrika callback:

- Salad: all answer indices are solved in the saved state;
- Ladder/raddle: all parsed ladder words are solved;
- Alphabetty: saved state has `won=true`;
- Replacements: all parsed lines are solved.

The current completion pipeline does not cover every game format. Assisted or
revealed state may satisfy the same terminal server condition. A completion row
can also be historical (`is_backfilled=true`); that timestamp is the time the
row was reconstructed, not necessarily the original completion time.

### Completion rate

The canonical product completion rate is **cohort-by-start**:

```text
unique actor-placements started in [since, until) that later complete
/ unique actor-placements started in [since, until)
```

The completion may fall after `until`. A completion in the window whose start
was before `since` is **out** of this rate (it can still appear in the raw
`completions` count). Completing a different placement does not close the
started one. Count unique actor-placements, not the event ratio of rows.

The diagnostic event ratio is separately:

```text
number of completion rows / number of start rows
```

It is useful for detecting instrumentation anomalies but is not the product
completion rate.

### Exact retention

Let `D0` be the `Europe/Moscow` calendar date of an actor's earliest trusted
start (`started_at >=` identity cutover).

- Exact D1 retention: fraction of the D0 new-player cohort with at least one
  valid start on calendar date `D0 + 1 day`.
- Exact D7 retention: fraction with at least one valid start on calendar date
  `D0 + 7 days`.

Each actor counts at most once in a cohort and once in the retained numerator.
Report a rate only when the target Moscow date has **fully elapsed** before
`until` (that is, `until` is at least the next midnight after the target day).
Immature windows use `status: not_yet_observable` and `value: null`. Do not
substitute 0.

### Rolling retention

Rolling Dn retention is the fraction of the D0 cohort with at least one valid
start on `D0 + n days` or any later calendar date. It is not interchangeable
with exact retention. The current project does not store a separate rolling
retention event; it is derived from starts. The same observation-window rule
applies: immature rolling D7 is `not_yet_observable`, not 0.

### `activated_player`

The current meaning is not changed by stage 1A. An actor becomes activated when
the count of distinct `PlayerCompletedGame` instances first crosses from fewer
than three to at least three. The backend stores `PlayerAnalyticsState.activated_at`
and sends the Yandex goal `activated_player`. If three completions already exist
when state is repaired, `activation_is_backfilled=true` and no historical
activation goal is sent.

This is “third unique completion”, not signup, first start, third active day, or
a retained player. Physical unique indexes keep the distinct-completion count
stable under concurrent writes.

### Core player

Core player is a **query-defined reporting segment**, not a stored flag and not
an event. Do not write `core_player=true` onto a user or analytics row. After
the identity cutover, compute from valid post-cutover `game_start` /
`game_complete` rows, for example in a 30-day window:

- ≥7 active days / 30d;
- ≥10 completions / 30d;
- ≥20 completions / 30d;
- ≥3 active calendar weeks / 30d.

Exact and rolling retention remain separate metrics; none of these segments is
a retention event.

## Session limitations

The product-event tables contain no product `session_id` and define no inactivity
timeout, renewal rule, or cross-tab session boundary. Django authentication/session
cookies and the pending-Yandex-goal session queue are implementation details, not
a measurable product session. A Yandex Metrika visit is an external-system concept.
No session metric should be presented as canonical from these backend tables.

## Instrumentation versions and legacy boundaries

Physical values on starts and completions are deliberately limited to:

- `NULL`: legacy instrumentation, interpreted logically as version 1;
- `2`: a live start/completion newly inserted by the stage-1A write path;
- any other value: unknown and a quality-check failure.

Physical value `1` is never written. The columns are nullable, have no application
or database default, and require no backfill. Old instances in a rolling deploy
can continue writing `NULL`; new instances explicitly write `2`. Existing rows
found by a new instance are not upgraded. Backfilled rows remain `NULL`.

`instrumentation_version=2` proves only that this particular start/completion was
inserted by the new live write path with the documented event semantics. It is
**not** the Stage 1C identity cutover. The identity cutover is production SHA
`6c53989`, validated 2026-09-13T21:24:38Z; see [identity.md](identity.md).
Do not treat anonymous ownership before that SHA as fully trusted.
Phase E adopts an unsigned cookie only when that key already has history;
it is not production-trusted until the live check after deploy.
**Stage 2A product metrics use this same 1C timestamp.** Signed-cookie
enforcement does not move the trusted metrics boundary.

## Stage 2A command

Canonical numbers live in `games.product_metrics.build_product_metrics_report`
and `manage.py product_metrics`. They are not a quality-check output.

```bash
../venv/interoves_django/bin/python manage.py product_metrics
../venv/interoves_django/bin/python manage.py product_metrics \
  --since 2026-09-14T00:00:00+03:00 \
  --until 2026-09-21T00:00:00+03:00 \
  --format json
../venv/interoves_django/bin/python manage.py product_metrics \
  --game-type salad --placement 'word_salad:123'
```

`--since` / `--until` are ISO datetimes, `[since, until)`. Naive values use
the configured project timezone (`Europe/Moscow`), same as
`check_product_analytics`. Default `since` is the trusted cutover; default
`until` is now. `--game-type` filters the stored `game_kind` field;
`--placement` filters `game_instance_id`. If `--since` is before the cutover,
the report sets `legacy_contaminated=true` and the text format prints a
WARNING. Pre-cutover identity is still not trusted.

Known historical boundaries from migrations and git history:

- completion instrumentation introduced by commit `1949839` on
  2026-08-10 23:18:43 +04:00; production migration `0168` was recorded at
  2026-08-10 22:20 Europe/Moscow;
- durable backend starts introduced by commit `fdb330c` on
  2026-08-15 17:48:05 +04:00; production migration `0172` was recorded at
  2026-08-15 16:49 Europe/Moscow;
- onboarding client instrumentation changed on 2026-08-26;
- Salad start/onboarding delivery was corrected by commit `f4c9293` on
  2026-08-28 18:39:02 +04:00.

These dates describe code availability, not complete production coverage. The
stage-1A deployment timestamp must be recorded after deployment. The dependable
boundary for versioned semantics is the row-level value `2`, followed by a clean
post-deploy quality check; it is not an inferred date applied to old rows.

