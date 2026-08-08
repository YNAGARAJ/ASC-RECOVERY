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
   underway.** Phases 4 (org/facility/membership access model), 5 (user
   lifecycle and enterprise access), and 6 (security and PHI controls)
   are all **complete**. Most of Phase 6's own prompt turned out to
   already be built by Wave 3/Phases 4-5 (see below); its four
   genuinely-new items (forced re-auth for PHI export, per-org
   rate-limiting ceiling, per-org encryption keys/BYOK, per-org data
   residency) are now all done too. **Phase 7 (async job infrastructure)
   has not been started** — see "Next steps" below.

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

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 6 (security and PHI controls) — COMPLETE

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

### Per-org rate-limiting ceiling — DONE

Second of the three deferred Phase 6 items, built this session (user
asked to continue after forced re-auth landed). `api/rate_limit.py`
already had a per-`(org_id, user_id)` `InMemoryTokenBucketRateLimiter`
(F-06, wired since Wave 3) — this was never a per-*org* limit despite
`docs/SECURITY.md` describing it loosely enough to be misread that way:
N users at one org each got their own full budget, independent of each
other, so nothing actually bounded that org's *combined* traffic.

Added a second, independent limiter instance (`app.state.org_rate_limiter`,
alongside the existing `app.state.rate_limiter`), keyed by `org_id`
alone. `enforce_rate_limit` now requires *both* to allow a request —
per-user check first (unchanged order/behavior for every existing
caller), org-wide check second. Default capacity/refill for the org
bucket (600 / 10 per second) is deliberately well above one user's own
budget (60 / 1 per second) — set it any lower and this would just be a
confusing, redundant copy of the per-user check rather than an actual
aggregate-traffic protection against one large customer starving shared
capacity. `create_app` gained a matching `org_rate_limiter` override
param, same test-injection convention as the existing `rate_limiter`
param (`tests/api/test_rate_limit.py`'s `_client_with_capacity`, now
joined by `_client_with_org_capacity`).

Two new tests prove the org ceiling is genuinely aggregate, not a
relabeled per-user check: two *different* users at the same org share
one budget (the org-level analog of, and structurally the inverse of,
the pre-existing `test_different_users_have_independent_budgets`, which
is about the per-user limiter); and the ceiling doesn't leak across
orgs (tenant B's request still succeeds while tenant A's is exhausted).

No schema change, no new migration. `docs/SECURITY.md`'s rate-limiting
row updated to describe both limiters; the "Not yet built" bullet
narrowed to the two items still actually outstanding (per-org
encryption keys, per-org data residency).

### Per-org encryption keys (BYOK-ready) — DONE

Third of the four Phase 6 items, user said "continue" without
re-scoping — proceeded since the design was already sketched in this
file's own "Next steps" from the prior checkpoint. Turned out smaller
than expected once the actual `EnvelopeEncryptor`/`KeyManagementService`
port shape was re-read: `EncryptedPayload.kek_id` already travels with
every ciphertext, and `decrypt`/`unwrap_key` already resolve whichever
key a given payload actually needs — the entire gap was on the *write*
side only (nothing let a caller choose a non-default `kek_id` to
encrypt under in the first place).

**Schema**: `organizations.kms_key_id` (nullable `String(500)`,
`alembic/versions/0010_org_kms_key.py`, additive on top of the sealed
0001 schema, same guarded-`add_column` idiom as every migration since
0002). `NULL` = "use the platform default" — same meaning
`EnvelopeEncryptor.encrypt`'s own `kek_id=None` fallback already has, so
the two defaults line up by construction, not by convention someone has
to remember.

**Encryption layer**: `EnvelopeEncryptor.encrypt` gained an optional
`kek_id` keyword (falls back to `self._kms.current_kek_id()` when
`None`); `security/phi_columns.py::encrypt_phi_field` forwards it.
`decrypt`/`decrypt_phi_field` needed **zero changes** — already correct
for mixed keys per record.

**KMS adapters — a real behavior change, not just plumbing**:
`AwsKmsAdapter.wrap_key` and `AzureKeyVaultAdapter.wrap_key` used to
raise `KeyError` for any `kek_id` other than the adapter's own
configured one. That restriction was self-imposed, not something either
cloud's real API requires (AWS's `Encrypt`/Azure's `wrap_key` both
already take an arbitrary key id per call) — removed it from both, so
`current_kek_id()` remains "the platform default when the caller has
nothing more specific" while `wrap_key` now honors whatever `kek_id` an
org's own key resolves to. Azure's docstring needed care here: the
existing restriction conflated two different properties (rotation-
safety: `current_kek_id()` must never be stale vs. blocking any
caller-supplied key at all), and only the second one needed to go.
**`EnvKMS` (the default stopgap adapter) deliberately keeps its
restriction** — it holds exactly one static key, so there's no safe way
for it to honor a per-org key even if asked; its docstring now explains
why, and `docs/RUNBOOK.md` states the ordering constraint explicitly:
**do not set `organizations.kms_key_id` until `KMS_PROVIDER` is
`aws-kms`/`azure-keyvault`**, or ingestion breaks (loudly, `KeyError`,
not silently) for that org's claims the moment a patient name needs
encrypting.

**Application wiring**: `ingestion/pipeline.py`'s `_ingest_file_impl`
already resolved a claim's org (`get_org_id_for_facility`, for contract
lookups) — added one more cheap lookup right next to it
(`get_organization_kms_key_id`), then threaded the result down through
`apply_ingestion_plan` → `_apply_claim` → `encrypt_phi_field` for both
`patient_name_encrypted`/`patient_member_id_encrypted`. Resolved once
per file, not once per claim.

**Key assignment is deliberately not a self-service API** — set at
onboarding time via `scripts/onboard_customer.py`'s new optional
`kms_key_id` config field, or afterward via a direct, operator-reviewed
`UPDATE organizations SET kms_key_id = ...` through the owner
connection. Same reasoning `onboard_customer.py` itself already
established for org creation: no "platform superadmin" API surface
exists, and misconfiguring an org's encryption key is exactly the kind
of high-blast-radius mistake that shouldn't be one HTTP call away for
an org_admin to trigger on themselves. **Existing data is never
re-encrypted by changing this column** — only future writes pick up a
new key; each payload's own stored `kek_id` is what it always decrypts
under, so flipping the column is safe and instant, not a migration.

