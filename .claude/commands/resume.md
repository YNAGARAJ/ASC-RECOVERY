Resume work on this project. Do not write any code yet.

**Step 1 — Orient.** Read in this order:
- `CLAUDE.md`
- `docs/PHASES.md`
- `docs/PROGRESS.md` if it exists
- `docs/MASTER-BUILD-PROMPT.md` — find the phase marked current and read its
  full prompt and its gate

**Step 2 — Verify against reality, not against the checkboxes.** The tracking
files may claim work is complete when it is not. Check the actual state:
- `git log --oneline -20` and `git status`
- List the files that exist under `src/`, `tests/`, `evals/`
- Run `make test` and report the true result
- Run `make lint` and report the true result
- If the current phase has an eval gate, run `make eval`

**Step 3 — Report.** Tell me:
- Which phase we are actually in
- Which files for this phase exist, and whether they are complete or stubs
- What is failing right now, with file:line
- The exact remaining items to satisfy this phase's gate
- Anything that looks half-finished or abandoned mid-edit

**Step 4 — Propose.** Give me a short ordered plan for the remaining work in
this phase only. Do not include work from later phases.

Then STOP. Do not start working until I approve the plan.
