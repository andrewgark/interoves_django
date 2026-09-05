# Realtime and team synchronization

Status: audit and target architecture, 2026-08-21.

## Product invariant

Two open pages representing the same actor scope must converge to the same server state without F5. This includes two members of one team. Lost, duplicated, delayed, or out-of-order notifications must not leave a page permanently stale.

The same mechanism covers site changes that become relevant while a page is open: task/checker edits, pending-attempt review, access changes, and clock-driven game start/end.

## Current implementation audit

| Area | Current state | Gap |
|---|---|---|
| Transport | Django Channels; authenticated game and user sockets; Redis in production; real Redis group delivery is tested between independent processes | Anonymous play has no socket |
| Scopes | game, game+internal-team-PK, and user groups; unsafe/long PKs are deterministically encoded into Channels-safe ASCII group components; game subscription validates existence/access; supported rename changes only `visible_name` | Legacy name-hash group remains temporarily; changing the internal `Team.name` PK is unsupported without a surrogate-ID migration |
| Team submissions | Task-group pages subscribe to the game/team socket; submissions, hints, raddle assist, review, and recheck use the central actor-task hook | Anonymous play still relies on the current POST response |
| Concurrent writes | Task/chain rows are locked during checking; simultaneous same-answer convergence is covered in two browsers; sequential replacements lines after a teammate live-update keep using the page CSRF token | Broader mixed-answer/load concurrency is not browser-tested |
| Ordering | Actor-scoped `seq`; shared Redis revision cache in production; two-process atomic allocation is tested; new client ignores older sequences and checks revisions every 25 seconds | No durable replay yet |
| Reconnect | New UI reconciles authoritative visible task fragments on first subscription, reconnect/revision gap, periodic check, and visibility return | Unsupported projections (currently proportions sheets) still fall back to a full reload |
| Task/hint edits | Model saves commit the row before broadcasting an admin task change; the consumer renders actor-specific HTML; task edit followed by recheck is covered in two browsers | Full HTML is rendered per consumer; multi-game resolution and access need hardening |
| Pending review/recheck | Set-ok, prestatus confirmation, single/batch/chain rechecks call the central actor-task hook; Ok and Wrong convergence are covered in two browsers | Direct ad-hoc `Attempt.save()` outside these services still has no live notification |
| Game start/end | Pages carry authoritative start/end timestamps and schedule a reload at the next boundary; saves also push lifecycle events; both boundaries are browser-tested | Reload is currently whole-page rather than a fine-grained access projection |
| Daily content boundary | Main hub, daily archives, and open ladder/week-task/alphabetty pages carry the next server-derived publish timestamp for an existing future row and reload at that boundary | Whole-page reload; midnight behavior lacks a dedicated browser assertion |
| Client update | New UI replaces task fragments; reconciliation events are queued so an older snapshot cannot overwrite a newer socket update; game/section task-list progress is rendered authoritatively in the first response, with the scoped JSON projection retained as a failure fallback; live HTML is restamped with the page CSRF token before the next POST | Large coupled HTML remains in direct socket payloads; some lifecycle events still reload the page |
| Delivery durability | Publish runs in an `on_commit` daemon thread | A process/Redis failure can drop an event permanently |
| Tests | Publisher and ASGI tests, thirteen Playwright scenarios, and opt-in real-Redis tests for cross-process delivery and revision allocation; GitHub Actions runs the latter against disposable Redis | Mobile keyboard behavior remains a manual smoke |

### What likely works today

- Two authenticated team members on the same game page normally receive successful task, hint, and raddle-advance updates while both sockets stay connected.
- A missed task-card event is detected on reconnect, within 25 seconds, or immediately when the tab becomes visible, then repaired from the authorized HTTP projection.
- Pending reviews converge to Ok or Wrong on both teammates' open pages.
- Saving corrected task content updates both open pages, and rechecking the old
  attempt then updates its verdict and points without reloading either page.
- A pre-start page reloads itself at the server-provided start boundary and exposes the tasks.
- An active tournament page reloads itself at the end boundary and exposes post-game UI.
- Open daily hubs and play pages schedule themselves to reload when the next already-created
  ladder, alphabetty, or week task reaches its Moscow publication boundary.
- Simultaneous identical submissions produce one authoritative attempt and both pages converge.
- After a teammate solves one replacements line, the other member can submit another line without F5 or «Ошибка сети».
- An in-flight raddle submit still converges after the teammate's live HTML replace; a client abort is not auto-retried on top of the chain lock.
- Raddle middle-row drafts and unused-clue strikethrough converge on the teammate page without replacing the ladder HTML.
- Changing a team's visible name does not interrupt its existing game subscriptions.
- DB locking prevents the main duplicate/chain-state races during simultaneous submissions.
- Older delivered messages do not overwrite newer HTML in the new UI.
- Redis transports group messages between processes, and concurrent processes allocate
  one monotonic scoped revision sequence without duplicates or gaps.

### Known unreliable or missing scenarios

- Unsupported partial projections such as proportions sheets still require a full reload after a gap.
- Ad-hoc attempt mutations that bypass the review/recheck services.
- Anonymous multi-tab synchronization.
- Direct mutation of the internal `Team.name` primary key is not supported; introduce a surrogate immutable ID before allowing it.
- Mobile keyboard-open intent across remote raddle replacement still needs a real-device smoke; desktop focus/draft preservation is automated.
- Daily publication scheduling has domain and client-unit coverage but not a real-clock browser test.

## Target architecture

Use three layers:

