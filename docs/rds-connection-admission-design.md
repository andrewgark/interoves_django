# RDS connection admission and projection lock design

Status: local HTTP admission prototype implemented; no production cap or
deployment setting is enabled. No production resources are changed by this
document.

## Evidence boundary

The 14 October 2026 investigation verified MySQL 8.4 with runtime
`max_connections=80`, `Max_used_connections=82`, and a cumulative
`Connection_errors_max_connections=29`. Green had four steady instances and
five during `RollingWithAdditionalBatch`. Four ECS worker services had one
task each and `maximumPercent=200`. Live Python process/thread counts and
connection attribution were not available. Therefore this document defines a
budget equation, not numeric per-process caps.

## Admission options

| Option | Hard per-process ceiling | Covers database sessions before view | Failure/cancellation behavior | Queue and fairness | Deployment/global budget | Assessment |
|---|---|---|---|---|---|---|
| A. ASGI admission gate before Django | Yes, if acquired before awaiting `django_asgi_app`; each admitted HTTP request enters Django's request-specific `ThreadSensitiveContext` | Yes: Django middleware, sessions, auth and views all run inside the gate | `try/finally` around `await app(...)` releases on normal return, exception, and ASGI task cancellation | Must use non-blocking or short bounded wait; never an unbounded semaphore wait. Return 503 directly from ASGI without touching the DB | Per-process only; aggregate via configured process/instance maxima and deployment surge | Preferred web control after measuring. It bounds request-created thread-sensitive contexts without wrapping ORM calls |
| B. Semaphore around ORM/DB calls | Only for instrumented call sites | No: session/auth middleware and unwrapped ORM paths bypass it | Nested acquisitions inside `atomic()` can deadlock; cancellation and sync thread lifetime complicate release | Queues can grow at each call site; unfair across requests | Hard to sum because call sites and aliases vary | Do not implement globally. Avoid arbitrary nested ORM semaphores |
| C. Limit HTTP requests per instance | Yes, if enforced before Django request handling; can be the ASGI gate in A or a verified proxy/nginx upstream cap | Yes only when before Django | Must release after response completion/cancellation; a queue needs a strict cap | Nginx/proxy queueing can hide overload and increase latency unless bounded | Per-instance; multiply by all active and surge instances | Technically viable; application ASGI gate can emit a specific 503 and expose in-flight metrics. Nginx limits need deployment-specific validation |
| D. Limit worker concurrency | Yes per service/process when all worker DB work uses the limited execution path | Worker tasks have no HTTP session middleware | Sequential `run_worker` loop is bounded; integrations `ThreadPoolExecutor` must be explicitly bounded and clean connections. Task replacement may briefly double tasks | Queue remains in SQS; use visibility/lease semantics and avoid local unbounded submission | Per task; account for ECS `maximumPercent` and every service | Implement explicit service budgets and stagger worker rollouts; verify queue and pool configuration first |
| E. Change process/deployment topology | Can reduce total instances/processes but does not bound concurrency within an instance by itself | Depends on whether the request path is gated | More processes can add thread-sensitive executors; fewer processes may increase request latency | Depends on topology | Must model EB five-host surge, ECS up to two tasks/service, jobs and operators | Not sufficient as a standalone connection safeguard |
| F. External pool / RDS Proxy | Proxy can cap backend pool; a client-side pool can cap clients if configured | Yes for proxy-routed connections, including sessions | Adds borrow timeouts, pool failure modes and pinning; no application semaphore permit to leak | Proxy queues can add latency; must set/monitor borrow timeout and pool size | Shared pool gives a DB-wide backend ceiling if every client routes through it | Evaluate later in staging; it does not remove active transactions or lock contention |

### ASGI execution model

The `Procfile` starts one Daphne process per web instance. Django 4.2's
`ASGIHandler.__call__` enters a `ThreadSensitiveContext` for the request.
`asgiref==3.8.1` removed `ASGI_THREADS` support for the `SyncToAsync` executor;
the current `ASGI_THREADS=4` must not be used as the connection cap. A bounded
ASGI gate placed around the Django HTTP app, before the handler is awaited,
limits concurrent Django request contexts and therefore the number of
request-owned sync DB contexts. It also covers DB-backed sessions and
synchronous middleware.

