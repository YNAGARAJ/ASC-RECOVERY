# Security Risk Analysis — ASC Underpayment Recovery Platform

> **Status: engineering-drafted, not adopted.** This document restructures
> the technical facts already established in `docs/SECURITY.md` (the
> control matrix built in Phases 4, 7, and 10) into the shape a HIPAA
> Security Risk Analysis takes. It is **not legal advice** and has **not**
> been reviewed by counsel, a compliance officer, or a qualified security
> assessor. Treat every risk rating and remediation priority below as a
> starting proposal for that review, not a conclusion. Do not process real
> PHI on the strength of this document alone — see
> `docs/compliance/README.md` for everything else that must also be true
> first.
>
> **Required review**: a compliance officer or outside HIPAA counsel signs
> off on this analysis, and it is re-performed at least annually (2026
> Security Rule requirement) or after any material system change.

## 1. Scope

This analysis covers the ASC Underpayment Recovery Platform as designed
through Phase 10: the application (`src/`), its data store (PostgreSQL),
object storage for inbound 835 files, the LLM integration used to draft
recovery packets (Anthropic's API), and the cloud infrastructure defined
in `terraform/` (AWS and Azure modules, functionally equivalent). It does
not cover workforce/physical/facility security controls — those belong in
the workforce-security and physical-safeguards sections a full risk
analysis needs, which are organizational rather than engineering
questions and are tracked as open items in `docs/compliance/README.md`.

**Confirmed as of Phase 10**: the technical controls below are not just
written — they were verified against a real Postgres 16 database, a real
built container image, and (for Terraform) a real `terraform validate` +
`tfsec` scan, all running in CI (`.github/workflows/ci.yml`). Prior to
Phase 10, this analysis would have had to describe intended controls;
after it, most of section 3 below describes controls that are running and
tested, not just designed.

## 2. Assets and data flows

See `docs/SECURITY.md`'s asset inventory template for the formal table
(owner and last-reviewed columns are blank pending real infrastructure —
Phase 9's Terraform is written but not yet applied against a real cloud
account). In brief: 835 remittance files (containing PHI: patient name,
member ID, claim/payment amounts) arrive via SFTP/S3/manual upload, are
parsed and priced against a contracted fee schedule, and the results
(findings, optionally an LLM-drafted appeal letter) are surfaced to ASC
billing staff through the API. PHI is at rest in Postgres (two columns,
envelope-encrypted as of Phase 10) and in object storage (encrypted via
cloud KMS per `terraform/`), and in transit between the app and Anthropic's
API (packet drafting) and between the app and the database.

## 3. Threats, existing controls, and residual risk

Each row: a plausible threat, the control(s) already built against it, and
a residual risk rating (**Low / Medium / High**) reflecting what's left
after that control — not the raw likelihood of the threat itself. Ratings
are proposed, not certified; a reviewer may reasonably disagree with any
of them.

| Threat | Existing control(s) | Residual risk | Why |
|---|---|---|---|
| Unauthorized read of PHI via a compromised or absent tenant filter | Row-Level Security enforced at the database level (`alembic/versions/0001...`), not just application-layer filtering — confirmed by a real cross-tenant-read test against live Postgres in CI | **Low** | RLS makes this a database-enforced invariant, not a code-review-dependent one; the app's own DB role (`asc_app`) has no `BYPASSRLS` |
| PHI exposed in logs, traces, or error responses | `security/redaction.py` (structured-field redaction, tested), `observability/tracing.py`'s `PHIScrubbingSpanExporter`, `api/errors.py` | **Medium** | Structured-field redaction is reliable; free-text regex scrubbing only catches dashed SSN/MBI shapes (`docs/SECURITY.md`'s own documented limitation) — an undashed identifier interpolated into a log string would pass through |
| Stolen or leaked database credentials | Credentials never committed (`security/secrets.py`); real deployments source them from Secrets Manager/Key Vault (`terraform/`); rotation is a config change, not a code change | **Medium** | Sound design, but **unverified in practice** — no real secret rotation has ever been exercised against a live deployment (Phase 9's infrastructure has never been applied) |
| PHI columns readable directly from a database dump or backup | Envelope encryption (`security/encryption.py`, AES-256-GCM) on the two PHI columns, wired end-to-end and tested against real Postgres as of Phase 10 | **Medium** | The encryption itself is real and verified; the KEK today comes from `EnvKMS` (`security/kms_env.py`), a static-secret stopgap, not a real cloud KMS with per-operation access logging and automatic rotation — a named, deferred gap |
| Unauthorized role escalation via a forged or stale token | Deny-by-default RBAC (`security/rbac.py`, full matrix tested); `api/auth.py::get_auth_context` cross-checks the token's role claim against the current DB-assigned role and rejects on mismatch (confirmed working as designed during Phase 10's CI debugging) | **Low** | Structural: a token minted for a role the subject no longer holds is rejected, not silently trusted |
| Brute-force credential guessing / account takeover | `security/rate_limit.py::AccountLockoutTracker` exists and is unit-tested | **High** | **Not wired into any route** (Phase 10 adversarial-review finding, `docs/SECURITY.md`) — login has no lockout today. Highest-priority remediation item below |
| PHI-reading endpoints with no request throttling | `api/rate_limit.py::enforce_rate_limit` exists and is unit-tested | **High** | Same gap as above — no route calls it |
| A dollar amount is silently fabricated or altered by the LLM | `packets/currency.py` + `packets/service.py` — every figure in a drafted letter is extracted and checked against the deterministic finding record; rejected/regenerated on mismatch; human approval required before any letter is used | **Low** | Structural defense plus a human-in-the-loop backstop |
| An LLM-drafted letter mislabels which figure is which (right numbers, wrong position) | Currency validation is membership-only, not positional (Phase 10 adversarial-review finding) | **Medium** | Mitigated by mandatory human approval before use, but that is a backstop, not a structural fix |
| Reversal/takeback not correctly netted against the original finding | Domain logic nets reversals; findings are still created for `CORRECT_NO_VARIANCE` cases and reversal netting doesn't fully supersede the original in all cases (Phase 10 adversarial-review finding) | **Medium** | Affects worklist accuracy (a biller might pursue money already clawed back), not stored dollar amounts or payment instructions |
| Vulnerable third-party dependency exploited | `pip-audit` + `bandit` run in CI against a clean install; `trivy` scans the built container image; `tfsec` scans Terraform — all now running for real per push (Phase 10) | **Low** | Real, automated, and now verified to actually run (not just configured) |
| Undetected secret committed to source control | `gitleaks` full-history scan runs in CI (Phase 10) | **Low** | Confirmed running; two known synthetic test secrets are explicitly allowlisted (`.gitleaks.toml`), not silently ignored |
| Compromised LLM provider / prompt-based PHI leakage | Patient identifiers are structurally excluded from the text sent to the LLM (`packets/prompt.py`, tested with distinctive names to rule out a lucky false negative) | **Low**, pending BAA | The technical control is sound; the *organizational* control (a signed BAA with Anthropic confirming zero-retention terms) is a Phase 11 open item — see `docs/compliance/README.md` |
| Extended outage / data loss | Multi-AZ RDS, automated backups (Terraform, both clouds) | **Medium** | Mechanism exists; **no restore has ever actually been rehearsed and timed** against a real database (Phase 9's infrastructure has never been applied) — an untested backup is not a verified control |

## 4. Remediation priorities (highest residual risk first)

1. **Wire rate limiting and account lockout into the authentication and
   PHI-read routes.** Currently the single highest residual-risk item —
   the modules exist and are tested in isolation but protect nothing in
   production today. Needs a policy decision (which routes, what limits,
   keyed on what) before implementation, not just a mechanical wiring-up.
2. **Sign the Anthropic BAA and confirm zero-retention terms** before any
   real PHI reaches the packet-drafting LLM call — see
   `docs/compliance/README.md`.
3. **Provision real cloud infrastructure and rehearse a timed restore**
   (Phase 9's Terraform has never been applied against a real account).
   Until this happens, "automated backups exist" is a design claim, not a
   verified control.
4. **Real cloud KMS adapter** (AWS KMS / Azure Key Vault) behind the
   existing `KeyManagementService` port, replacing the `EnvKMS` stopgap,
   once real cloud credentials exist to build and test one against.
5. **Broaden free-text PHI redaction** beyond dashed SSN/MBI shapes, or
   accept the residual risk explicitly with a documented rationale (the
   primary control — structured-field redaction — already covers the
   common path; this is defense-in-depth on top of it).

Everything above already has an owner-neutral description of the gap in
`docs/SECURITY.md`; this section exists to rank them by risk, not to
re-describe them.

## 5. Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| Prepared by (engineering) | | | |
| Reviewed by (compliance/counsel) | | | |
| Approved by (business owner) | | | |

**Next scheduled review**: within 12 months of the date above, or
immediately after any material change to data flows, infrastructure, or
subprocessors — whichever comes first.
