# Audit — Wave 1, Agent 2: Hardcoded values

Read-only. No application code was modified to produce this file.

**Scope:** every literal in `src/` (46 files, 7,035 lines) tested against the
three-bucket taxonomy: **Config/env** (differs per deployment), **Metadata
table** (differs per tenant/payer/contract), **Correctly in code** (fixed by an
external standard, but must be a *named* constant not an inline magic value).
The test applied throughout: *"would a different customer, payer, or deployment
ever need a different value?"*

**Headline:** the pricing/contract engine — the highest-money-risk area — is
**clean**: every rate, percentage, carve-out term, case rate, and fee-schedule
amount is injected as `ContractVersion` metadata, never a literal. No CRITICAL
or HIGH findings. The findings below are Config/env values that are hardcoded
(mostly as named constants or default parameters) but not wired to any
configuration source, plus one metadata default (timely-filing days) that can
silently produce a wrong appeal deadline on a fallback path.

**Totals:** CRITICAL 0 · HIGH 0 · MEDIUM 4 · LOW 5.

---

## Clean areas (checked, and genuinely fine)

- **Pricing / contract rule constants** (`domain/contract.py`, `domain/money.py`,
  `domain/variance.py`) — **CLEAN.** All pricing inputs (MPPR second/third rates,
  bilateral `total_rate`, assistant-surgeon rate, implant carve-out code sets,
  case-rate flat amounts, percent-of-charge rate, per-code fee schedule) arrive
  as fields on the `ContractVersion` dataclass, populated from the
  `contract_versions` / `fee_schedule_lines` tables — exactly the metadata-table
  home the taxonomy prescribes. The only bare literals are `"50"` (the X12
  bilateral modifier — fixed by standard), MPPR `rank == 2` / `rank >= 3`
  (fixed by the MPPR standard), `_VARIANCE_TOLERANCE = Money("0.01")` (a named
  constant equal to one cent — currency precision, fixed by arithmetic), and
  `TWO_PLACES` / `CENTS_PER_UNIT` in `money.py` (named, fixed by currency
  arithmetic and `ROUND_HALF_UP`). Nothing to move.
- **X12 / CARC / claim-status / crypto / regex constants** — **CLEAN.** X12
  status handling in `domain/x835.py`, the EICAR signature in
  `ingestion/virus_scan.py` (`EICAR_TEST_STRING`), the SSN/MBI regexes in
  `security/redaction.py` (`_SSN_PATTERN`, `_MBI_PATTERN`), the currency regex in
  `packets/currency.py`, AES-GCM sizes in `security/encryption.py` /
  `security/kms_env.py` (`_DEK_BITS = 256`, `_NONCE_LENGTH_BYTES = 12`,
  `_KEK_LENGTH_BYTES = 32`), and `_ALGORITHM = "HS256"` in `security/session.py`
  are all fixed by an external standard **and** already expressed as named
  constants. Correctly in code — do not flag, do not move.
