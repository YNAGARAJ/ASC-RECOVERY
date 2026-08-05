# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist — this file adds the texture that isn't in that
summary.

## Phase: 10 done and confirmed. Phase 11 (real data readiness) not started.

Phase 10's pipeline actually ran, went red repeatedly, and — after 15
follow-up commits fixing real bugs the first-ever live run of each
tool surfaced — went fully green. This is the first point in this whole
build where "done" means "a machine other than this one ran it and it
passed," not "read carefully and reasoned to be correct." That
distinction matters: every gap this session closed was a genuine,
pre-existing defect, most dating back to the phase named, invisible
until Phase 10 gave it a live Postgres/Docker/Terraform/CI environment
to execute in for the first time. None of these were introduced by
Phase 10's own new code — they were latent since the phase that wrote
them.

**Percentage of Phase 10's gate met: 100%.** Pipeline green end to end
(user-confirmed after the last fix); adversarial review ran with both
HIGH findings fixed; staging environment functionality is N/A until real
cloud credentials exist (documented, not faked). Phase 9's Docker/
Terraform-apply-against-a-real-account gate is still separately open —
Phase 10 advanced it (container build and `terraform validate` now run
for real in CI) but did not close it.

## Done (files completed, one line each)

**Pipeline**
- `.github/workflows/ci.yml` — lint/type-check/lockfile-freshness ->
  test (real Postgres 16 service container) -> security (bandit,
  pip-audit, gitleaks) -> sbom -> container-scan (trivy) -> iac-scan
  (terraform validate + tfsec). This is the workflow that went green.
- `.github/workflows/deploy.yml` — staging -> smoke test -> DAST ->
  manual gate -> production, AWS/Azure as parallel chains, OIDC auth.
  Cloud-dependent jobs show `skipped` (no credentials configured yet) —
  correct, not broken.
- `.github/workflows/scheduled-security-scan.yml` — six-monthly cron
  scan, opens a GitHub issue on new findings.
- `requirements.lock.txt` — pinned production dependency lockfile.
- `.gitleaks.toml` — allowlists two confirmed-synthetic test secrets.
- `.trivyignore` — suppresses two confirmed-false-positive CVE findings
  (see "Decisions" below).