The local prototype is `games.realtime.admission.DatabaseAdmissionASGIMiddleware`,
installed around Django's HTTP ASGI app before the `ProtocolTypeRouter` sends
requests to Django. `DB_ADMISSION_MAX_ACTIVE`, `DB_ADMISSION_MAX_WAITERS`, and
`DB_ADMISSION_WAIT_TIMEOUT_SECONDS` configure the per-process active cap,
finite FIFO queue, and queue deadline. With the cap unset/zero the wrapper is
disabled. This is intentional until an aggregate production budget is
approved. Overload replies are DB-free 503 responses with Retry-After,
Cache-Control no-store, and an X-Request-ID. Cancellation, exceptions, and
normal response completion release permits in `finally`; streaming HTTP
responses retain the permit until ASGI response completion.

This bounds HTTP request contexts that enter Django, including session,
authentication, synchronous middleware, and view work. It is not a global RDS
ceiling: WebSocket scopes bypass the limiter, as do work sent to independent
threads or background tasks. The application has DB-using WebSocket consumers,
so those contexts require a separately measured budget or a later per-message
admission design. Do not set the production cap until those paths, worker
concurrency, management jobs, and operator reserve are measured. Keep
`CONN_MAX_AGE=0`; it limits persistence between requests, not concurrency.

Do not wait indefinitely on the gate. Prefer immediate admission or a short,
bounded queue, and return 503 with `Retry-After` when the queue is full. The
gate's `finally` must release a permit after cancellation, disconnect, exception
or normal completion. Add tests for cancellation while waiting and while
admitted, and expose gauges for active permits, rejected requests and wait
duration.

## Connection budget equation

Let:

- `G` be the Green steady instance count (currently 4), `G_surge` the measured
  AdditionalBatch maximum (currently 5), and `C_web` the admitted
  DB-capable request contexts per Daphne process;
- `P_i` be the maximum simultaneous ECS tasks for worker service `i` (desired
  1, with a configured rolling maximum of 2 per service), and `C_i` its
  measured DB-capable concurrency per task;
- `J` be the maximum simultaneous management, cron and SSM jobs, with `C_j`
  measured contexts per job;
- `O` be reserved operator, monitoring and administration connections;
- `R` be MySQL/RDS internal or emergency headroom not available to the app.

Then the target bound is:

```text
steady = 4*C_web + Σ(1*C_i) + J_steady*C_j + O + R
surge  = 5*C_web + Σ(P_i*C_i) + J_surge*C_j + O + R
surge must remain <= 80
```

Every term except the configured instance/task counts is **UNKNOWN** until
process inspection and connection attribution are available. In particular,
there is no evidence-based value for `C_web`, `C_i`, `J`, or `O`. Do not turn
`ASGI_THREADS=4` into `C_web=4`.

### Phase 2.6 inventory and observed bounds (2026-10-15)

Read-only AWS inspection on 2026-10-15 found four Green instances and one
legacy integrations EB instance in `Grey` health. Each of the four ECS
services had one running task and no pending tasks; each service has
`maximumPercent=200`, allowing two tasks during replacement. The legacy EB
queue configuration matches the ECS `interoves-integrations` queue. There were
zero legacy Nginx integration-path deliveries in the preceding 24 hours, while
shared-queue CloudWatch metrics showed 2,907 received messages and 3,218 empty
receives. Those SQS metrics cannot attribute a consumer. Empty legacy polling
and its configured maximum therefore remain possible. No SSM process command
was run.

Code-level worker bounds are more specific than the task count. ECS worker
polling processes one SQS message at a time. The integrations handler can fan
out to eight `ThreadPoolExecutor` workers; its parent request may retain a DB
connection while waiting for those workers. Identity account merge can use a
separate lease-heartbeat thread while the main merge is active. This gives a
conservative per-task arithmetic bound of 1 background + 2 identity + 9
integrations + 1 recheck = 13 steady contexts, or 26 when each service has
two tasks. This is a code-derived ceiling for the currently inspected paths,
not a live measurement; scheduled/management jobs can add contexts.

Channels WebSocket consumers call `database_sync_to_async` and use asgiref's
thread-sensitive executor. The inspected ASGI routing does not put them in
Django's per-request `ThreadSensitiveContext`; asgiref 3.8.1 therefore routes
these synchronous calls through its process-level single-thread executor.
That bounds simultaneous WebSocket DB calls to one per Daphne process, subject
to no future custom executor/context changes. These operations are outside the
HTTP admission limiter. WebSocket broadcasts use daemon threads for Redis I/O;
their DB preparation runs before those threads are spawned. Management and
operator work remain separate budget terms.

