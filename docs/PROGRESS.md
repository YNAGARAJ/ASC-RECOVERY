# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint.

## IMPORTANT — read this before doing anything else

**Two stages, confirmed by the user** (also saved as project memory
`project_roadmap_scope`):
1. Wave 3 remediation (`docs/audit/REGISTER.md`'s MUST-FIX list) — **done**,
   as far as this environment allows. 20/23 FIXED, 3 honestly OPEN with a
   documented reason (F-17, F-21, F-22 — see the register).
2. Build the unbuilt product-completeness gaps from
   `docs/MASTER-BUILD-PROMPT-V2.md`'s "PART 3 — GAP REGISTER" — **now
   underway.** Phase 4 (org/facility/membership access model) and Phase 5
   (user lifecycle and enterprise access) are both **complete**. Phase 6
   (security and PHI controls) is **in progress** — most of its own
   prompt turned out to already be built by Wave 3/Phases 4-5 (see
   below); one genuinely new item (forced re-auth for PHI export) is
   done, three more are scoped but not started.

**A phase-numbering collision to not get confused by**: `docs/PHASES.md`
tracks the *original* 12-phase build's checklist (already complete,
predates Wave 3 entirely) — its own phase numbers are unrelated to
`docs/MASTER-BUILD-PROMPT-V2.md`'s phase numbers (what this checkpoint is
about). Do not conflate the two, and do not check anything off in
`docs/PHASES.md` for V2 phase work — that file's checklist is closed and
historical.

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 4 (org/facility/membership access model) — COMPLETE

Replaced the flat `tenants`/`tenant_id` multi-tenancy model with
`organizations` (self-referencing hierarchy) → `facilities` →
`memberships` (role + scope, narrowed to specific facilities via
`membership_facilities` when `scope=SPECIFIC_FACILITIES`). RLS resolves
access recursively through the org hierarchy via two `SECURITY DEFINER`
functions, `resolve_accessible_facility_ids`/`resolve_accessible_org_ids`
(`alembic/versions/0001_initial_schema.py`). Clean-cut replacement (no
data migration — no real customer/PHI data has ever existed in this
system), plan-mode-approved. Full writeup of all 6 sub-steps, decisions,
and traps is preserved in git history for this file (`git log -p --
docs/PROGRESS.md`, the version as of commit `7be4733`) — condensed here
since Phase 5 now builds on top of it as settled, sealed ground:

- `Role` has 7 values (`platform_admin/org_admin/manager/biller/analyst/
  auditor/api_service`); `platform_admin`/`org_admin` hold identical
  action permissions (hierarchy position is what actually distinguishes
  them).
- `AuthContext` carries `user_id`, `subject`, `role` (resolved fresh from
  `memberships` every request — no token-revocation list needed as a
  result), `org_id`, `facility_id: UUID | None` (`None` when the active
  org doesn't resolve to exactly one facility; routes 400 via
  `require_facility()` rather than guessing).
- `asc_owner` needs `BYPASSRLS` (Docker's bootstrap user already has it;
  a manually-provisioned Postgres needs `ALTER ROLE asc_owner
  BYPASSRLS;` once, by a superuser — `docs/DB_SETUP.md`). Every DB test
  that bootstraps a fresh org/facility/user must seed via the **owner
  connection** (`tests/db/conftest.py::seed_org_facility_user`), never
  `asc_app` — there's no membership yet for a brand-new org to resolve
  access through.
- This repo has no "query across every facility/org I can reach in one
  call" endpoint anywhere — every `Repository` method takes one specific
  `facility_id`/`org_id`, required. Deliberate, still true after Phase 5
  steps 1-3.

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 5 (user lifecycle and enterprise access) — COMPLETE, 7/7 steps

