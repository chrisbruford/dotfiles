---
name: deliver
description: Execute an approved implementation plan, design doc, or spec via an orchestrator plus a team of Sonnet delivery agents, where every delivery is gated by independent QA and adversarial review and a strict test-first loop until the orchestrator accepts it. Use when the user wants to deliver, implement, or build out a written plan with a multi-agent, review-gated, TDD workflow (e.g. "/deliver <plan>", "deliver this design doc with an agent team", "orchestrate delivery of this plan").
argument-hint: [path to a plan/design/spec doc, or a description of the plan]
---

# Deliver a plan with an orchestrator + review-gated agent team

You are the **orchestrator**. You coordinate a team to deliver a written plan;
you do **not** write the implementation yourself. You decompose the plan, brief
delivery agents, drive a review-gated loop, validate the work independently, and
you are the **sole authority** on whether each unit of work is accepted.

## 1. Establish the plan (the source of truth)

The plan to deliver is referenced in `$ARGUMENTS` — either a path to a
design/plan/spec document or a short description.

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

## 2. Team & models

- **Delivery (Implementer) agents** — one per work unit. `subagent_type: "claude"`,
  pinned to the **Sonnet** model (pass `model: "sonnet"` on every Agent and
  SendMessage call to them). They have full edit/test tooling. **Keep each one
  alive across feedback rounds via SendMessage** so it retains context; it ends
  only when you accept its unit.
- **QA reviewer** — `subagent_type: "qa-code-reviewer"`, the best available QA
  agent. **Do NOT set a model** — let it run on whatever model its own settings
  define. Read-only; can run the test suite.
- **Adversarial reviewer** — `subagent_type: "adversarial-reviewer"`, the best
  available adversarial agent. **Do NOT set a model** — it uses its own configured
  model. Read-only; stress-tests for weaknesses, regressions, injection/abuse
  paths, and broken assumptions.

Spawn QA and Adversarial **together in a single message** so they run concurrently.

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
   list from the plan's constraints. Spawn the Implementer (Sonnet) with it.
2. **Deliver.** The Implementer works test-first and returns a summary, the diff,
   and the red→green evidence (or, for non-code units, the diff + validator output).
3. **Review (parallel).** In one message, spawn the QA reviewer and the Adversarial
   reviewer (each on its own configured model — **no** model override), giving each
   the unit brief + the diff to review. Wait for both reports.
4. **Validate (you, independently).** Read the diff yourself; confirm the red→green
   evidence is real; run the relevant verification commands (§1). Weigh the QA +
   Adversarial reports against your own findings. **Never accept on the
   Implementer's say-so alone.**
5. **Decide.**
   - **Acceptable** → accept the unit; let the Implementer agent end; move on.
   - **Not acceptable** → send the Implementer (via SendMessage, same agent) a
     single consolidated list of required changes: your findings plus any blocking
     QA/Adversarial findings you endorse.
6. **Re-review.** When the Implementer reports the changes done, return to step 3:
   QA + Adversarial **re-review** (continue the same reviewer agents via SendMessage
   so they see the delta), then back to you (step 4). Repeat until you are happy.

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
