# Breach Notification Procedure — ASC Underpayment Recovery Platform

> **Status: engineering-drafted, not adopted.** This expands
> `docs/RUNBOOK.md`'s breach-notification section. **This document does
> not authorize sending any notification.** Every notification decision —
> whether an incident is a reportable breach, who must be told, and what
> the notification says — requires legal counsel's review before it goes
> out. What this procedure provides is the decision structure and the
> clock; it does not replace legal judgment on any specific incident.

## 1. The clock

The 2026 HIPAA Security Rule requires notification within **60 days of
discovery** of a breach of unsecured PHI. "Discovery" is the date the
breach is known or, by reasonable diligence, should have been known — not
the date it's fully investigated. The clock starts at discovery, so
`docs/compliance/INCIDENT-RESPONSE-PLAN.md`'s "Identify" step (section
4.1) is also what starts this clock; log that timestamp precisely.

## 2. Is this a reportable breach? (decision tree)

A breach is an impermissible use or disclosure of unsecured PHI, unless a
risk assessment shows a low probability the PHI was compromised. Work
through this with Privacy Officer + Legal Counsel for every incident that
reached `docs/compliance/INCIDENT-RESPONSE-PLAN.md`'s "Assess scope" step
with any confirmed PHI access:

1. **Was the PHI actually unsecured?** If it was properly encrypted per
   NIST guidance (this system's PHI columns are envelope-encrypted at
   rest as of Phase 10 — see `docs/compliance/SECURITY-RISK-ANALYSIS.md`)
   and the encryption key itself was not also compromised, this may fall
   under the encryption safe harbor. **Counsel determines this, not
   engineering** — the safe harbor has specific conditions this document
   doesn't attempt to fully enumerate.
2. **Was it a permitted use/disclosure under the BAA / minimum-necessary
   rules?** E.g., a role legitimately viewing a claim within their own
   tenant, logged via `phi_access_log`, is not a breach — this is exactly
   what `GET /claims/{claim_id}/access-history` exists to distinguish from
   unauthorized access.
3. **If neither of the above applies**: perform the 4-factor risk
   assessment (nature/extent of PHI involved; who accessed/received it;
   was it actually acquired or viewed; extent the risk was mitigated).
   Document the answer to each factor using the access-history/audit-log
   evidence gathered during incident response. **A written analysis is
   required even when the conclusion is "not a breach"** — the absence of
   a paper trail is itself a compliance gap.

## 3. Who gets notified, and by when

| Recipient | Threshold | Deadline | Decided by |
|---|---|---|---|
| Affected individuals | Any confirmed breach of their PHI | Without unreasonable delay, no later than 60 days from discovery | Privacy Officer + Legal Counsel |
| HHS Office for Civil Rights | Any confirmed breach | Within 60 days if ≥500 individuals affected; annually (by 60 days after the calendar year) if fewer | Privacy Officer + Legal Counsel |
| Media | ≥500 residents of a state/jurisdiction affected | Without unreasonable delay, no later than 60 days | Legal Counsel + Communications Lead |
| The customer (ASC) as the Covered Entity, if this platform is the Business Associate | Per the BAA's own notification terms — often *faster* than the 60-day statutory clock (many BAAs require BA-to-CE notice within 24-72 hours) | Per the signed BAA — **check the actual contract**, do not assume 60 days applies to this leg | Whoever signed the BAA on this platform's behalf |

**The BAA-to-customer deadline is frequently shorter than the statutory
60 days and is easy to miss if nobody checks the actual contract during
an incident.** Once BAAs are signed (`docs/compliance/README.md`), record
each customer's specific notification deadline here or in a linked
tracker.

## 4. Notification content (individual notice)

Required elements — draft the actual letter with counsel, but it must
cover:

1. A brief description of what happened, including the discovery date
   and (if known) the breach date
2. The types of PHI involved (name, member ID, claim/payment data — never
   list the actual affected individuals' data in the template)
3. Steps individuals should take to protect themselves
4. What this platform's operator is doing to investigate, mitigate harm,
   and prevent recurrence
5. Contact information (toll-free number, email, or postal address)

## 5. Required before this procedure is adopted

- [ ] Legal counsel confirms the encryption safe-harbor analysis in
      section 2 applies to this system's actual controls before relying
      on it in a real incident
- [ ] Each signed customer BAA's specific notification deadline recorded
      against this procedure (see the table in section 3)
- [ ] A named Privacy Officer and retained/on-call legal counsel (same
      names as `docs/compliance/INCIDENT-RESPONSE-PLAN.md`)
- [ ] A drafted notification letter template reviewed and pre-approved by
      counsel, so one doesn't have to be written from scratch under a
      60-day clock
