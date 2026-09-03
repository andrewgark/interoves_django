# Stage 1B design: MySQL-compatible deduplication

Status: stage 1B.1 code-first handling is implemented. The nine production
unique indexes and the Django constraint-state changes remain stage 1B.2 and
are not part of this release.

## Stage 1B.1 code contract

All three analytics models use the same strict create-or-reread primitive. It
first reads the exact namespace key, creates inside a local savepoint, and
recovers only a duplicate reported for the corresponding future index. After
rolling back that savepoint it reads the exact canonical key again. One row is
returned; zero rows re-raises the original database error; multiple rows raise
an invariant error. Deadlocks, lock timeouts, foreign-key failures, and
duplicates on unrelated keys are never classified as a recoverable race.

Before 1B.2, simultaneous first inserts can still both succeed because MySQL has
no unique key to arbitrate them. This is an explicit rollout limitation, not an
application-lock substitute.

Signup and activation use conditional updates (`timestamp IS NULL`) and then
refresh the canonical state. The first database winner cannot be overwritten by
a stale Python object. Completion history backfill, completion creation, counts,
and activation remain in their existing transaction and product order.

### Claim and account-merge provenance

For overlapping starts, the row with earlier `started_at` supplies the complete
bundle `(started_at, game_kind, public_game_id, is_backfilled,
instrumentation_version)`. For completions, earlier `completed_at` supplies
`(completed_at, game_kind, public_game_id, result, is_backfilled,
instrumentation_version)`. Equal timestamps keep the target bundle. Live rows
do not outrank earlier backfilled rows, and completion results have no ranking.

Lifecycle state is merged as two independent bundles. The earlier non-null
`signup_at` supplies `(signup_at, signup_method, signup_goal_acked_at)`; the
earlier non-null `activated_at` supplies `(activated_at,
activation_is_backfilled, activation_goal_acked_at)`. Equal timestamps keep the
target bundle. Fields are never assembled independently across bundle donors.
The ACK-specific safety rule below applies when the selected activation donor
is source: because payload equivalence cannot be proved, its ACK is cleared
rather than copied into target.

Placement foreign keys and `game_instance_id` must agree before an overlapping
event is merged. A mismatch is an invariant failure and is not repaired.

### ACK semantics and safe compromise

The physical row id is present in the browser's internal delivery/idempotency
key and in the signed same-origin callback token. It is not sent in the Yandex
`reachGoal` params. A start ACK can cross from source to target only when both
persisted Yandex params (`game`, `game_id`) equal the final canonical params. A
completion ACK additionally requires equal `result`. A target ACK is retained
when the target payload is unchanged.

If the final payload differs and no ACK for that exact payload exists, the
canonical ACK is null. This may cause safe repeat delivery after merge, but it
cannot falsely claim that another payload was delivered. Merge itself never
sends a goal. Activation's historical `games_completed` param is not stored, so
an ACK from a source activation bundle is cleared when that bundle replaces the
target bundle. A target activation ACK is retained only while the target bundle
stays selected. A future delivery ledger would be needed to eliminate every
possible repeat without weakening this rule; it is out of scope for 1B.1.

### Operator commands

`preflight_player_analytics_uniques` is full-key and read-only. Human-readable
aggregate output is the default; `--format=json` writes one JSON document to
stdout. It reports all nine duplicate-group counts, XOR violations, exact row
counts, table/index metadata, MySQL version, engine, collation and `anon_key`
type, transaction and metadata-lock visibility, and online-DDL eligibility. It
never emits actor values or creates report files. RDS `FreeStorageSpace` is
reported as `UNAVAILABLE`; the command does not call AWS APIs.

