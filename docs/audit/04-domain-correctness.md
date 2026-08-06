# Audit 04 — Domain Correctness

Scope: date-of-service contract pricing (the critical rule 4), 835 parsing
completeness, MPPR ranking, bilateral handling, implant carve-out, unpriced-code
surfacing. Read-only audit; every claim below was traced through the real call
chain, not inferred.

Call chain traced end to end:
`pipeline.ingest_file` → `parse_835` (domain/x835.py) →
`build_ingestion_plan` → `_plan_claim` (ingestion/plan.py) →
`_derive_date_of_service` → `find_effective_contract` (domain/contract.py) →
`price_claim` → `evaluate_claim` (domain/variance.py) →
`apply_ingestion_plan` (ingestion/apply.py) → `repository.save_findings`.

---

## 1. Date-of-service pricing — the critical rule

### [CLEAN] Every claim is priced against its own date of service, not today's

Traced the full path and it is correct. `date_of_service` originates from the
parsed 835, never from the clock:

- `domain/x835.py:444-452` — line-level `DTM` (e.g. `DTM*472`) sets
  `ServiceLine835.service_date`; claim-level `DTM*232/233` land in
  `Claim835.dates`. Dates come only from `_parse_date` of segment content
  (`x835.py:198-199`).
- `ingestion/plan.py:74-83` `_derive_date_of_service` picks the **minimum
  line-level service date**, falling back to claim-level `DTM*232`, then the
  first claim date. All claim-intrinsic; no `date.today()`.
- `ingestion/plan.py:121-132` passes that derived `date_of_service` straight
  into `find_effective_contract(payer_key, date_of_service, payer_versions)`.
- `domain/contract.py:112-123` `find_effective_contract` compares
  `version.effective_from`/`effective_to` **against the passed
  `date_of_service`** only — no `now()`/`today()` anywhere.

Evidence there is no clock leak in any pricing path: a repo-wide search for
`date.today|datetime.now|utcnow` returns only `repository.py:587,856`
(outcome/packet decision timestamps) and `security/session.py` (token issue
times) — none touch contract selection or line pricing. `domain/contract.py`
and `domain/variance.py` import no time source at all.

Test evidence is meaningful, not tautological: `test_contract.py:283-306`
proves `find_effective_contract` selects `v2` for a 2023-06-15 DOS across three
versions and returns `None` when no window covers the date; the DB layer
(`repository.get_effective_contract_version`, `list_contract_versions`) delegates
to the same tested domain function rather than re-implementing date logic
(`repository.py:275-293`, `296-318`).

Conclusion: rule 4 holds on the real ingestion path. The one residual risk is
finding 5.1 below (ordering dependency), which is latent, not active.

### [MEDIUM] find_effective_contract trusts caller ordering and does not guard overlapping windows

- **File:** src/domain/contract.py:112-123
- **What breaks:** the function returns the **first** version in the passed
  sequence whose window contains the DOS, not the one with the greatest
  `effective_from <= DOS`. It never sorts and never validates that windows are
  non-overlapping. If two versions for one payer are both open-ended
  (`effective_to = None` — nothing prevents this; `create_contract_version` at
  repository.py:237-272 performs no overlap check), the version returned depends
  entirely on caller sequence order. A wrong pick means a claim is priced
  against a stale fee schedule → wrong expected allowed → false or missed
  underpayment findings, i.e. a money error on the single most important rule.
  Not active today only because every production caller
  (`pipeline.py:160-161` via `list_contract_versions`, and
  `get_effective_contract_version`) orders `effective_from DESC`
  (repository.py:287, 314), so the most-recent match is returned first.
- **Reproduce:** `find_effective_contract("P", date(2024,6,1), [v_2024_open, v_2023_open])`
  returns `v_2024_open`, but swap the list order and it returns `v_2023_open` —
  same data, different price. No existing test passes an overlapping/open-ended
  pair in ascending order.
- **Fix:** inside `find_effective_contract`, select
  `max((v for v in versions if v.payer_id==payer_id and v.effective_from<=dos
  and (v.effective_to is None or dos<=v.effective_to)), key=lambda v: v.effective_from, default=None)`
  so correctness no longer depends on caller order; and/or add an overlap
  constraint at contract-version creation.
- **Effort:** S

---

## 2. 835 parsing completeness

### [CLEAN] CLP02 statuses 22 (reversal) and 4 (denial)

