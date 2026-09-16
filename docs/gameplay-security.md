# Gameplay actor context and forensic observability

## Actor context

Every new gameplay page receives a Django-signed `gameplay_context` token. It
contains only a schema version, actor namespace and canonical actor identifier,
plus the game and task (or task group for daily timing). Anonymous identifiers
are HMAC-derived; raw anonymous keys and session keys are not placed in the
token.

Mutation views resolve the current actor on the server and compare it with the
verified token. The token is not a client-supplied ownership assertion. A
changed user, anonymous identity, team, game or task returns HTTP 409 with
`reload_required=true` before any gameplay mutation. The browser displays an
account-change message and reloads; it never retries the action under the new
actor.

The token is actor-bound rather than session-key-bound, so normal Django
session-key rotation for the same user remains valid.

### Rollout

Phase A emits tokens and validates any token that is present. Requests without
one remain temporarily compatible with old pages and are marked as legacy by
request telemetry. After token coverage is verified, set
`GAMEPLAY_CONTEXT_REQUIRE_TOKEN=true` (Phase B). Missing or invalid tokens then
receive the same controlled reload response. A rollback may permit missing
tokens again; valid mismatches remain blocked.

Protected mutation families are ordinary attempts, hints, Raddle assist/UI
state, Alphabetty guess/hint/suggestion, and daily timing writes. The Track
WebSocket remains tracking/read-like: its identity is fixed at connection time,
and a context mismatch causes the page to reload and reconnect.

## Structured forensic events

The existing request ID and HMAC session fingerprint are reused. Authenticated
gameplay requests emit `authenticated_gameplay_request` with request ID, user
ID, session fingerprint, actor namespace, route, task, normalized user agent,
status and result. Successful persisted attempts additionally emit
`gameplay_attempt_created` with the same request ID and `attempt_id`.

Auth login/logout/claim events retain the existing canonical `event_name`
values (`auth_login_success`, `auth_logout`, `auth_account_claim`). Login and
claim events include the authenticated user, session fingerprint, provider or
method where available, normalized user agent, instance and deploy version.

Normalized user-agent fields describe a client assertion only; they are not
device attestation. Events exclude request bodies, answers, cookies, raw session
keys, OAuth material, authorization headers, CSRF secrets, passwords and
signing secrets. Logging failures are swallowed so telemetry cannot break a
gameplay request.

## Admin ownership

Existing Attempt ownership fields are read-only in the change form. Ownership
transfers must remain visible through the dedicated claim/merge workflows rather
than looking like an ordinary Attempt edit. A future legitimate support
workflow must be an explicit audited action with a reason.

## Database invariant proposal

Production currently has no actorless or multi-actor Attempt rows, but the
schema does not enforce that fact. A separate follow-up should add a database
check equivalent to:

```sql
(
  (user_id IS NOT NULL) +
  (anon_key IS NOT NULL) +
  (team_id IS NOT NULL)
) = 1
```

Before applying it, verify the production MySQL version, Django backend support,
all producers, and claim/merge transaction boundaries. This proposal is not a
migration and is intentionally not included in this rollout.