`apply_player_analytics_unique_index` requires an explicit `--index`. Without
`--execute` it does no DDL. Execution additionally requires the full data
preflight, explicit free-storage confirmation, operational confirmation when
database visibility is unavailable, and MySQL eligibility. It creates exactly
one index with `SET SESSION lock_wait_timeout = 30`,
`ALGORITHM=INPLACE LOCK=NONE`, verifies the full physical signature, and has no
COPY/SHARED/DEFAULT fallback. It is shipped in 1B.1 for a later controlled 1B.2
operation and must not be invoked as part of the 1B.1 deploy.

## Problem confirmed by audit

`PlayerStartedGame`, `PlayerCompletedGame`, and `PlayerAnalyticsState` declare
conditional `UniqueConstraint`s in Django. The configured MySQL backend does not
support partial indexes, so production has ordinary lookup indexes but none of
those conditional unique indexes. `get_or_create` reduces routine repeats but
cannot guarantee uniqueness under concurrent requests without a database unique
key.

The 2026-09-02 read-only production audit found no existing duplicate groups and
approximately:

- 8,179 `PlayerStartedGame` rows;
- 12,982 `PlayerCompletedGame` rows;
- 650 `PlayerAnalyticsState` rows.

Those counts lower current build cost, but they must be re-measured immediately
before DDL. A clean past audit is not a precondition check for a future deploy.

Stage 1A adds only nullable `instrumentation_version`; it does not affect the
dedupe key.

## MySQL `NULL` behavior

MySQL permits multiple `NULL` values in a nullable `UNIQUE` index. Therefore
three ordinary unique indexes can model the current exactly-one identity union:

```sql
UNIQUE (user_id, game_instance_id)
UNIQUE (anon_key, game_instance_id)
UNIQUE (team_id, game_instance_id)
```

For a valid user row, the user index enforces uniqueness while the anon/team
indexes contain `NULL` and do not collide; the equivalent applies to anon and
team rows. This also explains why one composite index containing all three
nullable identities would not work: every valid row contains two `NULL`s, so
duplicates would still be permitted.

For `PlayerAnalyticsState`, the corresponding ordinary unique indexes are:

```sql
UNIQUE (user_id)
UNIQUE (anon_key)
UNIQUE (team_id)
```

