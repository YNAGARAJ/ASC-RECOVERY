# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint. Read `docs/PHASES.md` first for
the phase checklist, then `docs/audit/REGISTER.md` for the full findings
list this checkpoint is tracking progress against — this file adds the
texture that isn't in either.

## IMPORTANT — two-stage work order for this repo, read before anything else

The user confirmed a **two-stage work order** (also saved as a project
memory, `project_roadmap_scope` — re-derive from here if it isn't in
context):

1. **Finish Wave 3 remediation first** — close `docs/audit/REGISTER.md`'s
   remaining MUST-FIX rows, in the register's own listed order.
2. **Then build the unbuilt product-completeness gaps** listed at the top
   of `docs/MASTER-BUILD-PROMPT-V2.md`'s "PART 3 — GAP REGISTER" —
   frontend, async jobs, lesser-of/stop-loss/prompt-pay-interest contract
   logic, SSO/SCIM, multi-org hierarchy, reprocessing, and more. Entirely
   unbuilt features, not bugs. Don't start this while register items
   remain open.

**Established pattern from F-17 and F-18 (both from prior sessions)**:
not every MUST-FIX row is a clean wiring fix. Some findings' "real" fix
requires inventing infrastructure/data storage that was never part of
any of the 12 original phases. **When a finding looks like this, ask the
user how to scope it before proceeding** — don't guess. F-20/F-21 (this
session) both hit this squarely: real cloud KMS accounts and a real
provisioned database don't exist in this environment, so "the real fix"
and "what's actually buildable here" are different things — asked the
user for both before writing code, see "Decisions" below for what was
chosen.

## Phase: Wave 3 remediation (docs/AUDIT-PROMPTS.md), against docs/audit/REGISTER.md's MUST-FIX list. 19 of 23 fully closed, 1 more partially addressed.

Phases 1-12 are code-complete (see `docs/PHASES.md`). A full three-wave
audit found 82 real defects — 1 CRITICAL, 22 HIGH, 34 MEDIUM, 25 LOW —
written up in `docs/audit/`. `docs/audit/REGISTER.md`'s 23-row MUST-FIX
table (all CRITICAL/HIGH) is the actual work list for *this* codebase;
that's what this checkpoint tracks.

**Percentage of the MUST-FIX list closed: 19/23 FIXED** (F-01 through
F-16, F-18, F-19, F-20). **F-21 is partially addressed but stays OPEN**
(the drill script exists; it's never been run for real). **F-17 remains
deliberately open** (see prior checkpoints — no purchasing-feed
integration exists to thread real `invoice_cost` through). **F-22, F-23
remain OPEN**, both explicitly last per the register's own ordering.

## Done (fixed this session, one line each on the actual change)

- **F-01 (CRITICAL)** through **F-19** — see git log and prior
  checkpoints for detail; unchanged this session.
- **F-20 (HIGH, this session)** — `AwsKmsAdapter`
  (`src/security/kms_aws.py`) and `AzureKeyVaultAdapter`
  (`src/security/kms_azure.py`) implemented behind the existing
  `KeyManagementService` port (`security/kms.py`). Wired into
  `main.py`'s new `_build_kms()`, selected by a new `KMS_PROVIDER` env
  var (`"env"`/unset → today's `EnvKMS`, unchanged default;
  `"aws-kms"` → `AwsKmsAdapter`, requires `AWS_KMS_KEY_ID`;
  `"azure-keyvault"` → `AzureKeyVaultAdapter`, requires
  `AZURE_KEY_VAULT_KEY_ID`). Commit `02c6e38`, register updated in
  `5ba2b60`.
  - **AWS**: `key_id` is meant to be a KMS *alias*
    (`alias/asc-recovery-kek`), not a raw key id — aliases are what let
    AWS's own automatic annual rotation (`enable_key_rotation = true`,
    already in Terraform) take effect with zero redeploys, since the
    alias reference stays valid across rotation.
  - **Azure**: fundamentally asymmetric with AWS here, documented at
    length in `kms_azure.py`'s docstring — a Key Vault key *version* is
    a distinct cryptographic object, not an alias. `current_kek_id()`
    is a *pinned* version id; picking up a rotation means redeploying
    with a new `AZURE_KEY_VAULT_KEY_ID` and running
    `EnvelopeEncryptor.rotate_kek()`, not something that happens
    automatically. `wrap_key` rejects any `kek_id` other than the
    pinned current version; `unwrap_key` deliberately does not, since
    it must keep working for a DEK wrapped under an older version
    that's still enabled in the vault (the per-payload `kek_id` on
    `EncryptedPayload` is exactly what makes that safe).
  - `AzureKeyVaultAdapter` takes an injectable `crypto_client_factory`
    (defaulting to a real lazy-importing factory) specifically so it's
    unit-testable without the real `azure-keyvault-keys` SDK installed.
    The wrap algorithm is passed as the literal string `"RSA-OAEP-256"`
    rather than importing `KeyWrapAlgorithm.rsa_oaep_256`, so the
    module has zero SDK imports at class-definition time.
  - `boto3`/`azure-identity`/`azure-keyvault-keys` added as a new
    `cloud-kms` optional-dependency group (`pyproject.toml`) — not a
    base dependency; neither is installed in this dev environment,
    which is itself the proof the lazy-import design works (tests pass
    with neither package present).
  - Tests: `tests/security/test_kms_aws.py` (5 tests, fake boto3-shaped
    client), `tests/security/test_kms_azure.py` (5 tests, fake
    Key-Vault-crypto-client via `crypto_client_factory`),
    `tests/test_main.py` (+5 tests: unset/`"env"` default, invalid
    `KMS_PROVIDER` value, missing `AWS_KMS_KEY_ID`/
    `AZURE_KEY_VAULT_KEY_ID` — both provider branches only test the
    pre-SDK-import validation path, since `boto3`/`azure-identity`
    genuinely aren't installed here; that's consistent with every other
    real-cloud adapter in this codebase, not a shortcut specific to
    this fix).
  - **Neither adapter has ever been exercised against a real AWS
    account or Azure Key Vault** — no live credentials exist in this
    build environment. This is disclosed in both modules' own
    docstrings, matching the SFTP/S3 poller adapters from F-18.
  - Documented in `docs/RUNBOOK.md`'s "Key rotation" section and
    `docs/SECURITY.md`'s control matrix + "Not yet built" list.
