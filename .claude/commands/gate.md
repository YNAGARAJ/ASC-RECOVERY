Run the full verification gate for the current phase. Report only — do not fix
anything, no matter what you find.

1. Run `make test`. Report any failures with file:line.
2. Run `make lint`. Report any violations with file:line.
3. Run `make eval`. Report recall, precision, and cause accuracy.
4. Run `make security`. Report findings by severity.
5. Grep the working diff (`git diff` and `git diff --cached`) for:
   - `float` usage under `domain/` or `services/` (type annotations, casts,
     literals)
   - PHI-shaped content in log statements (`log.`, `logger.`, `print(` lines
     containing name/DOB/SSN/MRN/member-id-looking fields)
   - Database queries missing `tenant_id` scoping (`session.query(`, `select(`,
     `.filter(`, `.where(` without a nearby `tenant_id`)
   - Pricing logic that references "today", `datetime.now()`, or the current
     contract instead of the claim's date of service

Do not modify any file during this command.

End by printing, on its own line, exactly one of:

```
GATE PASSED
```

or

```
GATE FAILED
```

If FAILED, list every blocking item immediately below that line, one per
line, each with a file:line reference.
