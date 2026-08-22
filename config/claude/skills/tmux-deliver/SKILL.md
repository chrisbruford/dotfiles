---
name: tmux-deliver
description: Execute an approved implementation plan, design doc, or spec with the review-gated TDD deliver loop, but run every delivery agent and reviewer in its own tmux window backed by either Claude Code or the OpenAI Codex CLI instead of in-process subagents. Use when the user wants to deliver a plan with visible, attachable, separately-modelled agent windows (e.g. "/tmux-deliver <plan> --codex", "/tmux-deliver <plan> --claude --model sonnet --effort high", "deliver this plan with codex agents in tmux").
argument-hint: <plan path> [--codex|--claude] [--model luna|sol|terra|opus|sonnet|haiku|fable] [--effort low|medium|high|xhigh|max] [--concurrency N]
---

# Deliver a plan with tmux agent windows

You are the **orchestrator**. This is the `deliver` skill's contract — decompose a
plan, brief delivery agents, drive a review-gated strict-TDD loop, validate work
independently, and be the **sole authority** on acceptance — with one difference:
delivery agents and reviewers run as **separate CLI processes in tmux windows**,
not as in-process subagents. You never write the implementation yourself.

All tmux, git-worktree, and state operations go through the bundled CLI:

```bash
python3 ~/.claude/skills/tmux-deliver/scripts/tmux_deliver.py --help
```

Read these references only when you need them:

- `references/runtimes.md` — runtime/model/effort resolution, exact launch commands, argument parsing.
- `references/state-protocol.md` — state directory layout, unit and role statuses, recovery.
- `references/brief-template.md` — the unit brief format the agents expect.

## 0. Parse the invocation

`$ARGUMENTS` carries the plan reference plus flags:

| Flag | Meaning | Default |
|---|---|---|
| `--codex` / `--claude` | runtime for **delivery agents** | **required** — ask if absent |
| `--model <alias>` | `luna\|sol\|terra` (codex) or `opus\|sonnet\|haiku\|fable` (claude) | codex → `luna`, claude → `opus` |
| `--effort <level>` | `low\|medium\|high\|xhigh\|max` | codex → `max`, claude → `medium` |
| `--concurrency <N>` | max delivery agents running at once | `3` |

**Reviewers are fixed by policy: always Codex on `gpt-5.6-sol` at `xhigh`, in both
runtimes.** There is no override — do not offer one. A model from the wrong family
(`--claude --model luna`) is an error; the CLI rejects it and so should you.

If the runtime flag is missing, ask which one to use before doing anything else.

## 1. Establish the plan (the source of truth)

- If the argument is a **path**, read the file **in full**, plus any docs it
  references (specs, task lists, sibling design notes).
- If it is a **description** or **empty**, ask the user to point you at the plan
  document before doing anything else.

Treat the plan as the single source of truth. Extract and **post back to the user
for confirmation before launching the first agent**:

- **Work units**, in dependency order. Prefer the plan's own ordered task list;
  otherwise derive the smallest independently verifiable units. Mark which are
  independent (parallelisable) vs. dependent (must follow another unit).
- **Constraints & out-of-scope** — "do NOT touch" files/modules/behaviours, and any
  **resolved decisions** the plan records that must be honoured exactly.
- **Manual / out-of-band steps** — provisioning, dashboards, secrets, infra or
  permission changes no agent can do. Surface these as blocking action items for
  the user up front. Don't block a unit's acceptance on them, but state clearly
  what stays broken until the user actions them.