- **Evidence:** `ClaimStatus` enum maps `"22"` → `REVERSAL_OF_PREVIOUS_PAYMENT`
  and `"4"` → `DENIED` (x835.py:19-25), resolved via `_CLP_STATUS_BY_CODE`
  (x835.py:35, 416). `test_x835.py:252-256` asserts reversal status **and** that
  the negative `total_paid_reported == Money("-430.00")` parses without crashing;
  `test_x835.py:258-264` asserts denial status, `total_paid == 0.00`, and
  line-level `paid_computed == 0.00` under a full CO write-off. Both assertions
  are substantive. Reversal then drives real netting in `_plan_claim`
  (plan.py:109-119). Unknown status codes are rejected as a parse error
  (x835.py:417-421).

### [CLEAN] CAS adjustment triplets at both claim and service level

- **Evidence:** `_parse_cas` (x835.py:206-239) walks repeating 3-element triplets
  and routes them to the current line if one is open, else to the claim
  (x835.py:453-461). `test_x835.py:142-167` asserts three claim-level triplets in
  one segment sum to `100.00`; `:170-195` asserts two service-level triplets and
  that `line.allowed == 400.00`; `:198-224` asserts two separate CAS segments on
  one claim concatenate to `150.00`. A dangling (incomplete) final triplet is
  surfaced as a warning without losing the claim (x835.py:214-224;
  test `:373-376`). Note (LOW, no finding): claim-level CO is retained in
  `claim.adjustments` but not subtracted from any line's `allowed`
  (`_finalize_line` uses line-level CO/PR only, x835.py:264-272) — acceptable for
  ASC/professional remits where claim-level CO is rare, but worth remembering if
  institutional claim-level write-offs ever need to reduce allowed.

### [MEDIUM] SVC04 revenue code is discarded; revenue_code is hardcoded None

- **File:** src/domain/x835.py:462-482 (SVC handler), specifically 474-481
- **What breaks:** the SVC handler reads the composite (SVC01), charge (SVC02),
  paid (SVC03) and units from `elements[5]` (SVC05), but never reads
  `elements[4]` (**SVC04 = NUBC revenue code**) — it sets `revenue_code=None`
  unconditionally. Combined with plan.py always passing `invoice_cost=None`, the
  implant carve-out's revenue-code trigger
  (`_is_implant`, contract.py:147-152) can never fire from a parsed 835. Any
  contract that identifies implants by revenue code (e.g. 0278) silently detects
  zero implant lines end to end.
- **Reproduce:** parse any 835 whose SVC carries a revenue code in SVC04; the
  resulting `ServiceLine835.revenue_code` is `None`. No test asserts revenue_code
  is ever populated (grep for `revenue_code` in tests/ finds no positive
  assertion).
- **Fix:** in the SVC handler read `_safe_element(elements, 4)` into
  `revenue_code` (guarding against SVC composites that omit it) and add a fixture
  + assertion.
- **Effort:** S

### [LOW] SVC composite with multiple modifiers works but is untested

- **File:** src/domain/x835.py:467-469; tests/domain/test_x835.py:106-140
- **What breaks:** the parser captures all trailing composite sub-elements as
  modifiers (`modifiers = tuple(composite[2:])`), so `HC:27447:50:LT` correctly
  yields `("50","LT")`. But the only modifier test asserts a **single** modifier
  (`assert line.modifiers == ("50",)`, :140). Multiple modifiers — which the
  audit brief explicitly calls out and which drive bilateral (`"50"`) and
  assistant-surgeon (`"80"/"AS"`) detection — have no coverage, so a future
  regression collapsing them would pass CI.
- **Reproduce:** no fixture emits a two-modifier composite; add one and assert.
- **Fix:** add a fixture with `HC:27447:50:LT` (or `:80:59`) and assert the tuple.
- **Effort:** S

### [CLEAN] PLB adjustments

- **Evidence:** `_parse_plb` (x835.py:242-260) reads provider id (PLB01), fiscal
  period date (PLB02), then repeating reason/reference/amount triplets from
  PLB03+. `test_x835.py:97-100` asserts exactly one PLB adjustment parses with
  `amount == Money("-10.00")` (a real negative takeback), which is substantive.
  A PLB segment also correctly flushes any open claim first (x835.py:499-505).

### [LOW] MIA/MOA are captured as raw element tuples only; tests prove presence, not content

- **File:** src/domain/x835.py:60-68, 491-498; tests/domain/test_x835.py:304-325
- **What breaks:** MIA (inpatient) and MOA (outpatient) adjudication segments are
  stored as opaque `raw_elements` tuples with no field extraction — MOA remark
  codes and any MIA/MOA-reported amounts are never turned into usable data, so
  they cannot inform underpayment detection. The tests assert only
  `claim.mia is not None` / `claim.moa is not None`, which would pass even if the
  raw tuple were empty or garbage. For an ASC (outpatient) workload the MOA
  remark codes are the more relevant of the two.
