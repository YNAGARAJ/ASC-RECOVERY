# PASTE THIS

Two prompts. That's the whole start.

---

## STEP 1 — Terminal (30 seconds)

```bash
mkdir asc-recovery && cd asc-recovery && git init
code .
```

Open Claude Code inside VS Code. Make sure it's working in the `asc-recovery` folder.

---

## STEP 2 — Paste this first

> Set up a new project. Create exactly these files and nothing else — no source
> code yet, no application logic. This is scaffolding only.
>
> **The project:** a system that reads X12 835 remittance files from health
> insurers, compares what was paid against a contracted fee schedule, and finds
> claims that were underpaid. Customers are ambulatory surgery centers. The data
> is protected health information, so security is not optional.
>
> **1. `CLAUDE.md`** — the project constitution. Keep it under 60 lines. Include:
> - What this is (one paragraph, as above)
> - Stack: Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2.x, Alembic, Pydantic v2, pytest, Docker, Terraform, OpenTelemetry
> - These eight non-negotiable rules, stated plainly:
>   1. No real PHI in this repo, tests, logs, fixtures or prompts. Synthetic only.
>   2. Money is `Decimal`, never `float`. ROUND_HALF_UP, 2 places.
>   3. No LLM ever computes or restates a dollar amount. LLMs draft prose only.
>   4. Claims are priced against the contract version effective on the claim's DATE OF SERVICE, never today's contract.
>   5. Every write to a PHI table goes through the audit log.
>   6. No PHI in logs, traces, error messages or URLs.
>   7. Cloud-agnostic — no proprietary service without an equivalent elsewhere.
>   8. Multi-tenant — every query scoped by `tenant_id`. No global reads.
> - Commands: `make test`, `make lint`, `make eval`, `make security`
>
> **2. `scripts/hooks/block_phi.sh`** — a PreToolUse hook. Reads the tool call as
> JSON on stdin (use `jq`, with a graceful fallback if `jq` is absent). Exits 2
> with an explanatory message on stderr if the content being written contains:
> a US SSN pattern, a Medicare Beneficiary Identifier pattern, a real-looking
> email (allow `@example.com`, `@test`, `@localhost`), a real-looking US phone
> (allow `555-01xx`), an AWS key / OpenAI key / private key block / GitHub token,
> or the phrases "real patient" / "production phi". Also block any write to a path
> under `data/real/`, `phi/`, `secrets/`, or ending `.pem` / `.key`. Exit 0 otherwise.
>
> **3. `scripts/hooks/check_money_types.sh`** — a PostToolUse hook. Only applies to
> files under `domain/`, `services/` or `pricing/`. Exits 2 if the file contains a
> `float` type annotation or `float()` cast, a bare decimal literal assignment that
> isn't a named ratio/rate/tolerance/threshold, or `round()` called on a variable
> named like money (amount, allowed, paid, charge, shortfall, total). Allow any line
> ending `# noqa: money`. Exit 0 otherwise.
>
> **4. `.claude/settings.json`** — register both hooks on `Edit|Write`.
>
> **5. `.claude/commands/gate.md`** — a slash command that runs `make test`, `make
> lint`, `make eval`, `make security`, then greps the working diff for: `float` in
> domain/services, PHI in log statements, queries missing `tenant_id`, and pricing
> against the current contract instead of the date-of-service contract. It must end
> by printing `GATE PASSED` or `GATE FAILED` on its own line with blocking items
> listed. It reports only — it never fixes.
>
> **6. `.claude/agents/adversarial-reviewer.md`** — a subagent with frontmatter
> (`name`, `description`, `tools: Read, Grep, Glob, Bash`, `model: opus`). Its role:
> a hostile reviewer on a healthcare payments system looking for the defect that
> costs the customer money or triggers a breach notification. Checks in order: PHI
> leakage, money errors, date-of-service contract correctness, tenant isolation,
> audit gaps, authorization. Reports `SEVERITY | file:line | what breaks | how to
> reproduce`. Never fixes, never softens.
>
> **7. `Makefile`** with those four targets, `.gitignore` (ignore `data/real/`,
> `phi/`, `secrets/`, `*.pem`, `*.key`, `*.835`, `*.edi`, plus normal Python
> ignores), and `docs/PHASES.md` listing phases 0–12 with checkboxes, current phase
> set to Phase 1.
>
> Make both hook scripts executable. Then **test them**: pipe a fake SSN through
> `block_phi.sh` and confirm it exits 2, and pipe a clean file through and confirm
> it exits 0. Show me the results.
>
> Do not write any application code. Stop when this is done.

**Then check:**

```
/hooks
```

Both hooks must appear. If they don't, fix that before anything else — they're the
only thing preventing a PHI leak later.

```bash
git add -A && git commit -m "Phase 0: scaffold, constitution, guardrails"
```

---

## STEP 3 — Enter plan mode

Press **`Shift+Tab` twice.** You should see plan mode is on.

This matters: it makes Claude explore and propose before writing. Reading a plan
takes two minutes. Unwinding a bad implementation takes hours.

---

## STEP 4 — Paste this second

> Build the pure domain core. No database, no network, no filesystem — pure
> functions and value objects only, so every part is trivially testable.
>
> **1. `src/domain/money.py`** — a `Money` value type wrapping `Decimal`,
> ROUND_HALF_UP to 2 places. Supports add, subtract, multiply by a rate, and
> comparison. Design it so float arithmetic is impossible by construction, not
> merely discouraged.
>
> **2. `src/domain/x835.py`** — an X12 835 parser. Must handle: ISA/GS/ST
> envelopes; BPR, TRN, N1 loops; CLP; NM1; SVC with composite
> `procedure:modifier` elements; CAS with repeating triplets at both claim and
> service level; DTM; LQ; MIA/MOA; PLB. Compute
> `allowed = charge − CO adjustments` and `paid = allowed − PR adjustments`.
> Must also handle reversals (CLP02=22), denials (CLP02=4), secondary-payer
> claims, malformed segments, and mixed line endings.
>
> **3. `src/domain/contract.py`** — fee schedule and payment rules: multiple
> procedure reduction with ranking, bilateral modifier 50, assistant surgeon,
> implant carve-outs, percent-of-charge, case rates, and per-payer rule overrides.
>
> **4. `src/domain/variance.py`** — expected vs actual allowed per line, with root
> cause classification and a human-readable evidence string for every finding.
>
> **Work test-first.** Write the test file for each module and show it to me
> BEFORE writing the implementation. I want to review the tests first.
>
> Constraints: strict mypy, no external dependencies in this layer, every public
> function fully type-annotated.
>
> Plan it first and show me the plan. Wait for my approval before writing code.

---

## STEP 5 — The loop, every phase after this

1. `/clear`
2. `Shift+Tab` twice (plan mode)
3. Paste the next phase prompt from `MASTER-BUILD-PROMPT.md`
4. Read the plan, push back, approve
5. Let it build
6. `> Use the adversarial-reviewer subagent to review everything in this phase.`
7. Fix anything HIGH or CRITICAL
8. `/gate`
9. **PASSED** → commit, tick `docs/PHASES.md`, `/clear`, next phase
   **FAILED** → fix and re-run. Never advance on a failed gate.

---

## The only two things to remember

**`/clear` between every phase.** Long sessions degrade. This is the single
biggest quality lever you have and it costs nothing.

**No real patient data until Phase 11 is signed.** Not a test file, not "just one
to check the parser." Not once.
