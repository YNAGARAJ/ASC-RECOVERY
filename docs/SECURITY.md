# Security controls (Phase 4, extended in Phase 7)

This maps each security control to its HIPAA Security Rule citation, where
it lives in the codebase, and its verification status. Built in Phase 4;
extended in Phase 7 with the LLM-prompt/output controls below, since that's
the first phase where PHI or a dollar figure could reach a third-party
service. This is engineering documentation, not legal advice — it must be
reviewed by counsel/compliance as part of Phase 11 before any real PHI
touches this system.

The 2026 HIPAA Security Rule update made encryption and multi-factor
authentication **required**, not "addressable" as under the prior rule.
Every control below is written to that bar.

## Control matrix

| Control | HIPAA citation | Implementation | Status |
|---|---|---|---|
| Encryption at rest for PHI columns (AES-256) | §164.312(a)(2)(iv) | `src/security/encryption.py` — `EnvelopeEncryptor`, AES-256-GCM; wired onto `claims.patient_name_encrypted`/`patient_member_id_encrypted` in `ingestion/apply.py` (write) and `api/repository.py` (read) as of Phase 10 | Implemented and wired end to end, tested (`tests/db/test_patient_columns_are_encrypted.py`, `tests/api/test_endpoints_live_db.py`). Previously the primitive existed but nothing called it — patient columns were plaintext in practice; closed as a Phase 10 adversarial-review HIGH finding |
| Key management, envelope encryption | §164.312(a)(2)(iv), §164.308(a)(5)(ii)(D) | `src/security/kms.py` (port) + `src/security/kms_local.py` (dev/test adapter) + `src/security/kms_env.py` (`EnvKMS`, static-KEK-from-secret stopgap, still `main.py`'s default) + `src/security/kms_aws.py`/`src/security/kms_azure.py` (F-20, real adapters, opt-in via `KMS_PROVIDER`) | Port, local adapter, and `EnvKMS` implemented and tested. Real `AwsKmsAdapter`/`AzureKeyVaultAdapter` added in F-20, unit-tested against fake SDK clients (`tests/security/test_kms_aws.py`, `test_kms_azure.py`), wired into `main.py` behind `KMS_PROVIDER` (unset/`"env"` keeps today's `EnvKMS` default unchanged) — but **neither has ever been exercised against a real AWS account or Azure Key Vault**, no cloud credentials available in this build environment. `EnvKMS` is meaningfully weaker (no per-operation KMS-side audit trail, no automatic rotation) but is a real, working encryption-at-rest mechanism today, not a placeholder |
| Key rotation without re-encrypting data | §164.308(a)(5)(ii)(D) | `EnvelopeEncryptor.rotate_kek()` re-wraps only the DEK | Implemented, tested — proves ciphertext bytes are unchanged after rotation |
| Transmission encryption (TLS 1.2+) | §164.312(e)(1), §164.312(e)(2)(ii) | AWS: `terraform/environments/aws`'s `aws_lb_listener.https` sets `ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"` (TLS 1.2 floor, TLS 1.3 supported) plus an `http_redirect` listener forcing HTTP→HTTPS. Azure: `azurerm_container_app`'s `ingress { external_enabled = true }` (`terraform/modules/azure/container_runtime.tf`) uses the platform's own managed TLS termination and HTTPS-only default, not an explicit Terraform-set minimum version the way AWS's listener has one | AWS side implemented, matches the 2026 rule's floor explicitly in code. Azure side relies on Azure Container Apps' platform default (HTTPS-only ingress, no `allow_insecure` override present) rather than an explicit pinned minimum — asymmetric with AWS, worth closing if `azurerm_container_app`'s schema ever exposes a TLS-version setting directly. Neither has been exercised against a real deployment (no cloud account in this build environment, same ceiling as the rest of Terraform) |
| Multi-factor authentication, mandatory, no bypass | §164.312(d) (2026 rule: MFA required) | `src/security/mfa.py` (TOTP) + `src/security/session.py` (`issue_session` refuses without `mfa_verified=True`) + `POST /auth/login` (`src/api/routes/auth.py`) as the sole production caller | Implemented, tested — `tests/security/test_session.py::test_only_issue_session_can_mint_a_token_from_a_bare_identity` proves no second door exists in this module. **Previously (through Phase 6) this mechanism had zero production callers** — no login endpoint existed anywhere, so "MFA cannot be bypassed" was true only because there was nothing to bypass. Closed in `docs/MASTER-BUILD-PROMPT-V2.md`'s Phase 5 step 3: `POST /auth/login` verifies password then TOTP, in that order, before ever calling `issue_session` (`tests/api/test_login.py`) |
| Unique user identification | §164.312(a)(2)(i) | `sub` claim in every session token, per-user; a machine credential (API key, see below) resolves to its own dedicated service `User` row, never a shared identity | Implemented as part of `session.py` |
| Automatic logoff | §164.312(a)(2)(iii) | Short-lived access tokens (`ACCESS_TOKEN_TTL`, 15 min default); `POST /org-policy`'s `session_timeout_seconds` lets an org tighten or loosen this per-org, applied at `issue_session` time (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 5 step 6) | Implemented, tested (`tests/security/test_session.py`, `tests/api/test_org_policy.py`). The override only affects the access token minted at login — a refreshed token always reverts to the plain default, since `refresh_session` has no database access to look up an org's policy with, and no `POST /auth/refresh` route exists yet regardless |
| Immediate access revocation (workforce termination / offboarding) | §164.308(a)(3)(ii)(C) (termination procedures) | `POST /organizations/members/{id}/revoke` (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 5 step 4) sets `memberships.revoked_at` — role/access is resolved fresh from `memberships` on every single request (never cached in the token), so this takes effect on the very next request against any route, with no token-revocation list to maintain | Implemented, tested end to end — `tests/api/test_offboarding.py::test_revoking_membership_kills_the_next_request_immediately` proves an already-issued, unexpired token stops working immediately. `tests/db/test_offboarding.py` proves the same at the RLS/`asc_app` layer. DB-backed half unverified against a live Postgres in this build environment, same disclosed ceiling as every RLS-dependent control in this table |
| Machine-to-machine (API key) authentication, least privilege | §164.312(d) (person or entity authentication, extended to a service credential) | `src/security/tokens.py` (`API_KEY_PREFIX`, `generate_api_key` — 256-bit random, prefixed, hashed and stored, never the raw value) + `POST /api-keys` (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 5 step 5) + `api/auth.py`'s bearer-prefix branch. A key resolves to its own dedicated, narrowly-scoped `api_service`-role service user (`docs/PERMISSIONS.md`'s role table) — same resolved-access/RLS machinery a human session goes through, not a parallel authorization system | Implemented, tested (`tests/api/test_api_keys.py`, `tests/db/test_api_keys.py`). Revoking either the `ApiKey` row or the underlying `Membership` kills access immediately, same no-token-revocation-list guarantee as offboarding above |
| Network-based access restriction (per-org IP allowlist) | §164.312(a)(1) (access control) | `src/security/ip_allowlist.py` (pure CIDR-aware matching, fail-closed on an unparseable client IP) enforced in `api/auth.py::_resolve_auth_context` after role resolution, for both JWT and API-key auth alike (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 5 step 6) | Matching logic fully unit-tested (`tests/security/test_ip_allowlist.py`). The *rejection* path is proven over a real HTTP request (`tests/api/test_org_policy.py`); the *match* path (a real client IP inside an allowed range) is only proven at the pure-function level in this environment — Starlette's `TestClient` always presents the literal string `"testclient"` as the request's client host, never a real IP, with no supported override in the installed version (0.41.3) |
| Forced re-authentication for sensitive actions (PHI export) | §164.312(d) | `security/session.py::require_recent_auth()` + `api/auth.py::require_permission_with_recent_auth` (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 6), gating `GET /findings/export.csv` | Implemented, wired, and tested (`tests/api/test_csv_export.py`) — `require_recent_auth` previously had zero call sites anywhere in the app (the exact "built but unwired" pattern this project's audit repeatedly caught elsewhere), closed this phase. Applies to `AuthContext.authenticated_at`, populated from the JWT's `auth_time` claim (unaffected by a token refresh) or, for an API key, the moment it authenticated (a raw secret has no separate "session age" the way a JWT does). Remediation on a 401 is simply calling `POST /auth/login` again — no separate step-up endpoint exists or is needed, since a successful login already mints a fresh `auth_time`. Scoped to the export route only; today's exported CSV columns carry no patient-identifying fields, but the control guards the export *pattern*, not the current column list (`api/routes/findings.py`'s own docstring) |
| Session/refresh token rotation | §164.312(a)(1) | `session.refresh_session()` — replay of a used refresh token is rejected | Implemented, tested |
| Role-based access control, minimum necessary | §164.312(a)(1), §164.514 (minimum necessary), §164.530(c) | `src/security/rbac.py` — deny-by-default `can(role, action)` | Implemented, full matrix tested |
| Audit controls | §164.312(b) | `audit_log` table (Phase 3), append-only via `REVOKE UPDATE, DELETE` from the app role; ingestion writes `claim`/`finding`-resource entries (`ingestion/apply.py`), not just a batch-level `remittance_ingested` row, as of Phase 10 | Schema + revoke implemented (Phase 3); claim/finding-level coverage closed as a Phase 10 adversarial-review HIGH finding (`GET /claims/{id}/access-history` previously couldn't show a claim's own ingestion). DB-level append-only enforcement now runs for real on every push via `.github/workflows/ci.yml`'s Postgres service container, not just pending-verification |
| PHI access logging, minimum-necessary reporting | §164.514(d), §164.312(b) | `phi_access_log` table (Phase 3) | Same status as audit_log above |
| No PHI in logs, traces, or error messages | §164.312(b), general minimum-necessary principle | `src/security/redaction.py` — `PHIRedactionFilter` | Implemented, tested. Structured `extra=` fields are reliably redacted; free-text regex scrubbing (SSN/MBI shapes) is defense in depth, not a substitute — see the module docstring for the honest limitation |
| Rate limiting, per user and per org | §164.308(a)(5)(ii)(C) (protection from malicious software / brute force, by extension) | `src/security/rate_limit.py` — `InMemoryTokenBucketRateLimiter`; `src/api/rate_limit.py::enforce_rate_limit`, applied as a router-level `dependencies=[...]` on every authenticated router (`findings`, `contracts`, `packets`, `remittances`, `audit`, `organizations`, `invitations`, `api_keys`, `org_policy`) | Register finding F-06 (`docs/audit/REGISTER.md`) — "complete, tested, and wired to zero routes" — was fixed in Wave 3 remediation (`5f8d462`) and every router built since (Phase 5) has followed the same wired-by-default convention. Two independent limiters, both must allow a request: one keyed per `(org_id, user_id)` (throttles a single noisy caller, capacity 60/refill 1 per second by default) and one keyed per `org_id` alone (`docs/MASTER-BUILD-PROMPT-V2.md` Phase 6's "rate limiting per org" — bounds the *combined* traffic of every user/API key at one org, capacity 600/refill 10 per second by default, deliberately well above any single user's own budget so it isn't a redundant copy of the per-user check). Tested (`tests/api/test_rate_limit.py`). Still single-process/in-memory only; a Redis-backed adapter is needed once the app runs as more than one instance |
| Account lockout after failed logins | §164.308(a)(5)(ii)(C) | `src/security/rate_limit.py::AccountLockoutTracker`, wired into `POST /auth/login` (`api/routes/auth.py`) | **Corrected from an earlier, now-stale claim**: fixed alongside rate limiting above (F-06, `5f8d462`) — login has real lockout after repeated failures, tested (`tests/api/test_login.py`, `tests/api/test_rate_limit.py`) |
| Secret management, nothing committed | §164.308(a)(3) (workforce security, by extension), general security hygiene | `src/security/secrets.py` — `SecretStore` port, `EnvSecretStore` dev adapter | Port + dev adapter implemented. Real Vault/cloud secret-store adapters are Phase 9/11 scope |
| Static application security testing | §164.308(a)(8) (evaluation) | `bandit` (`make security`) | Runs clean (`bandit -r . -x ./tests,./evals` — the Makefile's original `-x tests,evals` exclude syntax didn't actually exclude anything on this platform; fixed to `-x ./tests,./evals`) |
| Dependency vulnerability scanning | §164.308(a)(8) | `pip-audit` (`make security`, runs against a clean install in CI as of Phase 10) + `trivy` against the built container image (`container-scan` job) | CI's `pip-audit` runs in a fresh environment (`pip install -e ".[dev]"` on a GitHub-hosted runner), not a shared machine install, so its result reflects this project's actual tree. `trivy` additionally scans the built image and found `setuptools`/`msgpack` at vulnerable pinned versions — neither is a declared dependency of anything in `requirements.lock.txt`; `setuptools` is almost certainly bundled by `python -m venv`'s own bootstrap (a known pattern in slim Python images), `msgpack`'s exact origin wasn't pinned down. Both are explicitly force-reinstalled to safe versions in the `Dockerfile`'s builder stage. **`trivy`'s vulnerability table still reports the pre-upgrade versions even after that fix** — reproduced across two separate remediation attempts, while `trivy`'s own SBOM component listing consistently confirms the patched versions are what's actually on disk (every individual `dist-info/METADATA` entry, including the upgraded ones, shows 0 findings). Suppressed via `.trivyignore` as a documented false positive (this Trivy version, 0.70.0, is one behind current) rather than left unexplained or blocking the pipeline on a finding that doesn't reflect the real image |
| Full-history secret scanning | §164.308(a)(1)(ii)(D) (information system activity review, by extension) | `gitleaks` (`make security`) | **Not run** — `gitleaks` is a Go binary, not installable via `pip`, and unavailable in this build environment. Must be run before Phase 11 |
| Minimum-necessary PHI in LLM prompts | §164.514 (minimum necessary) | `src/packets/prompt.py` — patient name/member id are never interpolated into the text sent to the LLM; they're substituted back into the final letter only after generation | Implemented, tested — `tests/packets/test_prompt.py` asserts directly on the captured prompt text for a range of inputs, including distinctive names chosen so a false negative can't happen by luck |
| Integrity control on LLM-generated content | §164.312(c)(1) (integrity) | `src/packets/currency.py` + `src/packets/service.py` — every dollar figure in a generated appeal letter is extracted and asserted to exactly match a value in the deterministic finding record; the draft is rejected and regenerated (up to a small retry cap) if not, and no unvalidated draft is ever persisted or returned | Implemented, tested — `tests/packets/test_currency.py`, `test_service.py`. This is CLAUDE.md's hardest rule: an LLM never computes, adjusts, or restates a dollar amount |

## Not yet built (later phases, by design)

- **OIDC identity provider integration.** `session.py` builds what happens
  *after* a successful login (MFA-gated token issuance, refresh, and
  validation); the actual OIDC handshake needs a real IdP, same gap as
  the SSO bullet below — see there for the current disposition.
- **Per-org encryption keys (BYOK-ready) and per-org data residency.**
  `docs/MASTER-BUILD-PROMPT-V2.md`'s Phase 6 prompt names both, alongside
  a per-org rate-limiting ceiling (now built — see the rate limiting row
  above) and forced re-auth for PHI export (now built — see that row
  above). These two remain not started; scoped out of the session that
  built the other two, via the same `AskUserQuestion` scoping pattern
  Phase 5's SSO/SCIM deferral used. Per-org encryption keys need real
  schema + design work (an org-level KEK reference threaded through
  `EnvelopeEncryptor`'s call sites, currently one global KEK for every
  org). Data residency, given this build is one shared Postgres instance
  in one region today, would realistically mean a stored, honest
  preference/contractual flag rather than physical multi-region
  enforcement — worth confirming that framing before building it, not
  assuming.
- **GCP KMS / Vault adapters** — still named, deferred; AWS KMS and Azure
  Key Vault adapters exist now (F-20, `docs/audit/REGISTER.md`, opt-in via
  `KMS_PROVIDER`) but only those two clouds. `security/kms_env.py`'s
  `EnvKMS` remains the default in the meantime (see the key management
  row above).
- **TLS termination / network segmentation** — Phase 9 (Terraform layer);
  Phase 10 added explicit RDS-to-app encryption in transit
  (`terraform/modules/aws/database.tf`'s `rds.force_ssl` parameter group —
  RDS defaults to allowing unencrypted connections, Azure's flexible
  server does not).
- **SSO (OIDC/SAML per organization), SCIM provisioning/deprovisioning,
  impersonation, and break-glass access.** `docs/MASTER-BUILD-PROMPT-V2.md`'s
  Phase 5 prompt names all four; the user explicitly confirmed scoping
  Phase 5 down to invitation → accept → MFA → login, offboarding,
  delegated admin, API keys, and per-org policy first, deferring these
  four as an independent follow-up pass — each is independently large
  and/or has an external-verification ceiling this environment can't
  meet (a real IdP for SSO, a real SCIM client for provisioning), same
  disclosure pattern as the KMS/SFTP adapters above. `org_policies
  .ip_allowlist`/`session_timeout_seconds` (built) are a narrower,
  same-spirit substitute for some of what SSO conditional-access
  policies would otherwise provide, not a replacement for SSO itself.
- **BAA with the LLM provider, and confirmation of zero-retention terms**
  for the packet-drafting integration added in Phase 7
  (`src/packets/drafter.py`'s `AnthropicPacketDrafter`) — explicitly Phase
  11 scope, per `docs/MASTER-BUILD-PROMPT.md`'s compliance checklist. No
  real LLM call happens anywhere in this codebase's tests; the real
  adapter is untested by design (same deferral as real cloud KMS
  adapters), so there's nothing to retroactively unwind if the BAA terms
  require a different provider or configuration later.

## Phase 10 adversarial review: triaged findings not fixed this phase

`docs/MASTER-BUILD-PROMPT.md` requires a full adversarial-review pass
before Phase 10 closes, with every HIGH/CRITICAL finding fixed before
proceeding. Both HIGH findings (plaintext PHI columns; missing claim/
finding audit entries, both above) were fixed. The MEDIUM/LOW findings
below were explicitly triaged rather than silently dropped — each is a
real, scoped gap, deliberately not rushed into a fix this phase:

- **LLM currency validation is membership-only, not positional.**
  `packets/currency.py::validate_currency` confirms every dollar figure in
  a generated letter matches *some* allowed value, but not that it's
  labeled correctly (a draft could put the shortfall figure where the
  expected-allowed amount belongs). Mitigated today by human review —
  every packet requires biller approval (`POST /packets/{id}/approve`)
  before it can be used — but that's a backstop, not a fix. A real fix
  means either a much more rigid, single-blank-per-sentence template (no
  LLM freedom over figure placement) or a second verification pass;
  both are real design decisions, not something to guess at under time
  pressure.
- **Rate limiting, account lockout, and step-up re-auth are unwired**
  (see the control matrix rows above) — the modules exist and are
  tested in isolation, but no route calls them. Wiring them requires
  policy decisions (which routes need step-up? what limits, per what
  key?) that deserve a real conversation, not an assumed default.
- **`list_findings_by_payer_claim_control_number` was missing its
  tenant_id filter** despite accepting the parameter — fixed in Phase 10
  (`src/db/repository.py`); safe today only because every caller runs
  inside `tenant_session` (RLS-scoped), but it was a latent global-read
  footgun for any future caller that didn't.
- **Reversal netting is incomplete.** A reversal (CLP02=22) creates an
  offsetting finding but doesn't mark/supersede the original, so both
  rows persist; if the reversal's line layout doesn't match the original,
  the offsetting finding is silently dropped. Affects the worklist's
  accuracy, not stored dollar amounts. Deferred — the correct fix (an
  explicit supersession model) touches `domain.variance`, `ingestion.plan`,
  and `ingestion.apply` together and deserves its own design pass.
- **PLB sign convention in `ingestion/reconcile.py` may not match the
  X12 835 TR3 guide.** Flagged by the adversarial review; the existing
  test suite (`tests/ingestion/test_reconcile.py`) locks in the current
  convention with a fixture built consistently around it, and X12's PLB
  sign semantics are a well-known source of implementer disagreement.
  Changing this without checking the TR3 guide risks trading one
  plausible-but-wrong convention for another. Affects only the
  `reconciliation_mismatches` diagnostic counter, never a computed
  finding or dollar amount — deferred pending a real spec check.
- **Free-text PHI redaction regexes only match dashed SSN/MBI shapes**
  (`security/redaction.py`) — undashed 9-digit sequences pass through.
  This is explicitly documented as defense-in-depth, not the primary
  control (structured `extra=` field redaction is), but the gap is real.
- **`GET /findings/{id}` returns full patient name/member id to any role
  with `READ_FINDING`, including `VIEWER`.** Within-tenant, audited via
  `phi_access_log`, so not a leak — a minimum-necessary policy question
  (should a read-only role see raw identifiers?) worth raising with
  whoever owns that product decision, not something to unilaterally
  narrow.

## Phase 10 iac-scan: first real tfsec run against this Terraform

CI's `iac-scan` job ran `tfsec` against `terraform/` for the first time
(Phase 9's HCL was manual-review-only, never tool-scanned). Found 8
CRITICAL, 6 HIGH, 8 MEDIUM, 18 LOW findings. All CRITICAL/HIGH are
resolved — either a real fix or a `#tfsec:ignore` comment at the exact
finding with a justification, verified locally (`tfsec terraform`, 0
critical/0 high remaining). `ci.yml`'s tfsec step now runs with
`--minimum-severity HIGH`, so MEDIUM/LOW are still visible in the job log
but don't block the pipeline.

**Fixed**: ALB egress narrowed from all-ports/0.0.0.0/0 to the app
security group on port 8000 only; app task egress narrowed from
all-ports to HTTPS (443) only, with a matching new
`aws_security_group_rule` for the app→database Postgres egress that the
broad rule used to cover implicitly; ALB now sets
`drop_invalid_header_fields = true`; Azure Key Vault now has an explicit
`network_acls { default_action = "Deny", bypass = "AzureServices" }`
block (previously relied only on `public_network_access_enabled = false`,
a coarser control); CloudWatch log group now encrypted with the existing
KMS key.

**Ignored, with justification inline at each finding** (not fixable
without breaking the product): ALB ingress on 443 from 0.0.0.0/0 — this
is the public API's only ingress path, ambulatory surgery centers have no
fixed CIDR to allowlist. ALB `internal = false` — same reason, an
internal-only ALB isn't a public SaaS API. App task egress to 0.0.0.0/0
on port 443 — Anthropic's API is third-party with no fixed IP range to
scope to; the port is narrowed, the CIDR can't be. IAM policy's `/*`
suffix on the remittances bucket ARN — this is the correct, standard way
to scope S3 object-level actions to one specific already-named bucket,
not a true wildcard; tfsec's static check can't tell the difference.

**Deferred (MEDIUM/LOW, same triage discipline as the adversarial review
above)**: RDS IAM authentication (this system already uses
Secrets-Manager-backed password auth; adding IAM auth as a second
mechanism needs corresponding application-side changes, not a one-line
Terraform flag); VPC Flow Logs; S3 access-log bucket for the remittances
bucket (needs a second bucket with its own encryption/public-access-block
config); Key Vault key/secret expiry dates; RDS Performance Insights;
remaining security-group-rule descriptions and Key Vault secret
content-type metadata.

## Asset inventory and network map

The 2026 rule requires both, reviewed annually. This is the template Phase
4 establishes; it gets filled in as real infrastructure exists (Phase 9
onward) and reviewed for the first time before Phase 11.

**Asset inventory (template):**

| Asset | Type | Data classification | Owner | Last reviewed |
|---|---|---|---|---|
| Postgres primary DB | Data store | PHI | — | not yet in service |
| Application servers | Compute | Processes PHI | — | not yet in service |
| KMS / key store | Key management | Protects PHI encryption keys | — | not yet in service |
| Object storage (835 files) | Data store | PHI | — | not yet in service |

**Network map:** to be produced in Phase 9 once actual network topology
(VPC/subnets, ingress, segmentation) exists — a diagram of a system that
isn't deployed yet would be fiction, not documentation.

## Incident response and breach notification

Full procedures (60-day HIPAA clock, named owner, tested contact tree) are
Phase 11 scope — that phase is explicitly paperwork/process, not code, and
shouldn't be drafted piecemeal alongside unrelated engineering phases.
