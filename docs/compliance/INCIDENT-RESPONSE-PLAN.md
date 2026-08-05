# Incident Response Plan — ASC Underpayment Recovery Platform

> **Status: engineering-drafted, not adopted.** This expands
> `docs/RUNBOOK.md`'s existing incident-response section (which describes
> what the *system* can do to help — the tooling below already exists and
> is tested) into a full plan. The blanks in this document (owner names,
> phone numbers, escalation paths) are exactly what turns this from a
> procedure into a plan a real organization can actually execute under
> pressure. **This document is not usable until those blanks are filled
> and at least one tabletop exercise has been run.**

## 1. Purpose and scope

Covers any suspected or confirmed security incident affecting the
platform: unauthorized access, credential compromise, a vulnerability
actively being exploited, data loss, or a system compromise that could
expose PHI. Not a substitute for `docs/compliance/BREACH-NOTIFICATION-PROCEDURE.md`
— that document governs what happens *after* this plan determines a
reportable breach occurred.

## 2. Roles and responsibilities

| Role | Responsibility | Name | Contact (24/7) |
|---|---|---|---|
| Incident Commander | Owns the response end to end; only person who can declare the incident closed | | |
| Technical Lead | Runs containment/eradication; the person who actually rotates secrets, patches, or rolls back | | |
| Privacy Officer | Determines whether PHI was accessed/disclosed and drives the breach-notification decision | | |
| Communications Lead | Owns customer and (if needed) public communications — never improvised by whoever's online | | |
| Legal Counsel | Advises on notification obligations, evidence handling, law-enforcement contact | | |

**Do not leave this table blank in production.** An incident response
plan with no named people is a document, not a plan — the single most
important thing to fill in before this goes from Phase 11 checklist to
adopted policy.

## 3. Severity classification

| Severity | Definition | Example | Response time |
|---|---|---|---|
| SEV-1 | Confirmed or strongly suspected PHI exposure, active exploitation, or system-wide outage | Cross-tenant data returned by the API; credentials found in a public location | Immediate, all hands |
| SEV-2 | Suspicious activity with unclear scope, or a significant vulnerability discovered (not yet exploited) | Anomalous access pattern flagged by `observability/alerts.py`; a newly disclosed CVE in a direct dependency | Within 4 hours |
| SEV-3 | Isolated, contained issue with no evidence of PHI exposure | A single failed login lockout; a misconfigured non-production resource | Within 1 business day |

## 4. Response phases

### 4.1 Identify

- Trigger: an alert fires (`observability/alerts.py` — ingestion failure,
  eval regression, auth anomaly, unusual PHI access volume, or
  cross-tenant probe detection, per Phase 8), or an external report
  (customer, researcher, employee).
- On any SEV-1/SEV-2 trigger, the person who notices pages the Incident
  Commander immediately — do not wait for a scheduled check-in.

### 4.2 Contain

- **Suspected credential compromise**: rotate the affected secret in
  Secrets Manager / Key Vault immediately. Every secret the app reads is
  already indirected through `security/secrets.py::EnvSecretStore`, so
  rotating the underlying cloud secret and restarting the service requires
  no code change — this is the fastest containment path and should be the
  default first move for any credential-related SEV-1/SEV-2.
- **Suspected active exploitation of a code vulnerability**: consider
  taking the affected endpoint/service offline (`aws ecs update-service
  --desired-count 0` or the Azure Container Apps equivalent) rather than
  attempting a live patch under pressure.
- **Suspected data exfiltration**: preserve evidence before remediating —
  a premature fix can destroy the audit trail needed to determine scope
  (see 4.3).

### 4.3 Assess scope

This is the step this system is specifically built to make fast, not
slow and manual:

- `GET /claims/{claim_id}/access-history` (Phase 8) reconstructs exactly
  who accessed a given claim's PHI, when, and (for structured PHI views)
  why.
- `GET /audit-log` gives the full append-only record of every write to a
  PHI-bearing table (`audit_log`'s `REVOKE UPDATE, DELETE` from the app
  role, confirmed enforced against real Postgres in CI as of Phase 10).
- Both are AUDITOR/ADMIN-scoped endpoints (`security/rbac.py`) — ensure
  whoever is investigating has an appropriately privileged account ready
  *before* an incident, not scrambling to provision one during it.

Document, for every affected tenant: which claims/records were accessed,
by whom (or by what — a compromised credential vs. a person), over what
time window, and whether any data left the system (vs. was merely
viewed).

### 4.4 Eradicate

- Remove the root cause: revoke the compromised credential entirely (not
  just rotate it, if it may have been used elsewhere), patch the
  vulnerability, or fix the misconfiguration.
- Confirm via the same access-history/audit-log tooling that no further
  unauthorized activity is occurring post-containment.

### 4.5 Recover

- Restore normal operations. If a database restore was required, follow
  `docs/RUNBOOK.md`'s restore procedure and confirm data integrity
  (`alembic_version` matches expected, RLS policies present, row counts
  sane) before resuming traffic.
- Incident Commander confirms with Technical Lead and Privacy Officer
  before declaring the incident closed.

### 4.6 Lessons learned

- Within 5 business days of closure: a blameless post-incident review.
  What was the actual root cause (not just the proximate trigger)? What
  would have caught this sooner? What's the concrete follow-up (a code
  fix, a new alert, a process change) — with an owner and a date, not
  just a discussion.
- Feed any newly discovered gap back into
  `docs/compliance/SECURITY-RISK-ANALYSIS.md`'s risk register.

## 5. Contact tree template

```
Incident detected
  -> Incident Commander (primary: ______  backup: ______)
       -> Technical Lead (primary: ______  backup: ______)
       -> Privacy Officer (primary: ______  backup: ______)
            -> Legal Counsel (______, retained firm or in-house)
       -> Communications Lead (primary: ______  backup: ______)
            -> (only if Privacy Officer + Legal Counsel confirm
                notification is required) affected customers,
                per docs/compliance/BREACH-NOTIFICATION-PROCEDURE.md
```

**Test this tree.** A contact tree nobody has ever dialed is not a
verified control — schedule a tabletop exercise (a simulated incident
walked through on paper, 60-90 minutes) before treating this plan as
adopted.

## 6. Required before this plan is adopted

- [ ] Every blank in section 2 and section 5 filled with a real name and
      a real, currently-reachable contact method
- [ ] Legal counsel identified and retained (or on-call arrangement
      confirmed) — see `docs/compliance/README.md`
- [ ] At least one tabletop exercise run and any gaps it surfaced fixed
- [ ] This plan reviewed and approved by the same sign-off chain as
      `docs/compliance/SECURITY-RISK-ANALYSIS.md`