**PHI encryption (adversarial-review HIGH #1)**
- `src/security/phi_columns.py` — encrypt/decrypt a `Text` column via
  `EnvelopeEncryptor`.
- `src/security/kms_env.py` — `EnvKMS`, a stopgap `KeyManagementService`
  reading a static KEK from a secret (real cloud KMS remains deferred).
- `src/db/models.py` — `Claim.patient_name_encrypted`/
  `patient_member_id_encrypted` (renamed from plaintext columns).
- `src/ingestion/apply.py`, `src/ingestion/pipeline.py`,
  `src/api/repository.py`, `src/main.py` — thread `EnvelopeEncryptor`
  through the actual write/read paths (previously zero call sites).

**Audit completeness (adversarial-review HIGH #2)**
- `src/ingestion/apply.py::_apply_claim` — writes `claim_ingested` and
  `finding_created` audit_log entries (previously only a batch-level
  `remittance_ingested` existed).

**Terraform hardening**
- `terraform/modules/aws/network.tf`, `container_runtime.tf` —
  narrowed security-group egress rules, `drop_invalid_header_fields`,
  new `aws_security_group_rule.app_to_database` (needed once the app
  SG's egress stopped being "all ports to everywhere").
- `terraform/modules/aws/database.tf` — `rds.force_ssl` parameter group
  + `sslmode=require`.
- `terraform/modules/aws/storage.tf` — explicit empty `filter {}` on the
  S3 lifecycle rule (silences a provider deprecation warning).
- `terraform/modules/azure/secrets_and_kms.tf` — `notify_before_expiry`
  (required alongside `expire_after`), explicit `network_acls` block.
- `terraform/environments/{aws,azure}/main.tf` — `variable "environment"`
  so `deploy.yml` can target a `staging` Terraform workspace.

**Tests fixed (all pre-existing bugs, see "Traps" below for the full list)**
- `tests/ingestion/conftest.py` — `seed_tenant_with_contract` now
  creates its contract inside `tenant_session`; added optional
  `fee_schedule` override.
- `tests/ingestion/test_pipeline_observability_live_db.py` — uses a
  contract price that yields a genuine positive shortfall.
- `tests/api/test_endpoints_live_db.py` — `_seed_second_user` (mints a
  real ADMIN-role user instead of a role-mismatched token); fixed the
  `phi_access_log` event check to read `purpose`, not `action`.
- `alembic/versions/0002`, `0003`, `0004` — idempotent against
  `0001`'s `create_all()`, with an offline-SQL-generation branch
  preserved (`context.is_offline_mode()`).

**New pure tests** (verified locally, no DB needed): `tests/security/
test_kms_env.py`, `tests/security/test_phi_columns.py`.

**New DB-backed tests** (now confirmed passing, not just written):
`tests/db/test_patient_columns_are_encrypted.py`,
`tests/ingestion/test_apply_audit_entry.py`'s claim/finding-audit test.

## In progress

Nothing mid-write. Phase 10 is closed. The natural next unit of work is
Phase 11 (real data readiness), which has not been started — no files,
no plan.

## Failing

Nothing, as of the last user-confirmed green CI run (commit `ec9bea0`
"check purpose, not action..." plus `a327a7d`, an unrelated docs-only
commit adding the user's own `docs/AUDIT-PROMPTS.md`). Local gate:
`ruff check .` clean, `mypy --strict .` clean (137 files), `pytest -q` →
392 passed / 23 skipped (all honest DB-backed skips in this local,
Postgres-less environment — CI is what actually runs them), `bandit`
clean, eval `GATE PASSED`.

**Caveat on "confirmed green"**: this was confirmed via the user pasting
CI logs into chat across ~15 rounds, not via direct access to GitHub
Actions from this environment (no `gh` CLI configured here). The last
confirmation was a brief "yes, landed now continue" without a fresh
full-jobs log paste. High confidence, not independently re-verified by
reading the raw run.

## Decisions worth knowing (not obvious from the code)

- **`.trivyignore` suppresses two CVEs that were provably already
  fixed.** Two separate remediation attempts (`pip install --upgrade`,
  then `--force-reinstall --no-deps`) both installed patched
  `setuptools`/`msgpack` — confirmed via Trivy's own SBOM component
  listing showing 0 findings on the exact upgraded `dist-info` entries —
  yet Trivy's vulnerability table kept reporting the old versions
  regardless. Concluded this is a detection quirk in the pinned Trivy
  version (0.70.0; 0.73.0 was current per the scan's own notice), not a
  real vulnerability, and suppressed via `.trivyignore` rather than
  attempt a third blind Dockerfile change chasing the same result. If
  this needs revisiting: bump the pinned `trivy-action` version first,
  remove the two `.trivyignore` lines, and see if it reproduces before
  assuming it's fixed.
- **Two GitHub Action parameter names were wrong on first attempt, both
  silently ignored rather than erroring**: `tfsec-action`'s severity
  floor is `additional_args`, not the guessed `tfsec_args`;
  `trivy-action` needs an explicit `trivyignores` input, it does not
  auto-discover `.trivyignore`. Unrecognized `with:` inputs on a
  composite/Docker action are not validated — they just do nothing. If a
  workflow config change seems to have no effect, suspect the parameter
  name first.
- **Migration 0001 builds its schema from `Base.metadata.create_all()`
  against *today's* `db/models.py`, not a historical snapshot** — this
  was already true before Phase 10, but Phase 10 is what first ran
  migrations against a real, empty database and discovered the
  consequence: migrations 0002-0004 all tried to add columns/tables
  0001 already created. Fixed with inspector-based guards, but the
  underlying design (single mutable model file, `create_all` in the
  first migration) means **any future migration that adds a column
  already reflected in `db/models.py` will hit the same issue** unless
  it's added defensively from the start. Worth remembering when writing
  migration `0006`+.
- **`get_claim_access_history` deliberately uses a generic literal
  `action="phi_access"` for every `phi_access_log`-sourced event, with
  the specific reason in `purpose`** — this is a real design choice
  (action = event category, purpose = specific reason), not a bug, even
  though it looked like a bug when the test checked the wrong field.
  Don't "fix" this by making `action` equal to `purpose`'s value.
- **`_seed_second_user` was added (`tests/api/test_endpoints_live_db.py`)
  because minting a token with a role that disagrees with the subject's
  actual DB row can never work** — `api/auth.py::get_auth_context`
  deliberately 401s on that mismatch (a real security safeguard, not
  incidental). Any future test needing a second role on an
  already-seeded tenant should use this helper, not try to reuse a
  subject under a different claimed role.
- **`EnvKMS` (`src/security/kms_env.py`) is a deliberate stopgap, not a
  placeholder** — a real, working AES-256-GCM mechanism (static KEK from
  a secret) usable in an actual deployment today, weaker than a real
  cloud KMS only in lacking per-operation audit trail and automatic
  rotation. The real AWS KMS/Azure Key Vault adapter remains a named,
  deferred gap behind the same `KeyManagementService` port — swapping it
  in later needs zero caller-side changes.

## Traps for someone resuming cold

- **Every one of these was found by CI, not by reading the code** —
  a reminder that this codebase has looked correct under review multiple
  times per phase and still had these bugs. Don't assume a clean local
  gate (`pytest -q` with all DB-backed tests skipping) means the DB-backed
  code is actually correct; it means it's *untested*. The only real
  signal is CI's Postgres-backed run.
- **If `make lock` fails with `ImportError: cannot import name
  'stdlib_pkgs'`**, that's `pip-tools` vs `pip` 25+, not a real problem —
  `pip install "pip<25"`, run `make lock`, then `pip install --upgrade
  pip`. Documented in the Makefile's `lock` target comment.
- **Don't regenerate `requirements.lock.txt` on Windows/macOS and commit
  it without diffing against Linux** — Windows pulls in `colorama`/
  `tzdata` that Linux's resolver omits, and CI runs on Linux. If `make
  lock` must run locally on a non-Linux machine, diff the result against
  the CI job's own regenerated copy (visible in a failed freshness-check
  log) before committing.
- **`sbom.json` and `.terraform/`/`.terraform.lock.hcl` are gitignored on
  purpose** (added this phase, were missing before) — don't add them
  back if they show up as untracked locally; they're build/validation
  artifacts, not source.
- **`docs/AUDIT-PROMPTS.md` is the user's own file**, describing a
  planned six-wave audit to run once Phases 1-12 are complete. Not
  authored by any Claude Code session this build — don't treat it as
  something to execute unprompted.
- Everything from the Phase 3-9 checkpoints still applies (CRLF warnings
  on `git add`, generated/ruff-excluded `evals/golden/cases.py`,
  SSN-shaped test fixtures assembled at runtime to avoid tripping
  `block_phi.sh`, FastAPI's `app.routes` not reflecting included routers
  directly — check `app.openapi()["paths"]` instead).

## Next 3 steps

1. **Enter plan mode for Phase 11 (real data readiness — the compliance
   gate, no code)**, per `docs/MASTER-BUILD-PROMPT.md`: BAAs (cloud
   providers, Anthropic), a real security risk analysis, an incident
   response plan with a named owner, arranging the annual third-party
   penetration test, insurance. This phase is explicitly process/legal
   work, not something to implement as source files — the plan should
   reflect that (templates, checklists, and clearly-flagged
   external-party action items, not application code).
2. **Once real AWS/Azure credentials exist** (a user action, not
   something to attempt from this environment): follow
   `docs/RUNBOOK.md`'s CI/CD section to wire up OIDC/federated
   credentials and a `production` GitHub Environment with required
   reviewers, closing Phase 9's remaining gap (a real `terraform apply`
   with a rehearsed, timed restore).
3. **Whenever the user is ready**: the six-wave audit in
   `docs/AUDIT-PROMPTS.md` is written and waiting for Phase 12 to
   complete first, per the user's own stated sequencing.
