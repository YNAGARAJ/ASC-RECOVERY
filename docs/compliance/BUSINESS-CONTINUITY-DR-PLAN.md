# Business Continuity and Disaster Recovery Plan — ASC Underpayment Recovery Platform

> **Status: engineering-drafted, not tested.** This describes the DR
> *mechanism* already built into `terraform/` and `docs/RUNBOOK.md`, and
> proposes recovery targets for the business to confirm. **A plan that
> hasn't been rehearsed is not a verified control** — see section 4.

## 1. What's already built

- **Database**: multi-availability-zone RDS (AWS) / equivalent
  high-availability configuration (Azure), automated daily backups with
  point-in-time recovery, retention configurable via
  `db_backup_retention_days` (default 14 days).
- **Application**: stateless — the app itself holds no data that isn't in
  the database or object storage, so recovery is "redeploy the container
  from its last-known-good image," not a stateful failover.
- **Restore procedure**: documented step by step in `docs/RUNBOOK.md`'s
  "Restore (backup verification)" section — restore to a new instance
  (never over the live one), verify schema/RLS/row-count sanity, and
  record the actual wall-clock time taken.
- **Migration safety**: every migration follows an expand/contract
  pattern (`docs/RUNBOOK.md`'s "Zero-downtime migrations" section) so a
  rolling deploy or rollback never needs a maintenance window or risks
  the old and new app versions disagreeing about schema shape.

## 2. What's proposed, pending business confirmation

| Target | Proposed value | Basis |
|---|---|---|
| Recovery Point Objective (RPO) — how much data loss is acceptable | ≤ 24 hours | Matches the daily automated backup cadence; a shorter RPO would need continuous/more-frequent backup, a cost/complexity tradeoff for the business to weigh, not an engineering default |
| Recovery Time Objective (RTO) — how long an outage can last | To be measured, not assumed | This is exactly what section 4's rehearsal produces — do not publish an RTO to a customer before it's been timed for real at least once |

These are proposals. If the business has customer-facing SLA commitments
that need a tighter RPO/RTO, that requirement should drive the
infrastructure configuration (e.g., a shorter backup interval), not the
other way around.

## 3. Failure scenarios covered

- **Database instance failure**: multi-AZ configuration handles this with
  an automatic failover in both cloud modules — no manual restore needed
  for a single-instance failure.
- **Data corruption or accidental deletion**: point-in-time restore to a
  new instance, per `docs/RUNBOOK.md`.
- **Full region outage**: not currently covered — both Terraform modules
  provision into a single region. Multi-region failover would be new
  infrastructure work, not something to claim as covered until it exists.
- **Application-layer incident** (a bad deploy, not infrastructure): the
  existing rollback procedure (`docs/RUNBOOK.md` — redeploy the previous
  image tag) handles this without needing the database DR path at all.

## 4. Required before this plan is "tested," not just "documented"

- [ ] **Provision real infrastructure** (Phase 9's Terraform has never
      been applied against a real cloud account) — nothing below this
      line is possible until this happens.
- [ ] **Run a real restore-to-a-new-instance drill**, following
      `docs/RUNBOOK.md`'s procedure exactly, and record the actual
      wall-clock time. This number becomes the real RTO — do not publish
      an estimate instead of a measurement.
- [ ] **Confirm the RPO/RTO targets in section 2** against any customer
      SLA commitments before they're made — adjust backup frequency or
      infrastructure if the measured RTO doesn't meet the business's
      needs.
- [ ] **Schedule a recurring drill** (annually, at minimum, and after any
      material infrastructure change) — a DR plan tested once at launch
      and never again is a plan that will fail the first time it's
      actually needed, because the system will have changed underneath it.