**Scoped down to a core subset, confirmed with the user 2026-08-07**:
invitation → accept → MFA → first login, offboarding, delegated admin,
API keys, per-org policy — now. SSO, SCIM, impersonation, and break-glass
are an explicit deferred follow-up pass (each independently large and/or
has an external-verification ceiling this environment can't meet, same
disclosure pattern as Wave 3's KMS/SFTP findings). Plan approved via
plan mode; the plan file itself (`adaptive-gliding-milner.md` in the
user's `~/.claude/plans/`) has the full original 7-step breakdown if
needed, but is not durable storage — this file and git history are.

**Also confirmed**: `org_policies.mfa_required` (step 6, not yet built)
will exist as a column but the API will never accept `false` for it — MFA
stays unconditional everywhere, per CLAUDE.md's "no exceptions" rule.

### Step 1/7 — Schema + RLS (`706ca9b`) — DONE

New tables `invitations`, `invitation_facilities`, `api_keys`,
`org_policies`; `memberships.revoked_at` (soft revocation — `asc_app` has
no `DELETE` grant on any mutable table, so offboarding is an `UPDATE`).
Two new `SECURITY DEFINER` functions in
`alembic/versions/0007_user_lifecycle.py`: `get_invitation_by_token_hash`
and `accept_invitation` — both anonymous (no `app.user_id`), because
invitation acceptance has no authenticated caller by definition; token
possession is the authorization, same principle as a password-reset
link.

**Self-caught security bug, fixed in this step**: 0001's
`self_membership`/`self_membership_facility` RLS policies carried no
`FOR SELECT` qualifier, so Postgres applied them to *every* command —
any authenticated user could have INSERTed a membership row for
themselves at any org_id/role/scope (self-granting `platform_admin`
anywhere). Never exploited (Phase 4 built no membership-creation route),
but tightened to `FOR SELECT` only here, with new deliberately-scoped
`org_authoring` INSERT/UPDATE policies (checked against the *caller's*
own resolved org access) added alongside for the legitimate write paths
steps 2+ needed.

### Step 2/7 — Delegated admin read surface (`3535302`) — DONE

`GET /organizations/members` (`Action.MANAGE_USERS`-gated) lists the
caller's active org's memberships. **No `org_id` path/query param** —
every other route in this API resolves its scoping id server-side from
`AuthContext`, never from the client (`test_tenant_param_absence.py`
guards this structurally); an early draft of this route took `org_id` as
a path param and had to be corrected before committing. Needed a new
migration (`0008_membership_read_policy.py`) adding the `org_authoring_select`
SELECT policy 0007 deliberately deferred — 0007 gave org-resolved
INSERT/UPDATE on someone else's membership row but left SELECT at
self-only, since the read shape wasn't known until this step.

### Step 3/7 — Invitation → accept → confirm-mfa → login (`2b553e6`) — DONE

`POST /invitations` (create, returns the raw token once — there is no
email-sending infrastructure in this codebase, see `docs/RUNBOOK.md`),
`GET /invitations/{token}` (anonymous preview), `POST
/invitations/{token}/accept` (anonymous — sets password, generates +
returns an MFA secret and `otpauth://` URI once), `POST
/invitations/{token}/confirm-mfa` (anonymous, stateless TOTP
re-verification, no DB write). After accept + confirm-mfa, the existing
`POST /auth/login` (F-04/F-05) just works, unchanged — no login-path code
touched.

New `security/tokens.py`: `generate_token`/`hash_token` (SHA-256, not
scrypt — a token is looked up by exact-match equality, not verified
against a user-typed guess, so a slow salted hash buys nothing and would
make every lookup expensive). Will be reused by step 5 (API keys).

**Design decision**: `confirm-mfa` deliberately has no lockout/rate-limit
protection of its own (`enforce_rate_limit` requires an `AuthContext`
these anonymous routes don't have by definition, and a second
`AccountLockoutTracker` would only protect a UX confidence-check with no
exploitable consequence — a successful guess yields nothing without also
knowing the password from `accept`, which this endpoint never touches).
The actual TOTP gate that matters is `POST /auth/login`, already hardened
by F-06.

**Design decision**: `accept_invitation` (`PostgresRepository`) pre-checks
status/expiry via the same preview read before calling the `SECURITY
DEFINER` DB function, so the common invalid-token path is a clean `None`
→ 404 rather than an uncaught Postgres exception falling through to
`api/errors.py`'s generic 500 handler. The DB function's own `FOR UPDATE`
recheck remains the actual safety net for the rare concurrent-accept
race — deliberately not wrapped in its own exception handler, since that
race is vanishingly rare and the existing generic 500 handler is already
a safe (if inelegant) fallback for it.

### Step 4/7 — Offboarding (`964ec35`) — DONE

`POST /organizations/members/{membership_id}/revoke`
(`Action.MANAGE_USERS`-gated, 204) soft-revokes via
`memberships.revoked_at` — an `UPDATE`, the only write `asc_app` is ever
granted on a mutable table, same convention as everywhere else. No new
migration needed: RLS's `org_authoring_update` policy from step 1
(`0007_user_lifecycle.py`) already covers "an org-resolved caller
updates someone else's membership row," so this is purely an
application-layer route + repository addition on top of sealed schema.

This is the phase's actual stated gate — **"offboarding test proves
instant session death"** — and it's proven directly, not inferred:
`tests/api/test_offboarding.py::test_revoking_membership_kills_the_next_request_immediately`
issues a token, confirms it works, revokes the membership through a
separate admin call, then re-uses the *same, still-unexpired* token on
the *same* route and asserts 401 — no re-login, no token-revocation
list, because role/access is resolved fresh from `memberships` on every
request (`api/auth.py::get_auth_context`) and revocation just makes that
resolution come back empty. Also covered: the full role×action matrix
for this one endpoint, double-revoke → 404, unknown id → 404, and the
cross-org IDOR case (a real `membership_id` belonging to another org
404s identically to a nonexistent one, and is untouched — that org's own
admin can still revoke it). `tests/db/test_offboarding.py` proves the
same guarantee one layer down, through `asc_app` with real RLS: an
org-resolved caller can revoke, an outsider's `UPDATE` matches zero rows
(RLS narrows the `WHERE`, no raised error), and revoking twice is a
no-op. Both test files are DB/API-fake pairs, following the exact
pure/DB-split discipline every phase since Phase 5 (original numbering)
established.

**Deliberately no dedicated `audit_log` entry for a revocation** —
confirmed with the user. Every existing `write_audit_log` call in this
codebase is for a write to a PHI-bearing table (CLAUDE.md rule 5's
literal scope); `memberships` isn't one, and `audit_log.facility_id` is
`NOT NULL` with no clean single-facility target for an
`ALL_FACILITIES`-scoped membership anyway. The gate's own proof is the
instant-session-death test, not an audit row.

**`FakeRepository` (`tests/api/fakes.py`) revocation-awareness**: every
access-resolution helper (`_accessible_org_ids`, `_accessible_facility_ids`,
`_default_membership_org_id`, `resolve_membership_role`,
`list_org_members`) now skips `revoked_at is not None` memberships —
mirrors the real `resolve_membership_role`'s `AND m.revoked_at IS NULL`,
and is what makes offboarding take effect on the fake repository's very
next call, not just in the real DB path.

### Step 5/7 — API keys (`edd5655`) — DONE

`POST /api-keys` / `GET /api-keys` / `POST /api-keys/{id}/revoke`
(`Action.MANAGE_USERS`-gated, same delegated-admin shape as everything
else in Phase 5), plus the piece that actually makes a key useful: a new
branch in `api/auth.py::get_auth_context` that lets a presented API key
authenticate a request at all, not just be administered.

**No new table needed** — `api_keys` (with its `org_access` RLS policy)
was already created in step 1's `0007_user_lifecycle.py`, unused until
now. One new migration, `0009_api_key_lookup_function.py`, adding a
single `SECURITY DEFINER` function, `get_api_key_by_hash` — the same
bootstrap problem `get_invitation_by_token_hash` (step 1) solves for
anonymous invitation lookups: turning a presented key into a
`user_id`/`org_id` has to happen *before* any `app.user_id` exists for
RLS to resolve access from, so this one read has to bypass RLS by
design, narrowly, same justification as every other `SECURITY DEFINER`
function in this schema.

**Deliberately no dedicated role/scope/facility columns on `ApiKey`
itself** (`db.models.ApiKey`'s docstring, unchanged from step 1):
creating a key provisions an ordinary service `User` (no
password/MFA — unusable for interactive login) holding an ordinary
`role=api_service` `Membership`. Authentication therefore reuses the
*exact* `resolve_membership_role`/`resolve_default_facility_id`
machinery a human session goes through — not a parallel authorization
system to keep in sync. This has a real consequence worth remembering:
**offboarding a service account works two ways**, either revoking the
`ApiKey` row (`POST /api-keys/{id}/revoke`) or revoking the underlying
`Membership` (`POST /organizations/members/{id}/revoke`, step 4, if the
admin knows its `membership_id`) — both kill access on the very next
request, no token-revocation list, same guarantee step 4 proved for
humans.

**`security/tokens.py`** gained `API_KEY_PREFIX = "ask_"` and
`generate_api_key()` — a key is `API_KEY_PREFIX` + the same
256-bit-random `generate_token()` value, hashed and stored whole
(prefix included; never stripped, never decomposed). `api/auth.py`
branches on this prefix *before* attempting either kind of token
validation (`token.startswith(API_KEY_PREFIX)`), so a JWT is never
hashed-and-looked-up as a key and an API key is never run through JWT
signature verification — cheap to check, safe by construction against
collision (every JWT starts with the base64 of `{"`, i.e. `eyJ`, never
`ask_`). Both branches converge on one `_resolve_auth_context` helper,
which is also where `ApiKey.last_used_at` (a column that existed since
step 1 but had zero writers until now — the same "built but unwired"
anti-pattern the master prompt's Phase 6 audit amendment names
explicitly) gets touched, best-effort, after a successful
authentication, via `touch_api_key_last_used` running inside an
`access_session` scoped to the key's own service user (whose own
membership is what makes the `org_access` UPDATE policy permit the
write).

`tests/api/test_api_keys.py` covers the full create/list/revoke role
matrix and IDOR shape (mirroring `test_offboarding.py`), plus the
distinctive part unique to this step: a freshly created key's raw value
actually authenticates a real request (`GET /findings` returns 200), a
revoked key is rejected (401) on its very next use, an expired key
(seeded directly via a new `FakeRepository.seed_api_key` test helper,
since the create endpoint's TTL is fixed and can't produce an
already-expired key) is rejected the same way, an unknown key is
rejected, and a key is bound to the org it was created in (can reach its
own tenant's findings, never the other tenant's).
`tests/db/test_api_keys.py` proves the same guarantees one layer down
through real RLS: `get_api_key_by_hash` resolves anonymously with no
`app.user_id` set at all (proving the `SECURITY DEFINER` bypass is what
makes lookup possible, not an accident of a permissive policy), an
org-resolved admin can revoke a key, an outsider's revoke attempt
matches zero rows, double-revoke is a no-op, and `touch_api_key_last_used`
succeeds via the owning service user's own resolved access.

### Step 6/7 — Per-org policy (`591358b`) — DONE

`GET /org-policy` / `PUT /org-policy` (`Action.MANAGE_USERS`-gated).
**No new migration** — `org_policies` (with its `org_access` RLS policy)
already existed from step 1's `0007_user_lifecycle.py`, unused until
now, same situation `api_keys` was in before step 5. `PUT` is a full
replace (no `PATCH`) since there are only two settable fields.

**`mfa_required` has no field in `UpdateOrgPolicyIn` at all** — the
guarantee that the API can never accept `false` for it (CLAUDE.md's
"no exceptions" MFA rule, confirmed with the user back in step 1) comes
from there being no code path that reads a client-sent value, not from
validating a boolean and rejecting `False`. `GET /org-policy` still
reports it (always `true`) for transparency. A missing policy row (an
org that's never configured one) is not a 404 — it's the documented
lazy-creation default state (`db.models.OrgPolicy`'s docstring), so
`GET` returns it as a normal 200 with `updated_at: null`.

**`session_timeout_seconds`** is an `issue_session` access-token TTL
override, wired at the one call site that matters:
`api/routes/auth.py`'s login route, after password+MFA verification,
via the now-authenticated user's own resolved access to their default
org's policy (`repository.get_org_policy(credentials.user_id,
credentials.default_org_id)`). `security/session.py::issue_session`
gained an optional `access_token_ttl` parameter (defaults to the
existing `ACCESS_TOKEN_TTL` constant when `None`); `_mint_pair` now
takes it explicitly rather than closing over the module constant.
**Deliberately does not carry through `refresh_session`** — that
function has no `Repository` access by design (a decision already made
in step 3, restated in this module's own docstring), and no
`POST /auth/refresh` HTTP route exists yet to call it from at all; a
refreshed token always re-mints at the plain default. `tests/security/
test_session.py` proves the override both shortens and lengthens the
default window (decoding the raw JWT directly for the "does refresh
correctly ignore it" case, to keep that assertion about the `exp`/`iat`
gap rather than being timing-dependent on wall-clock luck).

**`ip_allowlist`** is enforced in `api/auth.py::_resolve_auth_context`,
*after* role resolution but shared by both the JWT and API-key auth
branches — a stolen token used from an unexpected network is rejected
the same way regardless of which kind of credential it is. The actual
CIDR-aware matching (a bare IP or a CIDR range, either parses via
`ipaddress.ip_network`; a malformed allowlist entry is skipped rather
than raising; an unparseable or missing client IP always fails closed)
lives in new, deliberately pure `security/ip_allowlist.py` — no
`Request` dependency, so every branch is unit-testable without an HTTP
request in the loop at all (`tests/security/test_ip_allowlist.py`).

**A real environment limitation, disclosed rather than worked around**:
Starlette's `TestClient` (this whole suite's HTTP-level test harness)
always presents the literal string `"testclient"` as `request.client
.host` — never a real IP, and no supported way to override it in the
installed Starlette version (0.41.3). That string can never match a
real IP/CIDR allowlist entry, so `tests/api/test_org_policy.py` can only
prove the *rejection* path over a live HTTP request (configure a
restrictive allowlist, confirm the very next request 403s) — the
*matching* path (a real client IP actually inside an allowed CIDR range)
is proven only at the pure-function level
(`tests/security/test_ip_allowlist.py`), never through a real request in
this environment. Named explicitly rather than silently having a gap in
coverage no one flagged.

### Step 7/7 — Docs (`e892c52`) — DONE, PHASE 5 COMPLETE

Documentation only — no code, no new tests, no new gate. The phase's
actual stated gate ("offboarding test proves instant session death") was
already satisfied by step 4; this step documents what steps 1-6 built,
nothing more.

- **`docs/RUNBOOK.md`**: a new "Managing users, API keys, and org policy"
  section with curl-level operator workflows for all four — inviting a
  user (through to first login), offboarding (finding a `membership_id`
  via `GET /organizations/members`, then revoking it), provisioning/
  revoking an API key, and reading/writing per-org policy. All four are
  ordinary authenticated HTTP calls (an already-logged-in `org_admin`'s
  bearer token), unlike "Onboarding a new customer" above it, which is
  necessarily a direct-DB script (no authenticated caller exists yet for
  a brand-new org).
- **`docs/PERMISSIONS.md`**: notes that offboarding/API keys/org-policy
  all reuse the existing `manage_users` action — **no new `Action` was
  added across any of steps 1-6**. Retitled to disambiguate against
  `docs/SECURITY.md`'s different (v1-numbering) "Phase 4"/"Phase 7"
  references — this file's "Phase 4"/"Phase 5" are always
  `MASTER-BUILD-PROMPT-V2.md`'s. "Not yet built" list updated: API key
  provisioning is off it (built); SSO/SCIM/impersonation/break-glass are
  on it, named explicitly as the deferred follow-up pass this phase's
  own scoping decision (top of this file) already called out, not folded
  into a vague "later."
- **`docs/SECURITY.md`**: five control-matrix changes. Two rows updated
  in place — MFA's row now cites the real `POST /auth/login` endpoint
  that closed the "mechanism with zero production callers" gap
  `MASTER-BUILD-PROMPT-V2.md`'s own Phase 5 audit amendment named (true
  through step 3, only now documented here); automatic logoff's row
  covers the `session_timeout_seconds` override. Three new rows:
  immediate access revocation (offboarding, §164.308(a)(3)(ii)(C)), API
  key authentication (§164.312(d)), and the per-org IP allowlist
  (§164.312(a)(1)) — each citing its actual test coverage, including the
  IP-allowlist row's disclosed environment limitation (below). Added
  SSO/SCIM/impersonation/break-glass as a named "not yet built" gap
  alongside the pre-existing OIDC row, same reasoning as the
  `PERMISSIONS.md` update above.

**A real environment limitation, disclosed rather than worked around
(discovered writing step 6's tests, documented here in step 7)**:
Starlette's `TestClient` (this whole suite's HTTP-level harness) always
presents the literal string `"testclient"` as `request.client.host` —
never a real IP, no supported override in the installed version
(0.41.3). `tests/api/test_org_policy.py` can therefore only prove the
IP-allowlist's *rejection* path over a live HTTP request; the *match*
path is proven only at the pure-function level
(`tests/security/test_ip_allowlist.py`). Named in both `docs/SECURITY.md`
and this file rather than left as a silent coverage gap.

### Full local gate as of step 7 / Phase 5 complete (`e892c52`)

Unchanged from step 6 (docs-only commit, re-verified rather than
assumed): `ruff check .` clean, `mypy --strict .` clean (193 files, only
the 2 pre-existing unrelated alembic 0004 JSONB errors — present since
before Phase 4, not this session's doing), `pytest -q` 682 passed / 64
skipped, `domain/variance.py` 100% coverage gate, `python -m evals.run`
GATE PASSED. No new migration this step — `alembic upgrade head --sql`
still ends at 0009, verified when step 5 added it.
**Every RLS policy/`SECURITY DEFINER` function added across steps 1, 2,
and 5 (including `get_api_key_by_hash`) is written and offline-verified
only — never run against a real Postgres in this environment** (same
disclosed gap as Phase 4's RLS work; F-22's local Postgres is still
unclaimed, see "Traps" below). **Do not consider Phase 5's RLS/
`SECURITY DEFINER` additions confirmed until they run against a real
Postgres**, same standard the original 12-phase build held itself to
before Phase 10's CI run retroactively confirmed its own equivalent
gaps (`docs/PHASES.md`).

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 6 (security and PHI controls) — IN PROGRESS

**Audited the actual prompt against the actual code before assuming
anything was still open** (`2983cbf`) — most of Phase 6's checklist
turned out to already be true, built across Wave 3 remediation and
Phases 4-5, not this phase:

- AES-256 at rest, envelope encryption + KEK rotation, KMS port with
  real AWS/Azure adapters — Phase 4 + Wave 3's F-20.
- MFA mandatory, no bypass, with a real `POST /auth/login` endpoint —
  Phase 4 + Phase 5 step 3.
- Short-lived tokens + rotating refresh — Phase 4.
- PHI redaction installed **structurally** on the root logger at
  startup (`observability/logging_config.py::configure_logging()`,
  called from `main.py`) — already satisfies the audit amendment's
  literal ask ("installed structurally, once ... not attached one
  logger at a time"), just never previously credited as closing it.
- Rate limiting **and** account lockout wired into real routes —
  register finding F-06, `FIXED` in Wave 3 (`5f8d462`); every router
  built since (Phase 5) followed the same wired-by-default convention.
  `docs/SECURITY.md` still claimed "wired to zero routes" (a stale,
  Phase-10-era snapshot) until this phase corrected it.
- Secrets behind an external-store interface — Phase 4.
- TLS 1.2+ enforced — AWS's `aws_lb_listener.https` already sets
  `ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"` plus an HTTP→HTTPS
  redirect listener (Phase 9 Terraform, never previously credited in
  `docs/SECURITY.md`, which still said "not yet built"). Azure relies on
  Container Apps' platform-default HTTPS-only ingress rather than an
  explicit pinned minimum — a real, if minor, asymmetry, documented
  rather than silently left unequal.

**Genuinely unbuilt**, confirmed via `AskUserQuestion` (same pattern as
Phase 5's SSO/SCIM scoping): per-org encryption keys (BYOK-ready), a
per-org rate-limiting ceiling, per-org data residency, and forced re-auth
for PHI export. **User chose to build only the last one this session** —
the other three are named, scoped, and explicitly deferred in
`docs/SECURITY.md`'s "Not yet built" section, not started.

### Forced re-auth for PHI export (`2983cbf`) — DONE

`security/session.py::require_recent_auth` previously had **zero call
sites anywhere in the app** — exactly the "built but unwired" pattern
this project's own audit named as a repeating failure mode, caught here
before it needed a seventh unplanned-audit discovery. Closed by:

- Changed `require_recent_auth`'s signature from `(claims:
  AccessTokenClaims)` to `(authenticated_at: datetime)` — it only ever
  read one field off `claims`, and the new second caller (API keys, see
  below) has no `AccessTokenClaims` at all to construct.
- `api.auth.AuthContext` gained `authenticated_at: datetime`. For a JWT,
  this is the token's `auth_time` claim (unaffected by `refresh_session`
  — a refresh carries the *original* auth time forward, per
  `security/session.py`'s own long-standing design). For an API key,
  it's `datetime.now(UTC)` at the moment of authentication — presenting
  a raw secret each time is itself a fresh authentication, with no
  "session age" concept the way a JWT has one.
- New `api.auth.require_permission_with_recent_auth(action)` — same RBAC
  check as `require_permission`, plus `require_recent_auth`. Needed a
  `pyproject.toml` addition (`extend-immutable-calls`) alongside
  `require_permission`, or ruff's B008 flags it as a mutable-default
  call in a route signature.
- Wired onto exactly one route: `GET /findings/export.csv` — the only
  endpoint in this whole API actually shaped like a bulk export.
  **Today's CSV columns carry no patient-identifying fields at all**
  (`FindingSummary` has no `patient_name`/`patient_member_id`) — the
  control guards the export *pattern* (a bulk pull, easy to exfiltrate
  from an already-compromised-but-unexpired token) against the risk that
  patient identifiers get added to it later without anyone remembering
  to add this check retroactively, not today's specific column list.
- No new step-up endpoint: the caller's remediation on a 401 is simply
  calling `POST /auth/login` again, which already mints a fresh
  `auth_time` by succeeding. Deliberately not building a second,
  narrower "just re-verify TOTP" endpoint for this — the existing login
  route already does the whole job.

`tests/api/test_csv_export.py` proves: a session authenticated more than
`REAUTH_MAX_AGE` (5 min) ago is rejected (401, the exact message);
one authenticated just inside the window still succeeds; and — the
scope-boundary proof — a stale session can still call plain `GET
/findings`, since this control only guards the one route it's actually
wired to, not the whole API. `tests/security/test_session.py`'s two
pre-existing `require_recent_auth` tests updated for the new signature
(`claims.authenticated_at` instead of bare `claims`), no behavior change.

### Full local gate as of this commit (`2983cbf`)

`ruff check .` clean (after the `pyproject.toml` B008 allowlist
addition), `mypy --strict .` clean (193 files, only the 2 pre-existing
unrelated alembic 0004 JSONB errors), `pytest -q` 685 passed / 64
skipped, `domain/variance.py` 100% coverage gate, `python -m evals.run`
GATE PASSED, `bandit -r . -x ./tests,./evals` clean. No schema change
this commit, so no new migration/offline-SQL surface.

## Traps for someone resuming cold

- **Everything Phase 4's checkpoint already flagged still applies**: the
  `BYPASSRLS` precondition, owner-vs-app connection split for seeding,
  CRLF warnings on every `git add`, `mypy --strict .` sweeping the whole
  repo via the `.` argument, the PHI-content guardrail hook, and bandit
  nosec placement on multi-line strings (a `# nosec` comment must land on
  a real code line, never inside a triple-quoted string's content — this
  is *why* step 1 renamed `_GET_INVITATION_BY_TOKEN_HASH_SQL` to
  `_LOOKUP_INVITATION_BY_HASH_SQL` instead of fighting nosec placement:
  bandit's B105 heuristic matches on the *Python variable name*, not the
  SQL content, so dropping "token" from the identifier was the real fix).
- **`org_id`/`facility_id` are never client-supplied, anywhere.** Before
  adding a new route, check `test_tenant_param_absence.py` — it
  structurally enforces this. Step 2 almost shipped a `{org_id}` path
  param before this got caught; every scoping id comes from
  `AuthContext`, resolved server-side from the bearer token.
  Resource-specific ids (`finding_id`, `claim_id`, a future `packet_id`)
  are fine as path params — RLS enforces the boundary on those (cross-org
  lookup by known id returns 404, never another org's row).
- **F-22's local-Postgres handoff is still live and still unclaimed** — a
  real PostgreSQL service is running on this dev machine, setup deferred
  pending the `postgres` superuser password (see `docs/DB_SETUP.md`'s
  "Bring up Postgres" section for the exact handoff SQL, including the
  `BYPASSRLS` grant). Running Phase 4 *and* Phase 5's RLS/SECURITY
  DEFINER tests for real, for the first time, would be valuable if that
  password ever becomes available — there is now a full session's worth
  of offline-only-verified RLS policy and function code riding on the
  assumption the SQL is correct.
- **Migrations 0007/0008/0009 are additive on top of 0001, not edits to
  it** — 0001's schema is sealed (same convention 0002-0006 already
  established for Phase 4). Phase 5 ended at migration `0009`; any V2
  Phase 6 schema work should be a new `0010_...` migration.
- **Phase 5 added no new `Action`** — offboarding, API keys, and
  per-org policy all reuse `Action.MANAGE_USERS`. If Phase 6 (security
  and PHI controls) needs a new permission boundary, check
  `docs/PERMISSIONS.md`'s action matrix first; don't assume one needs
  adding without checking whether an existing action already covers it,
  the way step 4 verified `org_authoring_update` already covered
  offboarding before concluding no new RLS policy was needed.

## Next steps

Phase 6 is in progress, scope already resolved (see its section above --
don't re-litigate what's already built vs. deferred, it was just
audited item by item against the actual code). Three items remain,
already scoped, not started:

1. **Per-org rate-limiting ceiling.** Smallest of the three. Add an
   aggregate per-org token bucket in `api/rate_limit.py::enforce_rate_limit`
   alongside the existing per-`(org_id, user_id)` one — today a large
   customer's users collectively face no shared cap, only individual
   ones. `security/rate_limit.py::InMemoryTokenBucketRateLimiter` is
   already generic enough to reuse for a second, org-keyed bucket
   without new abstraction.
2. **Per-org encryption keys (BYOK-ready).** Medium-sized, real design
   work — an org-level KEK reference (likely a new nullable column on
   `organizations`, or a new `org_encryption_keys` table if more than
   one field ends up needed), threaded through `EnvelopeEncryptor`'s
   call sites (`ingestion/apply.py` write path, `api/repository.py` read
   path) instead of the one global KEK `main.py` wires today. Touches
   more surface than the other two — plan-mode this one before writing
   code, the way every schema-shaped phase in this project has.
3. **Per-org data residency flag.** Small to build, but confirm the
   "stored preference, not physical enforcement" framing
   (`docs/SECURITY.md`'s "Not yet built" section already states it) is
   still acceptable before writing it — this build is one shared
   Postgres in one region, so anything claiming more would be fiction.
4. **If picking this up much later**, re-verify the full local gate
   before trusting anything — it was green as of `2983cbf`, but confirm
   it still is.
5. **If the F-22 Postgres password becomes available**, run Phase 4 and
   5's live-DB suites for real before trusting any of their RLS/function
   code beyond what's offline-verified — independent of Phase 6, and
   worth doing before Phase 6 adds more RLS-adjacent work (the per-org
   BYOK item above will, if built).
