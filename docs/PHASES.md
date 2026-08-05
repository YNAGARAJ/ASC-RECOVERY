# Build Phases

**Current phase: Phase 11 — Real data readiness (the compliance gate — no code)**

**Update from Phase 10**: Phases 3, 5, 6, 7, and 8's DB-backed halves —
previously all "code complete, unverified pending a live Postgres" — got
their first real execution when Phase 10's CI pipeline went green against
an actual Postgres 16 service container, and are now confirmed rather
than assumed. Each phase's entry below is left as originally written for
the historical record of what was and wasn't known at the time, with a
checkbox update reflecting the now-confirmed state. **Phase 9 remains the
one exception**: its gate needs a real Docker install and real, billed
AWS/Azure accounts, which Phase 10's CI gets partway toward (container
build + `terraform validate` both now run for real) but doesn't fully
close — no cloud credentials exist yet for an actual `terraform apply`,
so `deploy.yml`'s cloud-dependent jobs still show `skipped`. See Phase
9's and Phase 10's entries below for exactly what is and isn't checked.

One phase per session. `/clear` between phases. Never advance past a failing
gate. See `docs/MASTER-BUILD-PROMPT.md` for full phase prompts and gates.

- [x] Phase 0 — Scaffold, constitution, guardrails
- [x] Phase 1 — Domain core (pure, no I/O)
- [x] Phase 2 — Eval harness and golden dataset
- [x] Phase 3 — Persistence, tenancy, and effective-dated contracts —
      **confirmed by Phase 10's CI run** (RLS tenant-isolation,
      idempotent-remittance, effective-dated pricing, and audit-log
      append-only all passed against a real Postgres 16). Left below as
      originally written, describing the state before that confirmation —
      **code complete, hard gate not yet run.** The build environment this
      was written in has no Docker/WSL/Postgres, only `pip`. Everything
      checkable without a live database is green: `mypy --strict .`,
      `ruff check .`, `alembic upgrade head --sql` (offline DDL
      generation), and the full existing suite (`tests/db/`'s 11 tests
      skip with an explicit message rather than silently passing). The RLS
      tenant-isolation test, the idempotent-remittance test, the
      effective-dated-pricing round-trip, and the audit-log append-only
      test are all written but have never executed against a real
      Postgres. **Do not check this phase off until someone runs them** —
      see `docs/DB_SETUP.md` for exact steps (`docker compose up -d`,
      `alembic upgrade head`, then
      `TEST_DATABASE_URL=... pytest tests/db/ -v`).
- [x] Phase 4 — Security and PHI controls — fully verified. Envelope
      encryption (AES-256-GCM, KEK rotation without re-encrypting data),
      TOTP MFA, session issuance with a proven MFA-cannot-be-bypassed
      guarantee, deny-by-default RBAC (full role x action matrix tested),
      rate limiting + account lockout, and PHI log redaction all have
      passing tests with no external services required. `mypy --strict .`,
      `ruff check .`, full suite (219 passed, 11 skipped — the Phase 3
      `tests/db/` skips), `bandit`, and `pip-audit` all run clean — see
      `docs/SECURITY.md` for the full control -> HIPAA citation mapping
      and the two explicitly out-of-scope items (real cloud KMS adapters,
      deferred to Phase 9; `gitleaks` full-history scan, unavailable in
      this environment).