The RDS parameter group reports `max_connections=80`; runtime max connections
were confirmed as 80 in the earlier Phase 2.5 SQL snapshot, which also read
`Threads_connected=4`, `Threads_running=2`, `Max_used_connections=82`, and 29
cumulative `Connection_errors_max_connections`. A current SQL refresh was not
available: the read-only SSM tunnel reached its local endpoint, but MySQL TLS
hostname verification rejected the loopback host before a SQL session opened.
Do not treat the Phase 2.5 status or process-list aggregate as current. The
current 24-hour CloudWatch `DatabaseConnections` maximum is 75 for a one-minute
period; the latest sample was 6. CloudWatch is aggregated and can miss an
instantaneous peak. Over the same 24 hours, RDS CPU reached about 40.9% in a
five-minute sample; FreeableMemory reached a minimum near 79 MiB and was about
245 MiB in the latest sample.

With the legacy integrations consumer retained as potentially active, the
current scenario equations are `4*C_web + 13 + 9 + 4 + C_other + R <= 80`
for steady state and `5*C_web + 26 + 9 + 5 + C_other_surge + R <= 80` for
rollout surge. `C_other` includes management/SSM jobs and operator/monitoring
connections; `R` is additional unused headroom. None has a verified maximum.
For illustration, if their combined bound `X=C_other_surge+R` were 0, 10, 20,
or 30, the surge arithmetic ceiling would be 8, 6, 4, or 2 HTTP requests per
Daphne process. These are scenario calculations, not approved caps. No
production `C_web` is approved.

The local gate's `stats()` snapshot exposes active requests/permits, queue length,
configured cap/queue/time limit, accepted/rejected counts by fixed reason,
cancellations, admission wait and permit hold totals/maxima, a cumulative
high-water mark and a per-sample in-flight high-water mark, and fixed duration
buckets (5, 20, 50, 100, 250, 500, 1000, 5000 ms, +Inf).
The ASGI wrapper remains installed at `DB_ADMISSION_MAX_ACTIVE=0` in observe-only
mode, so the default does not throttle but still counts in-flight HTTP requests.
With a positive cap, that same count is also the active permit gauge. It emits a
flattened JSON metric sample every 10 seconds while requests are in flight, then
flushes the final sample after the burst becomes idle. A request burst shorter
than 10 seconds still yields a final sample; a process with no HTTP traffic emits
no admission sample because its counters and in-flight count have not changed.
Counter and histogram fields are deltas since that process's previous sample;
active and queue length are gauges. The interval high-water mark captures brief
peaks between samples. The sample carries the app deployment version and
source commit from the local release files; CloudWatch's per-instance log stream
provides the EC2 instance ID. Sampling uses only local memory and stderr logging,
with no database or network calls. No path, user, session or request-id labels
are included. CloudWatch metric filters can map the fixed JSON fields into a
dedicated namespace without a synchronous exporter. Sum counter deltas across
hosts and inspect gauges per instance. A silent log stream still needs EB/EC2
health and log-delivery monitoring to distinguish idle traffic from a stopped
process. Metric filters and dashboards remain a separate operations task.

Local MySQL 8.4.11 validation through Django's actual ASGIHandler observed
maximum simultaneous DB query sections of 6 with observe-only mode, and 1, 2,
and 4 at HTTP caps 1, 2, and 4. Eight mixed GET/POST requests used independent
MySQL connections and loaded a DB-backed session. In the latest correctness
run, local p95/p99 were approximately 312, 647, 326, and 193 ms at caps 0, 1, 2,
and 4; queue exhaustion returned 503 and the permit count returned to zero.
These are correctness-test measurements, not production capacity estimates.

The worker and WebSocket terms alone consume up to 40 possible contexts during
surge (`26+9+5`), leaving `40-5*C_web` slots for `C_other_surge+R`. A positive
web cap cannot be approved until those remaining sources have hard bounds.
Before setting a cap, collect per-instance HTTP high-water marks, actual
WebSocket DB activity, DB sessions by source, integrations executor limits,
management-job overlap, operator/monitor connections, and the maximum
simultaneous EB/ECS rollout state.

## ChainTaskState audit safety

The completeness SQL is one full aggregate statement, not a chunked audit. Its
MySQL statement timeout limits execution time only; it does not guarantee a
bound on CTE materialization or intermediate memory. Production EXPLAIN on
2026-10-15 estimated 839,119 expected actor contexts and repeated full scans of
that materialized CTE, plus scans of approximately 47,240 state rows. Do not
run its `--execute` mode on shared RDS.