**Tests**: pure — `tests/security/test_encryption.py` (explicit `kek_id`
used and defaults to `current_kek_id()` when omitted),
`test_phi_columns.py` (the override survives serialization),
`test_kms_aws.py`/`test_kms_azure.py` (both adapters' `wrap_key` now
accept an arbitrary kek_id and round-trip correctly; the old
raises-on-mismatch tests replaced, not just added to, since that
behavior is gone). DB-backed (skip without `TEST_DATABASE_URL`):
`tests/db/test_organization_kms_key.py` (the lookup itself, including
the `None`-default case) and `tests/ingestion/test_apply_org_kms_key.py`
(the full `ingest_file` path — an org with a dedicated key encrypts
under it, one without still gets the platform default).

No API/route change, no new `Action` (`docs/PERMISSIONS.md` untouched).
`docs/RUNBOOK.md` gained a "Per-org encryption keys (BYOK)" section.

### Full local gate as of `608653c`/`f320755` (BYOK)

`ruff check .` clean, `mypy --strict .` clean (196 files, only the 2
pre-existing unrelated alembic 0004 JSONB errors), `pytest -q` 690
passed / 68 skipped, `domain/variance.py` 100% coverage gate,
`python -m evals.run` GATE PASSED, `bandit -r . -x ./tests,./evals`
clean. Offline SQL generation (`alembic upgrade head --sql` /
`downgrade 0010:0009 --sql`) verified both directions through migration
0010.

### Per-org data residency — DONE, PHASE 6 COMPLETE

Fourth and last Phase 6 item, user said "continue" a third time without
re-scoping. Deliberately the smallest of the four: a stored declaration
on the already-existing `org_policies` table (Phase 5 step 6's home for
per-org settings), not a new table, not a technical control — the
framing flagged as needing confirmation in this file's own prior
checkpoint turned out uncontroversial to just build, since it was
already the only honest option (this system runs one shared Postgres in
one region; claiming physical enforcement would be fiction).

**Schema**: `org_policies.data_residency_region` (nullable
`String(100)`, `alembic/versions/0011_org_data_residency.py`, additive,
same guarded idiom as 0002/0010). Lives on `org_policies`, not
`organizations` — deliberately different from `kms_key_id` (BYOK,
previous item), which correctly lives on `organizations` since it's
closer to infrastructure/identity than to a configurable policy knob.

**API**: extends the *existing* `GET`/`PUT /org-policy` endpoints
(`docs/MASTER-BUILD-PROMPT-V2.md` Phase 5 step 6) rather than adding a
new route — `org_policies` already is "per-org configurable settings,"
and this is one more of those. Free-text, max 100 chars, no enum:
constraining the value would imply a precision (real, validated regions)
that doesn't exist. **Deliberately self-service** (`org_admin`/
`platform_admin` via a normal `PUT`) — a real, considered asymmetry
against BYOK's operator-only, direct-DB-write treatment: misdeclaring a
region doesn't lock anyone out of their own data the way a wrong KMS key
would, so the blast radius doesn't justify taking it out of the API.

**Tests**: `tests/api/test_org_policy.py` (round-trip through the
existing PUT/GET pair, the new `max_length=100` validation boundary,
defaults to `None` when omitted — the existing dict-equality default
test needed updating for the new field, not just extending) and
`tests/db/test_org_policy.py` (round-trip and clear-on-omit at the
repository layer, DB-backed, skips without `TEST_DATABASE_URL`). No new
role-matrix test needed — `GET`/`PUT /org-policy` are already in the
existing matrix tests, which don't need to know about individual field
additions.

`docs/SECURITY.md` gained a new control-matrix row explicitly contrasted
against the BYOK row above it (same table, adjacent, so the self-service-
vs-operator-only asymmetry reads as a deliberate design choice, not an
inconsistency); its own "Not yet built" bullet for this item removed
entirely, not just reworded, since there is nothing left to disclose as
missing. `docs/RUNBOOK.md` gained a "Per-org data residency" section.

### Full local gate as of this commit — Phase 6 complete

`ruff check .` clean, `mypy --strict .` clean (197 files, only the 2
pre-existing unrelated alembic 0004 JSONB errors), `pytest -q` 692
passed / 69 skipped, `domain/variance.py` 100% coverage gate,
`python -m evals.run` GATE PASSED, `bandit -r . -x ./tests,./evals`
clean. Offline SQL generation verified both directions through
migration 0011.

**All four of Phase 6's genuinely-unbuilt items are now done**: forced
re-auth for PHI export, the per-org rate-limiting ceiling, per-org
encryption keys (BYOK-ready), and per-org data residency. Combined with
everything the initial audit found already true (AES-256 at rest, MFA,
PHI log redaction, rate limiting/lockout wiring, TLS 1.2+ on AWS), this
closes every item `docs/MASTER-BUILD-PROMPT-V2.md`'s Phase 6 prompt
names. **Not independently re-verified against a live Postgres** — same
disclosed ceiling as Phases 4/5 (F-22's local-Postgres handoff, still
unclaimed) — migrations 0010/0011 and every DB-backed test this phase
added are offline-verified/skip-without-a-database only.

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 7 (async job infrastructure) — COMPLETE

Genuinely new ground, unlike Phase 6 — v1 of this codebase never had any
job queue/worker layer at all, so there was no "already built by an
earlier phase" overlap to audit away this time. Plan-moded given the
size (`C:\Users\523na\.claude\plans\temporal-crunching-bear.md`), scoped
down via two `AskUserQuestion` decisions confirmed with the user before
writing it: **Postgres-backed queue** (`SELECT ... FOR UPDATE SKIP
LOCKED`), not Redis+Celery/arq — this environment has zero existing
Redis infrastructure, and Postgres is already provisioned, already
verified via CI's real-Postgres container, and keeps the stack
cloud-agnostic per CLAUDE.md rule 7; and **generic job infrastructure +
ingestion as the one real, fully-wired job type** — the prompt names six
job types, only ingestion has existing business logic to move onto a
queue, so the other five (variance recomputation, report generation,
notification dispatch, reprocessing, export) are named, scoped, and
explicitly deferred, not built.

### What was built

- **`jobs` table** (`alembic/versions/0012_jobs.py`) — facility-scoped,
  RLS via the same `facility_access` policy shape every table since 0001
  uses. `payload_encrypted` reuses `security/phi_columns.py`'s existing
  `encrypt_phi_field`/`decrypt_phi_field` (the same machinery
  `claims.patient_name_encrypted` already uses, including Phase 6's
  per-org BYOK key resolution) for an ingestion job's raw file
  content — no new crypto. `dedup_key` (unique with `facility_id`/
  `job_type`) is the file's content hash, same idempotency property
  `record_remittance_if_new` already gave the old synchronous path.
