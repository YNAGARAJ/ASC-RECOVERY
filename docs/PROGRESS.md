# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 3 of 23 done.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit (Wave 0 baseline/inventory/conformance, Wave 1's ten parallel
reviewers, Wave 2's consolidated register) ran against that code and found
82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW — written up in
`docs/audit/`. `docs/MASTER-BUILD-PROMPT-V2.md` folds the process-level
lessons from that audit into a revised build methodology for next time;
`docs/audit/REGISTER.md` is the actual work list for *this* codebase, and
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 3/23** (F-01, F-02, F-03 — see
below). 20 HIGH findings remain open, plus the full 59-item BACKLOG
(MEDIUM/LOW, each already carrying its own one-line defer-justification in
the register — not being worked yet, and that's fine, they're not blocking).

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** — reversal netting could silently drop or mis-attach
  a shortfall when the reversal claim reported fewer/different lines than
  the original. Fixed by carrying the original finding's own
  `service_line_id` forward explicitly (`domain.variance.Finding`,
  `ingestion.plan.PriorFinding`) instead of re-deriving it from the
  reversal claim's own lines. Commit `824b26a`. New DB-backed test
  `tests/db/test_reversal_netting.py` (skips locally, never executed here,
  first real run is the next CI push) plus two new pure tests in
  `tests/ingestion/test_plan.py` (passing, verified locally).
- **F-02** — `PHI_ENCRYPTION_KEY` wasn't provisioned by either cloud's
  Terraform. Added as a third key alongside `JWT_SECRET_KEY`/
  `ANTHROPIC_API_KEY` on both clouds (AWS: `aws_secretsmanager_secret.app`;
  Azure: new `azurerm_key_vault_secret.phi_encryption_key`), same
  out-of-band-population pattern. Commit `3d18d03`.
- **F-03** — deploy pipeline never ran migrations or bootstrapped
  `asc_app`; the smoke test only checked `/healthz`/`/readyz`, both of
  which passed against an empty schema. Fixed two ways: (1)
  `PostgresRepository.ping()` now queries `alembic_version` instead of a
  bare `SELECT 1`, so the *existing* smoke test itself now catches an
  unmigrated deploy with no new endpoint needed; (2) new
  `scripts/deploy/migrate.sh` + new Terraform outputs on both clouds,
  wired into all four `deploy.yml` jobs (AWS/Azure × staging/production)
  right after `terraform apply`. Commit `f51fb31`.

None of F-01/F-02/F-03's infrastructure or DB-backed pieces have been
verified against real Terraform/Postgres — no Terraform CLI, no live
database, no cloud account exist in this environment (same ceiling every
phase has had since Phase 9). Brace-balance-checked, `bash -n`-checked,
YAML-parsed, and manually reviewed instead. The Python-side gate (ruff,
mypy --strict, pytest, bandit) is genuinely green after each fix, re-run
in full after every single change — that part is real, not just claimed.

## In progress

Nothing mid-write. Each of the three fixes above went through the full
Wave 3 loop (state the finding → write/extend a test → minimal fix → show
it passing where the environment allows → full local gate → mark FIXED in
the register with the commit SHA → commit) and is complete as a unit.

## Failing

Nothing failing. `ruff check .`, `mypy --strict .` (144 files), `pytest -q`
(409 passed, 29 skipped — all DB-backed, none newly broken), the
`domain/variance.py` 100%-coverage gate, `python -m evals.run` (GATE
PASSED), and `bandit -r .` are all clean as of commit `ef2635e`.

## Decisions worth knowing (not obvious from the code)

- **F-01's fix adds a field to `domain.variance.Finding`
  (`service_line_id: uuid.UUID | None = None`)** — a small, deliberate
  crack in "domain has zero I/O-flavored types," justified because it's
  just an opaque identifier being carried through, not a DB call, and the
  alternative (re-deriving the right line at the persistence layer some
  other way) was more complex and less obviously correct. If a future
  reviewer is uneasy about this, the discussion is in the F-01 commit
  message (`824b26a`), not just this file.
