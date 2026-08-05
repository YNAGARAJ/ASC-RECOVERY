# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist — this file adds the texture that isn't in that
summary.

## Phase: 11 (real data readiness — the compliance gate). Drafting done, the actual gate is nowhere close to met.

Phase 10 is done and CI-confirmed (see its own detailed entry in
`docs/PHASES.md`, including the "CI debugging round" list of 15 real bugs
the first-ever live pipeline run surfaced and fixed). Phase 11 is a
different kind of phase entirely: **no code**, per
`docs/MASTER-BUILD-PROMPT.md` — a 15-item checklist of legal/compliance/
business actions (BAAs, a Security Risk Analysis, incident response plan,
breach notification procedure, workforce training, insurance, a
third-party penetration test, legal review) that must all be genuinely
true, not just documented, before Phase 12 (first customer pilot) can
start.

**Percentage of Phase 11's gate met: roughly 0/15 by the gate's own
definition** ("all 15 read DONE with real evidence"), even though 7 of
the 15 items now have a real, useful drafted document behind them. A
drafted document is explicitly not a completed item in this phase — see
`docs/compliance/README.md`'s own framing. Don't let the volume of
writing produced this session read as more progress than it is: nothing
in `docs/compliance/` is signed, purchased, engaged, or tested yet.

## Done (files completed, one line each)

New `docs/compliance/` directory:
- `README.md` — the master tracker: all 15 checklist items from the
  master prompt, each with a status and a concrete next action.
- `SECURITY-RISK-ANALYSIS.md` — restructures `docs/SECURITY.md`'s control
  matrix into risk-analysis form (threat × existing control × residual
  risk × remediation priority). Flags unwired rate-limiting/account-
  lockout as the single highest residual-risk finding.
- `INCIDENT-RESPONSE-PLAN.md` — expands `docs/RUNBOOK.md`'s 4-step
  outline into the full response cycle, a severity scheme, and a
  contact-tree template — every name field deliberately blank.
- `BREACH-NOTIFICATION-PROCEDURE.md` — the 60-day clock, a reportability
  decision tree, and the notification recipient/deadline table.
- `DATA-RETENTION-SCHEDULE.md` — documents retention periods already true
  of the running system, separately from the genuinely undecided
  destruction procedure.
- `SECURITY-QUESTIONNAIRE-ANSWERS.md` — restates `docs/SECURITY.md` facts
  in customer-questionnaire form; lowest-risk document in this phase.
- `BUSINESS-CONTINUITY-DR-PLAN.md` — documents Phase 9's DR mechanism,
  proposes RPO/RTO targets, explicit that nothing is tested yet.
- `SANCTION-POLICY.md` — a standard progressive-discipline template for
  HIPAA workforce violations.

`docs/PHASES.md` — Phase 10 marked done with full "CI debugging round"
detail; Phases 3/5/6/7/8 checkboxes updated to reflect Phase 10's CI
confirmation; Phase 11 entry added (deliberately left unchecked).

## In progress

Nothing mid-write. All 8 planned Phase 11 documents are complete as
drafts. The remaining work in this phase is not writing — it's the real
external actions `docs/compliance/README.md` lists for the other 8
checklist items (BAAs ×3, insurance, pentest, workforce training, legal
review of the fee structure, and the asset-inventory item blocked on
Phase 9).

## Failing

N/A — no code changed this phase, nothing for `make test`/`make lint`/
`make eval`/`make security` to touch. Phase 10's gate remains green as of
its last confirmed CI run (see `docs/PHASES.md`'s Phase 10 entry) with
the same caveat noted there: confirmed via the user pasting CI logs into
chat, not independently re-verified from this environment (no `gh` CLI
configured here).

## Decisions worth knowing (not obvious from the code)

- **Every drafted compliance document opens with an explicit "engineering-
  drafted, not adopted" disclaimer**, mirroring the pattern
  `docs/SECURITY.md` already established in Phase 4 ("this is engineering
  documentation, not legal advice"). This is deliberate and should not be
  softened or removed when these documents are eventually revised — the
  risk of someone mistaking a well-written draft for adopted policy is
  exactly the failure mode this phase's own gate exists to prevent.
- **The asset inventory/network map item was left explicitly BLOCKED, not
  drafted with placeholder infrastructure.** `docs/SECURITY.md` already
  has the template; filling it in before Phase 9's Terraform is applied
  against a real account would mean documenting infrastructure that
  doesn't exist. Don't complete this item early with fictional
  owner/review-date values just to check a box.
- **The Anthropic BAA/zero-retention-terms item is flagged as needing
  active verification, not an assumption.** `docs/SECURITY.md` and
  `packets/drafter.py::AnthropicPacketDrafter` were both written without
  a BAA in place, and LLM API providers' retention terms can vary by tier
  and change over time — whoever handles this item should confirm current
  terms directly with Anthropic rather than trusting anything drafted
  here about what those terms are.
- **The data-retention destruction procedure (`DATA-RETENTION-SCHEDULE.md`)
  is a genuinely open question, not a gap to fill in unilaterally.** This
  system currently has no hard-delete path for any PHI-bearing row
  (`audit_log`/`phi_access_log` can't even be soft-deleted by the app
  role, by design). Deciding what "destruction" means here is a real
  compliance decision with implementation consequences (a future phase's
  code, not this one's).

## Traps for someone resuming cold

- **Don't mistake a drafted document for a completed checklist item.**
  `docs/compliance/README.md`'s status column exists precisely to prevent
  this — check it, not just whether a file exists.
- **The BAA-to-customer notification deadline in a real signed contract
  is often shorter than the statutory 60-day HIPAA clock**
  (`BREACH-NOTIFICATION-PROCEDURE.md` section 3) — easy to miss during a
  real incident if nobody checks the actual contract terms in advance.
- Everything from the Phase 3-10 checkpoints still applies (CRLF warnings
  on `git add`, cross-platform lockfile drift if `make lock` runs on
  Windows/macOS, `.terraform/`/`sbom.json` gitignored on purpose,
  `docs/AUDIT-PROMPTS.md` is the user's own file for a later phase — see
  `docs/PHASES.md`'s Phase 10 entry for the full, longer list).

## Next 3 steps

1. **Work through `docs/compliance/README.md`'s 8 externally-actioned
   items** — these require the user/business, not more engineering: BAAs
   (cloud provider via AWS Artifact/Azure's online acceptance; customer;
   Anthropic), cyber liability insurance quotes, engaging a third-party
   pentest firm, workforce training once real workforce exists, legal
   review of the contingency-fee contract structure.
2. **Fill in the blanks in `INCIDENT-RESPONSE-PLAN.md` and
   `SANCTION-POLICY.md`** (real names, real contacts) and run a tabletop
   exercise — both are explicitly not usable until this happens.
3. **Once Phase 9's real infrastructure exists**: complete the
   asset-inventory/network-map item, and run the actual timed restore
   drill `BUSINESS-CONTINUITY-DR-PLAN.md` calls for. Only after all 15
   items in `docs/compliance/README.md` read DONE should Phase 12 start.
