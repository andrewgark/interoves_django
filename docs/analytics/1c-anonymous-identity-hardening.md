# Stage 1C design: anonymous identity hardening

Status: read-only design. Stage 1A does not implement any option below.

## Confirmed baseline

The browser currently creates `anon_key`, stores it in localStorage and a
JS-readable cookie, and sends it in cookies/forms/headers/occasionally URLs. The
server uses the presented value as the anonymous actor key. The explicit claim
flow checks format, compares against the cookie when one exists, and records a
unique `AnonAccountClaim`, but absence of a cookie falls back to possession of
the posted bearer value.

This is persistent enough for ordinary repeat visits and shared across tabs, but
it does not establish ownership of a key. Logout does not rotate it. Signup/login
starts writing new activity to `user_id`; previous anonymous history joins only
through the explicit physical migration/merge.

## Evaluation criteria

Each option is compared for key substitution, signup/login/logout, claim,
multiple devices, shared devices, existing game history, implementation volume,
migration/rolling risk, and rollback.

## Option 1: keep the current scheme

Problem solved: none beyond existing persistence. It preserves current behavior
and avoids migration risk.

- Key substitution: no; a client that learns another valid key can present it.
- Signup/login: unchanged; new rows use `user_id`, optional claim moves history.
- Logout/shared device: unchanged; a later anonymous visitor can inherit the
  browser key/history.
- Claim: unchanged, including the cookie-absent bearer fallback.
- Multiple devices: registered activity joins by `user_id`; anonymous histories
  remain per browser and require separate claims.
- Existing history: fully preserved without rekeying.
- Scope/risk: zero code/migration work; known security and continuity risks remain.
- Rolling/rollback: no concern.

Best fit when the product accepts anonymous identity as low-assurance browser
state and prioritizes no behavioral change. Stage-1A version 2 is compatible with
this option but does not make it safer.

## Option 2: sign the existing `anon_key`

Issue a server-verifiable signature over the current key and require the
key/signature pair for sensitive identity use. The underlying analytics key and
existing history can remain unchanged.

- Key substitution: protects against inventing or modifying a key without a
  valid signature. It does not help if the full signed bearer token is copied or
  leaked, especially through URL propagation or JS-accessible storage.
- Signup/login: can continue using `user_id`; the signed token proves that the
  client received the key from this server, not that it is the same human.
- Logout/shared device: unless explicitly rotated on logout, the next person
  inherits the signed token and history. Rotation policy is a separate product
  choice.
- Claim: can require a valid signature even when the cookie is absent, removing
  the current unsigned posted-key fallback. Claims and physical merge logic can
  otherwise stay.
- Multiple devices: each device keeps a different anon key; login joins future
  activity, and each prior key can be claimed separately.
- Existing history: requires a compatibility window. Existing unsigned keys
  need either one-time server signing when presented under acceptable proof, or
  must remain legacy/unclaimable. Automatically signing any arbitrary presented
  legacy key would not fix substitution for legacy history.
- Scope: moderate changes to token issuance, validation, storage, all anonymous
  request extraction, and claim UX. URL transport should be removed or carefully
  constrained to avoid signed-token leakage.
- Rolling deploy: new server must accept old unsigned clients during transition,
  while new clients may hit old servers that do not issue/understand signatures.
  A staged “issue + observe, then require” protocol is needed.
- Rollback: keep accepting the old key and retain signatures as ignorable
  additive state; avoid irreversible rekeying in the first release.

This is the smallest option that can reject fabricated keys after transition,
but it is still a bearer credential and does not solve shared-device logout by
itself.

## Option 3: server-issued cookie, no Actor table

Move anonymous key issuance to the server and store it in a hardened cookie
(normally `Secure`, `SameSite`, and `HttpOnly` where frontend access is not
required). Continue storing the opaque key directly in existing model columns.

- Key substitution: strong for cookie-based endpoints if the server ignores
  client-supplied header/form/URL keys. An opaque cookie copied by an attacker is
  still a bearer credential.
- Signup/login: authenticated requests use `user_id`; the request can retain a
  separately read server cookie for an explicit, confirmed claim.
- Logout/shared device: the server can rotate the anonymous cookie on logout to
  prevent account/browser history inheritance. Whether logout should create a
  fresh anonymous identity must be explicit and tested.
