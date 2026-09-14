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
this is not implemented in Phase E either.

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
2. Valid UUID cookie, **no** signature, and that key already has anonymous
   history → **keep the same UUID**, mint `interoves_anon_sig`, and continue
   writing to the existing rows. This is the returning-visitor upgrade, not a
   new actor.
3. Missing, malformed, unknown unsigned, or **invalid signature** → issue a
   fresh server UUID (UUID4) and new signature. Gameplay is not 500.

A well-formed `interoves_anon` cookie without `interoves_anon_sig` is adopted
only when we already have rows for that key (starts, attempts, completions,
chain/salad state, daily timing, analytics state, hints, a claim, or an
Alphabetty personal dictionary). A random unsigned UUID with no history is not
inherited. Visitors who already have the HttpOnly signature keep the same UUID.

Setting someone else's **known** unsigned cookie can still inherit that
legacy actor until that browser receives a signature. Header/POST/URL cannot.

Tabs and revisits share the cookies, so they share one anonymous identity.
Corrupt cookies mint a new identity instead of failing the request.

Do not log raw UUID or signature. Diagnostics may use
`anon_key_fingerprint()`, which is not reversible.

## Rolling deploy limitation

During a mixed Stage 1C / Phase E deploy:

- Phase E instances ignore header/POST/URL for actor selection (same as 1C).
- Phase E instances **keep a known unsigned `interoves_anon`** (one that
  already has history) and mint a signature. Unknown unsigned cookies get a
  fresh identity.
- Remaining 1C instances still adopt any well-formed unsigned cookie.
- A visitor who already has a signature is stable on both code versions.
- Finish the rollout promptly. Mixed deploy can still split brand-new
  unsigned cookies that 1C would have adopted and Phase E would not.

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

## Trusted identity cutover

Stage 1C is **closed on production**. Anonymous identity is trusted from this
boundary forward under the cookie + HMAC protocol above.

| Field | Value |
| --- | --- |
| Production SHA | `6c53989` |
| Validation completed | 2026-09-13T21:24:38Z (2026-09-14 01:24 +04) |
| What this means | Production anonymous actor is the server-issued `interoves_anon` cookie, proved by `interoves_anon_sig` (or, at this SHA, the unsigned compat adopt path) |
| What this is not | `instrumentation_version=2` — that only versions live start/completion write semantics |
| Still open at this SHA | Unsigned `interoves_anon` was still adopted; Phase E (mandatory signature) follows |

Validated on `6c53989` against production data:

- signup auto-claim of the current cookie; rows reassigned, not copied; claim idempotent; cookie rotated;
- existing-account login does not write `AnonAccountClaim` and leaves prior anon starts on `anon_key`;
- logout rotates `interoves_anon` / `interoves_anon_sig`; later anonymous writes use the new key, not the previous user or key;
- explicit claim is 403 `anon_key_mismatch` without the matching cookie; success moves the start, writes one `AnonAccountClaim`, and rotates the cookie;
- `check_product_analytics --since 2026-09-13T21:24:00+00:00 --until 2026-09-13T21:26:00+00:00`: FAIL=0; `completion_without_start` and `completion_without_start_legacy` both 0 in that window (the earlier 15 legacy WARN rows stay outside it); no `anon_writes_after_claim`; no Traceback / IntegrityError / HTTP 500 in the test minute.

Cookie issuance, reuse, unsigned adopt, invalid-signature rotation, and header/query spoof rejection were first confirmed on `37858ee` after the identity deploy and remain in this SHA.

## Phase E: mandatory signature

Phase E is implemented in this tree. It is **not** production-trusted until a
live check after deploy: unknown unsigned cookies mint a fresh identity;
unsigned cookies that already have history keep the same `anon_key` and receive
a signature.

Do not treat this as a new analytics schema cutover. No `AnalyticsActor`, no
alias table, no copy of rows. Dual-key `ANALYTICS_ANON_SIGNING_KEY` rotation is
a later stage, not this one.

Stage 2A product metrics (`games.product_metrics`) use the Stage 1C cutover
above, not Phase E and not `instrumentation_version=2`. A future Phase E live
check still does not move that metrics boundary.

See also the Stage 1C design notes in [1c-anonymous-identity-hardening.md](1c-anonymous-identity-hardening.md).