- **Queue primitives** (`db/repository.py`'s new "Jobs" section) —
  `enqueue_job`, `get_job`, `list_jobs`, `cancel_job` (API-facing,
  ordinary RLS-scoped `access_session`) versus `claim_next_job`,
  `update_job_progress`, `is_cancel_requested`, `complete_job`,
  `fail_job`, `cancel_job_as_cancelled` (worker-facing, owner-privileged
  `BYPASSRLS` session — a worker sees the whole queue across every
  facility, which is not any single user's resolved access, the same
  reason `scripts/onboard_customer.py` needs `asc_owner`). The actual
  *business-logic execution* is neither of those: `src/jobs/runner.py`
  re-establishes the job's own submitter's resolved access via
  `access_session(session_factory, job.user_id)` — this is what makes
  "a worker can't read outside its facility scope" true, and why a
  revoked membership takes effect on a not-yet-run job exactly like it
  already does everywhere else.
- **Per-org concurrency limiting** lives entirely inside
  `claim_next_job`'s one query (a `facilities` join + `GROUP BY org_id
  HAVING count(*) >= per_org_limit` subquery on `running` jobs) — the
  phase's own wording is "per-org," not "per-facility," so an org owning
  several facilities is bounded in aggregate, mirroring the design
  language Phase 6's per-org rate-limiting ceiling already established
  (a different mechanism: that bounds HTTP request rate, this bounds
  concurrent job execution).
- **Stale-lock reclaim is built into the claim query itself**
  (`locked_at < now() - stale_lock_after`) — no separate sweep process.
  A worker that dies mid-claim leaves a row a later `claim_next_job`
  call picks back up once the lock goes stale.
- **Cancellation is cooperative**, checked every 25 claims
  (`ingestion/apply.py::_CALLBACK_INTERVAL`) inside
  `apply_ingestion_plan`'s loop via a new `should_cancel` callback;
  `True` raises `JobCancelledError`, which propagates out of
  `access_session`'s `with` block uncaught, triggering an automatic
  rollback — a cancelled job leaves zero partial claims/findings behind,
  the same "clean slate" property idempotent re-processing already
  relied on elsewhere in this module. The terminal `cancelled` status is
  recorded afterward, on the separate owner connection, only once the
  rollback has already happened.
- **Progress reporting** — same 25-claim interval, an `on_progress`
  callback that writes `progress_percent`/`progress_message` via the
  owner connection, avoiding a DB round-trip per claim.
- **Exponential backoff + dead-letter queue with alerting** —
  `src/jobs/runner.py::_backoff_seconds` (30s base, capped at 1h,
  doubling per attempt); `fail_job` transitions to `dead_letter` once
  `attempts >= max_attempts` (default 5); a new, threshold-free
  `observability/alerts.py::evaluate_job_dead_lettered_alert` fires on
  that exact transition via the existing `NotificationPort`, plus a
  `job_dead_lettered` audit-log entry. Errors are
  `security/redaction.py::scrub_text`'d before storage — CLAUDE.md rule
  6, never a raw exception message that might echo PHI.
- **`queue_depth` OpenTelemetry `UpDownCounter`** — previously a
  hardcoded-zero stub (`observability/metrics.py`), now `+1` on a
  genuinely new enqueue, `-1` on claim. Disclosed as *approximate*, not
  exact (doesn't track every later transition like retries or
  cancel-while-queued) — `SELECT count(*) FROM jobs WHERE status IN
  ('queued','failed')` is named as the authoritative source of truth in
  both the metric's docstring and `docs/RUNBOOK.md` if exact numbers
  matter.
- **API**: `POST /remittances` now enqueues and returns `202` with a
  `JobOut` (job id + `status: "queued"`) instead of running ingestion
  synchronously and returning `201` with the outcome. `GET /jobs`,
  `GET /jobs/{id}`, `POST /jobs/{id}/cancel` — all reuse the existing
  `upload_remittance` action (`docs/PERMISSIONS.md`), no new `Action`
  needed. `api.repository.JobSummary`/`JobOut` deliberately have **no
  field for `payload_encrypted`** — structurally incapable of leaking a
  job's PHI payload, not just filtered out at the response-building step.
- **Worker entrypoint** — `src/worker.py` (`python -m worker`), a thin
  `while True: claim_and_run_once(...) or sleep` loop around the
  testable `src/jobs/runner.py::claim_and_run_once` core, same
  testable-core/thin-CLI-wrapper split `scripts/ingestion/
  poll_remittances.py` already established. Needs **two** database
  connections: `DATABASE_URL` (`asc_app`, RLS-scoped, for job execution)
  and a new `QUEUE_DATABASE_URL` (`asc_owner`, `BYPASSRLS`, for queue
  bookkeeping) — never the same role for both.
- **`src/composition.py`** (new) — extracted the environment-driven
  adapter construction (`KMS_PROVIDER` switch, OTLP exporter selection,
  secrets validation) that used to live only in `src/main.py`, since
  `src/worker.py` now needs the identical logic and duplicating it would
  have meant two places to keep in sync on every future KMS/observability
  change.
- **Deployment** — `docker-compose.yml` gained a `worker` service
  (`entrypoint:` override, not `command:` — the Dockerfile's `ENTRYPOINT`
  is exec-form, so `command:` alone would only append to `uvicorn ...`,
  never replace it). AWS: a new `QUEUE_DATABASE_URL` Secrets Manager
  secret (assembled from the RDS-managed `asc_owner` master password,
  `data.aws_secretsmanager_secret_version.db_master` — previously read
  but never actually consumed anywhere in the module, now it is) plus a
  second `aws_ecs_task_definition`/`aws_ecs_service` (no ALB target
  group — the worker accepts no inbound traffic; `entryPoint` override to
  run the worker instead of uvicorn; reuses the app's own IAM role/
  security group rather than a new narrower one, since the worker's
  actual permission needs are a subset). Azure: the equivalent
  `queue-database-url` Key Vault secret (this module already
  Terraform-generates the admin password, `random_password.admin`,
  unlike AWS's RDS-managed one) plus a second `azurerm_container_app`
  with **no `ingress` block at all** (omitted entirely, not
  `external_enabled = false`), `command` override, reusing the app's
  user-assigned identity/Key Vault access policy. New Terraform
  variables: `worker_desired_count` (AWS), `worker_min_replicas`/
  `worker_max_replicas` (Azure) — scaling the worker is independent of
  scaling the API. **Written and reviewed, not exercised** — same
  disclosed ceiling as every other Terraform change in this project; no
  Docker or live cloud account in this environment, no `terraform`
  binary either (offline SQL generation is the only thing actually run
  for the migration; the `.tf` changes are unverified beyond careful
  reading against the existing app resources they mirror).
- **`docs/RUNBOOK.md`** gained an "Operating the job queue" section:
  scaling worker throughput (independent from API scaling, and
  concurrency is capped per org regardless of worker count), inspecting
  `jobs` directly for stuck/dead-lettered rows, and how cooperative
  cancellation actually behaves (not instant, rolls back cleanly).

### Deliberately deferred, named not silently dropped

- The other five job types (variance recomputation, report generation,
  notification dispatch, reprocessing, export) — the infrastructure
  supports them (`job_type` is a plain string + a handler-registry
  entry in `src/jobs/runner.py::_HANDLERS`), no business logic built,
  since none has an existing implementation to move onto a queue the way
  ingestion did.
- `scripts/ingestion/poll_remittances.py` (SFTP/S3 polling) stays
  calling `ingest_file` directly, unrouted through the new queue — it
  already satisfies "not inside an HTTP request" via its own existing
  external-cron scheduling; this phase's gate is specifically about the
  `POST /remittances` upload path.
- A Redis-backed rate-limiter adapter, if this ever runs as more than one
  API replica — unrelated to this phase's own Postgres-backed queue
  choice, same "single-process today" ceiling `security/rate_limit.py`
  already discloses.

### Tests added this phase

Pure: `tests/jobs/test_payload.py` (payload serialization round-trip,
including non-UTF8 binary content); `evaluate_job_dead_lettered_alert`
in `tests/observability/test_alerts.py`. DB-backed (skip without
`TEST_DATABASE_URL`): `tests/ingestion/test_apply_progress_cancel.py`
(`on_progress`/`should_cancel` checked every `_CALLBACK_INTERVAL` claims,
never every claim; `should_cancel() -> True` raises before any claim
persists); `tests/db/test_jobs_queue.py` (enqueue/dedup, API-facing
get/list/cancel with an RLS-outsider proof, claim/stale-lock-reclaim/
progress/cancel-check/complete/fail-with-backoff/dead-letter/
cancel-as-cancelled, and the per-org-concurrency-is-org-wide-not-
per-facility proof with two facilities under one org); `tests/jobs/
test_runner_live_db.py` (the actual Phase 7 gate — upload via the real
`POST /remittances`, run it with the real `claim_and_run_once`, observe
the outcome via the real `GET /jobs/{id}`, never leaking
`payload_encrypted`; a claim abandoned by a simulated dead worker gets
reclaimed by `stale_lock_after=0` and completes with exactly one
remittance/claim, never two, despite being claimed twice; the
ingestion-failure-rate alert's wiring proof, relocated here from
`tests/api/test_alerts_live_db.py` now that ingestion runs inside a job,
not inside `PostgresRepository`, at all).

### Full local gate as of this commit — Phase 7 complete

`ruff check .` clean, `mypy --strict .` clean (210 files, only the 2
pre-existing unrelated alembic 0004 JSONB errors — fixed one genuinely
new error this phase, `main.py`'s re-export of
`MissingConfigurationError` from the new `composition.py` needing an
explicit `as MissingConfigurationError` self-alias under
`--no-implicit-reexport`), `pytest -q` 696 passed / 84 skipped,
`domain/variance.py` 100% coverage gate, `python -m evals.run` GATE
PASSED, `bandit -r . -x ./tests,./evals` clean. `pip-audit` reports 17
pre-existing vulnerabilities across 5 packages (`ecdsa`, `pytest`,
`python-dotenv`, `python-jose`, `starlette`) — none introduced this
phase (no `pyproject.toml`/`requirements.lock.txt` change at all),
already the documented status quo in `docs/SECURITY.md`'s dependency-
scanning row; bumping them is an unrelated, separately-scoped task, not
this phase's. Offline SQL generation verified both directions through
migration 0012. **Not independently re-verified against a live
Postgres** — same disclosed ceiling as every phase since 4 (F-22's
local-Postgres handoff, still unclaimed) — every DB-backed test this
phase added is offline-verified/skip-without-a-database only, including
the actual Phase 7 gate test itself.

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 8 (contract modeling depth) — COMPLETE (gate scope)

The prompt's own framing: "v1's contract model produces false positives
on correctly paid claims. Fix it properly," explicitly plan-moded first.
Before writing any code, three parallel research agents audited the
actual pricing engine (`domain/contract.py`) against the phase's full
checklist. Findings: MPPR (multi-procedure reduction, fully configurable
per contract), bilateral modifiers (both conventions — `TWO_LINE_SPLIT`
fixed as F-16), assistant surgeon, implant carve-out (invoice-cost
pass-through only), and case-rate allocation all already existed, real
and tested. **Lesser-of and stop-loss/outlier pricing — the two items
the phase's own gate literally requires — did not exist at all.** Neither
did co-surgeon/discontinued-procedure modifiers, per-diem/RVU pricing,
annual escalators, payer-type rules, prompt-pay interest, or statute of
limitations.

Given the size of the full checklist, the user was asked to scope the
session via `AskUserQuestion` and chose **gate items only**: lesser-of +
stop-loss/outlier pricing, with the golden-set false-positive proof the
gate literally requires, plus re-verifying the F-16/F-17 audit amendment.
Everything else — including global periods/NCCI edits, which needs real
CMS reference data this project has no access to — is named and deferred
to a follow-up session, not silently dropped.

### What was built

- **Lesser-of billed charges vs. fee schedule** — the literal
  false-positive fix. `domain/contract.py::_apply_lesser_of` caps a
  fee-schedule amount at the billed charge whenever
  `ContractVersion.lesser_of_charge_enabled` and `charge < amount`;
  wired into both places `_base_price` returns a fee-schedule amount.
  Deliberately not applied to percent-of-charge amounts (already ≤
  charge whenever the rate is ≤ 100%, so this is specifically a
  fee-schedule-vs-charge concept). Stored per contract version, not
  hardcoded — defaults `true` at both the DB column and API-schema level
  (most real ASC contracts pay lesser-of), `false` at every pre-existing
  test/eval fixture (see "Blast radius" below).
- **Stop-loss/outlier pricing** — new `StopLossRule` (enabled, threshold,
  outlier_rate, first_dollar) and `PricingMethodUsed.STOP_LOSS_OUTLIER`.
  A new **step 0** in `price_claim`, before base pricing: when total
  claim charges exceed `threshold`, every non-implant line prices at
  `line.charge.times(outlier_rate)` — first-dollar, the percentage
  applies to each line's own full charge (summing to `outlier_rate` of
  the whole claim), not just the excess above threshold. Implants still
  carve out separately at invoice cost — same "implants are
  special-cased out of every other rule" precedent bilateral/assistant/
  MPPR already establish, now extended to stop-loss, along with
  explicitly excluding stop-loss-triggered lines from bilateral/
  assistant/case-rate so a triggered line can't get re-priced on top of
  its own stop-loss amount. New `RootCause.STOP_LOSS_NOT_APPLIED` in
  `domain/variance.py`'s root-cause chain. A marginal/excess-only stop-
  loss variant (as opposed to first-dollar) is out of scope, named not
  silently unsupported.
- **F-16 re-verification found a real, second gap**: `BilateralConvention
  .TWO_LINE_SPLIT`'s domain-layer pricing branch was fixed, but
  `api/schemas.py::BilateralRuleIn` had no `convention` field at all, and
  `api/repository.py` hardcoded `SINGLE_LINE_150_PCT` unconditionally —
  the only production entry point could never actually select
  `TWO_LINE_SPLIT`. Closed this phase: `convention` added to
  `BilateralRuleIn`, threaded through `RuleInput`/`ContractVersionInput`,
  proven end to end against a live Postgres
  (`tests/api/test_contracts_live_db.py`) — a contract created via the
  real `POST /contracts/{id}/versions` with `convention: "two_line_split"`
  now actually prices a two-line bilateral claim with the 100%/remainder
  split. `docs/audit/REGISTER.md`'s F-16 row updated with the full
  re-verification note. **F-17 (implant invoice-cost sourcing) is
  untouched** — stays open, per the prior recorded user decision; nothing
  in this phase's scope touches it.
- **Migration `0013_contract_stop_loss_lesser_of.py`** — the first real
  `ALTER TABLE contract_versions` since the table's creation (every prior
  schema change to this table came from 0001's `create_all()`). Adds
  `lesser_of_charge_enabled BOOLEAN NOT NULL DEFAULT true` and
  `stop_loss_rule JSONB NOT NULL DEFAULT '{"enabled": false, ...}'`,
  guarded/offline pattern copied from 0004's precedent on `contracts`. No
  RLS/grant changes needed — adding columns to an already-protected table
  doesn't touch policy.
- **Blast radius**: `ContractVersion` gained two new fields with **no
  dataclass default** (matching the existing convention — every field on
  this dataclass is explicit), so every construction site needed
  updating: `tests/domain/conftest.py`, `tests/ingestion/fixtures.py`,
  `tests/db/test_effective_dated_pricing.py`, `evals/generator.py`'s
  `_make_contract`, `db/repository.py`'s `_contract_version_to_domain`/
  `create_contract_version`, `api/repository.py`'s
  `_rule_input_to_contract_version`, and — caught only by a final
  whole-repo `mypy --strict .` sweep, not by the earlier targeted
  sweeps — `scripts/onboard_customer.py`. Every pre-existing site got
  `lesser_of_charge_enabled=False` and an inert, disabled `stop_loss_rule`
  — zero risk of silently perturbing an existing test's or golden case's
  expected values; verified by regenerating the eval golden set and
  confirming its 8 pre-existing categories' recall/precision/dollar-
  accuracy were unchanged.
- **Evals**: two new golden-set categories mirroring the generator's
  existing `_build_<category>_cases` pattern — `_build_stop_loss_cases`
  (new `DefectType.STOP_LOSS_NOT_APPLIED`, an underpayment where the
  payer paid the flat fee schedule instead of the outlier percentage) and
  new lesser-of/stop-loss patterns added to the existing
  `_build_correct_cases`/`DefectType.CORRECT_PAYMENT` builder — the
  gate's own explicit requirement: "cases that are correctly paid under
  the new rule and must not be flagged." Regenerated
  `evals/golden/cases.py` (504 → 560 cases). Gate: recall 100%
  (unchanged), precision 100% (unchanged, ≥ 98% required), dollar
  accuracy 100%, lesser-of and stop-loss correct-payment cases score
  `CORRECT_NO_VARIANCE` with zero false positives — the phase's own
  literal requirement.
- **Tests**: worked-example unit tests in `tests/domain/test_contract.py`
  (lesser-of enabled/disabled, above/below fee schedule; stop-loss below/
  above threshold, first-dollar whole-claim-basis summation, implant
  carve-out still applying over a triggered stop-loss, MPPR/bilateral/
  assistant/case-rate all correctly skipped for stop-loss-triggered
  lines); new `STOP_LOSS_NOT_APPLIED` root-cause test in
  `tests/domain/test_variance.py`; three real-ingestion-path tests added
  to `tests/ingestion/test_plan.py` (835 parse → plan, not just
  `price_claim` called directly) for lesser-of, stop-loss, and — closing
  the gap the audit-amendment re-verification found — bilateral
  `TWO_LINE_SPLIT`, satisfying Phase 8's own audit-amendment mandate that
  every domain rule have at least one case through the real parsing/
  ingestion path; a DB round-trip test in
  `tests/db/test_effective_dated_pricing.py`; a new
  `tests/api/test_contracts_live_db.py` (no dedicated API-level test of
  `POST /contracts/{id}/versions` existed before this phase at all).

### Deliberately deferred, named not silently dropped

Everything in Phase 8's checklist beyond lesser-of/stop-loss: co-surgeon/
discontinued-procedure modifiers, richer implant carve-out methodologies
(percent-of-billed, flat-rate — today's `ImplantCarveoutRule` only
identifies which lines are implants, no markup/methodology fields at
all), per-diem/RVU pricing with conversion factors, annual escalators,
payer-type-specific rules (Medicare/Medicaid/commercial/workers' comp/
auto), prompt-pay interest (configurable per state), recovery statute of
limitations per state, and global periods/NCCI edits (needs real CMS
reference data this project has no access to — out of scope regardless
of any future scope decision, not just this session's).

### Full local gate as of this commit — Phase 8 complete (gate scope)

`ruff check .` clean, `mypy --strict .` clean except 4 pre-existing-
category errors (212 files checked) — all four are the same known
SQLAlchemy-stub gap ("Call to untyped function JSONB in typed context")
migration 0004 already had two of; migration 0013 uses the identical
`JSONB()` idiom and adds two more instances of the same harmless,
already-documented class, not a new one. `pytest -q` 708 passed / 87
skipped, `domain/variance.py` 100% coverage gate, `python -m evals.run`
GATE PASSED (560 cases, recall 100%, precision 100%, dollar accuracy
100%), `bandit -r . -x ./tests,./evals` clean. Offline SQL generation
verified both directions through migration 0013. **Not independently
re-verified against a live Postgres** — same disclosed ceiling as every
phase since 4 (F-22's local-Postgres handoff, still unclaimed) — every
DB-backed test this phase added is offline-verified/skip-without-a-
database only.

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 9 (ingestion pipeline) — COMPLETE

User said "yes, proceed with Phase 9." Two research agents audited the
actual code before any implementation, per this session's established
discipline. Findings: **every checklist item except 837 claim file
ingestion already existed and was tested** — sources behind a port,
content-hash idempotency, quarantine with diagnostics, partial-batch
tolerance, virus scan, BPR/PLB reconciliation, reversal netting,
per-file audit, and running through Phase 7's job queue were all real,
already-built code. The phase's own gate ("same file 3× gives identical
totals · injected BPR mismatch is caught") was already satisfied by
existing tests, no new work needed there. Two real, distinct items
remained: F-01's audit amendment (the register's one CRITICAL finding)
was only half-closed despite a "FIXED" marker, and 837 ingestion was
100% unbuilt. Scoped via `AskUserQuestion`: user chose to build both
this session.

### Part 1 — F-01: unmatched reversal now quarantines

Re-verifying F-01 (mirroring how Phase 8 re-verified F-16 and found a
real, second gap under a "FIXED" marker) found the same pattern again:
the amendment's first requirement — match a reversing finding to the
*original* claim's service line, never the reversal claim's own line
positions, including when the reversal reports fewer lines than the
original — was genuinely fixed and tested, both at the plan layer and
end-to-end against Postgres (`tests/db/test_reversal_netting.py`'s
fewer-lines-than-original/`sum(shortfall)==0` proof). The amendment's
**second** requirement — "never let a reversing finding be silently
dropped — an unmatched reversal must fail loudly or quarantine the
file" — was never implemented: a reversal claim whose control number
matched zero prior findings silently produced `findings=()` and
ingested successfully, structurally indistinguishable from a
legitimately zero-variance original claim.

Closed: `ingestion/plan.py::build_ingestion_plan` now scans every claim
post-planning for `is_reversal and not findings` (an unambiguous signal
— `_reverse_finding` runs once per prior, and a genuinely zero-variance
original claim still gets a `CORRECT_NO_VARIANCE` finding row, so an
empty tuple can only mean the original was never found at all). Any
match quarantines the whole file, naming the unmatched control
number(s) in the diagnostic — reusing the exact same `quarantine_reason`
mechanism `apply_ingestion_plan` already checks first, no new "fail
loudly" exception machinery needed. `docs/audit/REGISTER.md`'s F-01 row
updated with the full re-verification note, same pattern as Phase 8's
F-16 entry.

### Part 2 — 837 claim file ingestion

New `domain/x837.py` — a hand-rolled 837P parser reusing `x835.py`'s
exact ISA-header/segment/element-splitting approach (no third-party X12
library in this project) but duplicated, not imported, same "second,
independent producer" convention `evals/generator.py` already
established. Deliberately narrow: only `CLM` (patient control number,
for correlation), `HI` (diagnosis code pointers), and `NM1` entity code
`82` (rendering provider) are parsed — no HL/SBR hierarchy tracking,
since 837 is **enrichment, not a parallel pricing/finding pipeline**. A
claim's dollar findings are still driven entirely by its 835; 837 data
only ever attaches to an already-ingested claim, correlated by
`patient_control_number` (`CLM01`/`CLP01`, the *submitter's* claim
identifier shared by both — deliberately not `payer_claim_control_number`,
which the payer only assigns post-adjudication and therefore never
appears on the claim-submission-side 837 at all).

- **New DB**: `claim_files` table (parallel to, not sharing with,
  `remittances` — an 837 isn't a remittance, no BPR/PLB/reconciliation
  concept applies), same `facility_access` RLS policy shape every table
  since 0001 has. `claims.diagnosis_codes_encrypted` (PHI — health
  condition data tied to a specific claim — envelope-encrypted with the
  exact same `security/phi_columns.py` machinery `patient_name_encrypted`
  already uses, including per-org BYOK). `claims.rendering_provider_name`
  and `service_lines.units` (not PHI — provider identity and a bare
  quantity, not patient identifiers) were **already parsed off every 835
  today** (`domain.x835.Claim835.rendering_provider`,
  `ServiceLine835.units`) but silently dropped before persistence —
  fixed in `ingestion/apply.py` alongside the 837 work, no 837 file
  needed for that half. Migration `0014_claim_files_and_837_enrichment.py`
  follows 0013's guarded/offline pattern for the new columns and 0012's
  pattern for the new table.
- **Dispatch**: `ingestion/pipeline.py::_ingest_file_impl` now peeks at
  the `ST01` transaction-set-identifier element (`_detect_transaction_set`,
  reading just the ISA header + first ST segment) before either parser
  runs, routing `"837"` to a new `_ingest_837_impl` and everything else
  — including anything undetectable/malformed — to the renamed
  `_ingest_835_impl` (a pure extract-function rename of the previous
  `_ingest_file_impl` body, zero behavior change to the well-tested
  existing path). An 837 arrives through the exact same upload/SFTP/S3
  sources and the same `ingest_remittance` job type as an 835 — only the
  parse+apply step branches, `src/jobs/runner.py` needed no new handler.
  New `ClaimFileOutcome`/`DuplicateClaimFileOutcome` outcome types
  rippled (as expected, caught by a whole-repo `mypy --strict .` sweep,
  not by targeted file-level checks) into `ingestion/poller.py`'s
  `PollableOutcome` type alias and `scripts/ingestion/poll_remittances.py`'s
  `ingest_one`/`_report`.
- **Unmatched-claim handling is the deliberate inverse of Part 1**: an
  837 claim whose `(facility_id, patient_control_number)` matches no
  existing claim is skipped for *that claim only*, counted in
  `claims_unmatched`, never a whole-file quarantine — a missing 835
  counterpart is an expected, recoverable ordering situation (the 837
  arrived first, or its 835 never will), not evidence of a
  financial-integrity bug the way an unmatched reversal is.
- **Packet integration**: `packets/prompt.py::PromptInput` gained
  `diagnosis_codes`/`rendering_provider_name`/`units`, all defaulting to
  empty/`None` so an 835-only claim drafts byte-for-byte as it did
  before this phase. Diagnosis codes are placeholder-substituted
  (`DIAGNOSIS_CODES_TOKEN`), never literal text in the prompt sent to
  the LLM — same F-13 PHI treatment claim reference/date of service
  already get; rendering provider/units are literal text, same as
  `procedure_code` already is, since neither is PHI. Wired into the one
  real call site, `api/repository.py::PostgresRepository.generate_packet`
  — `db.repository.FindingDetail` already joins the `claims`/
  `service_lines` rows the new columns live on, so no new query was
  needed, only three new `PromptInput(...)` keyword arguments.
- **Real bug found and fixed along the way**: `packets/currency.py`'s
  hallucination-detection regex (CLAUDE.md's "no LLM ever computes or
  restates a dollar amount" boundary) could false-positive on an ICD-10
  diagnosis code's own internal decimal point — `"E11.9"` produces a
  spurious `Decimal("9")` match (the bare-integer alternative's `\b`
  boundary matches right after the `.`), which would have caused
  `validate_currency` to reject an otherwise-valid packet draft as
  containing a "hallucinated" figure, forever, for any claim with an
  837-sourced diagnosis code. Caught by empirically testing the regex,
  not by reasoning about it — a first-pass hand-analysis concluded (
  wrongly) that letter-digit adjacency would prevent a match. Fixed by
  changing `extract_currency_figures`'s `exclude` semantics from
  filtering matched fragments after the fact to masking known-safe
  substrings out of the text before the regex ever runs — verified
  against all pre-existing `tests/packets/test_currency.py` cases
  (unchanged) plus the new diagnosis-code case.

### Deliberately deferred, named not silently dropped

837I (institutional) claim segments (`SV2`, institutional-specific
`NM1` loops) — only 837P's `SV1`-adjacent professional-claim grammar
subset this phase actually needs (`CLM`/`HI`/`NM1` entity `82`) was
built, since ASC billing is overwhelmingly professional-claim-shaped and
none of the deferred segments feed anything this phase's enrichment-only
architecture consumes. Per-service-line diagnosis pointers (SV1's own
composite element referencing which `HI` index applies to which line) —
diagnosis codes are treated as claim-level enrichment only, matching the
new `claims.diagnosis_codes_encrypted` column's own granularity, not a
new per-line column. HL/SBR hierarchical-level tracking (submitter/
subscriber identity) — unneeded since correlation is by
`patient_control_number` alone, already scoped by `facility_id`/RLS.

### Full local gate as of this commit — Phase 9 complete

`ruff check .` clean, `mypy --strict .` clean except 4 pre-existing-
category errors (216 files checked, same documented SQLAlchemy-stub gap
as every phase since — migration 0014 uses plain `sa.String`/`sa.Text`/
`sa.Numeric`, not `JSONB()`, so it adds none). `pytest -q` 722 passed /
89 skipped, `domain/variance.py` 100% coverage gate, `python -m
evals.run` GATE PASSED (560 cases, recall 100%, precision 100%, dollar
accuracy 100% — unchanged from Phase 8, confirming 837 adds no
golden-set category, by design: it's enrichment, not a pricing/finding
path), `bandit -r . -x ./tests,./evals` clean (one real finding caught
mid-phase and fixed: migration 0014's own `CREATE POLICY` statement
f-string-interpolated a hardcoded table-name constant into SQL text,
triggering B608 even though nothing user-controlled was involved —
fixed by hardcoding the literal table name directly, matching every
prior migration's own `CREATE POLICY` convention, which never
interpolated the table name via f-string in the first place). Offline
SQL generation verified both directions through migration 0014. **Not
independently re-verified against a live Postgres** — same disclosed
ceiling as every phase since 4 (F-22's local-Postgres handoff, still
unclaimed) — every DB-backed test this phase added is
offline-verified/skip-without-a-database only.

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
- **Migrations 0007-0012 are additive on top of 0001, not edits to it** —
  0001's schema is sealed (same convention 0002-0006 already established
  for Phase 4). Phase 7's `jobs` table ended at migration `0012`; any
  further schema work should be a new `0013_...` migration.
- **`organizations.kms_key_id` is only safe to set once `KMS_PROVIDER`
  is `aws-kms`/`azure-keyvault`** — setting it while the deployment
  still runs `EnvKMS` (the default) breaks ingestion for that org
  (`KeyError`, loud, not silent) the moment a patient name needs
  encrypting. `docs/RUNBOOK.md`'s "Per-org encryption keys (BYOK)"
  section has the full ordering constraint. Don't add a self-service API
  for setting this column later without re-reading why it was
  deliberately left operator-only (same section, and this file's Phase 6
  writeup above).
- **Phase 5 added no new `Action`** — offboarding, API keys, and
  per-org policy all reuse `Action.MANAGE_USERS`. If Phase 6 (security
  and PHI controls) needs a new permission boundary, check
  `docs/PERMISSIONS.md`'s action matrix first; don't assume one needs
  adding without checking whether an existing action already covers it,
  the way step 4 verified `org_authoring_update` already covered
  offboarding before concluding no new RLS policy was needed.
- **`POST /remittances` no longer runs ingestion synchronously** — it
  enqueues and returns `202`. Anything that used to assume a `201` with
  claim/finding counts in the response body (a script, a frontend call,
  a test) needs to poll `GET /jobs/{id}` instead; `tests/api/
  test_authz_matrix.py` and `test_openapi.py` both needed exactly this
  update this phase, and both are easy to miss if grepping only for
  `ingest_remittance` (the repository method was renamed to
  `enqueue_remittance_ingestion`, not just re-pointed).
- **The worker needs `QUEUE_DATABASE_URL` (`asc_owner`) in addition to
  `DATABASE_URL` (`asc_app`)** — `src/worker.py` will `MissingConfigurationError`
  at startup without it. `docker-compose.yml`'s `worker` service already
  sets both; a real deployment's Terraform provisions the
  `QUEUE_DATABASE_URL` secret but still needs the `asc_owner` role's
  actual database grants applied via the same migration-step handoff
  `docs/RUNBOOK.md` already documents for `asc_app` — provisioning the
  secret is not the same as the role having `BYPASSRLS` in the actual
  database.
- **A job's `payload_encrypted` is real PHI (an ingestion job's raw file
  bytes)** — never add a field for it to `JobSummary`/`JobOut`, even
  temporarily for debugging. If a future job type's payload is *not*
  PHI, that's a reason to reconsider whether it needs
  `payload_encrypted` at all, not a reason to relax this one column's
  existing contract.
- **`src/jobs/runner.py::_HANDLERS` is the one place a future job type
  gets registered** — before building variance recomputation/report
  generation/notification dispatch/reprocessing/export (this phase's
  deliberately-deferred five), re-read this phase's writeup above for
  what's already generic (queue primitives, RLS, encryption, retry/
  backoff/dead-letter, per-org concurrency) versus what's ingestion-
  specific (`src/jobs/payload.py`'s `build_ingestion_payload`/
  `parse_ingestion_payload`, `run_ingestion_job`'s own decrypt-then-call-
  `ingest_file` shape) and shouldn't be assumed to generalize without
  checking.
- **`domain.contract.ContractVersion` has no dataclass defaults on any
  field, including the two Phase 8 added** — every construction site
  must supply `lesser_of_charge_enabled`/`stop_loss_rule` explicitly.
  Before adding a third new field, grep for `ContractVersion(` across
  `tests/`, `evals/`, and `src/` first (`scripts/onboard_customer.py` was
  missed by every targeted search this phase and only caught by a final
  whole-repo `mypy --strict .` sweep) — the full list as of this phase:
  `tests/domain/conftest.py`, `tests/ingestion/fixtures.py`,
  `tests/db/test_effective_dated_pricing.py`,
  `evals/generator.py::_make_contract`, `db/repository.py`'s
  `_contract_version_to_domain`/`create_contract_version`,
  `api/repository.py::_rule_input_to_contract_version`,
  `scripts/onboard_customer.py::_build_contract_version`.
- **`evals/golden/cases.py` is generated, never hand-edit it** — a change
  to `evals/generator.py` (a new `DefectType`, a new `_build_*_cases`
  builder, a new field on `_make_contract`) needs `python -m
  evals.generator` rerun to actually take effect, and the generated
  file's own hardcoded `from domain.contract import (...)` header (in
  `write_golden_dataset`) needs updating by hand alongside any new type
  the generator's `_render()` needs to import — `_render()` itself is
  fully generic (dataclass-field reflection), so a new frozen dataclass
  like `StopLossRule` needed zero changes there, only the import list.
- **`price_claim`'s stop-loss step (step 0) runs before every other
  step, including implant detection (step 2)`** — it has to check
  `_is_implant` directly rather than reading the `is_implant_line` array,
  since that array isn't populated yet at step 0. Any future step added
  *before* step 2 needs the same direct-check treatment; any step added
  *after* step 2 can trust `is_implant_line`.
- **`contract_versions` had never been ALTERed before migration 0013** —
  every earlier schema change to that table came from 0001's
  `create_all()` reflecting `db/models.py` at migration-write time, not a
  real `ALTER`. 0013 is the first real precedent specifically for this
  table (`contracts` already had one, from migration 0004) — copy 0013's
  guarded/offline pattern for the next one, not 0004's (same shape, but
  0013 is the more recent, directly-analogous example).
- **A "FIXED" marker in `docs/audit/REGISTER.md` names what got fixed,
  not necessarily the finding's entire scope** — this is now the second
  time in two phases (F-16 in Phase 8, F-01 in Phase 9) that
  re-verifying an already-"FIXED" finding against its own full text
  found a real, second gap the fix commit never touched. Before trusting
  any register entry as fully closed, re-read the finding's complete
  original text (not just the "FIXED" line) and check whether every
  sentence of it is actually satisfied, not just the most obvious one.
- **`ingestion/plan.py::build_ingestion_plan`'s unmatched-reversal check
  and `ingestion/pipeline.py::_ingest_837_impl`'s unmatched-claim
  handling are deliberately opposite policies** — one quarantines the
  whole file, the other skips just the one claim and keeps going. Don't
  "fix" one to match the other later without re-reading why: a reversal
  with nothing to net against signals a financial-integrity problem (the
  system would otherwise silently under-report what it already told a
  customer they could recover); an 837 with no matching 835 yet signals
  ordinary file-arrival ordering, nothing has gone wrong.
- **Any new `PromptInput` field that carries caller-controlled or
  injected text must be checked against `packets/currency.py`'s
  hallucination regex before assuming it's safe** — `extract_currency_
  figures`'s bare-integer alternative matches on a `\b` word boundary,
  which a period, comma, or almost any non-alphanumeric character
  creates even *inside* an otherwise-safe string (`"E11.9"` → spurious
  `Decimal("9")`). `exclude` now masks known-safe substrings out of the
  text before matching (not fragment-filtering after), which handles
  this correctly going forward — but verify empirically
  (`extract_currency_figures(text, exclude=...)`, actually run it) for
  any new value shape, don't reason about the regex by inspection alone;
  a first-pass hand-analysis this phase concluded wrongly that
  letter-digit adjacency alone would prevent a match.
- **`domain/x837.py` is deliberately narrow (837P only, `CLM`/`HI`/`NM1`
  entity `82`, no HL/SBR hierarchy, no per-line diagnosis pointers)** —
  before extending it (837I institutional segments, per-service-line
  diagnosis correlation, billing/subscriber provider identity), re-read
  this phase's "Deliberately deferred" section above for why each of
  those was left out, and whether the reason still holds.

## Next steps

Phase 9 is complete (F-01's remaining gap closed, 837 claim file
ingestion built). Per `docs/MASTER-BUILD-PROMPT-V2.md`'s phase order,
**Phase 10 (API layer) is next**. Read that phase's prompt before
assuming it's unbuilt — it is very likely another Phase-6/9-shaped
situation: "FastAPI. Every endpoint resolves access through Phase 4 —
no endpoint queries directly. OpenAPI generated. Pagination everywhere.
No PHI in URLs or query strings. Structured errors that never echo PHI.
Request ID propagated into every log line and audit entry... Authorization
test matrix: every role × every endpoint × own-facility, other-facility-
same-org, other-org. Every cell asserted" describes almost exactly what
already exists (`tests/api/test_authz_matrix.py`, `security/redaction.py`,
`api/auth.py`'s Phase-4 access resolution used by every route built
across Phases 4-9) — audit line by line before planning as if starting
from zero. The one item that reads as genuinely new: **"API versioning
strategy with a deprecation policy."** No versioning scheme (URL prefix,
header, or otherwise) exists anywhere in this codebase's routes today —
that's very likely real, unbuilt scope, and worth scoping down given how
large "a full deprecation policy" can get, the same discipline Phase 8
applied to its own oversized checklist. **Gate**: full authz matrix
green (already true) · no parameter manipulation crosses a facility
boundary (already true, per Phase 4's `test_tenant_param_absence.py`-
style structural enforcement — verify this specific claim before
assuming it, same as every other "already built" claim this session).

1. **If picking this up much later**, re-verify the full local gate
   before trusting anything — confirm the current commit's status
   before trusting it.
2. **If the F-22 Postgres password becomes available**, run Phases 4-9's
   live-DB suites for real before trusting any of their
   RLS/function code beyond what's offline-verified — this now includes
   migrations 0010-0014 and every BYOK/data-residency/job-queue/contract-
   pricing/837-ingestion DB test (`tests/db/test_organization_kms_key.py`,
   `tests/ingestion/test_apply_org_kms_key.py`,
   `tests/db/test_org_policy.py`'s data-residency tests,
   `tests/db/test_jobs_queue.py`, `tests/jobs/test_runner_live_db.py`,
   `tests/ingestion/test_apply_progress_cancel.py`,
   `tests/db/test_effective_dated_pricing.py`'s lesser-of/stop-loss
   round-trip test, `tests/api/test_contracts_live_db.py`,
   `tests/ingestion/test_837_live_db.py`), never run against a real
   Postgres in this environment. Doing this before starting Phase 10's
   own DB-backed work would mean verifying one large batch together
   instead of several small ones.