- **F-21 (HIGH, this session) — partially addressed, register entry
  stays OPEN.** `scripts/db/restore_drill.sh` automates every step of
  `docs/RUNBOOK.md`'s "Restore (backup verification)" procedure for
  either cloud: restore the latest automated backup to a throwaway
  instance (`aws rds restore-db-instance-to-point-in-time` /
  `az postgres flexible-server restore`), wait for it, verify
  `alembic_version` matches head, print row counts on
  `claims`/`findings`/`recovery_packets`, confirm RLS
  (`relrowsecurity`) survived the restore on all three, print the
  elapsed wall-clock time, tear the instance down via a `trap` (so a
  failed verification still cleans up, `SKIP_TEARDOWN=1` escape hatch
  for manual inspection). Commit `7397b3d`, register updated in
  `5ba2b60`.
  - **User explicitly chose to leave this OPEN** rather than mark it
    FIXED: the finding's actual requirement is "execute this for real
    and record the actual wall-clock number," and no AWS/Azure account
    exists in this environment to do that. Writing the script closes
    "the procedure only exists as prose someone has to manually
    transcribe" — it does not, and cannot, close the finding itself.
  - Shell-syntax-checked (`bash -n`) only — never actually run, by
    construction (no cloud account, no live database).

## In progress

Nothing mid-write. Both F-20 and F-21 went through the full Wave 3 loop
(re-read the full row → ask the user to scope, since both hit the
"needs infrastructure never built in any phase" pattern → implement →
test → full local gate → register update with commit SHAs → commit) and
are complete as units, in the state the user asked for.

## Failing

Nothing failing that this session caused. `ruff check .`, `mypy --strict .`
(171 files), `pytest -q` (495 passed, 35 skipped — all DB-backed, none
newly broken), the `domain/variance.py` 100%-coverage gate
(`pytest -q --cov --cov-report=` then
`coverage report --include="*/domain/variance.py" --fail-under=100`),
`python -m evals.run` (GATE PASSED, 100% recall/precision/root-cause/dollar
accuracy on 504 golden cases), and `bandit -r . -x ./tests,./evals` are all
clean as of commit `5ba2b60`.

**Pre-existing, unrelated, still present:** `mypy --strict .` reports two
errors in `alembic/versions/0004_recovery_packets_and_timely_filing.py:65,82`
(`Call to untyped function "JSONB" in typed context`). Predates Wave 3
entirely. Still not blocking, still out of scope — flagging again so it
doesn't get silently attributed to a future fix by mistake.

## Decisions worth knowing (not obvious from the code)

- **F-20 and F-21 both got a two-question `AskUserQuestion` before any
  code was written**, per the established F-17/F-18 pattern. Both times
  the user picked the "build what's buildable, be honest about what
  isn't, opt-in only" option over the alternatives (skip entirely, or
  force a fake success):
  - F-20: **"Build both adapters, opt-in only via env var"** — not
    "build one," not "leave it deferred," not "make it the new
    default."
  - F-21: **"Build the restore-drill script, leave finding open"** —
    not "mark it FIXED since the script exists," not "skip the script
    since it can't be run here."
