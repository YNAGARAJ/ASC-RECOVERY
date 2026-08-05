# Security Questionnaire Answers — ASC Underpayment Recovery Platform

> **Status: engineering-drafted, ready for review.** This is the
> lowest-risk document in Phase 11 — it restates already-true technical
> facts from `docs/SECURITY.md` in the Q&A form a customer's security
> review (HECVAT-style or a custom vendor questionnaire) typically asks
> for. Still needs a business-side review before sending to a customer —
> some answers below explicitly flag what's not yet true (e.g., no signed
> BAAs yet, no completed pentest yet) and those gaps should not be glossed
> over in a real response.

## Data protection

**Do you encrypt data at rest?**
Yes. Database columns holding patient name and member ID use application-level
envelope encryption (AES-256-GCM), independent of and in addition to
disk/volume encryption at the infrastructure layer. Object storage for
inbound files is encrypted using the cloud provider's KMS.

**Do you encrypt data in transit?**
Yes, for infrastructure provisioned via this system's Terraform: the
application-to-database connection enforces TLS (`rds.force_ssl` on AWS;
enforced by default on Azure's flexible server). All external API traffic
is HTTPS-only.

**How is encryption key management handled?**
Via a dedicated port (`KeyManagementService`) with pluggable adapters —
today backed by a stopgap that derives a static key-encryption-key from a
managed secret; a real cloud KMS adapter (AWS KMS / Azure Key Vault) is
planned but not yet built, since it requires a real cloud account to
build and verify against. Key rotation re-wraps only the small wrapped
data-key, never re-encrypting bulk data, making rotation cheap.

## Access control

**Do you enforce multi-factor authentication?**
Yes, MFA is mandatory with no bypass path — session issuance is
structurally incapable of minting a token without MFA verification having
already occurred.

**How is role-based access control implemented?**
Deny-by-default: every action requires an explicit grant for the
requesting role, with four roles (Viewer, Biller, Admin, Auditor) and a
fully tested authorization matrix covering every role against every
endpoint.

**Is multi-tenant data isolation enforced at the application layer or the
database layer?**
The database layer. Every tenant-scoped table has Row-Level Security
enabled and forced, so tenant isolation holds even if an application-level
filter were ever missing or buggy — verified by an automated test that
disables application-level filtering and confirms the database still
blocks a cross-tenant read.

**Do you rate-limit or lock out accounts after repeated failed logins?**
Not yet in production — the mechanism exists and is tested in isolation
but is not yet wired into live authentication routes. Tracked as an open
item; see `docs/compliance/SECURITY-RISK-ANALYSIS.md`.

## Logging and monitoring

**Do you log access to sensitive data?**
Yes. Every PHI-bearing read is logged with actor, timestamp, and purpose,
queryable per-record via an access-history endpoint restricted to
auditor/admin roles.

**Are audit logs tamper-proof?**
The audit log table has no update or delete permission granted to the
application's own database role — enforced at the database level, not
just by application logic, and confirmed against a real database in
continuous integration.

**Is PHI excluded from application logs and traces?**
Structured log fields are reliably scrubbed. Free-text log messages have
a defense-in-depth regex scrubber covering common identifier shapes, but
this is a secondary control, not the primary one — see
`docs/compliance/SECURITY-RISK-ANALYSIS.md` for the honest residual-risk
assessment.

## Vulnerability management

**Do you run static application security testing and dependency
scanning?**
Yes, on every code change: static analysis (bandit), dependency
vulnerability scanning (pip-audit for application dependencies, Trivy for
the built container image), infrastructure-as-code scanning (tfsec), and
full-history secret scanning (gitleaks) — all running as required,
blocking checks in continuous integration, not optional or manual steps.

**Have you completed a third-party penetration test?**
Not yet. Planned as part of pre-production readiness; not yet scheduled.

**What is your process for the 2026 HIPAA Security Rule's six-monthly
vulnerability scan requirement?**
Automated: a scheduled scan re-runs the same dependency/container checks
above every six months independent of code changes, and opens an
actionable alert on any new finding.

## Business continuity

**Do you have a tested disaster recovery plan?**
Automated backup infrastructure is defined (multi-availability-zone
database, daily automated backups with point-in-time recovery), but has
not yet been exercised against a live deployment — no restore has been
timed and confirmed end to end, because no real cloud infrastructure has
been provisioned yet. See `docs/compliance/BUSINESS-CONTINUITY-DR-PLAN.md`.

## Subprocessors and data sharing

**Do you use any AI/LLM services, and what data do they receive?**
Yes, for drafting (not deciding) recovery-appeal letters. The LLM never
receives patient name, member ID, or any other direct identifier — those
are structurally excluded from the prompt and substituted back into the
letter only after the model responds. The LLM also never computes a
dollar amount that reaches a customer unvalidated — every figure in a
drafted letter is checked against the system's own deterministic
calculation, and a human must approve the letter before it's used.

**Do you have a Business Associate Agreement with your LLM provider?**
Not yet signed — tracked as a pre-production requirement; see
`docs/compliance/README.md`.

**Do you have Business Associate Agreements with your cloud provider(s)
and your customers?**
Not yet signed for either — both are tracked as pre-production
requirements. No real customer PHI is processed until they are in place.

---
*Answers current as of Phase 10. Re-verify against `docs/SECURITY.md`
before reusing this document for a new customer — some answers (BAA
status, pentest status) are expected to change as Phase 11's remaining
checklist items close.*
