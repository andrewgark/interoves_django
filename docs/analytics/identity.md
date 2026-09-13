# Current product analytics identity

Stage 1C hardens anonymous identity without introducing `AnalyticsActor`,
aliases, or a generic event warehouse. Product rows still store exactly one of
`user_id`, `anon_key`, or `team_id`.

## Identity namespaces

| Field | Meaning | Created/selected by |
| --- | --- | --- |
| `user_id` | registered Django account | authenticated request / `analytics_user` |
| `anon_key` | anonymous browser identity | server-issued opaque UUID in cookie `interoves_anon` |
| `team_id` | team attribution supported by schema | team-mode fallback where no analytics user is selected |

The type is part of the actor key. User `42`, team `42`, and anon string `"42"`
are three different actors.

## Cookie protocol

Canonical UUID cookie (JS-readable, needed by the existing claim UI):

    interoves_anon=<opaque uuid>

HttpOnly HMAC signature cookie (JS must not read it):

    interoves_anon_sig=<hex hmac-sha256>

Signing uses an explicit context `interoves-anon-identity-v1` and setting
`ANALYTICS_ANON_SIGNING_KEY` (dev/test may fall back to `SECRET_KEY`).
Production should set the same dedicated secret on every instance. Rotating
that secret invalidates signatures and needs a separate dual-key rollout;
Stage 1C does not implement secret rotation.

Both cookies use `path=/`, `SameSite=Lax`, `Secure` when `SESSION_COOKIE_SECURE`
is on, and `max-age=31622400` (about 366 days), matching the previous anonymous
retention.

GET, POST, header (`X-Interoves-Anon`), and URL `?anon=` / `?anon_key=` are
**not** authority for gameplay or analytics attribution. Old clients may still
send those fields; the backend ignores them as actor selectors.

## Issuance and validation

`games.analytics_identity` plus `AnonymousIdentityMiddleware` bind one identity
per request (except static/health/`/meta/` paths):

1. Valid UUID cookie + valid signature → reuse.
2. Valid UUID cookie and **no** signature → **compat adopt** and upgrade with a
   signature (unsigned legacy cookie window; Phase E mandatory-signature is
   **not** in Stage 1C).
3. Missing, malformed, or **invalid signature** → issue a fresh server UUID
   (UUID4) and new signature. Gameplay is not 500.

Tabs and revisits share the cookies, so they share one anonymous identity.
Corrupt cookies mint a new identity instead of failing the request.

Do not log raw UUID or signature. Diagnostics may use
`anon_key_fingerprint()`, which is not reversible.

## Rolling deploy limitation

During a mixed old/new deploy:

- New instances ignore header/POST/URL for actor selection immediately.
- Old instances still trust those client fields.
- New instances still **adopt an unsigned `interoves_anon` cookie** and mint a
  signature. An attacker who can **set that cookie** to a known legacy UUID can
  still inherit that legacy actor until a later mandatory-signature phase.
- Header-only spoof of another UUID does **not** work on new instances.

## Authentication transitions

### Signup

`user_signed_up` (request is available on the allauth signal) auto-claims the
**current canonical browser cookie** through the existing
`claim_and_migrate_anon_history` / `AnonAccountClaim` path. Rows are reassigned
or merged, not copied. The operation is idempotent. A later repeat of the
signal does not create duplicate attempts or events. If migrate fails, signup
itself still succeeds.

### Login of an existing account

Ordinary login does **not** auto-migrate. New authenticated activity uses
`user_id`. Previous anonymous history stays on `anon_key` until the explicit
claim UI.

### Explicit claim

The claim endpoints accept only the canonical browser cookie identity. A POST
or GET UUID must match that cookie. Missing cookie, posted foreign UUID, or
header-only identity is 403 (`anon_key_mismatch`) with no partial migration and
no `AnonAccountClaim` write. `claimed_elsewhere` / `hidden_anon` remain 409.
A successful claim rotates the browser to a new server-issued anonymous
identity so the claimed key does not stay in the cookie.

### Registered user on another device

Authenticated activity uses the same `user_id`. Anonymous history created on
the new device remains separate unless explicitly claimed.

### Logout and shared devices

Successful Django logout (`user_logged_out`, including `/logout/` and support
logout) rotates `interoves_anon` and `interoves_anon_sig`. Account history
stays on `user_id`. The next person in the browser gets a **new** anonymous
UUID; they do not keep writing as the previous user or the previous anon key.

Failed logout does not rotate.

Two accounts using one browser sequentially: logout rotation prevents B from
inheriting A's anonymous cookie. A's registered history remains on A's
`user_id`.

## Reporting consequences

- Do not equate Metrika visitors with backend actors.
- Do not join anon and user histories by heuristics; only claim/migrate does.
- Do not use email, Telegram username, names, phone, or IP for product metrics.
- Use only rows with exactly one identity.
- `instrumentation_version=2` versions event-writing semantics only. It is not
  the identity cutover.

Trusted identity cutover SHA/timestamp is recorded **only after** production
rollout and post-deploy validation. Until then, do not describe production
anonymous identity as fully trusted.

See also the Stage 1C design notes in [1c-anonymous-identity-hardening.md](1c-anonymous-identity-hardening.md).
