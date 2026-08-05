# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint (about to commit "Phase 10: CI/CD
and pre-production hardening"). Read `docs/PHASES.md` first for the phase
checklist — this file adds the texture that isn't in that summary.

## Phase: 10 code-complete. The pipeline itself has never actually run.

Phases 0–2 and 4 are fully done. Phases 3, 5, 6, 7, and 8 each have an
honest local-Postgres gap; Phase 9 additionally needs real Docker and
real, billed cloud accounts. **Phase 10 is different from all of them in
one important way: its own gap closes automatically the moment this gets
pushed.** `.github/workflows/ci.yml` runs on GitHub-hosted runners, which
have Docker and can spin up a real Postgres 16 service container —
something this local environment has never had. Once pushed, CI doesn't
just verify Phase 10's own new code; it retroactively verifies every
DB-backed test this whole build has been skipping since Phase 3. That
first real run might fail — this is genuinely the first time some of this
code touches a live database — so **do not check Phase 10 off in
`docs/PHASES.md` until it's actually gone green.**

Current phase per `docs/PHASES.md`: **Phase 10 — CI/CD and pre-production
hardening**, code-complete, adversarial review done and both HIGH findings
fixed, pipeline never executed.

## Done (this session, Phase 10)

- **`.github/workflows/ci.yml`** — lint (ruff, mypy --strict, lockfile
  freshness) -> test (Postgres 16 service container, `init_roles.sql`,
  `alembic upgrade head`, full `pytest -q`) -> security (bandit,
  pip-audit, full-history `gitleaks`) -> sbom (CycloneDX artifact) ->
  container-scan (`docker build` + `trivy`) -> iac-scan (`terraform
  validate` for both clouds + `tfsec`). Runs on every push/PR to
  `master`.
- **`.github/workflows/deploy.yml`** — staging -> smoke test -> OWASP ZAP
  baseline -> manual approval gate -> production, AWS and Azure as
  separate job chains, OIDC/federated-credential auth (no static keys
  stored in GitHub). Every cloud-dependent job guards on
  `if: secrets.* != ''` so it shows **skipped**, not failed, until real
  credentials exist. One-time setup (IAM OIDC provider/role, Azure App
  Registration federated credential, a `production` GitHub Environment
  with required reviewers) documented in `docs/RUNBOOK.md`'s new "CI/CD
  pipeline" section.
- **`.github/workflows/scheduled-security-scan.yml`** — six-monthly cron
  (bandit/pip-audit/trivy), opens a GitHub issue on new findings. The
  2026 rule's other requirement, an annual third-party penetration test,
  is documented as procurement/process work, not faked as a step.
- **`requirements.lock.txt`** (`make lock`, `pip-compile`) — genuinely
  generated and verified locally, including confirming it's *stable*
  under re-compilation (re-running `pip-compile` against its own existing
  output file introduces zero drift; only a cold resolve to a fresh file
  does). That distinction matters: it's what makes the CI freshness check
  (which regenerates in place and diffs) catch real `pyproject.toml`
  drift instead of false-positiving on every unrelated upstream package
  release between commits. `Dockerfile`'s builder stage now installs from
  this lockfile, not `pyproject.toml` directly.
- **SBOM** (`make sbom`, `cyclonedx-py environment`) — genuinely generated
  and verified locally. Not committed — an SBOM describes one build's
  contents, so it's a CI artifact per run, not a static file that would
  drift from reality.
- **Adversarial review**: ran the `adversarial-reviewer` subagent across
  the full `src/` tree, migrations, Terraform, Dockerfile, and the new
  workflow files, per `docs/MASTER-BUILD-PROMPT.md`'s explicit
  requirement. Found 2 HIGH, 5 MEDIUM, 3 LOW findings. Both HIGH fixed;
  one MEDIUM (tenant-filter gap) and one MEDIUM (RDS transit encryption)
  fixed opportunistically; the remaining 4 MEDIUM/3 LOW triaged and
  documented rather than dropped — see `docs/SECURITY.md`'s "Phase 10
  adversarial review" section.
