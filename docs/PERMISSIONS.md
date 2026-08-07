# Permissions (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 4, extended in Phase 5)

Two independent mechanisms decide what a request can do, and they answer
different questions:

1. **Action-level (RBAC, `src/security/rbac.py`)** — *can this role
   perform this kind of action at all.* A deny-by-default table:
   `can(role, action)`. This file documents that table.
2. **Record-level (resolved access, RLS)** — *which specific
   orgs/facilities/rows.* A user's `Membership` grants a role within one
   organization, plus every descendant organization (the recursive access
   rule — see `alembic/versions/0001_initial_schema.py`'s
   `resolve_accessible_facility_ids`/`resolve_accessible_org_ids`), narrowed
   further to specific facilities if `scope=SPECIFIC_FACILITIES`. Row
   Level Security enforces this at the database, not the application —
   the required proof is that it holds even with application-level
   filtering disabled.

`platform_admin` and `org_admin` deliberately have **identical**
action-level permissions (see the matrix below) — what actually
distinguishes them is where their membership sits in the org hierarchy: a
`platform_admin`'s membership is typically at the root `PLATFORM` node
(resolving to every org/facility in the system), while an `org_admin`'s
membership is at a specific `ASC_GROUP`/`ASC` node (resolving only to
that subtree). The permission table can't express that distinction —
resolved access does.

## Roles

- **platform_admin** — operates across the whole platform (membership at
  the `PLATFORM` org). Every action.
- **org_admin** — manages one organization and its descendants ("org
  admins manage only their own org"). Every action, scoped by resolved
  access.
- **manager** — oversees billers within a facility/org: everything a
  biller can do, plus audit-log and PHI-access-log visibility.
- **biller** — day-to-day claim/finding work: read, upload, draft and
  approve recovery packets, record outcomes.
- **analyst** — read-only, and the role field-level PHI masking exists
  for: sees amounts, codes, and worklists, but patient name/member id
  come back masked (`security/phi_masking.py`), never
  `view_unmasked_phi`.
- **auditor** — read-only across claims/findings/contracts plus both log
  types, with unmasked PHI (compliance investigation needs the real
  values) but no write actions of any kind.
- **api_service** — machine-to-machine (API keys). Narrow by design:
  read plus upload, no packet drafting/approval (needs human judgment),
  no user/contract management, no log reads, no unmasked PHI. Widen only
  once a real integration is shown to need more — least privilege for an
  unattended credential.

## Action matrix

Generated from `security.rbac._PERMISSIONS` — if this ever drifts from
the actual code, trust the code and regenerate this table, not the other
way around:

```
python -c "
from security.rbac import Role, Action, can
roles = list(Role)
for a in Action:
    cells = ['Y' if can(r, a) else '' for r in roles]
    print(f'{a.value}: ' + ', '.join(cells))
"
```

| Action | platform_admin | org_admin | manager | biller | analyst | auditor | api_service |
|---|---|---|---|---|---|---|---|
| read_claim | Y | Y | Y | Y | Y | Y | Y |
| read_finding | Y | Y | Y | Y | Y | Y | Y |
| read_worklist | Y | Y | Y | Y | Y | | Y |
| export_worklist | Y | Y | Y | Y | | | |
| upload_remittance | Y | Y | Y | Y | | | Y |
| read_contract | Y | Y | Y | Y | Y | Y | Y |
| manage_contract | Y | Y | | | | | |
| draft_recovery_packet | Y | Y | Y | Y | | | |
| approve_recovery_packet | Y | Y | Y | Y | | | |
| manage_users | Y | Y | | | | | |
| read_audit_log | Y | Y | Y | | | Y | |
| read_phi_access_log | Y | Y | Y | | | Y | |
| record_finding_outcome | Y | Y | Y | Y | | | |
| view_unmasked_phi | Y | Y | Y | Y | | Y | |

## Field-level PHI masking

Separate from encryption at rest (`security/encryption.py`,
`security/phi_columns.py` — those protect data *in the database*).
Masking decides whether an already-decrypted value reaches a given
role's *response*. Today's PHI fields: `patient_name`,
`patient_member_id` (the two encrypted columns on `claims`). Applied once,
in `api/repository.py`, right after decryption — not per-route, so it
can't be forgotten on a new endpoint.

## Phase 5 additions (`docs/MASTER-BUILD-PROMPT-V2.md`)

Offboarding (`POST /organizations/members/{id}/revoke`), API key
provisioning (`POST/GET /api-keys`, `POST /api-keys/{id}/revoke`), and
per-org policy (`GET/PUT /org-policy`) all reuse the existing
`manage_users` action from the table above — **no new `Action` was
added**. All three are delegated-admin operations in the same sense
`GET /organizations/members` already was: an org-resolved caller manages
their own org's users/credentials/policy, never anyone else's, enforced
by RLS the same way every other resolved-access write in this system is
(`org_authoring_update` on `memberships`, `org_access` on `api_keys`/
`org_policies`, both `alembic/versions/0007_user_lifecycle.py`).

An API key resolves to its own dedicated service `User` holding an
ordinary `role=api_service` `Membership` — the row in the matrix above
already covers what such a credential can do; provisioning one doesn't
grant it anything the table doesn't already say.

## Phase 7 additions (`docs/MASTER-BUILD-PROMPT-V2.md`)

`GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel` (the async
job-queue history/cancellation surface) reuse the existing
`upload_remittance` action from the table above — **no new `Action` was
added**: whoever can enqueue an ingestion job (`POST /remittances`,
already gated by `upload_remittance`) is exactly who should be able to
see and cancel their own facility's jobs, and RLS on the new `jobs` table
(`alembic/versions/0012_jobs.py`, same `facility_access` policy shape
every other facility-scoped table has since 0001) is what actually
narrows "their own facility's" the same way it does everywhere else in
this system — the action-level check only answers "can this role touch
jobs at all," never "which jobs."

The worker process itself (`src/worker.py`, `src/jobs/runner.py`) is not
a `role` in this table at all — it authenticates to Postgres as
`asc_owner` (`BYPASSRLS`) for queue bookkeeping (claim/progress/cancel-
check/complete/fail, system-wide across every facility, not any one
user's resolved access), and separately re-establishes the *job's own
submitter's* resolved access via `access_session(session_factory,
job.user_id)` to actually execute that job's business logic. See
`db/repository.py`'s "Jobs" section docstring for the full reasoning.

## Not yet built (later phases)

- **Worklist assignment scoping** ("a biller sees only *their assigned*
  worklists") — no assignment data model exists yet. Row-level filtering
  for this belongs at the query layer, on top of the action-level
  `read_worklist` check documented here, once that model exists.
- **SSO (OIDC/SAML per organization), SCIM provisioning/deprovisioning,
  impersonation, and break-glass access** — named in Phase 5's prompt but
  explicitly deferred as an independent follow-up pass (confirmed with
  the user); see `docs/SECURITY.md`'s "Not yet built" section for why.
  Phase 5 as built covers invitation → accept → MFA → login, offboarding,
  delegated admin, API keys, and per-org policy only.
