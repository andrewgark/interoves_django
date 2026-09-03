# Product analytics event contract

The backend rows below are the canonical inputs for product metrics. Yandex
Metrika goals are a delivery channel with a different visitor identity and may
not be used as the only source of truth.

## Common identity and placement rules

A backend event must have exactly one of `user_id`, `anon_key`, or `team_id`.
Game events must reference a real `GameTaskGroup(game_id, task_group_id)` and
must have `game_instance_id = game.id + ":" + task_group.id`.

Starts and completions have no `event_id`, product `session_id`, `occurred_at`,
`received_at`, or general metadata JSON. Their primary key is a storage-row ID;
`started_at`/`completed_at` is the database insertion time except that start
backfill can preserve an earlier attempt time. Stage 1A does not add fields that
the current architecture cannot populate reliably.

For both tables, physical `instrumentation_version` is `NULL` (legacy/logical v1)
or `2` (new live write path). Any other value is invalid. See
[definitions](definitions.md#instrumentation-versions-and-legacy-boundaries).

## Backend events

### `game_start`

| Contract item | Current behavior |
| --- | --- |
| Storage | `PlayerStartedGame` |
| Source | Server, triggered by a qualifying attempt/hint request |
| Source of truth | Backend row, not Metrika |
| Required links | exactly one actor; `game`; `task_group`; valid placement; `game_kind`; `game_instance_id` |
| Time | `started_at`; first insertion time, or earliest attempt time for explicit historical backfill |
| Logical multiplicity | at most one per actor-placement |
| Application dedupe | `get_or_create(actor, game_instance_id)` |
| Database dedupe | Django conditional constraints exist, but MySQL does not create partial unique indexes; not guaranteed until 1B |
| Metrika | `game_start`, retried until signed callback ack; delivery failure does not undo the backend row |
| v2 rule | newly inserted live row gets `2`; backfill and pre-existing rows stay `NULL` |

The exact qualifying actions for every currently instrumented format are in
[definitions](definitions.md#game_start). Opening a game page is not a start.

### `game_complete`

| Contract item | Current behavior |
| --- | --- |
| Storage | `PlayerCompletedGame` |
| Source | Server after saved game state satisfies a supported terminal condition |
| Source of truth | Backend row |
| Required links | exactly one actor; `game`; `task_group`; valid placement; supported `game_kind`; `game_instance_id`; `result` |
| Time | `completed_at`; insertion/reconstruction time, not original time for historical backfill |
| Logical multiplicity | at most one per actor-placement |
| Application dedupe | `get_or_create(actor, game_instance_id)` |
| Database dedupe | not guaranteed by MySQL until 1B |
| Metrika | `game_complete` only for non-backfilled server-confirmed completion, retried until signed callback ack |
| v2 rule | newly inserted live row gets `2`; automatic/history backfill and pre-existing rows stay `NULL` |

Client code cannot independently create a canonical completion. The terminal
conditions are listed in [definitions](definitions.md#game_complete).

### `activated_player`

| Contract item | Current behavior |
| --- | --- |
| Storage | `PlayerAnalyticsState.activated_at` and `activation_is_backfilled` |
| Source | Server completion pipeline |
| Condition | actor's distinct stored completion count crosses 3 |
| Multiplicity | one lifecycle marker per current actor identity |
| Metrika | retried until signed callback ack when not backfilled |
| Instrumentation version | not added to `PlayerAnalyticsState` in 1A |

This event retains its existing “third unique completion” meaning. It inherits
the current identity and MySQL concurrency limitations.

### Signup marker

The backend does not have canonical events named `signup_start` or
`signup_complete`. Successful registration is represented by
`PlayerAnalyticsState.signup_at`, with method and Metrika acknowledgement fields.
The external goal is named `signup`. Stage 1A does not rename or version it.

## Client/Metrika-only events

The following are not canonical backend product-event rows:

| Goal | Actual trigger |
| --- | --- |
| `onboarding_view` | `/start/` onboarding UI is viewed |
| `onboarding_game_select` | a format is selected on `/start/` |
| `onboarding_first_game_complete` | onboarding browser context observes a delivered backend completion payload |
| `onboarding_second_game_start` | onboarding browser context observes a later backend start payload |
| `social_follow_prompt_view` | onboarding follow-up shows the one-time social subscribe card |
| `social_follow_click` | a social link in that card is clicked (`platform` is telegram, instagram, or twitter) |
| `social_follow_prompt_dismiss` | the social subscribe card is dismissed |

The onboarding context lives in browser `localStorage` for 24 hours. Delivery
can be missing or duplicated relative to backend actors, so these goals describe
Metrika funnel behavior, not canonical actor counts.

There is no canonical `game_view` or `signup_start` in the current system. They
must not be synthesized from page requests or existing event names.

## Delivery and failure behavior

For starts and completions, the backend record is committed independently of
Yandex delivery. Until `reachGoal` callback acknowledgement, later valid responses
may carry the same goal payload and stable key again. The browser helper queues
pending goals and deduplicates delivery by that key. Ad blockers, closed pages,
or unavailable Metrika can leave `metrika_acked_at=NULL`; none of those failures
may block gameplay.

Metrika payloads contain game/result/public game values and do not contain
`user_id`, `team_id`, `anon_key`, email, name, Telegram username, phone, or IP.

The persisted `*_acked_at` fields mean that the corresponding semantic Yandex
goal was delivered. A physical backend row id is used only by the browser's
internal idempotency key and the signed same-origin ACK token; `reachGoal`
receives the goal name and params without that row id. During an explicit
identity merge, an ACK may be retained across rows only when all reconstructible
goal params equal the final canonical payload. Otherwise it is cleared, which
can safely repeat a goal but cannot suppress an undelivered, different payload.
Activation ACK stays with its complete activation provenance bundle because its
historical `games_completed` param is not persisted.

## Compatibility

Stage 1A changes neither trigger conditions nor delivery semantics. Legacy rows
remain readable with `instrumentation_version=NULL`. Version 2 is assigned only
on creation through the new live start/completion paths and never retroactively.
The deployment time and first clean post-deploy quality window must be recorded
operationally; local tests alone cannot verify production delivery.
