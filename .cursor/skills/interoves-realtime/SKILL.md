---
name: interoves-realtime
description: Audit, design, implement, and test reliable live UI synchronization in Interoves Django. Use for team members solving the same task concurrently, WebSocket/Channels changes, stale task HTML, pending-attempt rechecks, game start/end transitions, reconnect recovery, or any requirement that a page update without F5.
---

# Interoves realtime

Treat realtime as delivery of hints about authoritative server state, not as the state itself.

## Start here

1. From this skill directory, read [`../../../agents/AGENTS.md`](../../../agents/AGENTS.md) and [`../../../docs/realtime-architecture.md`](../../../docs/realtime-architecture.md) completely.
2. Map the mutation from DB commit to publisher, scope, consumer, client projection, and recovery path.
3. State which invariant and scenario row the change affects before editing.
4. Preserve actor isolation: team, user, and anonymous state must never share payloads.

For task-group recovery, use the authorized
`GET /games/<game_id>/live-state/?task_ids=...` projection. A full reload is only
the fallback when the endpoint reports that the current widget cannot be patched.

## Required invariants

- Commit state before publishing; schedule notifications with `transaction.on_commit`.
- In `Task.save()` and `Hint.save()`, call the live hook after `super().save()` so
  autocommit cannot render the previous row in a racing broadcast thread.
- Use stable IDs for scopes. Do not introduce mutable names or hashes as new identities.
- Treat `Team.name` as the immutable internal PK; supported rename changes only
  `visible_name`. Add a surrogate-ID migration before allowing internal team-key changes.
- Give every scoped state a monotonic revision. Ignore old events and reconcile gaps.
- Make reconnect, tab visibility return, and lost events converge by fetching current state.
- Keep WebSocket messages small where practical. Prefer invalidation plus authoritative fetch over durable HTML in events.
- Derive game and scheduled-content transitions from server-provided timestamps;
  do not rely on a model save occurring at the boundary. Daily section pages use
  `live_next_transition_at` / `data-live-next-transition-at` for the next existing row.
- Preserve active input, focus intent, scroll, and local drafts when applying remote state, especially for raddle.
- Never add a new mutation path without its live-update hook and test scenario.

## Testing workflow

Test four layers in proportion to the change:

1. Domain: concurrent submissions and final DB state.
2. Publisher: correct scope, event/revision, and `on_commit` behavior.
3. Transport: authenticated consumer, reconnect, ordering, and gap recovery.
4. Browser behavior: two independent sessions; verify DOM convergence and that active input is not destroyed.

Run focused Django tests with `../venv/interoves_django/bin/python`. Run `TrackWebsocketIntegrationTests` outside the restricted sandbox as required by `agents/AGENTS.md`. For a reported sync bug, add a deterministic regression test before relying on a manual two-browser smoke test.

Run the real two-context browser suite outside the sandbox with:

```bash
../venv/interoves_django/bin/python manage.py test games.tests.test_realtime_browser --settings=interoves_django.test_live_settings
```

Install Playwright Chromium first when its executable is absent. Keep this suite
opt-in through the dedicated settings module so ordinary SQLite test runs remain fast.
Keep direct and simultaneous team delivery, offline recovery, pending review,
task edit/recheck, both clock boundaries, visible-name rename, and raddle draft/focus
represented in this suite.

For daily/weekly publication scheduling, keep the pure server calculation and shared
client timer covered with:

```bash
../venv/interoves_django/bin/python manage.py test games.tests.test_daily_transitions
node static/js/track_ws.test.js
```

For cross-process transport, run the opt-in suite against disposable Redis:

```bash
INTEROVES_REAL_REDIS_TESTS=1 REDIS_HOST=127.0.0.1 REDIS_PORT=6379 \
  ../venv/interoves_django/bin/python manage.py test games.tests.test_realtime_redis
```

It must verify both Redis group delivery and atomic scoped revision allocation from
independent processes. The test refuses non-loopback Redis unless
`INTEROVES_ALLOW_REMOTE_REDIS_TESTS=1` is explicitly set for a disposable CI service.
Keep `.github/workflows/realtime-redis.yml` running the same suite with a disposable
Redis service when realtime transport, settings, dependencies, or tests change.

## Change checklist

- Identify source of truth and scope: task/team/user/game/project.
- Cover success, pending-to-final, content edit, reconnect, duplicate/out-of-order, and relevant clock boundary.
- Document temporary full-page reloads explicitly; do not call them fine-grained synchronization.
- Update [`../../../docs/realtime-architecture.md`](../../../docs/realtime-architecture.md) when capability status or the target design changes.