- [x] Phase 5 — Ingestion pipeline — **confirmed by Phase 10's CI run**
      (the idempotency, quarantine, and audit-entry DB-backed tests all
      passed against a real Postgres — with two real bugs fixed along the
      way, see Phase 10's "CI debugging round"). Left below as originally
      written — **code complete, DB-writing half
      unverified for the same reason as Phase 3.** Split into a pure
      planning layer (`src/ingestion/reconcile.py`, `plan.py`, `sources.py`,
      `virus_scan.py`) and a thin DB-apply layer (`apply.py`,
      `pipeline.py`), specifically so most of this phase's logic could
      actually run in this Postgres-less environment instead of joining
      Phase 3 in permanent-skip limbo. Fully verified without a live DB:
      quarantine decisions (unparseable file, all-claims-malformed file),
      partial-batch handling (one bad claim doesn't fail the file), BPR
      reconciliation math, reversal/takeback netting (sums to exactly
      zero), source adapters (SFTP/S3 against fake clients implementing
      minimal Protocols), and the EICAR-based virus-scan path — all real,
      passing tests, not skips. **Not yet verified**: the three tests that
      exercise `ingestion.pipeline.ingest_file` end to end against
      Postgres (`tests/ingestion/test_apply_idempotency.py`,
      `test_apply_quarantine.py`, `test_apply_audit_entry.py`) — written,
      skip cleanly without `TEST_DATABASE_URL`, never executed. Added
      `alembic/versions/0002_remittance_quarantine_reason.py` (verified
      offline only, same ceiling as 0001) and five additive functions to
      `src/db/repository.py`. **Do not check this phase off until the
      three DB-backed tests above are run against a live Postgres**, same
      standard as Phase 3.
- [x] Phase 6 — API layer — **confirmed by Phase 10's CI run** (the
      live-Postgres findings-list/finding-detail/RLS tests all passed,
      plus a real auth bug this phase's own new test coverage caught and
      fixed — see Phase 10's "CI debugging round"). Left below as
      originally written — **code complete, DB-writing half unverified
      for the same reason as Phases 3 and 5.** FastAPI service exposing
      upload remittance, list/detail findings, export worklist CSV,
      contract management, and audit log query. Same pure/DB split as
      Phase 5: route handlers depend on an `api.repository.Repository`
      port (`src/api/repository.py`), never on SQLAlchemy directly, with
      two adapters — `PostgresRepository` (real) and `FakeRepository`
      (test-only, in-memory, tenant-partitioned, `tests/api/fakes.py`).
      This is what let the **full authorization matrix** (every one of 4
      roles x 8 endpoints x own-tenant/other-tenant) run as real, passing
      tests without Postgres — not a subset, not skipped
      (`tests/api/test_authz_matrix.py`, 32 cases, all passing). Also
      fully verified without a live DB: no route anywhere accepts a
      client-supplied tenant identifier — proven both by inspecting every
      route's actual parameters and by inspecting the generated OpenAPI
      schema (`test_tenant_param_absence.py`) — which is what makes
      "no endpoint returns another tenant's data under any parameter
      manipulation" true by construction, not by defensive checking.
      OpenAPI spec generates and validates via `openapi-spec-validator`
      (`test_openapi.py`). Structured errors never echo PHI, proven by
      forcing an unhandled exception containing PHI-shaped text and
      asserting none of it reaches the response
      (`test_error_redaction.py`). Pagination and CSV export also covered
      (`test_pagination.py`, `test_csv_export.py`). **Not yet verified**:
      two representative endpoints (findings list, finding detail) run
      against real `PostgresRepository` + real RLS in
      `test_endpoints_live_db.py` — written, skips cleanly without
      `TEST_DATABASE_URL`, never executed; this is what would confirm
      `FakeRepository`'s tenant-isolation guarantee matches reality.
      Added a `users` table (ungated like `tenants`, for resolving a
      bearer token's subject to a tenant_id — ADR-style rationale in
      `src/db/models.py`'s `User` docstring) and an `audit_log.request_id`
      column via `alembic/versions/0003_users_and_audit_request_id.py`
      (offline-verified only, same ceiling as 0001/0002). Added
      `Action.READ_CONTRACT` to `security/rbac.py` (additive; Phase 4's
      full role x action matrix test updated and still green — now 47
      cases). No login/credential/OIDC endpoint was built — Phase 4 never
      built real credential verification to wire one to; tests mint
      tokens directly via the already-gated `issue_session()`. No
      user-management HTTP endpoint either — out of this phase's explicit
      scope. **Do not check this phase off until the live-DB tests above
      are run against a real Postgres**, same standard as Phases 3 and 5.
- [x] Phase 7 — Recovery packet generation (the only place an LLM appears)
      — **confirmed by Phase 10's CI run** (the generate-draft ->
      approve round-trip test passed against real Postgres, both audit
      entries confirmed). Left below as originally written —
      **code complete, DB-writing half unverified for the same reason as
      Phases 3, 5, and 6.** New `src/packets/` package (pure): `currency.py`
      (extracts every currency-shaped figure from LLM output, parses as
      `Decimal`, rejects if any value isn't in the finding record's known
      amounts), `prompt.py` (patient name/member id and every dollar
      figure are placeholder tokens in the text sent to the LLM — never
      the real values — substituted back in only after generation),
      `drafter.py` (`PacketDrafter` port: `ScriptedPacketDrafter` for
      every test, `AnthropicPacketDrafter` real adapter untested by
      design, same deferral as real cloud KMS), `templates.py`
      (per-payer letter boilerplate), `worklist.py` (deadline-proximity-
      then-dollar-value ranking), `service.py` (orchestrates draft ->
      reject-if-raw-figure -> substitute -> validate -> retry, up to a
      small cap; never returns an unvalidated draft). New
      `src/domain/deadlines.py` (pure `date` arithmetic, no timezone
      concept to get wrong by construction; leap-year-correct because
      Python's `date` already is). All of the above fully verified
      without a live DB — this **is** the phase's actual gate:
      `tests/packets/test_currency.py` proves a deliberately corrupted
      draft is rejected, `tests/packets/test_prompt.py` proves patient
      identifiers never reach the captured LLM prompt text (across
      distinctive names chosen so a false negative can't happen by
      luck), `tests/domain/test_deadlines.py` proves the deadline math
      across a leap-day-spanning window and a year rollover. New
      `Action.DRAFT_RECOVERY_PACKET` in `security/rbac.py` (additive;
      Phase 4's matrix test updated, now 51 cases). New `recovery_packets`
      table (tenant-scoped, RLS) plus `contracts.timely_filing_days` /
      `contracts.packet_template` via
      `alembic/versions/0004_recovery_packets_and_timely_filing.py`
      (offline-verified only) — deliberately **not** on
      `domain.contract.ContractVersion`, to avoid rippling through every
      test file that constructs one. Three new API routes
      (`POST/GET /findings/{id}/packets`, `POST /packets/{id}/approve`,
      `POST /packets/{id}/reject` — the human-approval step, never
      automatic) added to the same authz matrix discipline as Phase 6
      (`tests/api/test_authz_matrix.py`, now 44 cases). **Not yet
      verified**: one round-trip test (generate a packet against a real
      ingested finding, approve it, confirm both audit entries) in
      `tests/api/test_endpoints_live_db.py` — written, skips cleanly
      without `TEST_DATABASE_URL`, never executed. **Do not check this
      phase off until that test is run against a live Postgres**, same
      standard as Phases 3, 5, and 6.
- [x] Phase 8 — Observability and audit — **confirmed by Phase 10's CI
      run** (the finding-view -> claim-access-history round-trip passed
      against real Postgres — after fixing a test bug this phase's own
      run surfaced: checking the wrong field name, `action` instead of
      `purpose`, for a `phi_access_log`-sourced event; see Phase 10's "CI
      debugging round"). Left below as originally written — **code
      complete, one DB-backed
      round-trip test unverified for the same reason as Phases 3, 5, 6, and
      7.** Both of the phase's literal gate requirements are fully verified
      without a live database — this is the bulk of the phase's actual
      substance, not a workaround:
      - **"Trace sampling captures no PHI (assert on exported spans)"**:
        `src/observability/tracing.py`'s `PHIScrubbingSpanExporter` wraps
        any real `SpanExporter` (decorator pattern, no auto-instrumentation
        anywhere in this codebase — auto-instrumentors can capture raw SQL
        or route params as span attributes, an uncontrolled leak surface).
        `tests/observability/test_tracing.py` builds a span with a
        deliberately PHI-shaped attribute and asserts the exported span
        has none of it — real spans, real export pipeline, no mocking.
      - **"Audit report reconstructs a full access history for a given
        claim"**: `phi_access_log` (Phase 3 model, zero writers until now)
        is finally wired into every PHI-bearing read path
        (`api.repository.PostgresRepository.get_finding_detail`,
        `list_packets`, `generate_packet`). `src/db/access_history.py`
        (pure) merges `audit_log` and `phi_access_log` rows into one
        chronological picture; `GET /claims/{claim_id}/access-history`
        (`Action.READ_PHI_ACCESS_LOG`, already AUDITOR/ADMIN-only) exposes
        it, added to the same 4-role authz-matrix discipline as every
        Phase 6/7 endpoint (`tests/api/test_authz_matrix.py`, now 48
        cases) — including the cross-tenant proof that another tenant's
        claim history comes back empty, never a leak.

      Also built: `src/observability/metrics.py` (`dollars_detected`,
      `findings_per_remittance`, `ingestion_latency`, `ingestion_failures`,
      `llm_cost_per_packet`, all wired into real code —
      `ingestion.pipeline.ingest_file`, `ingestion.apply.apply_ingestion_plan`,
      `packets.drafter.AnthropicPacketDrafter` — plus a documented
      `queue_depth` stub, since ingestion is synchronous and there's no
      queue to measure); `src/observability/alerts.py` (5 pure evaluators
      for every alert the prompt names — ingestion failure, eval
      regression, auth anomaly, unusual PHI access volume, cross-tenant
      probe — detection logic only, wiring to a real paging service
      deferred to Phase 9/10, same as every other real-integration
      deferral this build has made); `evals/history.py` (JSONL-based eval
      run history + regression detection, deliberately **not** a DB table
      — eval scores are a build/CI signal, not tenant data, so `make eval`
      stays Postgres-independent).

      **Explicitly not built, named rather than silently skipped**:
      `dollars_recovered`, `recovery_rate_by_cause`, and `time_to_recovery`
      — all three require knowing whether a payer actually paid a finding,
      and no outcome-tracking data model exists anywhere in this codebase.
      `docs/MASTER-BUILD-PROMPT.md`'s own Phase 12 section frames "the
      outcome feedback loop" as work not yet done. This is a data-model
      gap, not a testing gap — revisit once Phase 12 exists.

      **Not yet verified**: one round-trip test
      (`tests/api/test_endpoints_live_db.py::test_viewing_a_finding_shows_up_in_its_claim_access_history`)
      — view a finding (writes `phi_access_log`), fetch the claim's access
      history, confirm it shows up — against real Postgres. Written, skips
      cleanly without `TEST_DATABASE_URL`, never executed. **Do not check
      this phase off until that test is run against a live Postgres.**
- [ ] Phase 9 — Cloud-agnostic deployment — **code complete, verification
      ceiling lower than every prior phase.** This phase's gate needs
      real Docker and real AWS/Azure accounts, not just local Postgres —
      a qualitatively bigger ask than anything gated so far. What's
      genuinely built and verified without any of that:
      - `src/main.py` — the production composition root that didn't
        exist before this phase (`api.app.create_app()` had never been
        wired to a real `PostgresRepository` from environment variables).
        `tests/test_main.py`: missing-env-var errors and successful
        construction both proven for real (`create_engine` doesn't
        connect until first use, so no DB is needed to test this).
      - `GET /healthz` / `GET /readyz` (`src/api/routes/health.py`,
        `Repository.ping()`) — liveness vs. readiness kept deliberately
        separate; fully tested in `tests/api/test_health.py`, plus one
        live-Postgres round-trip in `test_endpoints_live_db.py`.
      - `pyproject.toml` now separates runtime dependencies
        (`[project].dependencies`) from dev/lint tooling (`dev` extra) —
        previously everything lived under `dev` because nothing had
        packaged a runnable production entrypoint yet; this is what lets
        the Docker image's `pip install .` skip pytest/ruff/mypy.
      **Written but explicitly unverified, honestly, not silently**:
      `Dockerfile`/`.dockerignore`/`docker-compose.yml`'s new `app`
      service (no Docker in this environment — the user's plan is to
      verify these in a Codespace); `terraform/modules/{aws,azure}/` and
      `terraform/environments/{aws,azure}/` (no Terraform CLI, and
      `apply` additionally needs real billed cloud accounts — manually
      reviewed line by line, brace/quote-balance-checked, but not
      `terraform validate`-checked); `docs/RUNBOOK.md`'s restore
      procedure (needs a real provisioned database to actually time).
      **Do not check this phase off until**: (1) `docker compose up`
      + `alembic upgrade head` + exercising the API works in the
      Codespace, AND (2) `terraform validate`/`plan`/`apply` succeed
      against real AWS and Azure accounts with an actual restore
      rehearsed and timed — two separate, later milestones, not one.
- [x] Phase 10 — CI/CD and pre-production hardening — **the pipeline
      actually ran, went green, and in the process retroactively verified
      every DB-backed test Phases 3/5/6/7/8 could only ever skip locally**
      (RLS tenant-isolation, idempotent-remittance, effective-dated
      pricing, audit-log append-only, the packet approve round-trip, the
      claim-access-history round-trip). Getting there took 15 follow-up
      commits after the initial push, fixing real bugs the first-ever
      live-Postgres/Docker/Terraform/CI run surfaced — see "the CI
      debugging round" below for the full list; none of these were
      hypothetical, every one was a genuine defect this build had been
      carrying since the phase that introduced it.
      - **`.github/workflows/ci.yml`**: lint (ruff, mypy --strict,
        lockfile-freshness) -> test (real Postgres 16 service container,
        `scripts/db/init_roles.sql`, `alembic upgrade head`, full
        `pytest -q`) -> security (bandit, pip-audit, full-history
        `gitleaks`) -> sbom (CycloneDX artifact) -> container-scan
        (`docker build` + `trivy`) -> iac-scan (`terraform validate` for
        both clouds + `tfsec`).
      - **`.github/workflows/deploy.yml`**: staging -> smoke test -> OWASP
        ZAP baseline -> manual approval gate -> production, AWS and Azure
        as separate job chains, OIDC/federated-credential auth (no static
        keys). Every cloud-dependent job is guarded by
        `if: secrets.* != ''`, so it shows **skipped**, not failed, until
        real AWS/Azure credentials exist — same "no cloud account in this
        environment" ceiling as Phase 9's Terraform, now explicit in the
        pipeline's own status rather than just prose. One-time OIDC setup
        documented in `docs/RUNBOOK.md`.
      - **`.github/workflows/scheduled-security-scan.yml`**: six-monthly
        cron (bandit/pip-audit/trivy), opens an issue on new findings —
        satisfies the 2026 rule's vulnerability-scan cadence. The same
        rule's annual penetration test is named as what it actually is
        (a procurement/process item, Phase 11 scope) rather than faked as
        a workflow step.
      - **Dependency pinning**: `requirements.lock.txt` (`make lock`, via
        `pip-compile`) — genuinely generated and verified locally,
        including confirming it's stable under re-compilation (no
        spurious drift) so the CI freshness check catches real
        `pyproject.toml` drift, not unrelated upstream package releases.
        Discovered and worked around a real `pip-tools`/`pip 25+`
        incompatibility (`pip-tools` reaches into a private pip API that
        25+ removed) — pinned `pip<25` for just that CI step and in the
        Makefile's `lock` target comment, not project-wide. Dockerfile's
        builder stage now installs from the lockfile, not
        `pyproject.toml` directly, for byte-for-byte reproducible builds.
      - **SBOM**: `make sbom` (`cyclonedx-py`) — genuinely generated and
        verified locally; produced fresh as a CI artifact per build, not
        committed (an SBOM describes one build's contents; a committed
        static copy would drift from reality).
      - **Adversarial review**: ran the `adversarial-reviewer` subagent
        across the full `src/` tree, migrations, Terraform, Dockerfile,
        and the new workflow files, per `docs/MASTER-BUILD-PROMPT.md`'s
        explicit requirement. Found 2 HIGH, 5 MEDIUM, 3 LOW findings.
        **Both HIGH findings fixed**:
        1. *PHI columns were never actually encrypted.* The envelope-
           encryption primitive (`EnvelopeEncryptor`, Phase 4) existed but
           had zero call sites anywhere in the write/read path —
           `claims.patient_name`/`patient_member_id` were plain `Text`
           columns. Investigating this also surfaced a deeper bug:
           `ingestion/apply.py` never read `claim.patient` at all, so
           these columns were always `NULL` even though `domain/x835.py`
           correctly parses the NM1*QC segment into `Claim835.patient`.
           Fixed by: renaming the columns to
           `patient_name_encrypted`/`patient_member_id_encrypted`
           (`Text`, holding a JSON-serialized `EncryptedPayload` — see
           `src/security/phi_columns.py`); adding `src/security/kms_env.py`
           (`EnvKMS`, a real-but-weaker stopgap `KeyManagementService`
           reading a static KEK from a secret, wired into `main.py` —
           the real cloud KMS adapter remains a named, deferred gap, same
           as before); threading `EnvelopeEncryptor` through
           `ingestion.apply._apply_claim` (encrypt on write, from the
           already-parsed `claim.patient`), `ingestion.pipeline.ingest_file`,
           and `api.repository.PostgresRepository` (decrypt on the two
           read paths that surface patient info). No new migration
           needed — migration 0001 generates its schema from
           `Base.metadata` via `create_all`, and this has never run
           against a real database, so there was no live schema to
           migrate away from.
        2. *Ingestion wrote no claim/finding-level audit entries.* Only a
           batch-level `remittance_ingested` row existed, so
           `GET /claims/{id}/access-history` could never show a claim's
           own ingestion or its findings being created — a real gap
           against CLAUDE.md rule 5. Fixed in `ingestion/apply.py`:
           `_apply_claim` now writes a `claim_ingested` audit entry per
           claim and a `finding_created` entry per finding.
        One MEDIUM finding fixed opportunistically alongside the above:
        `db.repository.list_findings_by_payer_claim_control_number`
        accepted `tenant_id` but never filtered by it — safe today only
        because every caller runs inside `tenant_session` (RLS-scoped),
        but a latent global-read footgun for any future caller that
        didn't. One more MEDIUM fixed: AWS RDS defaults to allowing
        unencrypted connections (Azure's flexible server does not) —
        added an explicit `rds.force_ssl` parameter group and
        `sslmode=require` on the assembled `DATABASE_URL`
        (`terraform/modules/aws/`). **The remaining 4 MEDIUM and 3 LOW
        findings were triaged, not silently dropped** — see
        `docs/SECURITY.md`'s "Phase 10 adversarial review" section for
        each one and why it's deferred (currency positional validation,
        unwired rate-limiting/lockout/step-up auth, incomplete reversal
        netting, an X12 PLB sign-convention question that needs a TR3
        spec check before touching, undashed-SSN redaction gaps, and a
        minimum-necessary policy question about `VIEWER`'s PHI access).
      - New pure tests (verified now, no DB needed, same pure/DB-split
        discipline as every prior phase):
        `tests/security/test_kms_env.py`, `tests/security/test_phi_columns.py`.
        New DB-backed tests (written, will run for real on the first CI
        push): `tests/db/test_patient_columns_are_encrypted.py` (proves
        the column holds ciphertext, not plaintext),
        `tests/ingestion/test_apply_audit_entry.py`'s new
        claim/finding-audit-entry test, and new assertions in
        `tests/api/test_endpoints_live_db.py` (decrypted patient name
        round-trips through the API; claim-ingestion shows up in access
        history).
      - Full local gate green: `ruff check .`, `mypy --strict .` (137
        files), full suite (392 passed, 23 skipped — all DB-backed
        skips), 100% branch coverage on `domain/variance.py`, eval
        `GATE PASSED`, `bandit` clean, `pip-audit` clean against this
        project's actual dependency tree.
      - **The CI debugging round** — every one of these was a real,
        pre-existing bug (most dating to the phase named), invisible
        until this phase's pipeline gave it a live Postgres/Docker/
        Terraform/CI environment to run in for the first time:
        - `bandit`/`pip-audit` were installed globally on the dev
          machine all along but never declared in `pyproject.toml`'s
          `dev` extra — every local `make security` passed by accident.
        - `requirements.lock.txt`, generated on Windows, pulled in
          `colorama`/`tzdata` that Linux's resolver correctly omits —
          the CI freshness check must regenerate *in place* (not to a
          temp file) or it flags unrelated upstream releases as drift.
        - Azure's `azurerm_key_vault_key.main` was missing
          `notify_before_expiry`, a required companion to `expire_after`
          (Phase 9) — the first real `terraform validate` this HCL ever got.
        - Two gitleaks false positives (synthetic JWT/PHI-encryption test
          secrets) needed a documented `.gitleaks.toml` allowlist.
        - Migrations `0002`-`0004` all collided with `0001`'s
          `create_all()` (which reflects *today's* `db/models.py`, not a
          historical snapshot) — made idempotent, with an
          offline-SQL-generation branch preserved.
        - `aquasecurity/trivy-action@0.28.0` doesn't exist (`v0.36.0` is
          current); `tfsec-action`'s severity-floor input is
          `additional_args`, not the `tfsec_args` first guessed — both
          silently no-op on an unrecognized input name rather than error.
        - First real `tfsec` run: 8 CRITICAL/6 HIGH, resolved with a mix
          of real fixes (narrowed security-group egress, Key Vault
          network ACL, `drop_invalid_header_fields`) and justified
          `#tfsec:ignore` comments for what's inherent to a public API.
        - `tests/ingestion/conftest.py::seed_tenant_with_contract` (Phase
          5) created its contract outside `tenant_session`, failing
          against real RLS with "unrecognized configuration parameter
          app.tenant_id".
        - The observability test's shared contract fixture priced 99213
          below what the 835 reports as allowed, producing a *negative*
          shortfall that `record_ingestion_outcome` correctly never
          reports as `dollars_detected` (Counters can't go negative) —
          the test's own assumption was wrong, not the metrics code.
        - `_auth_headers_as(subject, Role.ADMIN)` minted a token claiming
          a role that disagreed with the subject's actual DB-assigned
          role — `api/auth.py::get_auth_context` deliberately 401s on
          that mismatch (a real safeguard, working as designed).
        - Trivy flagged `setuptools`/`msgpack` at stale versions even
          after `--force-reinstall` confirmed patched versions on disk —
          suppressed via `.trivyignore` as a documented tool quirk (this
          Trivy is one version behind current) after two remediation
          attempts both provably fixed the actual image.
        - The claim-access-history test checked `event["action"] ==
          "finding_detail_view"`, but PHI-access-log-sourced events
          always carry the generic literal `action="phi_access"` — the
          specific reason lives in `purpose`. A Phase 8 test bug that
          could never have matched any real event.
- [ ] Phase 11 — Real data readiness (the compliance gate — no code) —
      **drafting complete, the actual gate is nowhere near closed.** Per
      `docs/MASTER-BUILD-PROMPT.md`, this phase is a 15-item checklist —
      BAAs, a Security Risk Analysis, an incident response plan, a breach
      notification procedure, workforce training, insurance, a third-party
      penetration test, legal review — none of which can be *completed*
      by writing files. New `docs/compliance/` directory: for every item
      groundable in this system's already-built, already-verified
      technical controls (`docs/SECURITY.md`'s control matrix,
      `docs/RUNBOOK.md`'s operational procedures, `terraform/`'s actual
      infrastructure), wrote a real starting document, explicitly marked
      as an engineering draft pending legal/compliance sign-off — not a
      blank template, but not adopted policy either. For the items that
      are purely external actions (signing a vendor's own BAA, buying
      insurance, hiring a pentest firm), `docs/compliance/README.md`
      states exactly what needs to happen and who to involve, with no
      item marked done that isn't. **Do not check this phase off, and do
      not start Phase 12, until every one of the 15 items in
      `docs/compliance/README.md`'s tracker reads DONE with real
      evidence** — a signed contract, a purchased policy, a completed
      engagement report, a documented drill, not a drafted document
      however good.
      - `docs/compliance/SECURITY-RISK-ANALYSIS.md` — restructures
        `docs/SECURITY.md`'s control matrix into risk-analysis form
        (threats × existing controls × residual risk × remediation
        priority). Highest-priority open item it surfaces: rate limiting
        and account lockout exist and are tested but aren't wired into
        any route — the single highest residual-risk finding.
      - `docs/compliance/INCIDENT-RESPONSE-PLAN.md` — expands
        `docs/RUNBOOK.md`'s existing 4-step outline into the full
        Identify/Contain/Assess/Eradicate/Recover/Lessons-learned cycle,
        a severity classification, and a contact-tree template. Every
        name field is deliberately blank — filling those in and running
        a tabletop exercise is explicitly called out as required before
        this is usable, not just written.
      - `docs/compliance/BREACH-NOTIFICATION-PROCEDURE.md` — the
        60-day clock, a reportability decision tree, and the notification
        recipient/deadline table (including the easy-to-miss detail that
        a signed BAA's own notification deadline to the customer is often
        shorter than the statutory 60 days). Explicitly does not authorize
        sending any actual notification.
      - `docs/compliance/DATA-RETENTION-SCHEDULE.md` — documents
        retention periods that are already true facts about the running
        system (S3 lifecycle, RDS backup retention, log retention, token
        TTLs), separately from the genuinely undecided destruction
        procedure (this system currently has no hard-delete path at all).
      - `docs/compliance/SECURITY-QUESTIONNAIRE-ANSWERS.md` — the
        lowest-risk document in this phase, since it just restates
        already-true `docs/SECURITY.md` facts in customer-questionnaire
        form — while still honestly flagging what's not yet true (no
        signed BAAs, no completed pentest) rather than glossing over it.
      - `docs/compliance/BUSINESS-CONTINUITY-DR-PLAN.md` — documents
        the DR mechanism Phase 9 already built, proposes RPO/RTO targets
        for the business to confirm, and is explicit that a plan with no
        rehearsed restore is documented, not tested.
      - `docs/compliance/SANCTION-POLICY.md` — a standard,
        right-sized progressive-discipline template for HIPAA workforce
        violations, requiring HR/legal adoption.
      - `docs/compliance/README.md` — the master tracker: all 15
        checklist items from the master prompt, each with a status and a
        concrete next action — the asset-inventory/network-map item is
        explicitly marked blocked on Phase 9's real infrastructure (a
        template filled with placeholder infrastructure would be
        fiction), not silently skipped.
- [ ] Phase 12 — First customer pilot