- **Reproduce:** `test_moa_present` passes against a MOA whose only non-empty
  element is `A1`; nothing asserts that `A1` is retrievable as a remark code.
- **Fix:** either extract the relevant MOA remark codes / MIA amounts into typed
  fields with assertions, or document explicitly that MIA/MOA are retained raw
  for audit only and out of scope for pricing.
- **Effort:** M

### [CLEAN] Secondary payer claims

- **Evidence:** `test_x835.py:266-273` parses a CLP02=2 secondary claim and
  asserts the OA-group claim-level adjustment is retained (`len == 1`) but **not**
  subtracted from `line.allowed` (which stays `500.00`), matching
  `_finalize_line` summing only CO/PR (x835.py:264-272). Tertiary (CLP02=3) is
  also covered (`:276-298`). This is a deliberate, tested design choice.

---

## 3. MPPR ranking logic

### [CLEAN] Ranking is by current allowed, highest first, with correct exclusions

- **Evidence:** `price_claim` builds the MPPR pool excluding implants,
  case-rate, and assistant-surgeon lines (`excluded_from_mppr`), lines with no
  allowed, and `mppr_rule.exempt_codes`; sorts by current allowed descending;
  applies full rate to rank 1, `second_procedure_rate` to rank 2,
  `third_and_subsequent_rate` to rank ≥3 (contract.py:236-254).
  `test_contract.py:90-104` proves ranking follows **value** not input order
  (input C,A,B → A rank1/300, B rank2/100, C rank3/25). `:107-127` proves
  exempt codes keep full price and `mppr_rank is None`. `:129-148` proves
  `enabled=False` leaves all lines at full rate. `:215-226` proves implants are
  excluded from the ranked pool. All substantive.
- The reduction is applied to the **already-current** allowed (post-base,
  post-bilateral), which is correct: a bilateral line's 150% amount participates
  in ranking at that value.

### [LOW] MPPR tie-breaking is deterministic but untested

- **File:** src/domain/contract.py:246; tests/domain/test_contract.py
- **What breaks:** equal-allowed lines rank by original input order (Python's
  sort is stable and preserves original order of equal keys even with
  `reverse=True`), so behavior is deterministic. But no test pins this, so a
  future switch to a non-stable ordering (or a `key` change) could silently
  reorder which equal-value line takes the rank-2 reduction — a small money
  shift per claim.
- **Reproduce:** price two lines with identical allowed; no test asserts which
  one receives the reduction.
- **Fix:** add a test with two equal-allowed lines asserting the stable outcome.
- **Effort:** S

---

## 4. Bilateral modifier handling

### [HIGH] BilateralConvention.TWO_LINE_SPLIT is defined and storable but never implemented — silent no-op

- **File:** src/domain/contract.py:19-21 (enum), 182-194 (only SINGLE_LINE handled)
- **What breaks:** `price_claim` applies bilateral pricing **only** when
  `convention == BilateralConvention.SINGLE_LINE_150_PCT` (contract.py:184-186).
  If a contract version is configured with `TWO_LINE_SPLIT`, the entire bilateral
  block is skipped and **no bilateral adjustment is applied at all** — each of
  the two bilateral lines prices at 100% of the fee schedule (200% combined)
  instead of the intended split totalling `total_rate`. Expected allowed is then
  wrong by roughly 50% of the procedure fee on every bilateral case for that
  payer, producing false underpayment findings (or masking real ones) — a direct
  money error. The value round-trips cleanly from the DB
  (`_bilateral_rule_from_json`, repository.py:92-97), so a customer can persist a
  configuration that the pricer silently ignores. A repo-wide search confirms
  `TWO_LINE_SPLIT` appears **only** at its enum definition — no handler, no test.
- **Reproduce:** create a contract version with
  `BilateralRule(enabled=True, total_rate=Rate.percent(150), convention=TWO_LINE_SPLIT)`,
  price two lines of the same code each carrying modifier `50` (or an `RT`/`LT`
  pair) → both come back at full fee schedule, no `BILATERAL` method, no
  reduction.
- **Fix:** implement the `TWO_LINE_SPLIT` branch (split `total_rate` across the
  paired lines per the convention), or, if unsupported this phase, reject the
  value at contract-version creation so it can never be silently stored and
  ignored.
- **Effort:** M

### [CLEAN] Single-line 150% convention and modifier-50 detection

- **Evidence:** for `SINGLE_LINE_150_PCT`, a line carrying `"50"` is multiplied
  by `total_rate` and marked `BILATERAL` (contract.py:188-194), skipping implants
  and unpriced lines. `test_contract.py:154-159` asserts `1000 → 1500` with
  method `BILATERAL`; `:162-173` asserts `enabled=False` leaves it at `1000`.
  Detection is exact membership on the modifier tuple (`"50" not in line.modifiers`).