1. **Authoritative state and revision.** Each mutation commits state and increments a revision for a stable scope such as `game:<id>:team:<pk>` or `user:<pk>`.
2. **Best-effort notification.** After commit, publish a small typed invalidation containing scope, revision, entity, and reason. WebSocket is an accelerator, never the only recovery mechanism.
3. **Reconciliation.** On initial subscription, reconnect, sequence gap, and `visibilitychange`, ask an HTTP snapshot/fragment endpoint for current revisions and affected projections. Apply only newer state.

During migration, rendered task fragments may remain the projection returned by HTTP. Do not require a wholesale SPA rewrite. Move large HTML out of broadcast messages gradually.

Use stable team primary keys in new group/scope names and keep the current internal
`Team.name` immutable; UI rename must remain a `visible_name` change. Authorize
subscription against the requested game and actor. Treat game phase as derived
state: render `server_now` and `next_transition_at`, schedule a local reconciliation
at that instant, and optionally push the same invalidation from a scheduler.

For reliable cross-process delivery, add a transactional outbox later. A worker publishes committed rows with idempotent event IDs; reconciliation still remains mandatory.

## Delivery plan

### Phase 0 — make failures observable and self-healing

- [x] Add scoped sequence namespaces with a shared production revision cache.
- [x] Handshake on socket open/reconnect and every 25 seconds; reconcile visible task cards when the server revision is newer, with safe reload fallback.
- Show connection/recovery diagnostics in development logs.
- [x] Add deterministic client tests for stale/duplicate/reconnect-gap behavior.

### Phase 1 — close mutation coverage

- [x] Add central `track_actor_task_change(actor, task, game, reason)` and route submission, hint, raddle, dependent-task, pending-review, and recheck paths through it.
- Keep checker/content edits on their model-level game broadcast until typed content events replace rendered HTML.
- [x] Add team-PK groups and validate game access on connect. Keep dual legacy delivery only through the rolling-deploy transition.

### Phase 2 — authoritative reconciliation API

- [x] Add an authorized revision/snapshot endpoint for game+actor state and visible task fragments.
- [x] Reconcile on connect, reconnect, sequence gap, periodic handshake, and visibility return.
- [x] Queue live events during snapshot application so old HTML cannot roll back a newer event.
- [x] Keep local raddle drafts and active focus intent across replaced server fragments; cover desktop behavior in two-browser Playwright.
- Keep the Android keyboard-close/scroll cases in the manual device smoke until mobile automation is available.

### Phase 3 — clock and durability

- [x] Add client scheduling for game start/end boundaries from authoritative timestamps.
- [x] Add opt-in real-Redis cross-process tests for group delivery and revision allocation.
- [x] Add the real-Redis suite to CI with a disposable local service.
- [x] Add equivalent `next_transition_at` scheduling for daily content boundaries.
- Add scheduler invalidations where immediate push is useful.
- Add a transactional outbox if production loss metrics justify it.

## Test matrix

| Scenario | Required assertion | Automated status |
|---|---|---|
| Teammate solves ordinary task | Other browser converges without reload/input loss | Playwright |
| Teammate solves a replacements line | Other browser can submit another line without reload or «Ошибка сети» | Playwright |
| Teammate advances raddle from either end | Both ladders converge; active draft and intentional focus survive | Playwright |
| In-flight raddle submit during teammate HTML replace | Held POST still solves its word; no «Ошибка сети» | Playwright |
| Teammate types a raddle middle draft or strikes an unused clue | Other browser shows the letters/mark without HTML replace or reload | Playwright |
| Simultaneous same answer | One authoritative progression; both browsers converge | Playwright |
| Pending becomes Ok/Wrong | All actor pages show final verdict and points | Playwright, both verdicts |
| Task/checker edited and rechecked | Open pages receive new text and recalculated state | Playwright + publisher tests |
| Socket disconnected during mutation | Reconnect reconciliation repairs the missed update | Playwright offline context |
| Duplicate/out-of-order event | No rollback or duplicate UI effect | Deterministic JS test |
| Game start/end clock boundary | Access/UI changes without admin save or F5 | Playwright, both boundaries |
| Daily content publish boundary | Main/archive/play page refreshes from a server timestamp and exposes the new row without F5 | Domain + JS unit; browser pending |
| Team renamed while open | Subscription continues under stable internal team key | Playwright (`visible_name`) |
| Unauthorized game socket | Connection is rejected and no task content is disclosed | ASGI integration test |
| Cross-process Redis transport | Independent publisher/receiver processes exchange an event; concurrent allocators produce one monotonic revision sequence | Opt-in real-Redis integration test |

The Playwright suite currently covers direct and simultaneous team submissions,
offline/reconnect snapshot repair, raddle draft/focus, raddle draft/clue-mark
sync, an in-flight raddle submit across a teammate live-update, sequential
replacements lines after a teammate live-update, both pending-review verdicts, task
edit/recheck, both game clock boundaries, and visible-name rename:

```bash
../venv/interoves_django/bin/python manage.py test games.tests.test_realtime_browser --settings=interoves_django.test_live_settings
```

Run it outside the restricted sandbox. Keep a short manual two-device smoke for
mobile focus/keyboard behavior.

The real-Redis suite is opt-in and must target disposable Redis:

```bash
INTEROVES_REAL_REDIS_TESTS=1 REDIS_HOST=127.0.0.1 REDIS_PORT=6379 \
  ../venv/interoves_django/bin/python manage.py test games.tests.test_realtime_redis
```

It is locally verified and runs in `.github/workflows/realtime-redis.yml` with a
disposable Redis service. Non-loopback Redis is refused unless explicitly allowed
for disposable CI.