- **HIGH #1 fixed: PHI columns were never actually encrypted.** The
  envelope-encryption primitive (`EnvelopeEncryptor`, Phase 4) existed
  but had zero call sites in any write/read path —
  `claims.patient_name`/`patient_member_id` were plain `Text` columns.
  Digging into this surfaced a second, deeper bug in the same area:
  `ingestion/apply.py` never read `claim.patient` at all when writing a
  claim row, so these columns were always `NULL` regardless — even
  though `domain/x835.py` correctly parses the NM1*QC segment into
  `Claim835.patient` (an `Entity` with `.name`/`.id_code`). The columns
  weren't just unencrypted; the feature was never wired end to end. Fixed
  by:
  - Renaming the columns to `patient_name_encrypted`/
    `patient_member_id_encrypted` (`db/models.py`) — no new migration
    needed, since migration `0001` generates its schema from
    `Base.metadata` via `create_all` and has never run against a real
    database (no live schema existed to migrate away from).
  - New `src/security/phi_columns.py` — `encrypt_phi_field`/
    `decrypt_phi_field`, JSON-serializing an `EncryptedPayload` into the
    `Text` column.
  - New `src/security/kms_env.py` (`EnvKMS`) — a real-but-weaker stopgap
    `KeyManagementService` that derives a static KEK from a secret
    (`PHI_ENCRYPTION_KEY`, via the existing `SecretStore` port) instead of
    generating one in-process, so it survives restarts unlike
    `kms_local.LocalKMS`. Wired into `main.py`. The real cloud KMS
    adapter (AWS KMS / Azure Key Vault) remains a named, deferred gap —
    unchanged from before this phase.
  - Threaded `EnvelopeEncryptor` through `ingestion.apply._apply_claim`
    (encrypts `claim.patient.name`/`.id_code` before `create_claim`),
    `ingestion.pipeline.ingest_file` (a required kwarg — deliberately
    *not* optional-with-a-no-op-default the way `tracer`/`instruments`
    are, since there's no safe "don't encrypt" default), and
    `api.repository.PostgresRepository` (decrypts on the two read paths
    that surface patient info: finding detail, packet prompt
    construction).
- **HIGH #2 fixed: ingestion wrote no claim/finding-level audit
  entries.** Only a batch-level `remittance_ingested` row existed, so
  `GET /claims/{id}/access-history` (Phase 8) could never show a claim's
  own ingestion or its findings being created — a real gap against
  CLAUDE.md rule 5 ("every write to a PHI-bearing table goes through the
  audit log"). Fixed in `ingestion/apply.py::_apply_claim`: a
  `claim_ingested` audit entry per claim, a `finding_created` entry per
  finding (using the DB-generated `FindingModel` rows `save_findings`
  already returned but the caller previously discarded).
- **MEDIUM fixed: `list_findings_by_payer_claim_control_number` accepted
  `tenant_id` but never filtered by it.** Safe today only because every
  caller runs inside `tenant_session` (RLS-scoped) — a latent global-read
  footgun for any future caller that didn't (a superuser connection, a
  batch job). Added the filter.
- **MEDIUM fixed: AWS RDS defaults to allowing unencrypted DB
  connections; Azure's flexible server does not.** Added an explicit
  `aws_db_parameter_group` (`rds.force_ssl = 1`) and `sslmode=require` on
  the assembled `DATABASE_URL` secret (`terraform/modules/aws/`) so PHI
  queries can't fall back to plaintext-in-transit on AWS specifically.
- **Discovered and worked around: `pip-tools` 7.6.0 is incompatible with
  `pip` 25+.** `pip-tools` imports a private pip API
  (`pip._internal.utils.compat.stdlib_pkgs`) that pip 25 removed —
  confirmed locally (pip 24.3.1 works, pip 26.2.1 raises `ImportError`).
  Pinned `pip<25` scoped to just the CI lockfile-freshness step and noted
  in the Makefile's `lock` target comment — not a project-wide pin, since
  nothing else needs it.
- New pure tests (verified now, no DB needed — same pure/DB-split
  discipline as every prior phase): `tests/security/test_kms_env.py`,
  `tests/security/test_phi_columns.py`.
- New DB-backed tests (written, will get their first real execution on
  the CI push): `tests/db/test_patient_columns_are_encrypted.py` (proves
  the column holds ciphertext, not plaintext — not just that decryption
  round-trips through the app, which could pass even if storage were
  plaintext-then-relabeled), a new test in
  `tests/ingestion/test_apply_audit_entry.py` for the claim/finding audit
  entries, and new assertions in `tests/api/test_endpoints_live_db.py`
  (decrypted patient name/member id round-trip through the finding-detail
  endpoint; claim ingestion shows up in access history).
- Terraform: added a `variable "environment"` (default `"production"`) to
  both `terraform/environments/{aws,azure}/main.tf` so `deploy.yml` can
  target a `staging` Terraform workspace from the same environment
  directory instead of duplicating it — a small, directly-motivated
  change (the deploy pipeline needs somewhere to deploy that isn't prod).

## Failing

Nothing. `pytest -q` -> **392 passed, 23 skipped** (11 `tests/db/`
including the 2 new encryption tests, 5 `tests/ingestion/` including the
new audit-entry test, 7 `tests/api/test_endpoints_live_db.py` — all
honest skips, up from Phase 9's 20 because of the new DB-backed tests
this phase added). `mypy --strict .` and `ruff check .` clean across
**137 source files** (was 132; new: `security/kms_env.py`,
`security/phi_columns.py`, plus test files). `python -m evals.run` ->
100%/100%/100%/100%, unaffected. `bandit -r . -x ./tests,./evals` clean.
`pip-audit` clean against this project's actual dependency tree (see the
`pip-tools`/pip pin above for a *tooling* compatibility issue, unrelated
to any vulnerable runtime dependency). Branch coverage on
`domain/variance.py` still 100%.

**What could not be run, and why, stated plainly**: the three new GitHub
Actions workflows themselves — no way to execute a GitHub Actions
workflow without GitHub Actions. Manually reviewed for YAML syntax and
job-dependency correctness, same discipline as every prior phase's
Terraform review, but that is not the same as a green run. `terraform
validate`/`tfsec` against the two small HCL changes this phase made (RDS
parameter group, `environment` variable) — no local Terraform CLI, same
as Phase 9. `deploy.yml`'s cloud-dependent jobs — no AWS/Azure credentials
exist to configure, so they'll show `skipped` on the very first run too,
not just today locally.

## Decisions worth knowing (not obvious from the code)

- **No new Alembic migration for the `patient_name`/`patient_member_id`
  column rename.** This looks wrong at first glance (renaming a column
  usually needs a migration), but migration `0001` builds its schema by
  calling `Base.metadata.create_all()` against whatever's currently in
  `db/models.py` — it doesn't hardcode column definitions. Since no real
  deployment has ever run these migrations (every DB-backed test this
  whole build has been skip-pattern-avoiding a live Postgres), there was
  no live schema to migrate away from. The very first real
  `alembic upgrade head` (in CI) will just create the new column names
  directly. This reasoning stops being valid the moment any real database
  actually runs migration `0001` — from that point on, renaming a column
  needs a real additive migration, per `docs/RUNBOOK.md`'s
  expand/contract pattern.
- **`encryptor` is a required parameter on `ingest_file`, not an optional
  one defaulting to a no-op — deliberately inconsistent with how
  `tracer`/`instruments` were added in Phase 8.** Tracing/metrics have a
  safe "do nothing" default (`NoOpTracer`, `noop_instruments()`); there is
  no safe "don't encrypt PHI" default. Making it required means every
  caller (production and test) must explicitly supply a real encryptor —
  a compile-time (well, mypy-time) guarantee against silently
  reintroducing plaintext storage, rather than a runtime hope.
  `tests/ingestion/conftest.py::make_test_encryptor()` is the one place
  that got easier to call instead.
- **One shared `EnvelopeEncryptor` instance per test file, not a fresh one
  per call, in `tests/api/test_endpoints_live_db.py`.** `LocalKMS` holds
  its KEK in memory, scoped to the instance — a fresh
  `make_test_encryptor()` per `PostgresRepository`/`ingest_file` call
  would wrap DEKs under unrelated, incompatible KEKs, and decryption
  would fail across calls. Production has exactly this same shape (one
  encryptor, constructed once in `main.py`, shared by the whole running
  process) — the test fixture mirrors it rather than working around it.
- **`EnvKMS` reads one static KEK from a secret and never rotates it
  itself.** This is intentionally a stopgap, not a placeholder: it's a
  real, working AES-256-GCM encryption-at-rest mechanism usable in an
  actual deployment today (unlike `LocalKMS`, which loses its keys on
  every restart), but it lacks a real cloud KMS's per-operation access
  audit trail and automatic rotation. Swapping in AWS KMS/Azure Key Vault
  later requires zero changes to `EnvelopeEncryptor` or any caller — only
  a new adapter behind the same `KeyManagementService` Protocol, exactly
  the port's own docstring promise.
- **The CI lockfile-freshness check regenerates `requirements.lock.txt`
  *in place*, not to a separate temp file — this was a real bug caught
  and fixed during this same session, not a design chosen from the
  start.** The first version wrote to a fresh temp path; `pip-compile`
  had no reason to treat that as a "keep existing pins stable" hint, so
  it did a cold resolve and picked up an unrelated transitive package's
  patch release (`mako` 1.4.0 -> 1.4.1) that had nothing to do with this
  repo's own `pyproject.toml`. Confirmed by testing both ways locally.
  Regenerating in place (using the tracked file itself as the output
  path, then diffing against a backup) makes `pip-compile` preserve
  already-pinned versions and only show real drift.

## Traps for someone resuming cold

- Everything from the Phase 3–9 checkpoints still applies (CRLF warnings
  on `git add`, generated/ruff-excluded `evals/golden/cases.py`,
  SSN-shaped test fixtures assembled at runtime to avoid tripping
  `block_phi.sh`, FastAPI's `app.routes` not reflecting included routers
  directly — check `app.openapi()["paths"]` instead).
- **If `make lock` fails with
  `ImportError: cannot import name 'stdlib_pkgs' from 'pip._internal.utils.compat'`**,
  that's the `pip-tools`/pip 25+ incompatibility above, not a real
  problem with this repo — `pip install "pip<25"`, run `make lock`, then
  `pip install --upgrade pip` to restore. Don't "fix" it by pinning pip
  project-wide; it's a local dev-tooling issue, not a production one (the
  Dockerfile's builder stage always upgrades pip itself before installing
  from the lockfile).