---

## 5. Implant carve-out

### [CLEAN — unit level] Carve-out uses invoice cost, not fee schedule, and excludes the line from MPPR

- **Evidence:** step 2 of `price_claim` (contract.py:169-180) sets
  `allowed = line.invoice_cost` with method `INVOICE_COST_IMPLANT` and
  `excluded_from_mppr = True`, and when `invoice_cost is None` sets `UNPRICED`
  (never 0). `test_contract.py:205-212` asserts an implant with fee schedule
  empty and invoice `1750` prices at `1750` via `INVOICE_COST_IMPLANT`;
  `:215-226` asserts it is excluded from MPPR ranking; `:229-236` asserts missing
  invoice cost → `allowed is None`/`UNPRICED` (not zero); `:255-277` asserts case
  rate + implant carve-out coexist correctly. Substantive.

### [HIGH] Implant carve-out is inert on the real ingestion path — invoice_cost is always None

- **File:** src/ingestion/plan.py:149-159 (invoice_cost=None); compounded by
  x835.py:474-481 (revenue_code=None, finding 2 above)
- **What breaks:** in `_plan_claim`, every `ClaimLineInput` is built with
  `invoice_cost=None` (there is no purchasing feed this phase). So on the real
  pipeline **every** implant line hits the `UNPRICED` branch of the carve-out and
  is surfaced as a `UNPRICED_CODE` finding with `shortfall = 0`, never as a
  computed implant shortfall (`IMPLANT_NOT_CARVED_OUT`). Because implants are
  typically the **highest-dollar** underpayment category for an ASC, the system
  quantifies $0 of recovery on exactly the line items that matter most — the
  carve-out logic proven correct in unit tests never executes against production
  data. This is acknowledged in the code comment as "Phase 1 behavior," but from
  a production-readiness standpoint it means implant underpayment detection does
  not function end to end.
- **Reproduce:** ingest an 835 containing an implant procedure code; the
  resulting finding is `UNPRICED_CODE` with `shortfall == 0`, never a dollar
  figure — trace plan.py:149-159 → contract.py:175-180 → variance.py:112-131.
- **Fix:** wire the implant invoice-cost source (purchasing feed) into
  `ClaimLineInput.invoice_cost`, and read SVC04 into `revenue_code` (finding
  above) so revenue-code-based implant identification works. Until then, document
  that implant recovery is out of scope and that `UNPRICED_CODE` on an implant
  code is expected.
- **Effort:** L

---

## 6. Unpriced codes

### [CLEAN] Unpriced codes are surfaced as findings, never silently dropped

- **Evidence, traced end to end:**
  - `price_claim`/`_base_price` return `allowed=None, method=UNPRICED` when
    neither fee schedule nor percent-of-charge yields a price
    (contract.py:126-144). The line still appears in `priced.lines` — it is not
    removed (contract.py:256-265).
  - `evaluate_claim` detects `expected_allowed is None` and emits an explicit
    `RootCause.UNPRICED_CODE` finding (shortfall `Money.zero()`) with evidence,
    then `continue`s (variance.py:112-131). The line is kept.
  - `apply._apply_claim` persists it: `persistable` keeps any finding whose
    `line_index` maps to a real service line (apply.py:139), and UNPRICED
    findings carry a valid `line_index`, so `save_findings` writes them
    (apply.py:140-148, repository.py:501-532).
  - `test_contract.py:242-249` proves no-fee-schedule + no-percent-of-charge →
    `allowed is None`/`UNPRICED`; `test_contract.py:229-236` proves a missing
    implant invoice cost is `UNPRICED`, not zero.
- The `shortfall = 0` on these is correct by design: an unpriced code is
  surfaced for human review, not asserted as a recoverable dollar amount
  (consistent with `dollars_detected` summing shortfalls, apply.py:219-221).

---

## Summary of findings

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 2 |
| LOW | 3 |

- HIGH: TWO_LINE_SPLIT bilateral convention unimplemented (silent no-op, money
  error); implant carve-out inert end to end (invoice_cost always None).
- MEDIUM: SVC04 revenue code discarded; find_effective_contract trusts caller
  ordering with no overlap guard.
- LOW: multiple-modifier SVC parsing untested; MIA/MOA raw-only with weak tests;
  MPPR tie-breaking untested.

Clean, with evidence: date-of-service pricing (rule 4 holds on the real path);
CLP02 reversal/denial statuses; CAS triplets at both levels; PLB; secondary/
tertiary payer; MPPR ranking/exclusions; single-line bilateral + modifier-50
detection; implant carve-out arithmetic at unit level; unpriced-code surfacing.