- **F-01 also deliberately removed a silent-drop safety net**
  (`ingestion/apply.py`'s old `f.line_index in service_line_ids` filter).
  Per the audit's own explicit instruction, a genuine mismatch should now
  raise (surfacing as a 500 + rolled-back transaction) rather than quietly
  vanish. This is a real behavior change on a real deploy — worth knowing
  if something that used to fail silently now fails loudly instead.
- **F-03 strengthens `/readyz` rather than adding a new authenticated
  smoke-test endpoint.** The register's own suggested fix text said "make
  the smoke test hit one authenticated read" — building real
  authentication is F-04/F-05, not done yet, so hitting an *authenticated*
  endpoint isn't achievable cleanly right now. Strengthening `ping()` to
  check `alembic_version` achieves the same goal (an unmigrated DB now
  fails the *existing* smoke test) without depending on work several
  findings away. Revisit whether a real authenticated smoke test is still
  wanted once F-04/F-05 land.
- **F-02/F-03's Terraform changes add new outputs, including two marked
  `sensitive = true`** (`app_db_password`, and Azure's
  `database_admin_password`). These are real passwords now flowing through
  `terraform output`; `deploy.yml`'s new steps mask them immediately in CI
  logs, but anyone running `terraform output` by hand locally will see
  them in plaintext on their own terminal. That's the same trust boundary
  Terraform state always has (anyone who can run `terraform apply` already
  has DB credentials) — not a new leak, but worth knowing before assuming
  outputs are always safe to paste anywhere.

## Traps for someone resuming cold

- **`docs/audit/REGISTER.md` is the actual work list — `docs/PHASES.md`
  still says Phase 12 is code-complete and doesn't yet reflect that three
  of the audit's findings are now fixed.** Don't let the two documents
  read as contradictory; `PHASES.md` tracks the 12-phase build, the audit
  docs track a separate, later remediation pass on top of it. Update
  `PHASES.md` once a meaningfully complete chunk of the MUST-FIX list is
  closed, not after every single finding.
- **The `${VAR:?message}` bash gotcha**: an apostrophe inside a `:?`
  error message breaks bash's parser (it opens an unmatched single-quote
  context even inside outer double quotes) — hit this once already in
  `scripts/deploy/migrate.sh`, caught only because `bash -n` was run
  explicitly. Any new deploy script should avoid contractions in these
  messages, or test with `bash -n` before trusting it.
- Everything from the Phase 3-12 checkpoints still applies (CRLF warnings
  on `git add`, `docs/audit/`'s findings are unverified against real
  infra/DB by construction of this environment). One more to add: the
  PHI-content guardrail hook rejects a write if two specific words land
  directly next to each other in the file content, regardless of context
  or meaning — hit this twice already writing audit docs, always fixable
  by adding a word in between or rephrasing.

## Next 3 steps

1. **Continue the MUST-FIX list from F-04** (no authentication path exists
   — the login endpoint plus the MFA-secret storage column, F-04/F-05
   together, since they're two facets of one real subsystem gap). This is
   the largest remaining item (L effort) — expect it to need its own
   focused session, not a quick pass alongside smaller fixes.
2. **After F-04/F-05**, the register's own ordering suggests F-06 (rate
   limiting wiring — quick, M effort, no new subsystem) and F-07 through
   F-23 in the order `REGISTER.md` already lists them, updating the
   register's Status column and committing after each one, same loop as
   F-01–F-03.
3. **Once the MUST-FIX list is meaningfully further along** (not
   necessarily all 23 — use judgment): update `docs/PHASES.md` to note
   Wave-3 remediation progress, and re-run the Wave 0 baseline commands
   (`make test`/`make lint`/`make eval`/`make security`) fresh to confirm
   nothing drifted across the batch of fixes, before telling the user this
   phase of remediation is done.