A safe follow-up needs keyset batches in one `REPEATABLE READ` consistent,
read-only snapshot. Page source attempts by primary-key range and state rows by
their primary key; cap rows per page, total pages, and total elapsed time.
Resolve each page's singleton GameTaskGroup mapping, deduplicate within that
page, and compare expected contexts against both required modes through the
unique actor-context index. Check extra state rows and `last_attempt`
consistency in separate state-ID pages, using the Attempt task/actor indexes.
If any page or time budget is hit, return `INCONCLUSIVE`, never `COMPLETE`.
Before production execution, EXPLAIN every page query and confirm index use.
The read-only snapshot itself retains an MVCC view, so its duration and
frequency need strict limits. This chunked design has not been implemented or
run against production.

The release gate should evaluate the aggregate **surge** formula before
starting a rollout. Green and worker rollouts should be staggered unless their
combined maximum fits the budget. Blue remains zero-capacity in the observed
configuration and cannot be counted as a running replica.

## Timing retry limits and remaining outbox decision

The local client keeps each logical event envelope fixed across attempts and
serializes mutations within one page session. It allows one request in flight,
keeps at most eight queued envelopes, and coalesces only adjacent unsent
heartbeats. A normal event gets at most three sends and a 20-second age limit.
Start keeps one envelope across at most six sends within that same age limit.
`Retry-After` is treated as a minimum, capped at eight seconds, with positive
jitter above the minimum; an individual wait is capped at ten seconds. Queue
failure stops local heartbeat authority and surfaces an unacknowledged state.

The client retains queued events in page memory only. If the browser reports an
offline connection, the head event waits for the `online` event, subject to its
remaining attempt budget and 20-second age. A `pagehide` keepalive send is best
effort and is not an acknowledgement; a page closing before the response can
lose the in-memory queue. Browser reload recovery and durable delivery are not
provided. `sessionStorage`/IndexedDB persistence is deferred because replaying
late timing actions requires actor/session ordering and stale-lease validation.

The backend advertises `timing_event_capability=stable-event-v1` in its GET
snapshot. A client opts in only after receiving that backend capability; old
backends do not advertise it and new clients then omit the protocol marker.
HTTP 2006/2013 unknown commit outcomes are retryable only for opted-in events.
The ledger uniqueness key is `(game, task_group, actor_key, session_id,
event_id)`, and an existing entry is compared against the logical payload
before applying any new mutation. Reusing an event ID with a different action,
sequence, actor or payload returns 409. The ledger and timing snapshot remain
inside the same atomic transaction. Concurrent delivery still needs a real
MySQL staging test; local SQLite tests cannot establish MySQL lock behavior.

The queue orders requests within a page, including start before heartbeat and
control/finish events behind earlier queued events. It cannot wait for an
earlier request during browser unload; in that case the lease expiry remains
the server-side fallback. A failed queue is cleared and the UI asks the player
to refresh/reconcile instead of claiming persistence. This is a bounded
best-effort delivery protocol, not durable or exactly-once delivery.

## Projection-state lock graph

```text
Attempt.save outer atomic transaction
  ├─ save Attempt + read Task/Game
  └─ register on_commit callback
       COMMIT source transaction (its locks are released)
       └─ _schedule_actor_projection_after_commit
            └─ schedule_actor_projection
                 └─ mark_projection_dirty transaction
                      └─ _state_for_update (SELECT ... FOR UPDATE)
                           update revision/validity and dirty actor rows
                      COMMIT
                      └─ on_commit publish worker event OR local refresh

refresh_daily_result_projection
  ├─ short transaction: lock state, capture source_revision, mark invalid
  ├─ compute canonical results outside transaction
  └─ publication transaction:
       ├─ _state_for_update (SELECT ... FOR UPDATE)
       ├─ revision check
       ├─ delete and bulk-create projection rows
       └─ mark state valid; COMMIT
```

Confirmed: the 1205 wait occurred in `mark_projection_dirty` while acquiring
the projection-state row; slow log recorded roughly 50 seconds of lock wait.
Confirmed: the full refresh has a final transaction that holds this same state
row while replacing projection rows. **Hypothesis:** a slow final publication
transaction was the blocker. The historical blocking transaction, request,
and lock holding interval are unknown, so no projection algorithm change is
prescribed in this phase.

Next safe investigation: enable/query lock-wait and transaction-age evidence
in a staging environment; on a future production incident capture only
read-only `performance_schema`/InnoDB wait data or Database Insights around the
event, with application request/job IDs and transaction start times. Compare
full-refresh write duration with `mark_projection_dirty` wait duration.

If confirmed, separately evaluate shortening publication transactions or
serializing refresh work per `(game, task_group)`. Validate canonical result
parity, revision conflict handling, actor dirty-set reconciliation, concurrent
Attempt writes, full refresh fallback, and stale/invalid projection reads
before rollout. Do not add indexes without a specific slow SQL and query plan.