- **Infrastructure connection details** — **CLEAN.** No database URL, bucket
  name, hostname, port, credential, or API key is hardcoded anywhere in `src/`.
  `main.py` reads `DATABASE_URL`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`,
  `PHI_ENCRYPTION_KEY`, and `OTEL_EXPORTER_OTLP_ENDPOINT` from the environment
  via `EnvSecretStore`, and every adapter is injected. This is the one area
  people most expect to fail an audit; it passes.

---

## MEDIUM

### [MEDIUM] Timely-filing fallback of 90 days is hardcoded and can produce a wrong appeal deadline
- **File:** `src/api/repository.py:62` (`_DEFAULT_TIMELY_FILING_DAYS = 90`), used at `src/api/repository.py:658`; mirrored as a DB column default at `src/db/models.py:100` (`server_default=text("90")`)
- **What breaks:** `generate_packet` computes the appeal deadline as
  `date_of_service + timely_filing_days`. The correct value comes from the
  per-contract `contracts.timely_filing_days` column, but when a finding has no
  `contract_version_id` (unpriced/undetermined findings, or any finding whose
  contract link is null), the code silently falls back to 90 days. If the payer's
  real timely-filing window is shorter than 90 days, the generated packet and any
  worklist ranking will show a deadline *later* than the true one — the ASC
  believes it still has time, misses the real window, and permanently loses the
  ability to recover that underpayment (real money, silently). If it is longer,
  appeals are filed with false urgency. The value is also duplicated in two
  places (code + migration) with no single source of truth, so they can drift.
- **Reproduce:** create a finding with `contract_version_id = NULL` for a payer
  whose contract row has `timely_filing_days = 60`, call
  `POST /findings/{id}/packet`, and observe the deadline computed at 90 days from
  DOS rather than 60.
- **Fix:** **Metadata table.** Timely-filing days is explicitly a per-payer
  metadata value (the taxonomy lists "appeal deadline windows, timely-filing
  days"), and the `contracts.timely_filing_days` column is already the right
  home. The defect is the *hardcoded 90-day fallback*: findings without a linked
  contract should not silently receive a payer-agnostic guess. Either resolve the
  contract via `payer_id` even when `contract_version_id` is null, or refuse to
  compute a deadline (surface "unknown deadline") rather than inventing 90 days.
  If a single global default must exist, it belongs in config with one source of
  truth, not duplicated between code and migration.
- **Effort:** M

### [MEDIUM] LLM model name, token cap, and price table are hardcoded and not deployment-configurable
- **File:** `src/packets/drafter.py:22-32` (`DEFAULT_MODEL = "claude-sonnet-5"`, `DEFAULT_MAX_TOKENS = 1024`, `_PRICING_PER_MILLION_TOKENS`, `_DEFAULT_PRICING`)
- **What breaks:** `main.py:95` constructs `AnthropicPacketDrafter(anthropic_api_key, ...)`
  without passing `model` or `max_tokens`, so the compiled-in `DEFAULT_MODEL`
  and `DEFAULT_MAX_TOKENS` are the only values a production deployment can use.
  When the provider deprecates or renames the model, every packet-draft call
  starts returning API errors and no packet can be generated until code is
  changed and redeployed — a production outage that a config change should have
  fixed in seconds. The `_PRICING_PER_MILLION_TOKENS` rates ($3.00/$15.00 per
  million tokens) are self-described in the code comment as "illustrative"; they
  feed the `llm_cost_usd` metric, so any real cost/budget alerting is computed
  from stale, non-deployment-specific numbers.
- **Reproduce:** grep `main.py` for `AnthropicPacketDrafter(` — confirm no `model=`
  argument and no `os.environ` read for a model name; the constructor default is
  the only reachable value in production.
- **Fix:** **Config/env.** Model name, endpoint, max-tokens, and the per-model
  price table all "differ per deployment" (the taxonomy names "LLM model names
  and endpoints"). Read `LLM_MODEL` / `LLM_MAX_TOKENS` (and ideally a pricing
  source) from the environment in `main.py` and pass them through, keeping the
  current values only as documented `.env.example` defaults. Named constants are
  fine as defaults; the gap is that nothing wires them to configuration.
- **Effort:** M

### [MEDIUM] Session/token lifetimes are hardcoded, not driven by deployment security policy
- **File:** `src/security/session.py:37-39` (`ACCESS_TOKEN_TTL = timedelta(minutes=15)`, `REFRESH_TOKEN_TTL = timedelta(days=7)`, `REAUTH_MAX_AGE = timedelta(minutes=5)`)
- **What breaks:** Access-token lifetime, refresh-token lifetime, and the
  re-auth window for sensitive actions (e.g. PHI export) are compiled in. A
  customer or compliance regime that requires, say, a 5-minute access token or a
  24-hour refresh token cannot get it without a code change and redeploy. Too-long
  a TTL is a security exposure (a stolen token stays valid longer); too-short
  degrades UX. These are exactly the knobs a security review or a specific BAA
  will want to tune per deployment.
- **Reproduce:** inspect the three module-level `timedelta` constants; no
  environment variable or config object feeds any of them.
- **Fix:** **Config/env.** Session timeouts "differ per deployment" (taxonomy:
  timeouts). Source them from config (`ACCESS_TOKEN_TTL_MINUTES`,
  `REFRESH_TOKEN_TTL_DAYS`, `REAUTH_MAX_AGE_MINUTES`) with the current values as
  defaults. They are already named constants, which is correct; only the wiring
  to configuration is missing.
- **Effort:** S

### [MEDIUM] API rate-limit capacity/refill are hardcoded at module import
- **File:** `src/api/rate_limit.py:13` (`_limiter = InMemoryTokenBucketRateLimiter(capacity=60, refill_per_second=1.0)`)
- **What breaks:** The per-user request budget (60 requests, refilling 1/sec) is
  a module-level literal fixed at import time. Different deployments and tenants
  legitimately need different rate limits (a bulk-ingestion customer vs. a
  read-only auditor). Changing it requires editing source. (Note: this module is
  also currently an orphan — `enforce_rate_limit` is never wired to a route, per
  `00-inventory.md` — so today the limit is inert; but when it is wired, the
  value must not be a literal.)
- **Reproduce:** open `src/api/rate_limit.py`; the limiter is constructed with
  inline numeric arguments and no config lookup.
- **Fix:** **Config/env.** Rate limits "differ per deployment" (taxonomy:
  rate limits). Read `RATE_LIMIT_CAPACITY` / `RATE_LIMIT_REFILL_PER_SECOND` from
  config and construct the limiter from them. This is the one place where the
  value is an *inline* magic literal rather than a named constant, which is the
  stricter version of the violation.
- **Effort:** S

---

## LOW

### [LOW] Alert thresholds are hardcoded default parameters with no config source
- **File:** `src/observability/alerts.py:39` (`max_rate=0.10`), `:57` (`max_drop=0.02`), `:77` (`threshold=5`), `:92` (`threshold=50`), `:107` (`threshold=10`)
- **What breaks:** Ingestion-failure rate, eval-regression drop, auth-anomaly
  count, unusual-PHI-access volume, and cross-tenant-probe count all carry
  hardcoded default thresholds. A high-volume tenant will trip the PHI-access
  alert (50) constantly; a low-volume one will never trip the auth-anomaly alert
  (5) even during a real attack. Wrong thresholds either bury real signals or
  drown responders in false positives. Impact is currently limited because the
  module is only consumed by `evals/` tooling and not wired to a live pager.
- **Reproduce:** inspect the default keyword arguments on each `evaluate_*`
  function; none is fed from configuration.
- **Fix:** **Config/env.** Alert thresholds "differ per deployment." They are
  already named keyword parameters (good — a caller *can* pass them), so the fix
  is to have the eventual production caller read them from config rather than
  accept the compiled-in defaults. No structural change to this module needed.
- **Effort:** S

### [LOW] Account-lockout policy defaults are hardcoded
- **File:** `src/security/rate_limit.py:87-88` (`max_failures=5`, `lockout_seconds=900`)
- **What breaks:** Lockout threshold (5 failures) and cooldown (15 minutes) are
  compiled-in defaults. Different deployments' security policies want different
  values; too lax weakens brute-force protection, too strict enables lockout-based
  denial of service. (Also currently an orphan per `00-inventory.md` —
  `AccountLockoutTracker` is never constructed in production.)
- **Reproduce:** inspect the constructor defaults; no config feeds them.
- **Fix:** **Config/env.** Named parameters already exist; wire the production
  construction site to `LOCKOUT_MAX_FAILURES` / `LOCKOUT_SECONDS`.
- **Effort:** S

### [LOW] Default page size (20) is hardcoded across list endpoints
- **File:** `src/api/repository.py:75` (`limit: int = 20`) and `src/db/repository.py:322,669,781` (`limit: int = 20`)
- **What breaks:** The default pagination window is a literal repeated in four
  places. Not a correctness or money risk, but a deployment that wants a
  different default page size (or a maximum-page-size cap, which is absent) must
  edit multiple call sites. Duplication invites drift between the API layer and
  the DB layer.
- **Reproduce:** grep `limit: int = 20` under `src/`.
- **Fix:** **Config/env** (soft) — a default/max page size is a deployment tuning
  knob. At minimum name it as a single shared constant rather than repeating the
  literal `20`; ideally source the default (and a hard max) from config. Low
  priority.
- **Effort:** S

### [LOW] OTel service name defaults to a hardcoded string
- **File:** `src/observability/metrics.py:86` (`service_name: str = "asc-recovery"`), also used in `noop_instruments()` at `:99`
- **What breaks:** The telemetry `service.name` resource attribute defaults to
  the literal `"asc-recovery"`. In a multi-environment setup (dev/staging/prod)
  you typically want the environment reflected in the service name so dashboards
  and traces can be separated. Minor.
- **Reproduce:** inspect the default parameter; `main.py` calls `setup_metrics`
  without overriding it.
- **Fix:** **Config/env** — read `OTEL_SERVICE_NAME` (an OTel-standard variable)
  and pass it through. Named default is fine; wiring is the gap.
- **Effort:** S

### [LOW] Database engine has no configurable pool sizing
- **File:** `src/db/base.py:14` (`create_engine(database_url, pool_pre_ping=True)`)
- **What breaks:** The engine is created with only `pool_pre_ping=True`; pool
  size, max overflow, and pool timeout fall to SQLAlchemy's built-in defaults
  (pool_size 5, max_overflow 10) with no way to tune them per deployment. Under a
  larger production instance these defaults can throttle throughput or, under
  load, exhaust connections. Not a hardcoded *literal* so much as a missing
  config surface, but it belongs in the same bucket.
- **Reproduce:** inspect `make_engine`; no pool arguments are accepted or read
  from config.
- **Fix:** **Config/env.** Pool sizing "differs per deployment" (taxonomy: pool
  sizes). Accept `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` from the
  environment and pass them to `create_engine`.
- **Effort:** S

---

## Explicitly considered and deliberately NOT flagged

- `DEFAULT_TEMPLATE` in `packets/templates.py` — appeal-letter boilerplate
  (salutation/closing/legal footer). Arguably per-tenant, **but** a per-payer
  override path already exists (`select_template(payer_override)` backed by the
  `contracts.packet_template` JSONB column), so the in-code value is a genuine
  fallback default, not the system of record. Acceptable as-is.
- `db/rules_version.py::RULES_VERSION = "2026.08.0"` — a deliberately hand-bumped
  provenance stamp, not a deployment/tenant value. Correctly a code constant.
- `main.py` OTLP path suffixes `/v1/traces`, `/v1/metrics` — fixed by the OTLP
  specification. Correctly in code.
- `security/session.py::_ALGORITHM = "HS256"`, `kms_env.py::_CURRENT_KEK_ID`,
  all AES/nonce/KEK byte-length constants — fixed by standard and already named.
- `packets/prompt.py` `{{...}}` placeholder tokens — internal template markers,
  not secrets or config (the `# nosec B105` annotations are correct).
