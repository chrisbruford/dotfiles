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

## Proof of receipt — put the load-bearing content where truncation shows

Order the brief so that anything truncation could quietly remove is *not* the
only copy of something that matters. The launcher appends
`TMUX-DELIVER-RECEIPT: <token>` as the final line of every prompt and requires the
agent's first report to quote it:

```bash
... report --unit retry-policy --role implementer --status running \
    --receipt <token from the prompt's final line> --message "Started..."
```

An agent that cannot see that line has a truncated prompt and is told to report
`blocked` rather than start. `report` rejects a wrong token outright (exit 10),
and `start-agent` prints `receipt_check=` for the launch.

This matters because of *where* a truncated brief loses content. Acceptance
criteria and the "do NOT touch" list sit at the end, and an agent that never saw
them does not know it is missing anything — it just delivers something plausible
against the half of the brief it received. That failure mode has happened: a
delivery agent was launched with 10,137 of 11,657 characters and the tooling
reported nothing wrong.

If you write your own sentinel into a brief rather than relying on the launcher's,
apply the same rule: it goes **last**, it appears nowhere else, and the agent must
quote it before starting.

## Scope changes mid-round — they go in the brief, not in a message

The brief is the **only artefact all three roles share**. If you authorise the
implementer to touch a file its brief excluded and you say so only in a `send`,
the reviewers never see it: they read the brief, see a file changed that the brief
forbids, and block the delivery. Correctly, given what they can see. That has
happened, and both reviewers blocked the same delivery for it.

So write it into the brief:

```bash
... extend-scope --unit retry-policy \
    --message "Also authorised: src/payments/ledger.py — read-only wiring for the retry counter, needed because the policy is otherwise unobservable. Requested by the implementer at round 2; I have verified it is the minimum change."
```

That appends a section to `briefs/<unit>.md` and messages every live role telling
them to re-read it:

```markdown
## SCOPE AMENDMENT — round 2, 2026-08-12T13:04:11+00:00

Authorised by the orchestrator, binding on implementer and reviewers alike, and
effective from this timestamp. It amends the scope above; where the two disagree,
this section wins.

Also authorised: src/payments/ledger.py — ...
```

The heading is fixed (`## SCOPE AMENDMENT`) so reviewers can find it, and every
reviewer prompt and re-review message names it.

**Amendments are not retroactive.** They carry the round and timestamp they were
made in, and a delivery is judged against the brief as it stood when its round
started. Keep the same discipline for any shared decisions document: if you add a
rule mid-run, date it and mark it as binding from that point, or a reviewer will
judge an already-finished delivery against a rule that did not exist when it was
written — which is not a real finding, and costs a round to unpick.

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
`.tmux-deliver/messages/<unit>-r<N>-feedback.md` and hand it to `next-round`,
which bumps the round **and** dispatches it, so the two cannot come apart:

```bash
... next-round --unit retry-policy --feedback-file .tmux-deliver/messages/retry-policy-r2-feedback.md
```

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

Note the last line of that example: if you have since *widened* the scope, do not
say it here alone. Amend the brief with `extend-scope` as well, or the reviewers
will still be judging against the original file list.

## Re-review messages (the delta)

When the implementer redelivers, both reviewers are still `stale` — they have been
shown the change request but not the work. Dispatch the delta to both at once:

```bash
... re-review --unit retry-policy --file .tmux-deliver/messages/retry-policy-r2-delta.md
```

With no `--file` it sends a generated request naming the round's delivery summary,
the brief, and the diff command, and telling each reviewer to go through its own
previous findings one at a time. Write your own when the delta needs framing —
which findings you endorsed, which you overruled and why.

**Do not send the implementer's feedback file to the reviewers.** It is written in
the imperative ("apply these exact changes now, commit, report done") and a
reviewer whose contract forbids touching the worktree will refuse it — correctly.
That refusal costs a round-trip and reads like a stall. `send` and `re-review`
detect implementer-only phrasing aimed at a reviewer and refuse it before it goes
out; `--anyway` overrides if you really mean it.
