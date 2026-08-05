# Phase 11 — Real Data Readiness Tracker

Per `docs/MASTER-BUILD-PROMPT.md`: **"No code in this phase. Paperwork and
process. Do not skip it — this is the phase that determines whether you
have a business or a lawsuit."** Every item below must be genuinely true
— not merely drafted — before a single real 835 file touches this system.
Phase 12 (first customer pilot) does not start until this entire list is
signed off.

This directory holds engineering-drafted starting documents for the items
that could be meaningfully grounded in this system's actual technical
controls. **A drafted document is not a completed checklist item.**
Nothing here substitutes for the real-world action each item actually
requires — a signature, a purchase, an engagement, a review by someone
qualified to give it.

| # | Item | Status | What's needed |
|---|---|---|---|
| 1 | BAA signed with your cloud provider(s), covering every service used | **NEEDS EXTERNAL ACTION** | AWS: accept AWS's Business Associate Addendum via AWS Artifact in the account console (a click-through process, not a drafted contract) and confirm every AWS service actually used is on AWS's HIPAA-eligible list (`terraform/README.md` already restricts this build to eligible services). Azure: equivalent BAA acceptance via the Microsoft Online Services Terms / Trust Center. No drafting needed — this is the vendor's own paperwork. |
| 2 | BAA signed with your customer (you are their Business Associate) | **NEEDS EXTERNAL ACTION** | Requires counsel — either your own template reviewed by a healthcare attorney, or review of the customer's proposed BAA. Do this before the first real 835 file is exchanged, not after. |
| 3 | Written Security Risk Analysis, completed and documented | **DRAFTED — pending review** | [`SECURITY-RISK-ANALYSIS.md`](./SECURITY-RISK-ANALYSIS.md) — restructures `docs/SECURITY.md`'s control matrix into risk-analysis form. Needs compliance/counsel sign-off and annual re-review. |
| 4 | Written asset inventory and network map (mandatory, annual review) | **BLOCKED on Phase 9** | Template exists in `docs/SECURITY.md`; cannot be genuinely completed (owner, last-reviewed columns; a real network diagram) until Phase 9's Terraform is applied against a real cloud account. Filling in the template with placeholder infrastructure would be fiction, not documentation. |
| 5 | Incident response plan with named owner and tested contact tree | **DRAFTED — needs names + a drill** | [`INCIDENT-RESPONSE-PLAN.md`](./INCIDENT-RESPONSE-PLAN.md) — the procedure is written and uses tooling already built (`GET /claims/{id}/access-history`, `GET /audit-log`); every name/contact field is blank and a tabletop exercise hasn't been run. Not adopted until both are done. |
| 6 | Breach notification procedure — 60-day clock, who decides, who notifies | **DRAFTED — needs counsel review** | [`BREACH-NOTIFICATION-PROCEDURE.md`](./BREACH-NOTIFICATION-PROCEDURE.md). Explicitly does not authorize sending any notification — every real notification decision needs counsel. |
| 7 | Workforce security training completed and evidenced | **NEEDS EXTERNAL ACTION** | No workforce exists to train yet in this build's context — once hired/engaged, needs actual training content (HIPAA basics + this system's specific controls, e.g. `docs/compliance/SANCTION-POLICY.md`'s examples of sanctionable conduct make a reasonable outline) delivered to real people, with signed acknowledgment kept on file. Cannot be "evidenced" by a document alone. |
| 8 | Sanction policy for workforce violations | **DRAFTED — needs HR/legal adoption** | [`SANCTION-POLICY.md`](./SANCTION-POLICY.md). |
| 9 | Data retention and destruction schedule (6-year documentation minimum) | **DRAFTED — destruction procedure undecided** | [`DATA-RETENTION-SCHEDULE.md`](./DATA-RETENTION-SCHEDULE.md) — retention periods are documented facts about what's already implemented; the actual destruction procedure is a genuinely open decision, not yet made. |
| 10 | Business continuity and disaster recovery plan, tested | **DRAFTED — not yet tested** | [`BUSINESS-CONTINUITY-DR-PLAN.md`](./BUSINESS-CONTINUITY-DR-PLAN.md) — the mechanism is built (Phase 9's Terraform); "tested" requires a real timed restore drill against real infrastructure, which doesn't exist yet. |
| 11 | Subcontractor BAAs for every vendor touching PHI, including your LLM provider — confirm zero-retention terms | **NEEDS EXTERNAL ACTION** | Anthropic: request their standard BAA and confirm the specific zero-/limited-retention terms that apply to API usage (terms can vary by API tier and have changed over time — verify current terms directly with Anthropic rather than assuming, since `docs/SECURITY.md` and `packets/drafter.py::AnthropicPacketDrafter` were written without one in place). Any other PHI-touching vendor (cloud provider already covered by #1) needs the same treatment before use. |
| 12 | Cyber liability insurance | **NEEDS EXTERNAL ACTION** | Get quotes from a broker experienced with healthcare-tech/tech E&O + cyber liability combined policies. Coverage amount should be informed by the Security Risk Analysis's (#3) assessed exposure, not picked arbitrarily — involve whoever owns that document in the conversation with the broker. |
| 13 | Customer-facing security questionnaire answers prepared | **DRAFTED — ready for review** | [`SECURITY-QUESTIONNAIRE-ANSWERS.md`](./SECURITY-QUESTIONNAIRE-ANSWERS.md) — lowest-risk item in this phase, since it restates already-true facts from `docs/SECURITY.md` in a different shape. Still flags what's not yet true (no signed BAAs, no completed pentest) rather than glossing over it. |
| 14 | Penetration test completed by a third party | **NEEDS EXTERNAL ACTION** | Engage a firm with healthcare/HIPAA experience for a focused web-app + API engagement. Typical lead time 2-4 weeks to schedule; budget accordingly against the pilot timeline (Phase 12). The 2026 Security Rule requires this **annually**, not just once before launch — put a recurring engagement on the calendar now, not just a one-time task. |
| 15 | Legal review of your contingency-fee contract structure | **NEEDS EXTERNAL ACTION** | This is specific to the underpayment-recovery business model (fee structures contingent on recovered amounts) and needs a healthcare/contracts attorney's review — outside engineering's or this document's scope entirely. |

## How to read "DRAFTED — pending review"

Every document above marked drafted was written by restructuring facts
already established and verified elsewhere in this codebase
(`docs/SECURITY.md`'s control matrix, `docs/RUNBOOK.md`'s operational
procedures, `terraform/`'s actual infrastructure definitions) into the
shape a real compliance document needs — not invented from scratch, and
not claiming anything that isn't already true about the system. But
"grounded in real facts" is not the same as "reviewed and adopted." Each
document names, in its own status line, exactly what still needs to
happen before it's real policy rather than a well-informed draft.

## Definition of done for this phase

All 15 rows above read **DONE**, with real evidence (a signed contract, a
purchased policy, a completed engagement report, a documented drill) —
not a drafted document, however good. Do not advance to Phase 12 until
that's true; the master prompt is explicit that this is not optional
sequencing.