- Claim: cookie possession becomes the minimum proof; the posted `anon_key` is no
  longer authoritative. Existing `AnonAccountClaim` and merge functions can be
  retained.
- Multiple devices: each device gets a key; login joins future registered
  activity, with explicit claim per device.
- Existing history: legacy browser keys require a controlled upgrade. A server
  may adopt a legacy cookie only if it trusts that cookie provenance; adopting a
  header/URL value recreates the vulnerability. Some legacy histories may remain
  separate.
- Scope: medium-to-large changes to middleware/request resolution, CSRF-safe
  issuance/rotation, frontend assumptions, URL fallbacks, logout, and tests, but
  no analytics actor migration.
- Rolling deploy: old servers may trust client headers while new servers do not;
  cookie naming/versioning and staged dual-read/single-write behavior are needed.
- Rollback: retain the old browser key during the transition or make the new
  cookie readable by old code; avoid deleting legacy storage until rollback is
  no longer required.

This may be the best middle ground if the goal is to stop arbitrary substitution
without introducing actor aliases. It needs a product decision about storage-
blocked browsers and logout continuity.

## Option 4: `AnalyticsActor` + credential + aliases

Create a stable internal actor row, separate client credentials from actor IDs,
and map anonymous credentials and registered accounts through aliases. Events
reference the internal actor; credentials can be rotated without changing
history.

- Key substitution: strongest separation. The client sees only a rotatable,
  hashed/verified credential, never an assignable actor ID.
- Signup/login: aliases can link the authenticated account and confirmed browser
  actor without physically rewriting all events. Conflict policy is still needed
  when both histories exist.
- Logout/shared device: rotate/revoke the browser credential and issue a new
  anonymous actor/credential without exposing the registered actor.
- Claim: becomes alias creation based on credential possession plus authenticated
  account, with auditable idempotency.
- Multiple devices: several credentials/anonymous aliases can resolve to one
  registered actor; device revocation is possible.
- Existing history: requires mapping legacy `user_id`/`anon_key`/`team_id` rows or
  query-time aliases. Physical rewrite is avoidable but reporting queries become
  more complex during transition.
- Scope: largest—new tables, constraints, middleware, credential lifecycle,
  endpoint conversion, claim/account-merge changes, reporting joins, migrations,
  privacy review, and extensive concurrency tests.
- Rolling deploy: requires additive schema, dual resolution/dual read phases, a
  clear source of truth, and compatibility for old instances writing legacy
  identities. Partial rollout can split histories if ordering is wrong.
- Rollback: keep legacy identity columns and writes until the new resolution path
  is proven; actor/alias rows can remain unused on rollback. Removing them or
  rewriting history must be a later irreversible step.

This is justified only if stable cross-device aliases, revocation, non-rewriting
merges, or stronger auditability are concrete requirements. It should not be
selected merely because it is the most comprehensive design.

## Comparison summary

| Option | Rejects fabricated key | Solves shared-device logout alone | Preserves rows without rewrite | Change size | Main residual risk |
| --- | --- | --- | --- | --- | --- |
| Current | No | No | Yes | None | substitution and inheritance |
| Signed existing key | Yes after transition | No | Usually | Medium | copied bearer token; legacy bootstrap |
| Server-issued cookie | Yes when client IDs ignored | Yes with rotation policy | Usually | Medium/large | legacy upgrade and storage restrictions |
| Actor + credential + aliases | Yes | Yes | Yes via mapping | Very large | migration/query/operational complexity |

## Decision gates before implementation

1. Decide whether the threat is fabricated keys, copied bearer keys, shared
   devices after logout, cross-device identity, or all of these.
2. Decide what anonymous continuity is promised when cookies/localStorage are
   unavailable.
3. Measure how much unclaimed legacy history exists and whether losing automatic
   continuity is acceptable.
4. Define explicit signup, login, logout, claim-conflict, multiple-tab, and
   multiple-device behavior before choosing storage.
5. Prototype rolling compatibility across old/new client and old/new server
   combinations.
6. Keep PII out of credentials, aliases, logs, diagnostics, and analytics rows.

No option is selected by stage 1A. Option 2 or 3 may be sufficient if the only
confirmed requirement is preventing arbitrary `anon_key` substitution; option 4
is reserved for broader identity lifecycle requirements.