Reference: [MySQL 8.4 `CREATE TABLE`, `UNIQUE` and nullable columns](https://dev.mysql.com/doc/refman/8.4/en/create-table.html).

## Options

### A. Three ordinary unique constraints per table

Add the separate keys shown above to each event table and the three single-column
keys to lifecycle state.

What it guarantees: duplicate logical rows for each populated identity type are
rejected atomically by MySQL, including concurrent inserts. It fits the current
schema and `NULL` semantics and requires no actor table.

What it does not guarantee: exactly one identity is populated; valid placement;
anon-key ownership; or equivalence between anon and registered identities. An
identity XOR check needs a separate check constraint or application validation.

Application impact: start/completion `get_or_create` already has duplicate-key
recovery around event creation. `PlayerAnalyticsState.get_or_create` should be
stress-tested under real MySQL concurrency, including transaction visibility,
before rollout. Claim/account-merge paths must also be tested because changing an
identity can collide with an existing target row and must continue to merge first.

Migration cost: nine secondary unique indexes in total. The tables are presently
small, but each build reads/sorts its table and consumes temporary space. InnoDB
supports in-place creation of secondary indexes and concurrent DML in general,
but briefly needs metadata locks and a concurrent duplicate can fail the build at
the end. References: [online index operations](https://dev.mysql.com/doc/refman/8.4/en/innodb-online-ddl-operations.html)
and [online DDL failure conditions](https://dev.mysql.com/doc/refman/8.4/en/innodb-online-ddl-failure-conditions.html).

Rolling deploy: additive indexes are readable by old and new code. Deploy code
that safely handles duplicate-key races before or with DDL; do not assume a model
constraint existing in Django means the production index already exists.

Rollback: indexes can be left in place during application rollback. Removing
them should be a separate online DDL only if old code genuinely requires
duplicates (no such requirement is currently known).

Assessment: smallest compatible database guarantee and the leading candidate,
subject to a fresh duplicate check and concurrency tests.

### B. Generated actor columns plus one unique key

Add generated columns such as `actor_type` and a normalized `actor_key`, then a
single unique key with `game_instance_id` (or just actor key for lifecycle state).

Benefits: expresses the tagged union in one index and can make actor-oriented
queries uniform. A generated expression can deliberately return `NULL` for an
invalid identity combination.

Costs/risks: more schema machinery, type/collation choices to normalize integer
IDs and strings, expression-version compatibility, and potentially hidden
acceptance of invalid multi-identity rows. It changes more than required to solve
the confirmed race and complicates rollback. It still does not authenticate
anonymous keys.

Assessment: useful only if query simplification or an identity check justifies
the complexity; not the default for 1B.

### C. Materialized actor type/key fields maintained by application

Store explicit `actor_type`/`actor_key` columns, backfill them, and enforce one
unique key.

Benefits: portable and easy to query.

Costs/risks: requires a backfill and dual-write transition, creates mismatch risk
between old identity columns and new fields during rolling deployment, and
duplicates current identity data. This is close to introducing a parallel actor
representation without solving credential ownership.

Assessment: not proportionate while three nullable identity columns remain the
accepted architecture.

### D. Application locks or serialized `SELECT ... FOR UPDATE`

Serialize each actor-instance creation with an advisory lock, lock row, or
dedicated mutex record.

Benefits: can work without new unique indexes.

Costs/risks: there is no existing row to lock for a first event, advisory locks
are connection-scoped, failure handling is difficult with pooling, and every
write path/merge/backfill must obey the same protocol. A missed path restores the
race. It adds latency and operational failure modes.

Assessment: inferior to a database unique key for this invariant.

## Key-by-key evaluation

### `(user_id, game_instance_id)`

- Enforces registered-user uniqueness; rows with `user_id=NULL` do not collide.
- Supports one account across multiple devices naturally.
- Concurrent same-user requests converge on one row if application retry handles
  duplicate-key errors.
- Account merge can collide and must continue merging/dropping the source row
  before reassignment.

### `(anon_key, game_instance_id)`

- Enforces uniqueness for the same presented anonymous bearer key.
- Does not prevent an attacker/client from presenting another key or one browser
  from generating several keys.
- Collation must be checked. The opaque key should use equality semantics
  consistent with current lookups; changing collation in the same migration is
  out of scope.
- Claim migration can collide with a user row and must merge deterministically.

### `(team_id, game_instance_id)`

- Enforces the schema-supported team actor namespace.
- Current product write paths often prefer `analytics_user`, but team rows remain
  readable and must not be ignored.
- Team/account merge or deletion behavior should be exercised before DDL.

### `PlayerAnalyticsState`

Separate unique keys on `user_id`, `anon_key`, and `team_id` enforce one state per
identity. They do not prevent one malformed row from having multiple identities;
the stage-1A quality command detects that condition. Adding an XOR database check
should be evaluated separately for compatibility with historical rows and old
writers.

## Proposed 1B rollout, not yet authorized

1. Re-run bounded and full-key aggregate duplicate diagnostics; report only
   counts, never actor values.
2. Verify `SHOW INDEX`, table engine/collation, MySQL version, row counts, table
   size, free disk/temp capacity, and long transactions holding metadata locks.
3. Add MySQL concurrency tests for start, completion, activation state, claim,
   and account merge.
4. Decide whether to represent the indexes as Django state plus controlled online
   database operations, since conditional model constraints currently misdescribe
   MySQL reality.
5. Build one index at a time with an explicitly supported online algorithm/lock;
   monitor metadata-lock wait and application errors.
6. Re-run duplicate diagnostics after each build and after rolling deployment.

Do not delete or heal duplicates automatically. If the fresh preflight finds any,
stop and agree on a deterministic remediation separately.
