# Data Retention and Destruction Schedule — ASC Underpayment Recovery Platform

> **Status: engineering-drafted, not adopted.** Section 1 documents
> retention periods *already implemented in code* — these are facts about
> the system, not proposals. Section 2 (destruction) is genuinely
> undecided and needs a real business/compliance decision.

## 1. What's already implemented

| Data | Retention | Where it's enforced | Notes |
|---|---|---|---|
| 835 remittance files (object storage) | 6 years minimum (HIPAA documentation requirement), via lifecycle transition to cold storage at 90 days | `terraform/modules/aws/storage.tf` / `modules/azure/storage.tf` — `noncurrent_version_transition` to Glacier (AWS) / cool tier (Azure) | Transitions to cheaper storage, does **not** delete — actual deletion policy is section 2, deliberately left to a real compliance decision rather than an engineering default |
| Database records (claims, findings, contracts, audit log) | Indefinite (soft-delete only, no hard-delete path) | `deleted_at` columns throughout `src/db/models.py`; `audit_log`/`phi_access_log` have no `DELETE` grant for the app role at all (append-only, enforced at the database level, confirmed against real Postgres in CI) | Nothing in this system currently deletes a database row — retention here is bounded only by the destruction decision in section 2 |
| Automated database backups | 14 days (default, configurable) | `terraform/modules/{aws,azure}/variables.tf::db_backup_retention_days` | This is a point-in-time-recovery window, not long-term archival — do not confuse it with the 6-year documentation requirement above |
| Application/audit logs (CloudWatch / Azure Monitor) | 30 days | `terraform/modules/{aws,azure}/container_runtime.tf` | Operational logs, separate from the `audit_log`/`phi_access_log` database tables, which retain indefinitely (see above) |
| Access tokens | 15 minutes | `security/session.py::ACCESS_TOKEN_TTL` | Not a retention control in the compliance sense, included for completeness |
| Refresh tokens | 7 days, single-use (rotates on every refresh, replay rejected) | `security/session.py::REFRESH_TOKEN_TTL` | |
| Eval run history (`evals/history/`) | Local only, gitignored | Not synced anywhere; a real deployment would send this to a metrics backend instead (`docs/PROGRESS.md`, Phase 8 notes) | Contains no PHI — golden-dataset scores only |

## 2. What's not yet decided

- **Hard-deletion / destruction procedure.** HIPAA requires retention *up
  to* 6 years for required documentation, and secure destruction
  thereafter — but this system currently has no mechanism to hard-delete
  a claim, finding, or audit entry at all (only soft-delete via
  `deleted_at`, and `audit_log`/`phi_access_log` can't even be
  soft-deleted by the app role). Before this platform processes real PHI
  for 6+ years, a decision is needed: does destruction mean actually
  deleting rows, or is "no longer queryable via the application" (a
  contract/tenant marked inactive) sufficient, with physical deletion
  happening later via a separate, audited process? **This is a real
  design decision requiring compliance input, not something to default
  without one.**
- **What happens to a customer's data when they leave.** No offboarding/
  data-return-or-destruction procedure exists yet. Typically required by
  the BAA itself (see `docs/compliance/README.md`) — the BAA's terms
  should drive this, not the other way around.
- **Backup retention beyond 14 days.** The current 14-day window covers
  operational disaster recovery, not the 6-year documentation
  requirement — confirm whether a longer-retention backup tier is needed
  for compliance purposes distinct from operational recovery, or whether
  the object-storage lifecycle (section 1) is considered sufficient
  documentation retention on its own.

## 3. Required before this schedule is adopted

- [ ] Compliance sign-off on treating "6-year documentation retention" as
      satisfied by the object-storage lifecycle policy alone, vs.
      requiring an additional database-level retention/archival plan
- [ ] A decided, documented destruction procedure for section 2's open
      item, with an actual implementation plan (this would be code —
      tracked as a future phase, not Phase 11 itself)
- [ ] Customer offboarding data-handling terms defined, ideally before
      the first BAA is signed rather than after
