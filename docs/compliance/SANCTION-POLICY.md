# Workforce Sanction Policy (HIPAA) — ASC Underpayment Recovery Platform

> **Status: template, requires HR/legal adoption.** HIPAA requires a
> documented sanction policy for workforce members who violate PHI
> policies or procedures (§164.308(a)(1)(ii)(C)). This is a standard,
> right-sized starting template for an organization this size — it needs
> HR and legal review before adoption, and should be referenced in
> (not replace) an actual employee handbook.

## 1. Purpose

Any workforce member — employee or contractor — with access to this
system or the PHI it processes is subject to sanctions for violating
HIPAA policies, this platform's security procedures, or applicable law.
This applies regardless of whether the violation was intentional,
negligent, or accidental; intent affects the severity of the sanction,
not whether one applies.

## 2. Examples of sanctionable conduct

- Accessing PHI without a legitimate business reason (including
  "browsing" a claim or finding out of curiosity, even within one's own
  tenant/role)
- Sharing credentials, or using another person's credentials
- Disabling, bypassing, or attempting to bypass a security control (MFA,
  access logging, rate limiting, etc.)
- Removing PHI from the system (export, screenshot, copy-paste) outside
  an approved, documented business process
- Failing to report a known or suspected security incident
- Retaliating against someone who reports a suspected violation in good
  faith

## 3. Progressive discipline (typical structure — adapt to actual
   employment policy)

| Tier | Example | Typical response |
|---|---|---|
| 1 | First unintentional, low-impact violation (e.g., an accidental access outside job duties, self-reported) | Verbal counseling, re-training, documented in personnel file |
| 2 | Repeat violation, or a first violation involving negligence | Written warning, mandatory re-training, documented |
| 3 | Intentional violation, or any violation causing actual PHI exposure | Suspension pending investigation; may proceed directly to termination depending on severity |
| 4 | Intentional PHI theft, sharing, or malicious access; retaliation against a good-faith reporter | Termination; may include referral to law enforcement and/or civil action |

Every sanction (all tiers) must be documented: date, workforce member,
nature of violation, evidence reviewed, sanction applied, and who made
the decision. This documentation is itself part of demonstrating HIPAA
compliance during an audit or investigation — undocumented sanctions are,
from a compliance standpoint, indistinguishable from no sanction policy
at all.

## 4. Reporting

Workforce members must report suspected violations (their own or
others') to [Privacy Officer — same role as
`docs/compliance/INCIDENT-RESPONSE-PLAN.md`]. Good-faith reports are
protected — no retaliation for reporting a suspected violation, even if
the investigation concludes no violation occurred.

## 5. Required before this policy is adopted

- [ ] Reviewed by HR and legal counsel, and reconciled with the actual
      employee handbook / employment agreements (this template does not
      override or replace either)
- [ ] Named Privacy Officer filled in (section 4) — same name as
      `docs/compliance/INCIDENT-RESPONSE-PLAN.md`'s roles table
- [ ] Communicated to all workforce members with system access, with
      acknowledgment of receipt documented (ties into
      `docs/compliance/README.md`'s workforce-training checklist item)
