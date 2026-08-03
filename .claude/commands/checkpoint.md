Write `docs/PROGRESS.md`, overwriting it. This is a handoff note for a fresh
session with no memory of our conversation. Be precise and honest.

Include:
- **Phase:** which phase, and what percentage of its gate is met
- **Done:** files completed in this phase, one line each on what they do
- **In progress:** what is half-written, in which file, and what was the next
  intended step
- **Failing:** current test/lint failures with file:line
- **Decisions:** any design decision made this session that is not obvious
  from the code, and why — especially anything we chose *against*
- **Traps:** anything discovered that would trip up someone resuming cold
- **Next 3 steps:** the specific next actions, in order

Do not be optimistic. If something is broken or shaky, say so plainly. A
fresh session that believes the code is fine when it is not will waste hours.

Then commit it: `git add -A && git commit -m "checkpoint: <phase>"`
