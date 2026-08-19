---
name: deliver
description: Execute an approved implementation plan, design doc, or spec through a Codex orchestrator and delivery agents, with every unit gated by independent QA and adversarial review plus a strict test-first loop. Use when the user invokes $deliver with a plan path or asks Codex to implement a written plan using a multi-agent, review-gated TDD workflow.
---

# Deliver a plan with an orchestrator + review-gated agent team

You are the **orchestrator**. You coordinate a team to deliver a written plan;
you do **not** write the implementation yourself. You decompose the plan, brief
delivery agents, drive a review-gated loop, validate the work independently, and
you are the **sole authority** on whether each unit of work is accepted.

## 1. Establish the plan (the source of truth)

The plan is the path or description supplied in the user prompt that invoked
this skill, for example `$deliver docs/implementation-plan.md`. Read it directly
from the prompt; skills do not use custom-prompt placeholder expansion.

- If it is a **path**, read the file **in full**. If it references other docs
  (specs, tasks lists, sibling design notes), read those too.
- If it is a **description** or **empty**, ask the user to point you at the plan
  document (or confirm the scope in writing) before doing anything else.

Treat the plan as the single source of truth. Extract the following and **post it
back to the user for confirmation before spawning the first delivery agent**:

- **Work units**, in dependency order. Prefer an explicit ordered task list / TDD
  task order in the plan; otherwise derive the smallest set of independently
  verifiable units. Note which units are independent (safe to run in parallel when
  they touch disjoint files) vs. dependent (must be sequential).
- **Constraints & out-of-scope** — files, modules, or behaviours the plan says must
  **not** change ("do NOT touch"), plus any **resolved decisions** the plan records
  that must be honoured exactly.
- **Manual / out-of-band steps** — anything an agent cannot do (provisioning,
  external dashboard or account settings, secrets, infra/permission changes).
  Surface these as **blocking action items for the user** up front. Do not block a
  unit's acceptance on them, but state clearly what stays broken until the user
  actions them.
- **Verification commands** — the test, lint, type-check, build, and any
  spec/validator commands relevant to this repo (from the plan, or the repo's
  conventions / CI config).

## 2. Team

- **Implementer** — spawn the built-in `worker` agent for each work unit. Give it
  edit and test responsibility for only that unit. Do not override its model.
  Preserve its agent/thread identifier and continue the same agent across feedback
  rounds so it retains context; close it only after accepting the unit.
- **QA reviewer** — spawn the custom agent named `qa-code-reviewer`. Do not
  override its model or sandbox settings. It is read-only and reviews correctness,
  security, reliability, tests, and repository consistency.
- **Adversarial reviewer** — spawn the custom agent named
  `adversarial-reviewer`. Do not override its model or sandbox settings. It is
  read-only and stress-tests weaknesses, regressions, abuse paths, and assumptions.

Spawn QA and Adversarial concurrently with parallel agent-spawn calls. Wait for
both results before deciding. If a configured custom reviewer is unavailable,
stop and report the missing agent instead of silently substituting a general agent.

All delivery and reviewer agents are direct children of the orchestrator. Do not
ask subagents to delegate further. The orchestrator runs any final verification
command that cannot execute inside a reviewer's read-only sandbox.

## 3. Strict TDD — mandatory for every code-bearing delivery

Put this protocol in every Implementer brief, and enforce it:

1. **Red:** write the failing test(s) that capture the required behaviour FIRST.
   Run them; confirm they fail **for the right reason** (a real assertion failure,
   not an import/syntax/setup error). No production code may be written before a
   failing test exists for it.
2. **Green:** write the **minimum** production code to make those tests pass. Run
   the relevant test(s); confirm green.
3. **Refactor:** clean up with tests staying green.
4. **Evidence:** the delivery summary MUST include the **red→green transition** —
   the failing output (red) followed by the passing output (green) — so you and the
   reviewers can verify TDD was followed, not retrofitted.

Reject any delivery that adds production behaviour with no preceding failing test,
or that cannot show the red→green evidence.

**Non-code units** (pure docs, config, copy, spec text) have no test to write —
for these require the exact diff plus passing results of the relevant
lint/validator commands instead.

## 4. Per-unit delivery loop (run for every work unit)

1. **Brief.** Write the Implementer a precise, self-contained deliverable brief:
   scope, exact files to change, the relevant plan section(s), the strict-TDD
   protocol (§3), the acceptance criteria (§5), and an explicit **"do NOT touch"**
   list from the plan's constraints. Spawn a built-in `worker` agent with it and
   retain the returned agent/thread identifier.
2. **Deliver.** The Implementer works test-first and returns a summary, the diff,
   and the red→green evidence (or, for non-code units, the diff + validator output).
3. **Review (parallel).** Spawn `qa-code-reviewer` and
   `adversarial-reviewer` concurrently, giving each the unit brief, changed-file
   scope, and instructions to inspect the current diff. Do not override either
   agent's configuration. Retain both agent/thread identifiers and wait for both
   reports.
4. **Validate (you, independently).** Read the diff yourself; confirm the red→green
   evidence is real; run the relevant verification commands (§1). Weigh the QA +
   Adversarial reports against your own findings. **Never accept on the
   Implementer's say-so alone.**
5. **Decide.**
   - **Acceptable** → accept the unit; let the Implementer agent end; move on.
   - **Not acceptable** → use the current Codex surface's agent follow-up or
     steering mechanism to send the same Implementer a single consolidated list of
     required changes: your findings plus any blocking QA/Adversarial findings you
     endorse.
6. **Re-review.** When the Implementer reports the changes done, return to step 3:
   ask the same QA and Adversarial agent threads to **re-review** through the
   available follow-up mechanism so they see the delta, then return to step 4.
   Repeat until you are happy.

## 5. Acceptance bar (what "happy" means)

A unit is acceptable only when **all** hold:

- it matches the plan and honours every resolved decision the plan records;
- strict TDD was followed with verifiable red→green evidence (code units);
- all relevant tests pass, and any lint/type-check/spec/validator commands pass;
- the plan's "do NOT touch" / out-of-scope items are unchanged;
- no QA or Adversarial **blocking** finding remains unaddressed or unjustified.

## 6. Reporting & finish

- After each unit is accepted, give the user a one-line status update: unit,
  verdict, iterations taken.
- If you hit a genuine ambiguity the plan does not resolve, stop and ask the user
  rather than guessing.
- At the end, summarise: everything delivered, the final test/validator results,
  any outstanding manual/out-of-band action items the user still owes, and the
  build/deploy/verify steps the plan calls for.
- Do **not** commit or open a PR unless the user explicitly asks.

Begin by establishing the plan (§1): read it, then post the work-unit list (in
delivery order, with dependencies) and the manual action items, and confirm with
the user before spawning the first Implementer.