- **Verification commands** — test, lint, type-check, build, and any spec/validator
  commands for this repo (from the plan, or the repo's conventions / CI config).
- **The launch table** — runtime/model/effort for delivery agents, the fixed
  reviewer config, concurrency, and the integration branch name.

## 2. Initialise

Run `init` once. It resolves and validates the runtime triple, sets up the tmux
session, creates the `tmux-deliver/<slug>` **integration branch** and a dedicated
**integration worktree**, and records everything in `meta.json`.

```bash
python3 ~/.claude/skills/tmux-deliver/scripts/tmux_deliver.py init \
  --repo . \
  --codex --model luna --effort max \
  --slug payments-retry \
  --plan docs/payments-retry-plan.md \
  --concurrency 3 \
  --verification "$(cat <<'EOF'
- pytest -q
- ruff check .
- mypy src
EOF
)"
```

You orchestrate **in place** — this session is the orchestrator; `init` does not
create a supervisor window. If you are already inside tmux it adopts the current
session (renaming it first if the name is bare-numeric, which would otherwise
break every agent window after the first). If you are not inside tmux it creates
a detached session to hold the agent windows; tell the user to
`tmux attach -t <session>` if they want to watch.

The user's own working tree is **never touched**: all merges happen in the
integration worktree, and their original branch never moves.

`init` creates `.tmux-deliver/` at the repo root, which shows up as untracked. Tell
the user it is scratch state and offer to add it to `.git/info/exclude` (local, not
a repo change) — do not add it to their tracked `.gitignore` unless they ask.

Then start the watcher with the **Monitor** tool, in the background:

```bash
python3 ~/.claude/skills/tmux-deliver/scripts/tmux_deliver.py watch --state-dir .tmux-deliver
```

Every line it emits is a unit/role state change. Trust it as the source of truth
for progress — but understand what it cannot tell you. The watcher reports what
agents report, and an agent that never received its prompt reports nothing at
all: the unit sits at `launched`, which the *launcher* wrote, and the watcher
stays silent because nothing changed.

That gap is now covered from the other side: the watcher also samples the panes
and emits `ALERT` when a role recorded as working has a pane that has been idle,
with no busy indicator, past `--idle-threshold` (default 120s), and `ACTION` when
a unit has roles nothing has been dispatched to. **Act on those lines.** They
exist because a status table that agrees with itself is not evidence.

If you are attached to a different session from the run's, mirror the windows so
the user can see them — see §7.

**Verifying a launch is not babysitting.** `start-agent` now does it for you: it
waits for the CLI's composer before pasting, checks the prompt landed before
pressing Enter, confirms the submission, and then blocks until the agent
self-reports `running`. If any of that fails it exits **non-zero** with the pane
tail attached. So:

- **Read `start-agent`'s exit code and output.** `agent_status=running` and
  `receipt_check=verified` mean the agent has the whole brief. Anything else
  means it does not — do not proceed to review a unit whose implementer was never
  confirmed to have started.
- `... await-running --unit <u> --role <r> --timeout 300` if you passed
  `--no-wait-running` or the launch wait timed out on a genuinely slow boot.
- Beyond that, do not babysit panes — agents submit their own prompts and resolve
  their own permission prompts. `tmux capture-pane -p -t <pane>` when a launch
  fails, an agent reports `blocked`, or a role is still `launched`.

## 3. Strict TDD — mandatory for every code-bearing unit

The launcher already embeds this in every implementer prompt. Enforce it:

1. **Red:** the failing test(s) capturing the required behaviour come FIRST. Run
   them; confirm they fail **for the right reason** (a real assertion failure, not
   an import/syntax/setup error). No production code before a failing test exists.
2. **Green:** the **minimum** production code to pass. Run the tests; confirm green.
3. **Refactor:** clean up with tests staying green.
4. **Evidence:** the delivery summary MUST contain the **red→green transition** —
   real failing output followed by real passing output.

Reject any delivery that adds production behaviour with no preceding failing test,
or that cannot show real red→green evidence.

**Non-code units** (pure docs, config, copy, spec text) have no test to write —
require the exact diff plus passing output of the relevant lint/validator commands.

## 4. Per-unit delivery loop

For each unit, in dependency order, respecting the concurrency cap:

1. **Prepare.** Write the brief to `.tmux-deliver/briefs/<unit>.md` (see
   `references/brief-template.md`), then create the unit's isolated worktree:

   ```bash
   ... prepare-unit --unit retry-policy --title "Add retry policy to payment client" \
       --depends-on payments-config
   ```

   The worktree branches off the **integration branch**, so it inherits every
   already-accepted unit's work. `prepare-unit` refuses if a declared dependency
   is not yet `accepted` — that guard is protecting correctness; satisfy the
   dependency rather than passing `--ignore-deps`.

2. **Deliver.** Launch the implementer window:

   ```bash
   ... start-agent --unit retry-policy --role implementer --round 1 \
       --context-file docs/payments-retry-plan.md
   ```

   This returns only once the agent has confirmed it is running, so it can take
   a minute or two. Check the tail of its output before moving on:
   `pane_ready=ready`, `paste_check=ok`, `submitted=working`,
   `agent_status=running`, `receipt_check=verified`. A non-zero exit means the
   agent never got its prompt — fix that first; do not wait for a delivery that
   is not coming.

   Then wait for the watcher to report `implementer=done` (unit status
   `delivered`), and read `.tmux-deliver/deliveries/<unit>-r<N>.md`.

3. **Review (parallel).** Launch both reviewers **in one shell command** so they
   run concurrently, each in its own window on Sol/xhigh:

   ```bash
   ... start-agent --unit retry-policy --role qa --round 1 && \
   ... start-agent --unit retry-policy --role adversarial --round 1
   ```

   Wait for unit status `reviewed` (both reviewers done), then read both files in
   `.tmux-deliver/reviews/<unit>-r<N>-*.md`.

4. **Verify the reviewers behaved.** Reviewers launch with unrestricted execution
   so they can run tests, and their contract forbids touching the worktree.
   Confirm it:

   ```bash
   ... verify-readonly --unit retry-policy
   ```

   Non-zero exit means a reviewer modified the worktree — discard that review,
   revert the stray changes, and re-run the review.

5. **Validate (you, independently).** Read the diff yourself
   (`git -C <worktree> diff <integration-branch>...HEAD`). Confirm the red→green
   evidence is real, not retrofitted. Run the verification commands yourself.
   Weigh both reviewer reports against your own findings. **Never accept on the
   implementer's say-so alone, and never on the reviewers' say-so alone.**

6. **Decide.**
   - **Acceptable** → go to §5.
   - **Not acceptable** → write **one** consolidated list of required changes,
     then bump the round and dispatch it in a single command:

     ```bash
     ... next-round --unit retry-policy --feedback-file .tmux-deliver/messages/retry-policy-r2-feedback.md \
         --message "round 1 rejected: 2 blocking findings"
     ```

     `--feedback-file` is not optional in practice. It sends the change request
     to the implementer **and** shows both reviewers what was asked for, in one
     step, because the step that keeps getting skipped is the one that is
     separate. Without it the round still bumps, but every finished role is left
     `stale` — visibly un-tasked — and the command says so loudly.

     Keep the same windows alive so the implementer and both reviewers retain
     context. Be explicit and imperative — "apply these exact changes now" — and
     verify the working tree yourself rather than trusting the reply.

7. **Re-review.** When the implementer reports done again, dispatch the delta to
   **both** reviewer windows — the same ones, which remember round N-1 and can
   judge whether their findings were actually addressed:

   ```bash
   ... re-review --unit retry-policy --file .tmux-deliver/messages/retry-policy-r2-delta.md
   ```

   Until you do, both reviewers stay `stale` and `status` keeps saying so. With no
   `--file` it sends a generated re-review request pointing at the round's
   delivery summary, brief and diff. Then return to step 4.

**Each role gets the document its contract expects — they are not the same
document.** The implementer gets a change list ("apply these exact changes now,
commit, report done"). The reviewers get a re-review prompt ("here is the delta;
go through your previous findings and say whether each was addressed"). Sending
the implementer's change list to a reviewer asks it to do the one thing its
contract forbids, and a good reviewer refuses — which costs you a round-trip and
looks like a stall. `send` and `re-review` refuse implementer-shaped text aimed at
a reviewer and point you at `re-review` (`--anyway` overrides).

Having been burned by a forgotten dispatch, the tempting rule is "always send to
all three". That is wrong in a new way. The rule is: **every role that is owed
something gets something, and each gets the right thing.** `next-round
--feedback-file` and `re-review` encode exactly that.

**Mid-round scope changes go in the brief, never only in a message.** If an
implementer reports `blocked` and you authorise files outside its original scope,
`send`ing that authorisation to the implementer alone is invisible to the
reviewers — who will then block the delivery for going outside scope, correctly,
given what they can see. The brief is the only artefact all three roles share:

```bash
... extend-scope --unit retry-policy --message "Also authorised: src/payments/ledger.py, read-only wiring for the retry counter. Reason: the policy cannot be observed without it."
```

That appends a `## SCOPE AMENDMENT — round N, <timestamp>` section to the unit's
brief and tells all three roles to re-read it. Amendments are **not retroactive**:
they carry the timestamp and round they were made in, and a delivery is judged
against the brief as it stood when that round started. The same applies to any
shared decisions document you keep — if you change a rule mid-run, date the
change and say plainly that it binds from that point on, or a reviewer will judge
an already-finished delivery against a rule that postdates it.

**Escalation:** `next-round` refuses past **3 rejected rounds** and marks the unit
`escalated`. When that happens, stop work on the unit and bring the user the
sticking point, both reviewer reports, and the current diff. Only pass `--force`
if the user explicitly authorises another round.

## 5. Acceptance bar

A unit is acceptable only when **all** hold:

- it matches the plan and honours every resolved decision the plan records;
- strict TDD was followed with verifiable red→green evidence (code units);
- all relevant tests pass, and lint/type-check/spec/validator commands pass;
- the plan's "do NOT touch" / out-of-scope items are unchanged;
- no QA or Adversarial **blocking** finding remains unaddressed or unjustified;
- `verify-readonly` is clean, and the unit worktree has no uncommitted changes.

Then accept it — this merges the unit branch into the integration branch with
`--no-ff`, closes its three windows, and removes its worktree:

```bash
... accept --unit retry-policy
```

If the merge conflicts, `accept` leaves the conflict in the integration worktree
and marks the unit `blocked`. Resolve it there yourself (or
`git merge --abort`), then re-run `accept`. Do not have an agent resolve
integration conflicts — that is your job.

Give the user a one-line status update after each acceptance: unit, verdict,
rounds taken.

## 5b. Make the windows visible to the user

If the run has its own session (`--session`) the agent windows land somewhere the
user is not attached to, so **they see nothing** unless you do something about it.
Set the mirror at `init`:

```bash
... init --session tmux-deliver-payments --mirror-session auto   # 'auto' = your own session
... init --session tmux-deliver-payments --mirror-session my-work
```

Every window `start-agent` creates is then linked into that session automatically
and `start-agent` prints `mirror=linked into <session>:<idx>`. Linking is not
copying — it is the same window in two sessions, so the run is not disturbed.

For a run already in flight, or to change or drop the mirror:

```bash
... mirror --session my-work      # link every live agent window, and record it
... mirror --unlink               # remove the mirrored view; the run keeps its windows
```

**Tell the user about the sharp edge before it happens:** `accept` closes a unit's
three windows, and because they are the same windows, they vanish from the
mirrored session too. Their view thins out as units are accepted. `accept` says so
in its output; say it to the user as well, so a shrinking window list reads as
progress rather than as something breaking.

## 6. Finish

```bash
... finish --state-dir .tmux-deliver     # integration branch, diffstat vs base, per-unit status
... cleanup --state-dir .tmux-deliver    # close remaining windows, drop unit worktrees
```

`cleanup` keeps the integration worktree and branch (pass `--all` to drop the
worktree; the branch always survives). Then summarise for the user:

- everything delivered, unit by unit, with rounds taken;
- the integration branch name and how to merge it (`git merge --no-ff <branch>`);
- final test/validator results;
- outstanding manual/out-of-band action items the user still owes;
- the build/deploy/verify steps the plan calls for.

Do **not** commit to the user's branch, merge into it, or open a PR unless they
explicitly ask. The integration branch is the deliverable.

If you hit a genuine ambiguity the plan does not resolve, stop and ask the user
rather than guessing.

## Operating notes

- **Full autonomy panes.** Every agent window launches with approvals bypassed
  (`--dangerously-bypass-approvals-and-sandbox` / `--dangerously-skip-permissions`)
  so nothing stalls on a keypress. Worktree isolation is the containment boundary —
  never point an agent at the user's own working tree.
- **`--claude` mode has a machine precondition.** Claude Code blocks on two
  first-run dialogs that its CLI flags do not suppress. `start-agent` clears the
  per-worktree one (a new worktree can never be pre-trusted), but it will **not**
  set the global `bypassPermissionsModeAccepted` — that is a permanent change to
  the user's own Claude Code behaviour, not a delivery run's decision. If that key
  is not already set, `start-agent` **fails fast** with instructions before
  creating any window. Do not work around it by editing `~/.claude.json` yourself;
  relay the message and let the user accept the dialog once by hand, or switch to
  `--codex` (which needs no acceptance and writes nothing).
- **Never send stray keystrokes** to a pane. Use `send`, which pastes via a tmux
  buffer, verifies the paste landed, submits, and confirms the CLI took it.
  Broadcasting Enter can re-trigger a finished agent into duplicate work.
- **Prompts go in by reference, not by value.** Pasting a whole prompt into a TUI
  is how runs break: a real launch arrived 1,520 characters short, truncating the
  brief exactly where its acceptance criteria were. So `start-agent` and `send`
  type **one line** telling the agent to read the file in
  `.tmux-deliver/prompts/` (or `messages/`). One line has no newlines to be read
  as Enter and no paste heuristic to misfire. `--prompt-mode inline` pastes the
  text instead — now with bracketed paste (`paste-buffer -p`), which is reliable,
  and verified before Enter either way.
- **If a pane's composer is mangled, restart it.** `C-u` and `C-c` will not clear
  one. `tmux respawn-pane -k -t <pane> '<launch cmd>'` keeps the pane id, so unit
  state stays valid. `start-agent` does this for you between delivery attempts;
  never do it to a reviewer or implementer window you want to keep, because it
  discards the context that makes re-review meaningful.
- **`launched` is a launcher-written status, not an agent's word.** Only
  `running` comes from the agent. Treat a unit still at `launched` as a failed
  launch and go look at the pane. The same holds for `notified` (you sent it
  something; it has not acknowledged) and `stale` (you have sent it nothing since
  the round bumped — it is idle and it is waiting on *you*).
- **The recorded status is bookkeeping; the pane is the fact.** `status` samples
  both and prints a `LIVENESS` block — alive/dead, busy/idle, time since the pane
  last changed — and shouts where the two disagree. Read it. Two consecutive
  rounds were lost to a table that said `running` about reviewers nobody had
  tasked. `--no-liveness` turns sampling off; it then says so rather than
  implying it checked.
- **`DEAD` means the pane is gone** (usually a tmux server restart, which takes
  every agent window at once); `no-window` means none was ever recorded. Both
  need `start-agent --relaunch` — neither is a slow agent.
- **Never read the composer line as evidence.** Both CLIs render rotating
  placeholder hints there ("Explain this codebase", "Improve documentation in
  @filename"), so a pane can look alive with nothing running. The busy indicator
  and changes in output above the composer are the honest signals, and they are
  what the liveness check uses.
- **Reviewer windows are per-unit and long-lived** by design — killing them
  between rounds throws away the context that makes re-review worth anything.
- After an interruption, run `recover` before doing anything else: it reports
  which panes are still alive and which died, counting a dead pane as a
  contradiction. Treat `delivered` and `reviewed` as *awaiting your judgement*,
  never as accepted.

Begin by parsing the invocation (§0) and establishing the plan (§1): read it, post
the work-unit list in delivery order with dependencies, the manual action items,
and the launch table — then confirm with the user before running `init`.