- **`KMS_PROVIDER` defaults preserve F-04-through-F-19's status quo
  exactly.** No existing deployment env or test fixture needed to
  change for this fix to land — `_build_kms()`'s `"env"` branch is
  byte-for-byte the same `EnvKMS(secrets)` construction `main.py` did
  before this session, just factored out. This was a deliberate
  constraint going in, not a discovery.
- **`AzureKeyVaultAdapter`'s first draft was not unit-testable** —
  initial version imported `CryptographyClient`/`KeyWrapAlgorithm`
  lazily *inside* `wrap_key`/`unwrap_key`, which meant even a
  fake-client-based test would hit a real SDK import line. Redesigned
  with an injectable `crypto_client_factory` constructor parameter and
  the wrap algorithm as a literal string instead of an enum import —
  worth knowing if another adapter in this codebase ever needs the same
  "lazy-import SDK, but still unit-testable" treatment.
- **`restore_drill.sh` restores to "use latest restorable time," not a
  named snapshot** — matches what an actual disaster-recovery restore
  would use (latest automated backup + WAL replay), not a specific
  point that could be stale by the time someone runs the drill.

## Traps for someone resuming cold

- **Read the "IMPORTANT" section at the top of this file before doing
  anything else** — the two-stage work order, and the reminder that
  "ask before assuming new infrastructure is in scope" is now confirmed
  by three separate findings (F-17, F-18, F-20/F-21), not just one.
- **Everything F-01 through F-19's checkpoints already flagged still
  applies** (CRLF warnings on `git add`, `docs/audit/`'s findings
  unverified against real infra/DB by construction of this environment,
  the `${VAR:?message}` bash-apostrophe gotcha, the PHI-content
  guardrail hook's quirks, the Makefile's `domain/variance.py`-only
  coverage gate, `mypy --strict .` actually sweeping the whole repo
  (not just `src`+`tests`) because of the `.` CLI argument — this is
  exactly why `kms_aws.py`/`kms_azure.py` needed their own
  `[[tool.mypy.overrides]]` entries even though `boto3`/`azure-identity`
  are never installed here, remembering
  `dependencies=[Depends(enforce_rate_limit)]` on any new authenticated
  router, remembering `api.alerting.record_not_found(...)` on any new
  direct-id-lookup route, `required_figure_lines()` on any new scripted
  packet-draft fixture, `FakeRepository`'s audit-write gaps, OTel's
  global-provider write-once-per-process rule, and re-reading a
  finding's *full* row — not just its "Fix" column — once more right
  before marking it FIXED).
- **If a real AWS or Azure account ever becomes available**, the very
  first useful thing to do with it is run `scripts/db/restore_drill.sh`
  for real (closes F-21) and separately exercise `KMS_PROVIDER=aws-kms`
  / `KMS_PROVIDER=azure-keyvault` end to end against a real KMS/Key
  Vault (upgrades F-20 from "built and unit-tested" to "verified") —
  neither has ever touched real infrastructure, and both docstrings say
  so explicitly.
- **`pyproject.toml` now has two extras that both pull in `boto3`**
  (`[poller]` from F-18, `[cloud-kms]` from F-20) — deliberately not
  factored into a shared group, since they're for unrelated concerns
  (SFTP/S3 polling vs. KMS) that happen to both touch AWS.
  `pip install -e ".[poller,cloud-kms]"` if a real environment ever
  needs both.

## Next 3 steps

1. **F-22 and F-23 are next per `REGISTER.md`'s order.** F-22 (every
   cross-seam integration test gated behind `TEST_DATABASE_URL`, so
   this environment's runnable suite proves no integration behavior) is
   itself a real-infrastructure-shaped finding — read its full row
   before assuming there's code to write; it may turn out to be another
   "ask the user how to scope it" case, or it may turn out to be
   genuinely just a Docker-based dev-container Postgres setup task.
2. **F-23 must come last, after F-01 through F-22 are triaged** — it
   literally depends on that (re-running the `adversarial-reviewer`
   subagent against a tree where every other finding has a final
   disposition).
3. **Once F-22/F-23 land, the MUST-FIX list is as closed as this
   environment allows** (19 FIXED, F-17/F-21 honestly partial/open on
   infrastructure grounds, F-22/F-23 whatever they turn out to be).
   That's the point to explicitly check in with the user about how
   close is "close enough" before declaring Wave 3 done and starting
   stage 2 (the unbuilt product-completeness gaps in
   `docs/MASTER-BUILD-PROMPT-V2.md`) — per the IMPORTANT section, don't
   assume.
