# Current product analytics identity

Stage 1A documents the existing model; it does not add `AnalyticsActor`, change
cookies, or alter signup/login/logout/claim behavior.

## Identity namespaces

Product rows store exactly one identity:

| Field | Meaning | Created/selected by |
| --- | --- | --- |
| `user_id` | registered Django account | authenticated request / `analytics_user` |
| `anon_key` | anonymous browser-held bearer value | frontend JS, normally `crypto.randomUUID()` |
| `team_id` | team attribution supported by schema | team-mode fallback where no analytics user is selected |

The type is part of the actor key. User `42`, team `42`, and anon string `"42"`
are three different actors.

## Anonymous key lifecycle

For unauthenticated new-UI pages, the browser reads in this order:

1. `localStorage['interoves_anon_key']`;
2. cookie `interoves_anon`;
3. `anon` or `anon_key` URL parameter as a fallback;
4. a newly generated value (`crypto.randomUUID()` where available; a weaker
   time/random fallback otherwise).

It persists the value to localStorage and a JS-readable `SameSite=Lax`, path `/`
cookie. Existing templates currently set inconsistent maximum ages: the base
template uses 31,622,400 seconds (about 366 days), while the game template can
refresh it to two years. LocalStorage has no application expiry. This is a
confirmed limitation, not normalized in 1A.

Multiple tabs normally share localStorage and the cookie, so they normally reuse
one key. If storage is blocked, in-memory/URL fallback behavior can create a new
identity. A damaged, absent, or cleared value creates a new key.

The server currently accepts the anonymous key from URL, cookie, header, or form
data in different endpoints. It validates syntax in the claim flow, but ordinary
gameplay treats the key as a bearer identifier. It is not signed and possession
is not generally proven. Therefore it is opaque in the common UUID path but not
a hardened identity credential.

## Authentication transitions

### Signup or login

After authentication, new product activity is attributed to `user_id`. Anonymous
history is not silently aliased for reporting. The UI may offer an explicit
“move this device's game” flow. That flow checks syntax, compares the posted key
to the cookie when a cookie exists, checks `HiddenAnonKey`, and uses the unique
`AnonAccountClaim` record to prevent a key being claimed by two different users.
It then physically moves or merges attempts, state, starts, completions, and
analytics state.

If the cookie is unavailable, `_anon_key_matches_browser` currently accepts the
posted bearer key. This does not fully prove browser ownership. Repeated claim by
the same user is intended to be idempotent; merge functions collapse overlaps
rather than copying event history wholesale.

### Registered user on another device

Authenticated activity uses the same `user_id`, so new activity joins the
account without needing the old browser key. Anonymous history created on the
new device remains separate unless explicitly claimed.

### Logout and shared devices

Logout does not rotate or clear `interoves_anon_key` / `interoves_anon`. A later
anonymous visitor in the same browser can therefore reuse the previous browser's
anonymous key. A successful claim UI rotates the browser key, but logout alone
does not. This shared-device risk remains for stage 1C.

## Reporting consequences

- Do not equate Metrika visitors with backend actors.
- Do not join anon and user histories by heuristics.
- Do not use email, Telegram username, names, phone, or IP for product metrics.
- Use only rows with exactly one identity.
- Treat anonymous and registered identities as separate unless the existing
  claim/migration has actually reassigned the rows.
- `instrumentation_version=2` versions event-writing semantics only. It neither
  authenticates `anon_key` nor changes any transition described above.

The alternatives and migration trade-offs for hardening this model are evaluated
without implementation in [stage 1C](1c-anonymous-identity-hardening.md).

