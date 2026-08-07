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
   underway.** Phase 4 (org/facility/membership access model) is
   **complete**. Phase 5 (user lifecycle and enterprise access) is
   **in progress, 3 of 7 planned sub-steps done** — see below.

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

## Phase: MASTER-BUILD-PROMPT-V2.md Phase 5 (user lifecycle and enterprise access) — IN PROGRESS, 3/7 steps

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

### Full local gate as of step 3 (`2b553e6`)

`ruff check .` clean, `mypy --strict .` clean (182 files, only the 2
pre-existing unrelated alembic 0004 JSONB errors — present since before
Phase 4, not this session's doing), `pytest -q` 616 passed / 51 skipped,
`domain/variance.py` 100% coverage gate, `python -m evals.run` GATE
PASSED, `bandit -r . -x ./tests,./evals` clean. Offline SQL generation
(`alembic upgrade head --sql` / `downgrade 0008:0007 --sql`) verified
both directions through migration 0008. **The two `SECURITY DEFINER`
functions and every RLS policy added in steps 1-2 are written and
offline-verified only — never run against a real Postgres in this
environment** (same disclosed gap as Phase 4's RLS work; F-22's local
Postgres is still unclaimed, see "Traps" below).

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
- **Migrations 0007/0008 are additive on top of 0001, not edits to it** —
  0001's schema is sealed (same convention 0002-0006 already established
  for Phase 4). Any further Phase 5 schema work (steps 4-6) should be a
  new `0009_...` migration, not an edit to 0007/0008.

## Next steps

1. **Step 4/7 — Offboarding (the phase's actual stated gate: "offboarding
   test proves instant session death").** An endpoint that sets
   `memberships.revoked_at`, writes an audit entry, and a dedicated test
   proving the very next authenticated request (any route) fails
   immediately — at the route/API level this time, matching what Phase
   4's `test_revoking_membership_revokes_access_immediately` already
   proved at the DB layer via a hard `DELETE` through the owner
   connection. Likely needs a small new migration (`0009`) if any RLS
   gap surfaces the way step 2 needed one for step 1's gap — check
   whether the existing `org_authoring_update` policy from step 1
   already covers "an org-resolved caller sets `revoked_at` on someone
   else's membership row" (it should, since `UPDATE` was already granted
   broadly, but verify before assuming).
2. **Step 5/7 — API keys.** Create (returns the raw key once, reusing
   `security/tokens.py`), revoke, list (masked), and a new bearer-token
   branch in `api/auth.py` distinguishing an API key from a JWT by a
   fixed prefix before attempting either kind of validation.
3. **Step 6/7 — Per-org policy.** `org_policies` CRUD route,
   `session_timeout_seconds` as an optional TTL override at
   `issue_session` time, `ip_allowlist` checked in `get_auth_context`.
   Remember: `mfa_required` exists as a column but the API must never
   accept `false` for it.
4. **Step 7/7 — Docs.** `docs/RUNBOOK.md` (invite/offboard/API-key
   operator workflows), `docs/PERMISSIONS.md` if the action set changed,
   `docs/SECURITY.md` control-matrix entries for the new controls.
5. **If picking this up much later**, re-verify the full local gate
   before trusting anything — it was green as of `2b553e6`, but confirm
   it still is.
6. **If the F-22 Postgres password becomes available**, run Phase 4 and
   5's live-DB suites for real before building steps 4-6 further on top
   of RLS/function code that's only ever been offline-verified.
