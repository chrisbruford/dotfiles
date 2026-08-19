# Unit brief template

Write one brief per unit to `.tmux-deliver/briefs/<unit-id>.md` before calling
`prepare-unit`. The launcher wraps this verbatim in the implementer prompt and
shows it to both reviewers, so it must be **self-contained**: the agent has none of
your conversation, none of the plan unless you list it as a context file, and no
knowledge of the other units.

```markdown
# <unit-id> — <one-line title>

## Objective
What this unit must make true, in behavioural terms. One paragraph.

## Plan reference
- Document: docs/<plan>.md
- Sections that govern this unit: §3.2 "Retry policy", §5 "Config surface"
- Resolved decisions that MUST be honoured exactly:
  - <decision from the plan, quoted or tightly paraphrased>

## Scope — files you may change
- src/payments/retry.py            (new)
- src/payments/client.py           (modify: wire the policy in)
- tests/payments/test_retry.py     (new)

## Do NOT touch
- src/payments/ledger.py           — out of scope per plan §6
- Any migration under migrations/  — plan defers schema work to a later unit
- Public function signatures in src/payments/__init__.py

## Acceptance criteria
1. <observable, checkable statement>
2. <observable, checkable statement>
3. Every new code path has a test that fails before the implementation exists.

## Verification
- pytest tests/payments -q
- ruff check src/payments
- mypy src/payments

## Dependencies and assumptions
- Assumes `RetryConfig` from unit `payments-config` is already merged; it is
  available on this branch.
- If that assumption is wrong, report `blocked` rather than reimplementing it.

## Out-of-band items (not yours to fix)
- The staging retry dashboard has to be provisioned by hand; the orchestrator is
  tracking it. Do not attempt it, and do not let it block your unit.
```

## Rules for a good brief

- **Name the exact files.** "Refactor the payment module" is not a brief; a file
  list with an intent per file is.
- **Copy the plan's constraints in.** The "do NOT touch" list is what stops an
  eager agent from expanding scope, and it is the first thing you check at
  validation time.
- **Make acceptance criteria checkable.** Each one should be something you can
  confirm from the diff or from a command's output — not "code is clean".
- **State dependency assumptions explicitly**, and tell the agent to report
  `blocked` if one turns out to be false. That is far better than an agent quietly
  reimplementing a sibling unit's work.
- **Pass the plan as a context file**, not by pasting it. Use
  `start-agent --context-file docs/plan.md`; the agent reads it from disk. Paste
  only the sections that govern the unit into the brief itself.
- **One unit, one brief, one worktree.** If a brief needs "and also", it is two
  units.

## Feedback messages (rejected rounds)

Round-2+ feedback is a different document. Write it to
`.tmux-deliver/messages/<unit>-r<N>-feedback.md` and `send` it to the implementer:

```markdown
Round <N> is rejected. Apply these exact changes now, then re-run the verification
commands and report done.

## Blocking — must fix
1. src/payments/retry.py:42 — the backoff never caps, so a 30-attempt retry sleeps
   for hours. Cap at `max_backoff_seconds` from RetryConfig. (QA finding 1,
   adversarial finding 2 — I have verified both.)
2. tests/payments/test_retry.py:18 — this test was written after the
   implementation; it asserts the current behaviour rather than the required
   behaviour. Rewrite it to fail against the pre-change code and show me the red
   output.

## Not blocking, do not action
- Adversarial finding 4 (thread-safety of the shared clock) is out of scope for
  this unit per plan §6. Ignore it.

Do not change anything outside the file list in your original brief.
```

Consolidate everything into **one** message per round — your own findings plus only
the reviewer findings you endorse. State plainly which reviewer findings you are
overruling and why, so the agent does not action them anyway.