- **Don't add a `sbom.json` to git even though `make sbom` creates one
  locally** — it's `.gitignore`d deliberately-not-yet (check before
  committing); an SBOM is a per-build artifact, not source.
- **The first real CI run might fail, and that's expected, not a
  regression to panic about.** Every DB-backed test this whole build has
  written has been running against a skip-pattern, never real Postgres.
  If something fails, fix it as a normal follow-up commit and re-run —
  don't revert the CI workflow itself to make the red go away.
- **`deploy.yml`'s cloud jobs will show `skipped`, not `success`, on the
  very first run** — that's correct until the one-time OIDC/federated-
  credential setup in `docs/RUNBOOK.md` happens. Skipped is not broken.

## Next 3 steps

1. **Push this branch and watch `ci.yml` run for real.** This is the
   actual gate for Phase 10 — if it goes green (including every
   previously-skipped DB-backed test), update `docs/PHASES.md` to check
   the phase off with that confirmation. If something fails, it's almost
   certainly a genuine bug this session's skip-pattern testing couldn't
   have caught — fix it, don't paper over it.
2. **Once CI is green**: per the user's stated plan, do a full code audit
   of the repository, then move to deploying in a GitHub Codespace
   (install Docker there, exercise the full application) — this is the
   Phase 9 Docker verification that's been deferred through Phases 9 and
   10, now with a green CI pipeline backing it up.
3. **Separately, whenever real AWS/Azure accounts become available**:
   follow `docs/RUNBOOK.md`'s new CI/CD section to set up OIDC/federated
   credentials and a `production` GitHub Environment with required
   reviewers, so `deploy.yml`'s staging/production jobs go from `skipped`
   to actually running. Then Phase 9's original Terraform
   `validate`/`plan`/`apply`-against-a-real-account-with-a-timed-restore
   gate is finally reachable, now through a reviewed pipeline instead of
   a manual `terraform apply`.
